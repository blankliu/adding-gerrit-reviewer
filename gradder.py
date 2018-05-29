#!/usr/bin/python

import os
import re
import sys
import json
import string
import logging
import requests
import argparse

from ConfigParser       import  SafeConfigParser
from requests.auth      import  HTTPDigestAuth

# Authentication file for Gerrit REST API
GERRIT_AUTH_CONFIG_FILE         =   '.gerrit/grcauth.json'

# Parent folder of project-specific config of reviewers
REVIEWERS_CONFIG_FOLDER         =   'reviewer-config'

GLOABLE_REVIEWERS_CONFIG_FILE   =   'global_reviewers.cfg'

REVIEWERS_EMAIL_CONFIG_FILE     =   'reviewers_email.cfg'
REVIEWERS_EMAIL_SECTION_NAME    =   'Reviewers Email'

REVIEWERS_OPTION_NAME           =   'reviewers'

# Branches which are outside of coverage of adding reviewers
EXCEPTION_BRANCHES = [
    '',
]

# Topics which are outside of coverage of adding reviewers
EXCEPTION_TOPIC_RE = re.compile(
    r'(ui|bugfix|feature|pointfix|backward)_int_sig-[0-9]*'
)

# Valid HTTP status codes returned by Gerrit REST APIs
VALID_HTTP_CODES_OF_REST_API    =   [
    200,
    204,
]

# Define error codes
ERROR_CODE_GERRIT_AUTH_CONFIG_NOT_FOUND     =   1
ERROR_CODE_UNDESIRABLE_PATCHSET_FOUND       =   2
ERROR_CODE_GERRIT_CHANGE_NOT_FOUND          =   3
ERROR_CODE_REVIEWERS_NOT_ALL_ADDED          =   4


class TrimmedGerritChange:
    def __init__(self, project, branch, change_number, patchset_number, topic,
        files):
        self.project = project
        self.branch = branch
        self.change_number = change_number
        self.patchset_number = patchset_number
        self.topic = topic
        self.files = files

class GerritRestClient:
    def __init__(self, log_level):
        self.logger = config_logger(__name__, log_level)

        login_cfg_file = os.path.join(os.path.expanduser('~'),
            GERRIT_AUTH_CONFIG_FILE)
        if not os.path.isfile(login_cfg_file):
            self.logger.error('found no Gerrit config file: %s' %
                login_cfg_file)
            raise SystemExit(ERROR_CODE_GERRIT_AUTH_CONFIG_NOT_FOUND)

        with open(login_cfg_file) as f:
            cfg_json = json.load(f)
        self.__initializeClient(cfg_json['canonicalurl'],
            cfg_json['username'],
            cfg_json['password'])

    def __initializeClient(self, server, username, password):
        self.apiUrl = server.rstrip('/')

        if username and password:
            self.apiUrl = self.apiUrl + '/a'
            self.auth = HTTPDigestAuth(username, password)
        else:
            self.auth = None

        self.session = requests.Session()
        self.session.auth = self.auth
        self.session.verify = False

    def __analyseResponse(self, response):
        self.logger.info('response status_code: %s' % response.status_code)

        result = response.content
        if response.status_code not in VALID_HTTP_CODES_OF_REST_API:
            self.logger.error("response content: '%s'" % result)

            return None

        return json.loads(result[5:])

    def __get(self, endpoint):
        self.logger.info('request: [%s] %s', 'GET', endpoint)
        return self.__analyseResponse(
            self.session.get(self.apiUrl + endpoint, auth=self.auth))

    def __post(self, endpoint, data):
        self.logger.info('request: [%s] %s', 'POST', endpoint)
        return self.__analyseResponse(
            self.session.post(self.apiUrl + endpoint,
                data,
                headers={'content-type': 'application/json'}))

    def __put(self, endpoint, data):
        self.logger.info('request: [%s] %s', 'PUT', endpoint)
        return self.__analyseResponse(
            self.session.put(self.apiUrl + endpoint,
                data,
                headers={'content-type': 'application/json'}))

    def __delete(self, endpoint):
        self.logger.info('request: [%s] %s', 'DELETE', endpoint)
        return self.__analyseResponse(
            self.session.delete(self.apiUrl + endpoint, auth=self.auth))

    def __queryChanges(self, query_option, change_number):
        endpoint = '/changes/?q=%s&%s' % (change_number, query_option)
        change = self.__get(endpoint)
        if not change:
            self.logger.error('found no Gerrit change: %d' % change_number)
            raise SystemExit(ERROR_CODE_GERRIT_CHANGE_NOT_FOUND)

        return change

    def getServerUrl(self):
        return self.apiUrl.rstrip('/a')

    def getChange(self, change_number):
        # Get the only change by index 0
        query_option = 'o=CURRENT_REVISION&o=CURRENT_FILES'
        change_json = self.__queryChanges(query_option, change_number)[0]
        self.logger.info('current patchset of change: %d\n%s' % (change_number,
            json.dumps(change_json, indent=4)))

        change = dict(change_json)
        cur_revision= change['current_revision']
        files_dict = change['revisions'][cur_revision]['files']
        files_list = []
        for key, value in files_dict.items():
            files_list.append(str(key))
            if value.has_key('old_path'):
                files_list.append(str(value['old_path']))

        patchset_number = change['revisions'][cur_revision]['_number']
        trimmed_change = TrimmedGerritChange(change.get('project'),
            change.get('branch'),
            change_number,
            patchset_number,
            change.get('topic', None),
            files_list)

        return trimmed_change

    def addReviewer(self, change_number, reviewer_email):
        data = json.dumps({'reviewer': reviewer_email})
        result = self.__post('/changes/%s/reviewers' % change_number, data)
        if not result:
            self.logger.error('fail to add reviewer: %s' % reviewer_email)
            return False

        return True


