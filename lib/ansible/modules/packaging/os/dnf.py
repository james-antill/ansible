#!/usr/bin/python
# -*- coding: utf-8 -*-

# Copyright 2015 Cristian van Ee <cristian at cvee.org>
# Copyright 2015 Igor Gnatenko <i.gnatenko.brain@gmail.com>
#
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)

from __future__ import absolute_import, division, print_function
__metaclass__ = type


ANSIBLE_METADATA = {'metadata_version': '1.1',
                    'status': ['stableinterface'],
                    'supported_by': 'core'}


DOCUMENTATION = '''
---
module: dnf
version_added: 1.9
short_description: Manages packages with the I(dnf) package manager
description:
     - Installs, upgrade, removes, and lists packages and groups with the I(dnf) package manager.
options:
  name:
    description:
      - "A list of package names, or package specifier with version, like C(name-1.0)
        When using state=latest, this can be '*' which means run: dnf -y update.
        You can also pass a url or a local path to a rpm file."
    required: true

  list:
    description:
      - Various (non-idempotent) commands for usage with C(/usr/bin/ansible) and I(not) playbooks. See examples.

  state:
    description:
      - Whether to install (C(present), C(latest)), or remove (C(absent)) a package.
    choices: [ "present", "latest", "absent", "installed", "removed", "enable", "disable" ]
    default: "present"

  enablerepo:
    description:
      - I(Repoid) of repositories to enable for the install/update operation.
        These repos will not persist beyond the transaction.
        When specifying multiple repos, separate them with a ",".

  disablerepo:
    description:
      - I(Repoid) of repositories to disable for the install/update operation.
        These repos will not persist beyond the transaction.
        When specifying multiple repos, separate them with a ",".

  conf_file:
    description:
      - The remote dnf configuration file to use for the transaction.

  disable_gpg_check:
    description:
      - Whether to disable the GPG checking of signatures of packages being
        installed. Has an effect only if state is I(present) or I(latest).
    type: bool
    default: 'no'

  installroot:
    description:
      - Specifies an alternative installroot, relative to which all packages
        will be installed.
    version_added: "2.3"
    default: "/"

  autoremove:
    description:
      - If C(yes), removes all "leaf" packages from the system that were originally
        installed as dependencies of user-installed packages but which are no longer
        required by any such package. Should be used alone or when state is I(absent)
    type: bool
    version_added: "2.4"

  modularity:
    description:
      - If C(yes), group install/upgrade/remove operations now apply to modules if set to false then those operations apply to "comps groups".
    type: bool
    version_added: "?.?"
    default: true

notes:
  - When used with a `loop:` each package will be processed individually, it is much more efficient to pass the list directly to the `name` option.
requirements:
  - "python >= 2.6"
  - python-dnf
  - for the autoremove option you need dnf >= 2.0.1"
  - for the modularity option to be set you need dnf with modularity support
author:
  - '"Igor Gnatenko (@ignatenkobrain)" <i.gnatenko.brain@gmail.com>'
  - '"Cristian van Ee (@DJMuggs)" <cristian at cvee.org>'
  - "Berend De Schouwer (github.com/berenddeschouwer)"
'''

RETURN = '''
failures:
    description: list of install failures
    returned: failure, when needed
    type: list
results:
    description: list of pkgs installed
    returned: success, when needed
    type: list
stdout:
    description: output from dnf API
    returned: success, when needed
    type: string
stderr:
    description: error output from dnf API
    returned: success, when needed
    type: string
'''

EXAMPLES = '''
- name: install the latest version of Apache
  dnf:
    name: httpd
    state: latest

- name: remove the Apache package
  dnf:
    name: httpd
    state: absent

- name: install the latest version of Apache from the testing repo
  dnf:
    name: httpd
    enablerepo: testing
    state: present

- name: upgrade all packages
  dnf:
    name: "*"
    state: latest

- name: install the nginx rpm from a remote repo
  dnf:
    name: 'http://nginx.org/packages/centos/6/noarch/RPMS/nginx-release-centos-6-0.el6.ngx.noarch.rpm'
    state: present

- name: install nginx rpm from a local file
  dnf:
    name: /usr/local/src/nginx-release-centos-6-0.el6.ngx.noarch.rpm
    state: present

- name: install the 'Development tools' package group
  dnf:
    name: '@Development tools'
    state: present

- name: Autoremove unneeded packages installed as dependencies
  dnf:
    autoremove: yes

- name: Uninstall httpd but keep its dependencies
  dnf:
    name: httpd
    state: absent
    autoremove: no
'''
import os

