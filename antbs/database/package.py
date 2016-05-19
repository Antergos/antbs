#!/usr/bin/python
# -*- coding: utf-8 -*-
#
# package.py
#
# Copyright © 2013-2016 Antergos
#
# This file is part of The Antergos Build Server, (AntBS).
#
# AntBS is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 3 of the License, or
# (at your option) any later version.
#
# AntBS is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with AntBS; If not, see <http://www.gnu.org/licenses/>.


import os
import re
import shutil
import subprocess
import time
import zipfile

import gevent
from gitlab import Gitlab

from database.base_objects import RedisHash, db
from database.server_status import status
from gevent import sleep
from github3 import login
from utils.logging_config import logger
from utils.pkgbuild import Pkgbuild

REPO_DIR = "/var/tmp/antergos-packages"
GITLAB_TOKEN = status.gitlab_token
GITHUB_REPO = 'http://github.com/antergos/antergos-packages'


class PackageMeta(RedisHash):
    """
    This is the base class for ::class:`Package`. It initalizes the fields for
    the package metadata that is stored in the database. You should not use this
    class directly.

    """

    def __init__(self, namespace='antbs', prefix='pkg', key='', *args, **kwargs):
        super().__init__(namespace=namespace, prefix=prefix, key=key, *args, **kwargs)

        self.attrib_lists.update(dict(
            string=['git_name',     'build_path',     'description',    'epoch',
                    'git_url',      'failure_rate',   'gh_project',     'iso_md5',
                    'name',         'gh_repo',        'iso_url',        'monitored_last_result',
                    'pkgdesc',      'heat_map',       'monitored_type', 'monitored_project',
                    'pkgrel',       'monitored_repo', 'pbpath',         'monitored_service',
                    'pkgver',       'pkgname',        'pkgbuild',       'short_name',
                    'success_rate', 'url',            'version_str'],

            bool=['is_metapkg',   'auto_sum',     'is_split_package', 'is_initialized',
                  'push_version', 'is_monitored', 'saved_commit',     'is_iso'],

            int=['pkg_id'],

            list=['allowed_in',    'builds', 'tl_events', 'transactions',
                  'split_packages'],

            set=['depends', 'groups', 'makedepends']
        ))

        self.all_attribs.append('_build')
        self.__namespaceinit__()

        is_on_github = self.is_package_on_github(key)

        if not self or (not self.pkg_id and (key in status.all_packages or is_on_github)):
            # Package is not in the database, so it must be new. Let's initialize it.
            self.__keysinit__()

            self.pkgname = self.name = key

            next_id = self.db.incr('antbs:misc:pkgid:next')
            self.pkg_id = next_id

            status.all_packages.add(self.name)

    @staticmethod
    def is_package_on_github(name):
        raise NotImplementedError('Subclass must implement this method')