class GerritReviewerAdder:
    def __init__(self, gerrit_client, log_level):
        self.logger = config_logger(__name__, log_level)

        self.grestClient = gerrit_client
        self.projectParser = SafeConfigParser()
        self.emailParser = SafeConfigParser()

        self.wsAbsolutePath = os.path.dirname(os.path.realpath(sys.argv[0]))

    def __collectReviewerEmails(self, reviewers):
        reviewer_emails = []
        for name in reviewers:
            if self.emailParser.has_option(REVIEWERS_EMAIL_SECTION_NAME, name):
                email = self.emailParser.get(REVIEWERS_EMAIL_SECTION_NAME, name)
                reviewer_emails.append(email)
            else:
                self.logger.warning('no email configured for reviewer: %s' %
                    name)

        return reviewer_emails

    def prepareReviewerParser(self, config_file):
        # Get absolute path of the reviewers config file
        cfgfile_rel_path = os.path.join(REVIEWERS_CONFIG_FOLDER, config_file)
        cfgfile_abs_path = os.path.join(self.wsAbsolutePath, cfgfile_rel_path)

        # Clean up sections in config parser before reading a new config file
        if len(self.projectParser.sections()) != 0:
            for item in self.projectParser.sections():
                self.projectParser.remove_section(item)

        if os.path.isfile(cfgfile_abs_path):
            self.logger.info('found reviewer config file: %s' %
                cfgfile_rel_path)

            # Parse project config file
            self.projectParser.read(cfgfile_abs_path)
        else:
            self.logger.warning('found no reviewer config file: %s' %
                cfgfile_rel_path)

            proj = config_file.rstrip('.cfg').replace('^', '/')
            while True:
                proj_prefix_name = os.path.dirname(proj)
                if not proj_prefix_name:
                    return False

                proj_prefix_cfgfile = proj_prefix_name.replace(
                    '/', '^') + '.cfg'
                proj_prefix_cfgfile_rel_path = os.path.join(
                    REVIEWERS_CONFIG_FOLDER,
                    proj_prefix_cfgfile)
                proj_prefix_cfgfile_abs_path = os.path.join(
                    self.wsAbsolutePath,
                    proj_prefix_cfgfile_rel_path)

                self.logger.info('search for upper level'
                    ' reviewer config file: %s' % proj_prefix_cfgfile_rel_path)

                if os.path.isfile(proj_prefix_cfgfile_abs_path):
                    self.logger.info('found upper level config file: %s' %
                        proj_prefix_cfgfile_rel_path)

                    # Parse project config file
                    self.projectParser.read(proj_prefix_cfgfile_abs_path)

                    break
                else:
                    self.logger.warning('found no upper level config file: %s' %
                        proj_prefix_cfgfile_rel_path)

                proj = proj_prefix_name

        return True

    def prepareEmailParser(self):
        emailfile_rel_path = os.path.join(REVIEWERS_CONFIG_FOLDER,
            REVIEWERS_EMAIL_CONFIG_FILE)
        emailfile_abs_path = os.path.join(self.wsAbsolutePath,
            emailfile_rel_path)
        if os.path.isfile(emailfile_abs_path):
            self.logger.info('found reviewers email config file: %s' %
                emailfile_rel_path)
        else:
            self.logger.error('found no reviewers email config file: %s' %
                emailfile_rel_path)

            return False

        # Parse email config file
        self.emailParser.read(emailfile_abs_path)

        return True

    def getReviewers(self, project, branch, change_number, files=[], topic=None):
        result = []

        # Check exception branches
        if branch in EXCEPTION_BRANCHES:
            self.logger.info('skip adding reviewers for exception branch: %s' %
                branch)
            return result

        # Check exception topics
        if topic and EXCEPTION_TOPIC_RE.match(topic):
            self.logger.info('skip adding reviewers for exception topic: %s' %
                topic)
            return result

        self.logger.info('workspace path: %s' % self.wsAbsolutePath)

        # Check emails parser
        if not self.prepareEmailParser():
            return result

        # First, check global reviewers
        if self.prepareReviewerParser(GLOABLE_REVIEWERS_CONFIG_FILE):
            filters_list = self.projectParser.sections()
            for item in filters_list:
                filter_re = string.split(item, 'filter ')[1].strip('"')
                self.logger.info('<G> found filter RE: "%s"' % filter_re)
                if re.match(r'^branch:\S*$', filter_re):
                    filter_branch = filter_re.split(':')[1]
                    if re.match(filter_branch, branch):
                        reviewers = self.projectParser.get(item,
                            REVIEWERS_OPTION_NAME)
                        self.logger.info('<G> found reviewers: %s' %
                            str(reviewers.split(' ')))

                        result.extend(self.__collectReviewerEmails(
                            reviewers.split()))
                    else:
                        self.logger.info('<G> filter RE not matched for'
                            + ' branch: %s' % branch)
                else:
                    self.logger.warning('<G> encounter unqualified filter'
                        + ' RE: "%s"' % filter_re)

        # Second, check project-based reviewers
        project_cfg_file = project.replace('/', '^') + '.cfg'
        if not self.prepareReviewerParser(project_cfg_file):
            self.logger.info('found no reviewer config file for project: %s' %
                project)
            self.logger.warning('no reviewers will be added for change: %s' % 
                os.path.join(self.grestClient.getServerUrl(), str(
                    change_number)))

            return result

        self.logger.info('found reviewer config file for project: %s' % project)

        filters_list = self.projectParser.sections()
        for item in filters_list:
            filter_re = string.split(item, 'filter ')[1].strip('"')
            self.logger.info('<P> found filter RE: "%s"' % filter_re)

            # Deal with filters which contains branch info
            if 'branch:' in item:
                if 'file:' in item:
                    # Deal with filter "branch:... file:..."
                    if not re.match(r'^branch:\S* file:\S*$', filter_re):
                        self.logger.warning('encounter unqualified'
                            + ' filter RE: "%s"' % filter_re)
                        continue

                    filter_branch  = filter_re.split(' ')[0].split('branch:')[1]
                    filter_file_re = filter_re.split(' ')[1].split('file:')[1]

                    if re.match(filter_branch, branch):
                        self.logger.info('<P> RE pattern to match file: "%s"' %
                            filter_file_re)
                        for f in files:
                            if re.match(filter_file_re, f):
                                self.logger.info('<P> found matched file: %s' % f)

                                reviewers = self.projectParser.get(
                                    item, REVIEWERS_OPTION_NAME)
                                self.logger.info('<P> found reviewers: %s' %
                                    str(reviewers.split(' ')))

                                result.extend(self.__collectReviewerEmails(
                                    reviewers.split()))
                            else:
                                self.logger.info('<P> found unmatched file: %s' % f)
                    else:
                        self.logger.info('change branch [%s] not match'
                            ' filter branch [%s]' % (branch, filter_branch))
                else:
                    # Deal with filter "branch:..."
                    filter_branch = filter_re.split(':')[1]
                    if re.match(filter_branch, branch):
                        reviewers = self.projectParser.get(item,
                            REVIEWERS_OPTION_NAME)
                        self.logger.info('<P> found reviewers: %s' % str(
                            reviewers.split(' ')))

                        # Once a filter is matched, adds reviewers and exits
                        result.extend(self.__collectReviewerEmails(
                            reviewers.split()))
                    else:
                        self.logger.info('<P> found no reviewer configured for'
                            + ' branch: %s' % branch)
            else:
                if 'file:' in item:
                    # Deal with filter "file:..."
                    filter_file_re = filter_re.split('file:')[1]
                    for f in files:
                        if re.match(filter_file_re, f):
                            self.logger.info('<P> found matched file: %s' % f)
                            reviewers = self.projectParser.get(item,
                                REVIEWERS_OPTION_NAME)

                            self.logger.info('<P> found reviewers: %s' % str(
                                reviewers.split(' ')))
                            # Once a filter is matched, adds reviewers and exits
                            result.extend(self.__collectReviewerEmails(
                                reviewers.split()))

                            break
                        else:
                            self.logger.info('found unmatched file: %s' % f)
                else:
                    # Find unqualified filter
                    self.logger.warning('encounter unqualified'
                        + ' filter RE: "%s"' % filter_re)

        reviewers_list = list(set(result))
        self.logger.info('all qualified reviewers: %s' % reviewers_list)

        return reviewers_list

    def addReviewers(self, change_number, force=False, dryrun=False):
        trimmed_change = self.grestClient.getChange(change_number)

        if not force and trimmed_change.patchset_number != 1:
            self.logger.error('under normal mode,'
                ' no reviewers will be added for patchset: %d' % patchset)
            raise SystemExit(ERROR_CODE_UNDESIRABLE_PATCHSET_FOUND)

        allAdded = True
        reviewers = self.getReviewers(trimmed_change.project,
            trimmed_change.branch,
            trimmed_change.change_number,
            trimmed_change.files,
            trimmed_change.topic)
        for reviewer in reviewers:
            if not dryrun:
                self.logger.info('add reviewer: %s' % reviewer)
                if not self.grestClient.addReviewer(change_number, reviewer):
                    allAdded = False
            else:
                self.logger.info('<DRY_RUN> add reviewer: %s' % reviewer)

        if not allAdded:
            raise SystemExit(ERROR_CODE_REVIEWERS_NOT_ALL_ADDED)