try:
    import dnf
    import dnf.cli
    import dnf.const
    import dnf.exceptions
    import dnf.subject
    import dnf.util
    HAS_DNF = True
except ImportError:
    HAS_DNF = False

from ansible.module_utils.basic import AnsibleModule
from ansible.module_utils._text import to_native
from ansible.module_utils.six import PY2
from distutils.version import LooseVersion

_modularity_available = False
try:
    from dnf.module.exceptions import NoModuleException
    _modularity_available = True
except:
    pass
# _modularity = False
_modularity = _modularity_available
# Needed for the FIXUP code
import fnmatch


def _ensure_dnf(module):
    if not HAS_DNF:
        if PY2:
            package = 'python2-dnf'
        else:
            package = 'python3-dnf'

        if module.check_mode:
            module.fail_json(msg="`{0}` is not installed, but it is required"
                             "for the Ansible dnf module.".format(package))

        module.run_command(['dnf', 'install', '-y', package], check_rc=True)
        global dnf
        try:
            import dnf
            import dnf.cli
            import dnf.const
            import dnf.exceptions
            import dnf.subject
            import dnf.util
        except ImportError:
            module.fail_json(msg="Could not import the dnf python module. "
                                 "Please install `{0}` package.".format(package))


def _configure_base(module, base, conf_file, disable_gpg_check, installroot='/'):
    """Configure the dnf Base object."""
    conf = base.conf

    # Turn off debug messages in the output
    conf.debuglevel = 0

    # Set whether to check gpg signatures
    conf.gpgcheck = not disable_gpg_check

    # Don't prompt for user confirmations
    conf.assumeyes = True

    # Set installroot
    conf.installroot = installroot

    # Change the configuration file path if provided
    if conf_file:
        # Fail if we can't read the configuration file.
        if not os.access(conf_file, os.R_OK):
            module.fail_json(
                msg="cannot read configuration file", conf_file=conf_file)
        else:
            conf.config_file_path = conf_file

    # Read the configuration file
    conf.read()


def _specify_repositories(base, disablerepo, enablerepo):
    """Enable and disable repositories matching the provided patterns."""
    base.read_all_repos()
    repos = base.repos

    # Disable repositories
    for repo_pattern in disablerepo:
        for repo in repos.get_matching(repo_pattern):
            repo.disable()

    # Enable repositories
    for repo_pattern in enablerepo:
        for repo in repos.get_matching(repo_pattern):
            repo.enable()


def _base(module, conf_file, disable_gpg_check, disablerepo, enablerepo, installroot):
    """Return a fully configured dnf Base object."""
    base = dnf.Base()
    _configure_base(module, base, conf_file, disable_gpg_check, installroot)
    _specify_repositories(base, disablerepo, enablerepo)
    base.fill_sack(load_system_repo='auto')
    return base


def _package_dict(package):
    """Return a dictionary of information for the package."""
    # NOTE: This no longer contains the 'dnfstate' field because it is
    # already known based on the query type.
    result = {
        'name': package.name,
        'arch': package.arch,
        'epoch': str(package.epoch),
        'release': package.release,
        'version': package.version,
        'repo': package.repoid}
    result['nevra'] = '{epoch}:{name}-{version}-{release}.{arch}'.format(
        **result)

    return result


def list_items(module, base, command):
    """List package info based on the command."""
    # Rename updates to upgrades
    if command == 'updates':
        command = 'upgrades'

    # Return the corresponding packages
    if command in ['installed', 'upgrades', 'available']:
        results = [
            _package_dict(package)
            for package in getattr(base.sack.query(), command)()]
    # Return the enabled repository ids
    elif command in ['repos', 'repositories']:
        results = [
            {'repoid': repo.id, 'state': 'enabled'}
            for repo in base.repos.iter_enabled()]
    # Return any matching packages
    else:
        packages = dnf.subject.Subject(command).get_best_query(base.sack)
        results = [_package_dict(package) for package in packages]

    module.exit_json(results=results)


