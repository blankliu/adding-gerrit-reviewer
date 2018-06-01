# Project adding-gerrit-reviewer
A Python script used to add reviewers for Gerrit changes.

# Features
- Two modes to add reviewers: global mode and project-specific mode
- Global mode use branch as unit to filter reviewers
- Project-specific mode use either branch or file or both as unit to filter
  reviewers
- RE is used to enhancing specifying branches and files
- A set of reviewer configuration is used to control how to add reviewers

# Usage
Script gradder.py is designed to used for a Jenkins job whose builds is
triggered by Gerrit Trigger plugins automatically. Therefore, every time a
change is uploaded to Gerrit, corresponding reviewers can be added according to
reviewer configuration.

Besides, it supports manual triggering as well by accepting Gerrit change number
as argument.

### How It Works
To add reviewers for a Gerrit change, two things must be figured out.
- Which project the change belongs to
- Which branch the change is uploaded to

And as an extension, file contained in a Gerrit change could be used as a
criterion to decide what files must be reviewed by who.

##### 1. In Global Mode
- The Gerrit project a change belongs to is ignored
- Only use the branch a change is uploaded to decide reviewers

##### 2. In Project-Specific Mode
- Each Gerrit project has a separate configuration file to decide reviewers
- Branch, or file or their combination can be used to decide reviewers

# Prerequisites

### 1. Python Environment
- Python 2.7+
- Python module requests

### 2. Gerrit Version
- Gerrit 2.14
> Note
> 1. Ideally, this script work for Gerrit 2.14+ because Digest Authentication
>    is removed since Gerrit 2.14.
> 2. For Gerrit 2.13 and older versions, replace module **HTTPBasicAuth** with
>    with **HTTPDigestAuth** in the script.
> 3. Anyway, you should test the script under your own Gerrit version.

### 2. Authentication for Gerrit REST API
- In the machine which runs this script, an authentication file named
  **$HOME/.gerrit/grcauth.json** which contains following information is
  required.
```json
{
  "username": "blankliu",
  "password": "000000000000000000000000000000000000000000",
  "canonicalurl": "https://gerrit.example.com"
}
```
> Note
> 1) Field password is the **HTTP password** of the specified user.
>    This password can be found via Gerrit "Settings" -> "HTTP Password".
> 2) Use fild mode 600 for this file so that nobody else can read it.

# How to Understand Reviewer Configuration
Take the following configuration structure to illustrate how reviewers are
configured.
```
reviewer-config/
├── global_reviewers.cfg
├── devops^buildtools.cfg
├── platform^hardware^ti.cfg
├── platform^hardware^ti^omap3.cfg
├── platform^hardware^ti^omap4-aah.cfg
├── platform^manifest.cfg
├── platform^packages^apps^browser.cfg
├── platform^packages^apps^calendar.cfg
└── reviewers_email.cfg

```

### 1. Folder reviewer-config
- All configuration files must be placed under folder **reviewer-config**.
- It must be placed at the same path with script **gradder.py**.

### 2. File reviewers_email.cfg
- It maps readable name and email for reviewers so that names can be used
  conveniently.
- For example, the following file defines 8 users whose names are ENG.Alpha,
  ENG.Beta, ENG.Gamma, ENG.Omega, App, DevOps, Service and TI.
```shell
$ cat reviewers_email.cfg
[Reviewers Email]
ENG.Alpha           =   eng.alpha@example.com
ENG.Beta            =   eng.beta@example.com
ENG.Gamma           =   eng.gamma@example.com
ENG.Omega           =   eng.omega@example.com
App                 =   app@example.com
DevOps              =   devops@example.com
Service             =   service@example.com
TI                  =   ti@example.com
```

### 3. File global_reviewers.cfg
- It's used to implement global mode which means once a branch specified in this
  file matches with a Gerrit change's branch, all related reviewers are added.
- For example, the following file indicates any Gerrit change uploaded to branch
  prefixed with **release/** or **bugfix/** will use DevOps as reviewer.
```shell
$ cat global_reviewers.cfg
# Match branches prefixed with release/ or bugfix/
[filter "branch:(release/.*|bugfix/.*)"]
reviewers = DevOps
```

### 4. File <converted_project_name>.cfg
- It's used to implement project-specific mode which limit adding reviewers to a
  specific Gerrit project.
- The name <converted_project_name> comes from replacing every slash (/) with
  caret (^) in a project name, such as "platform/packages/apps/browser" becomes
  "platform^packages^apps^browser".
- For example, the following file indicates these two reviewing rules.
> 1. Add ENG.Omega as reviewer for all Gerrit changes belonging to project
>    platform/packages/apps/browser under branch specified by RE feature/.*
> 2. Add Service as reviewer for Gerrit changes belonging to the same project
>    under branch series bugfix/.* and containing at least one file whose path
>    starts with service.
```shell
$ cat platform^packages^apps^browser.cfg
# Match all branches
[filter "branch:feature/.*"]
reviewers = ENG.Omega

# Match files whose patch is prefixed with service/ under branch bugfix/.*
[filter "branch:bugfix/.* file:service/.*"]
reviewers = Service
```

**Special Case: platform^hardware^ti.cfg**
> In fact, there is no project named **platform/hardware/ti** in Gerrit.
> Using this configuration file as an alternative to add reviewers for Gerrit
> changes which belongs to any Gerrit project whose name prefixes with path
> platform/hardware/ti, such as project platform/hardware/ti/omap5.
> Once project platform/hardware/ti/omap5 has it own configuration file, this
> file doesn't work for its changes anymore.