def config_logger(module, log_level='info'):
    logger = logging.getLogger(module)
    log_format  = ('%(module)-8s [%(levelname)-.1s]'
        ' %(asctime)s [%(lineno)3d] %(message)s')
    date_format = '%Y-%m-%d %H:%M:%S'
    logging.basicConfig(level=log_level.upper(),
        format=log_format,
        datefmt=date_format)

    return logger

def parse_options():
    desc = ('Gerrit Reviewer Adder'
        '\n\n'
        'A script used to add reviewers for Gerrit changes according to'
            'reviewing configuration.'
        '\n\n'
        'Two modes are designed for adding reviewers.'
        '\n1. Normal Mode'
        '\n   - It works on condition that Gerrit change patchset number'
            ' must be 1.'
        '\n2. Force Mode'
        '\n   - It works no matter what the Gerrit change patchset number is.')

    parser = argparse.ArgumentParser(
        description=desc,
        epilog='Note: Python 2.7.x required.',
        formatter_class=argparse.RawTextHelpFormatter,
        conflict_handler='resolve')

    parser.add_argument('--log-level', dest='log_level',
        action='store',
        default='info',
        choices=['debug', 'info', 'warning', 'error'],
        help='Specify logging level.')

    parser.add_argument('--gerrit-change-number', dest='gerrit_change_number',
        action='store',
        type=int,
        required=True,
        help='Specify the change number of a Gerrit change.')

    parser.add_argument('-f', '--force', dest='force',
        action='store_true',
        required=False,
        help='In force mode, reviewers will be added unconditionally.')

    parser.add_argument('-n', '--dry-run', dest='dryrun',
        action='store_true',
        default=False,
        help='In dry-run mode, do not add reviewer for Gerrit changes.')

    return parser.parse_args()

def main(options):
    gerrit = GerritRestClient(options.log_level)
    adder = GerritReviewerAdder(gerrit, options.log_level)
    adder.addReviewers(options.gerrit_change_number,
        options.force,
        options.dryrun)

if __name__ == '__main__':
    main(parse_options())

# vim: set shiftwidth=4 tabstop=4 expandtab