def _mark_package_install(module, base, pkg_spec):
    """Mark the package for install."""
    try:
        base.install(pkg_spec)
    except dnf.exceptions.MarkingError:
        module.fail_json(msg="No package {0} available.".format(pkg_spec))


# FIXME: This is because the DNF API seem to just abort inside hawkey with
#        various input. Including things like installed modules passed to
#        install. This is all bad.

def _mod_match_profiles(prefix, profiles, ui):
    if fnmatch.fnmatch(prefix, ui):
        return True
    for prof in profiles:
        data = prefix + "/" + prof
        if fnmatch.fnmatch(data, ui):
            return True
    return False


def _mod_match(mod, ui):
    data = mod.name
    if _mod_match_profiles(data, mod.profiles, ui):
        return True
    data = mod.name + ":" + mod.stream
    if _mod_match_profiles(data, mod.profiles, ui):
        return True
    data = mod.name + ":" + mod.stream + ":" + str(mod.version)
    if _mod_match_profiles(data, mod.profiles, ui):
        return True

    return False


def _mod_available(base, ui):
    amods = base.repo_module_dict.list_module_version_all()
    for amod in amods:
        if _mod_match(amod, ui):
            return True
    return False


def _mod_enabled(base, ui):
    emods = base.repo_module_dict.list_module_version_enabled()
    for emod in emods:
        if _mod_match(emod, ui):
            return True
    return False


def _mod_installed(base, ui):
    imods = base.repo_module_dict.list_module_version_installed()
    for imod in imods:
        if _mod_match(imod, ui):
            return imod
    return None


def _mod_upgrade(base, ui):
    amods = base.repo_module_dict.list_module_version_all()
    for amod in amods:
        if _mod_match(amod, ui):
            imod = _mod_installed(base, amod.name + ":" + amod.stream)
            if imod is None:  # Shrug
                continue
            if amod.version > imod.version:
                return True
    return False


def _parse_ui_spec(names):
    pkg_specs, mod_specs, grp_specs, filenames = [], [], [], []
    for name in names:
        if name.endswith(".rpm"):
            filenames.append(name)
        elif _modularity and name.startswith("@"):
            mod_specs.append(name[1:])
        elif name.startswith("@"):
            grp_specs.append(name[1:])
        else:
            pkg_specs.append(name)
    return pkg_specs, mod_specs, grp_specs, filenames


def _install_remote_rpms(base, filenames):
    if int(dnf.__version__.split(".")[0]) >= 2:
        pkgs = list(sorted(base.add_remote_rpms(list(filenames)), reverse=True))
    else:
        pkgs = []
        for filename in filenames:
            pkgs.append(base.add_remote_rpm(filename))
    for pkg in pkgs:
        base.package_install(pkg)