class Package(PackageMeta):
    """
    This class represents a "package" throughout this application. It is used to
    get and set package data from/to the database as well as from PKGBUILDs.

    Args:
        name (str): The name of the package, AKA the pkgname.

    Attributes:
        (str)
            name, pkgname, pkgver, epoch, pkgrel, description, pkgdesc: see `man PKGBUILD`
            version_str: The package's version including pkgrel for displaying on the frontend.
            short_name: Optional name to use on frontend instead of the pkgname.
            pbpath: Absolute path to the package's PKGBUILD file (from most recent build).
            build_path: Absolute path to the the package's most recent build directory.
            success_rate: The package's rate of successful builds.
            failure_rate: The package's rate of build failures.

        (bool)
            push_version: Should version be automatically push to Github? (for monitored package)
            auto_sum: Does the package's PKGBUILD download checksums when makepkg is called?
            saved_commit: When pushing to github, do we have any previous commits to be pushed?
            is_iso: Is this a dummy package for building an install iso image?
            is_metapkg: Is this a "metapkg" (don't check or download dependencies during build).
            is_monitored: Are we monitoring this package's releases with a `Monitor` object?

        (int)
            pkg_id: ID assigned to the package when it is added to our database for the first time.

        (list)
            allowed_in: The repos that the package is allowed to be in (repo names).
            builds: The IDs of all builds (completed & failed) for the package.
            tl_events: The IDs of all timeline events that include this package.

        (set)
            depends, groups, makedepends: see `man PKGBUILD`

    """

    def __init__(self, name, fetch_pkgbuild=False):
        super().__init__(key=name)

        if fetch_pkgbuild or not self.pkgbuild:
            self.pkgbuild = self.fetch_pkgbuild_from_github(name)

        if not self.pkgbuild:
            raise RuntimeError('self.pkgbuild cannot be Falsey!')

        if not self.is_initialized:
            self.is_initialized = self.initialize_once()

        self._pkgbuild = None

    def initialize_once(self):
        allowed_in = self.get_from_pkgbuild('_allowed_in')
        auto_sum = self.get_from_pkgbuild('_auto_sum')
        is_metapkg = self.get_from_pkgbuild('_is_metapkg') in ['True', 'yes']
        is_monitored = self.get_from_pkgbuild('_is_monitored') in ['True', 'yes']
        patterns = ['pkgname=(', 'pkgbase=(']
        is_split_package = [True for pattern in patterns if pattern in self.pkgbuild]

        if '-x86_64' in self.name or '-i686' in self.name:
            self.is_iso = True

        if allowed_in:
            self.allowed_in = allowed_in

        if auto_sum:
            self.auto_sum = auto_sum

        if is_metapkg:
            self.is_metapkg = is_metapkg

        if is_monitored:
            self.is_monitored = is_monitored

        if is_split_package:
            self.is_split_package = is_split_package
            split_packages = self.get_split_packages()

            if split_packages:
                self.split_packages.extend(split_packages)

        return True

    def get_split_packages(self):
        split_pkgs_string = self.get_from_pkgbuild('pkgname')
        split_packages = []

        logger.debug(split_pkgs_string)

        for pkg in split_pkgs_string.split(' '):
            if pkg and pkg != self.pkgname:
                split_packages.append(pkg)

        return split_packages

    def get_from_pkgbuild(self, var):
        """
        Get a variable from this package's PKGBUILD (which is stored in antergos-packages gh repo).

        :param var: (str) A variable to extract from the PKGBUILD.
        :return: (str) The variable's value after extracted from PKGBUILD.

        """

        val = ''

        if not self.pkgbuild:
            self.pkgbuild = self.fetch_pkgbuild_from_github(self.pkgname)

        if not self._pkgbuild:
            self._pkgbuild = Pkgbuild(self.pkgbuild)
            self._pkgbuild.parse_contents()

        if var not in self.pkgbuild:
            logger.error('%s not found in PKGBUILD for %s.', var, self.pkgname)
        else:
            val = self._pkgbuild.get(var)

        return val

    def prepare_package_source(self, dirpath=None):
        if not dirpath:
            logger.error('dirpath cannot be None')
            raise ValueError

        if 'numix-icon-theme-square' == self.name:
            zpath = os.path.join(dirpath, self.name + '.zip')
            gl = Gitlab('https://gitlab.com', GITLAB_TOKEN)
            gl.auth()
            nxsq = gl.projects(id='61284')
            source = nxsq.archive()
            with open(zpath, 'wb') as fd:
                fd.write(source)
            return
        if 'cnchi-dev' == self.name:
            zpath = os.path.join(dirpath, self.name + '.zip')
            gh = login(token=status.github_token)
            repo = gh.repository('antergos', 'cnchi')
            repo.archive('zipball', zpath, ref='master')
            zfile = zipfile.ZipFile(zpath, 'r')
            zfile.extractall(dirpath)
            return

        os.putenv('srcdir', dirpath)

        if os.path.exists(os.path.join(dirpath, self.git_name)):
            shutil.rmtree(os.path.join(dirpath, self.git_name), ignore_errors=True)
        try:
            res = subprocess.check_output(['/usr/bin/git', 'clone', self.git_url, self.git_name],
                                          cwd=dirpath)
            logger.info(res)
        except subprocess.CalledProcessError as err:
            logger.error(err.output)

    def determine_git_repo_info(self):
        if not self.git_url or not self.git_url.endswith('.git'):
            source = self.get_from_pkgbuild('source')
            url_match = re.search(r'((https*)|(git:)).+\.git', source)
            if url_match:
                logger.info('url_match is: %s', url_match)
                setattr(self, 'git_url', url_match.group(0))
            else:
                setattr(self, 'git_url', '')

        if not self.git_name:
            setattr(self, 'git_name', self.name)
            if self.name == 'pamac-dev':
                setattr(self, 'git_name', 'pamac')
            elif self.name == 'cnchi-dev':
                setattr(self, 'git_name', 'cnchi-dev')
                setattr(self, 'git_url', 'http://github.com/lots0logs/cnchi-dev.git')
            elif self.name == 'cnchi':
                setattr(self, 'git_url', 'http://github.com/antergos/cnchi.git')

    @staticmethod
    def fetch_pkgbuild_from_github(name):
        gh = login(token=status.github_token)
        repo = gh.repository('antergos', 'antergos-packages')
        pbfile_contents = repo.file_contents(name + '/PKGBUILD').decoded.decode('utf-8')

        return pbfile_contents

    @staticmethod
    def is_package_on_github(name):
        found = False

        if name in status.all_packages:
            found = True
        else:
            gh = login(token=status.github_token)
            repo = gh.repository('antergos', 'antergos-packages')

            try:
                if repo.file_contents(name + '/PKGBUILD'):
                    found = True
            except Exception as err:
                logger.error(err)

        return found


    @staticmethod
    def maybe_update_pkgbuild_repo():
        if not db.exists('PKGBUILD_REPO_UPDATED') or not os.path.exists(status.PKGBUILDS_DIR):
            if db.setnx('PKGBUILD_REPO_LOCK', True):
                db.expire('PKGBUILD_REPO_LOCK', 150)

                if not os.path.exists(status.PKGBUILDS_DIR):
                    subprocess.check_call(
                        ['/usr/bin/git', 'clone', GITHUB_REPO],
                        cwd=os.path.dirname(status.PKGBUILDS_DIR)
                    )

                try:
                    subprocess.check_call(['/usr/bin/git', 'fetch'], cwd=status.PKGBUILDS_DIR)
                    subprocess.check_call(
                        ['/usr/bin/git', 'reset', '--hard', 'origin/master'],
                        cwd=status.PKGBUILDS_DIR
                    )
                    db.setex('PKGBUILD_REPO_UPDATED', 350, True)
                except subprocess.CalledProcessError as err:
                    logger.error(err)
                    db.delete('PKGBUILD_REPO_UPDATED')

                db.delete('PKGBUILD_REPO_LOCK')
            else:
                while not db.exists('PKGBUILD_REPO_UPDATED') and db.exists('PKGBUILD_REPO_LOCK'):
                    gevent.sleep(2)

    def update_and_push_github(self, var=None, old_val=None, new_val=None):
        if not (self.push_version and not self.is_monitored) or old_val == new_val:
            return
        gh = login(token=status.github_token)
        repo = gh.repository('antergos', 'antergos-packages')
        pb_file = repo.file_contents(self.name + '/PKGBUILD')
        pb_contents = pb_file.decoded.decode('utf-8')

        search_str = '{0}={1}'.format(var, old_val)
        if 'pkgver=None' in pb_contents:
            search_str = '{0}={1}'.format(var, 'None')

        replace_str = '{0}={1}'.format(var, new_val)
        new_pb_contents = pb_contents.replace(search_str, replace_str)

        with open(self._pbpath, 'w') as pbuild:
            pbuild.write(new_pb_contents)

        pbuild.close()

        if 'pkgver' == var:
            commit_msg = '[ANTBS] | [updpkg] {0} {1}'.format(self.name, new_val)
        else:
            commit_msg = '[ANTBS] | Updated {0} to {1} in PKGBUILD for {2}.'.format(var, new_val,
                                                                                    self.name)
        commit = pb_file.update(commit_msg, new_pb_contents.encode('utf-8'))

        if commit and 'commit' in commit:
            try:
                logger.info('commit hash is %s', commit['commit'].sha)
            except AttributeError:
                logger.error('commit failed. commit=%s | content=%s', commit, new_pb_contents)
            return True
        else:
            logger.error('commit failed')
            return False

    def get_version(self):
        # TODO: This is so ugly. Needs rewrite.
        changed = {'epoch': None, 'pkgrel': None, 'pkgver': None}
        old_vals = {'pkgver': self.pkgver, 'pkgrel': self.pkgrel, 'epoch': self.epoch}
        version_from_tag = self.is_monitored and 'releases' == self.monitored_type
        if not version_from_tag:
            for key in ['pkgver', 'pkgrel', 'epoch']:
                new_val = self.get_from_pkgbuild(key)

                if new_val != old_vals[key]:
                    changed[key] = new_val
                    setattr(self, key, new_val)

            if not any([True for x in changed if changed[x] is not None]):
                return self.version_str
        else:
            changed['pkgver'] = self.monitored_last_result
            setattr(self, 'pkgver', changed['pkgver'])
            self.update_and_push_github('pkgver', old_vals['pkgver'], changed['pkgver'])
            gevent.sleep(8)
            self.update_and_push_github('pkgrel', old_vals['pkgrel'], '1')
            setattr(self, 'pkgrel', '1')
            changed['pkgrel'] = '1'

        version = changed.get('pkgver', self.pkgver)

        if changed['epoch']:
            version = '{0}:{1}'.format(changed['epoch'], version)
        elif self.epoch:
            version = '{0}:{1}'.format(self.epoch, version)

        if changed['pkgrel']:
            version = '{0}-{1}'.format(version, changed['pkgrel'])
        elif self.pkgrel:
            version = '{0}-{1}'.format(version, self.pkgrel)
        else:
            version = '{0}-{1}'.format(version, '1')

        if version and len(version) > 2:
            setattr(self, 'version_str', version)
        else:
            version = self.version_str

        if 'cnchi-dev' == self.name and self.pkgver[-1] not in ['0', '5']:
            if not self.db.exists('CNCHI-DEV-OVERRIDE'):
                version = False
            else:
                self.db.delete('CNCHI-DEV-OVERRIDE')

        return version

    def get_deps(self):
        """


        :return:
        """
        depends = []
        deps = self.get_from_pkgbuild('depends').split()
        logger.info('deps are %s', deps)
        mkdeps = self.get_from_pkgbuild('makedepends').split()

        all_deps = deps + mkdeps
        for dep in all_deps:
            has_ver = re.search(r'^[\d\w-]+(?=\=|\>|\<)', dep)
            if has_ver:
                dep = has_ver.group(0)

            depends.append(dep)

            if dep in deps:
                self.depends.add(dep)
            elif dep in mkdeps:
                self.makedepends.add(dep)

        return depends

    def sync_database_with_pkgbuild(self):
        if not self.pkgname:
            setattr(self, 'pkgname', self.name)
        if not self.pkg_id:
            next_id = self.db.incr('antbs:misc:pkgid:next')
            setattr(self, 'pkg_id', next_id)
        if not self.pkgver:
            self.get_from_pkgbuild('pkgver')
        if not self.pkgdesc or not self.description:
            setattr(self, 'pkgdesc', self.get_from_pkgbuild('pkgdesc'))
            setattr(self, 'description', self.pkgdesc)
        if not self.url:
            setattr(self, 'url', self.get_from_pkgbuild('url'))
        if not self.depends:
            self.get_deps()
        if not self.groups:
            setattr(self, 'groups', self.get_from_pkgbuild('groups'))


def get_pkg_object(name, fetch_pkgbuild=False):
    pkg_obj = Package(name=name, fetch_pkgbuild=False)

    return pkg_obj