def ensure(module, base, state, names, autoremove):
    # Accumulate failures.  Package management modules install what they can
    # and fail with a message about what they can't.
    failures = []
    allow_erasing = False

    # Autoremove is called alone
    # Jump to remove path where base.autoremove() is run
    if not names and autoremove is not None:
        names = []
        state = 'absent'

    if names == ['*'] and state == 'latest':
        base.upgrade_all()
    else:
        pkg_specs, mod_specs, group_specs, filenames = _parse_ui_spec(names)
        if group_specs:
            base.read_comps()

        pkg_specs = [p.strip() for p in pkg_specs]
        filenames = [f.strip() for f in filenames]
        groups = []
        environments = []
        for group_spec in (g.strip() for g in group_specs):
            group = base.comps.group_by_pattern(group_spec)
            if group:
                groups.append(group.id)
            else:
                environment = base.comps.environment_by_pattern(group_spec)
                if environment:
                    environments.append(environment.id)
                else:
                    module.fail_json(
                        msg="No group {0} available.".format(group_spec))

        if state in ['installed', 'present']:
            # Install files.
            _install_remote_rpms(base, filenames)

            # Install modules.
            for mod in mod_specs:
                if _mod_installed(base, mod):  # API seems to exist if installed
                    continue
                if not _mod_available(base, mod):
                    failures.append((mod, "Can't find module"))
                    continue

                try:
                    fmods = base.repo_module_dict.install([mod])
                except dnf.exceptions.Error as e:
                    failures.append((mod, to_native(e)))

            # Install groups.
            for group in groups:
                try:
                    base.group_install(group, dnf.const.GROUP_PACKAGE_TYPES)
                except dnf.exceptions.Error as e:
                    # In dnf 2.0 if all the mandatory packages in a group do
                    # not install, an error is raised.  We want to capture
                    # this but still install as much as possible.
                    failures.append((group, to_native(e)))

            for environment in environments:
                try:
                    base.environment_install(environment, dnf.const.GROUP_PACKAGE_TYPES)
                except dnf.exceptions.Error as e:
                    failures.append((environment, to_native(e)))

            # Install packages.
            for pkg_spec in pkg_specs:
                _mark_package_install(module, base, pkg_spec)

        elif state == 'latest':
            # "latest" is same as "installed" for filenames.
            _install_remote_rpms(base, filenames)

            # Upgrade modules.
            for mod in mod_specs:
                if not _mod_installed(base, mod):  # API doesn't install
                    if not _mod_available(base, mod):
                        failures.append((mod, "Can't find module"))
                        continue
                    try:
                        fmods = base.repo_module_dict.install([mod])
                    except dnf.exceptions.Error as e:
                        failures.append((mod, to_native(e)))
                        continue
                if not _mod_upgrade(base, mod):  # API seems to exist if not
                    continue

                try:
                    skipped_grps, dummy, dummy = base.repo_module_dict.upgrade([mod], True)
                except dnf.exceptions.Error as e:
                    failures.append((mod, to_native(e)))

            for group in groups:
                try:
                    try:
                        base.group_upgrade(group)
                    except dnf.exceptions.CompsError:
                        # If not already installed, try to install.
                        base.group_install(group, dnf.const.GROUP_PACKAGE_TYPES)
                except dnf.exceptions.Error as e:
                    failures.append((group, to_native(e)))

            for environment in environments:
                try:
                    try:
                        base.environment_upgrade(environment)
                    except dnf.exceptions.CompsError:
                        # If not already installed, try to install.
                        base.environment_install(environment, dnf.const.GROUP_PACKAGE_TYPES)
                except dnf.exceptions.Error as e:
                    failures.append((environment, to_native(e)))

            for pkg_spec in pkg_specs:
                # best effort causes to install the latest package
                # even if not previously installed
                base.conf.best = True
                base.install(pkg_spec)

        elif state == 'enable':
            # Enable modules.
            if not mod_specs:
                module.exit_json(msg="Nothing to do")
            if module.check_mode:
                module.exit_json(changed=True)
            changed = False
            for mod in mod_specs:
                if _mod_enabled(base, mod):
                    continue
                if not _mod_available(base, mod):
                    failures.append((mod, "Can't find module"))
                    continue
                try:
                    base.repo_module_dict.enable(mod, True)
                    changed = True
                except dnf.exceptions.Error as e:
                    failures.append((mod, to_native(e)))
            response = {'changed': changed, 'results': []}
            module.exit_json(**response)

        elif state == 'disable':
            # Disable modules.
            if not mod_specs:
                module.exit_json(msg="Nothing to do")
            if module.check_mode:
                module.exit_json(changed=True)
            changed = False
            for mod in mod_specs:
                if not _mod_enabled(base, mod):
                    continue
                try:
                    base.repo_module_dict.disable(mod, True)
                    changed = True
                except dnf.exceptions.Error as e:
                    failures.append((mod_specs, to_native(e)))
            response = {'changed': changed, 'results': []}
            module.exit_json(**response)

        else:
            # state == absent (or removed)
            if autoremove is not None:
                base.conf.clean_requirements_on_remove = autoremove

            if filenames:
                module.fail_json(
                    msg="Cannot remove paths -- please specify package name.")

            # Remove modules.
            for mod in mod_specs:
                if not _mod_installed(base, mod):
                    continue

                try:
                    fmods = base.repo_module_dict.remove([mod])
                except dnf.exceptions.Error as e:
                    failures.append((mod, to_native(e)))

            for group in groups:
                try:
                    base.group_remove(group)
                except dnf.exceptions.CompsError:
                    # Group is already uninstalled.
                    pass

            for environment in environments:
                try:
                    base.environment_remove(environment)
                except dnf.exceptions.CompsError:
                    # Environment is already uninstalled.
                    pass

            installed = base.sack.query().installed()
            for pkg_spec in pkg_specs:
                if installed.filter(name=pkg_spec):
                    base.remove(pkg_spec)

            # Like the dnf CLI we want to allow recursive removal of dependent
            # packages
            allow_erasing = True

            if autoremove:
                base.autoremove()

    if state in ['enable', 'disable']:
        pass
    elif not base.resolve(allow_erasing=allow_erasing):
        if failures:
            module.fail_json(msg='Failed to install some of the '
                                 'specified packages',
                             failures=failures)
        module.exit_json(msg="Nothing to do")
    else:
        if module.check_mode:
            if failures:
                module.fail_json(msg='Failed to install some of the '
                                     'specified packages',
                                 failures=failures)
            module.exit_json(changed=True)

        base.download_packages(base.transaction.install_set)
        base.do_transaction()
        response = {'changed': True, 'results': []}
        for package in base.transaction.install_set:
            response['results'].append("Installed: {0}".format(package))
        for package in base.transaction.remove_set:
            response['results'].append("Removed: {0}".format(package))

        # We need to do this so that DNF writes the files into modules.d
        # generic ansible shutdown doesn't always trigger the correct things.
        base.close()
        del base

        if failures:
            module.fail_json(msg='Failed to install some of the '
                                 'specified packages',
                             failures=failures)
        module.exit_json(**response)


def main():
    """The main function."""
    module = AnsibleModule(
        argument_spec=dict(
            name=dict(aliases=['pkg'], type='list'),
            state=dict(
                choices=[
                    'absent', 'present', 'installed', 'removed', 'latest',
                    'enable', 'disable']),
            enablerepo=dict(type='list', default=[]),
            disablerepo=dict(type='list', default=[]),
            list=dict(),
            conf_file=dict(default=None, type='path'),
            disable_gpg_check=dict(default=False, type='bool'),
            installroot=dict(default='/', type='path'),
            autoremove=dict(type='bool'),
            modularity=dict(type='bool'),
        ),
        required_one_of=[['name', 'list', 'autoremove']],
        mutually_exclusive=[['name', 'list'], ['autoremove', 'list']],
        supports_check_mode=True)
    params = module.params

    _ensure_dnf(module)

    if params['modularity'] is not None:
        global _modularity
        if _modularity_available and params['modularity']:
            _modularity = True
        else:
            _modularity = False

    # Check if autoremove is called correctly
    if params['autoremove'] is not None:
        if LooseVersion(dnf.__version__) < LooseVersion('2.0.1'):
            module.fail_json(msg="Autoremove requires dnf>=2.0.1. Current dnf version is %s" % dnf.__version__)
        if params['state'] not in ["absent", None]:
            module.fail_json(msg="Autoremove should be used alone or with state=absent")

    # Set state as installed by default
    # This is not set in AnsibleModule() because the following shouldn't happend
    # - dnf: autoremove=yes state=installed
    if params['state'] is None:
        params['state'] = 'installed'

    if params['list']:
        base = _base(
            module, params['conf_file'], params['disable_gpg_check'],
            params['disablerepo'], params['enablerepo'], params['installroot'])
        list_items(module, base, params['list'])
    else:
        # Note: base takes a long time to run so we want to check for failure
        # before running it.
        if not dnf.util.am_i_root():
            module.fail_json(msg="This command has to be run under the root user.")
        base = _base(
            module, params['conf_file'], params['disable_gpg_check'],
            params['disablerepo'], params['enablerepo'], params['installroot'])

        ensure(module, base, params['state'], params['name'], params['autoremove'])


if __name__ == '__main__':
    main()
