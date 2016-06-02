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
from glob import glob

import gevent
import requests
from gitlab import Gitlab

from database.base_objects import RedisHash, db
from database.server_status import status
from gevent import sleep
from github3 import login
from github3.exceptions import UnprocessableResponseBody
from utils.logging_config import logger
from utils.pkgbuild import Pkgbuild

REPO_DIR = "/var/tmp/antergos-packages"
GITLAB_TOKEN = status.gitlab_token
GH_REPO_BASE_URL = 'http://github.com/Antergos/antergos-packages/blob/master/'


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
                    'success_rate', 'url',            'version_str',    'gh_path'],

            bool=['is_metapkg',   'auto_sum',     'is_split_package', 'is_initialized',
                  'push_version', 'is_monitored', 'saved_commit',     'is_iso'],

            int=['pkg_id'],

            list=['allowed_in',    'builds', 'tl_events', 'transactions',
                  'split_packages'],

            set=['depends', 'groups', 'makedepends']
        ))

        self.__namespaceinit__()

        if (not self or not self.pkg_id) and self.is_package_on_github(name=key):
            # Package is not in the database, so it must be new. Let's initialize it.
            self.__keysinit__()

            self.pkgname = key
            self.name = key

            next_id = self.db.incr('antbs:misc:pkgid:next')
            self.pkg_id = next_id

            status.all_packages.add(self.name)

    def is_package_on_github(self, name=None):
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

        self._pkgbuild = None

        if not self.gh_path:
            self.determine_github_path()

        if fetch_pkgbuild or not self.pkgbuild:
            self.pkgbuild = self.fetch_pkgbuild_from_github()

        if not self.pkgbuild:
            raise RuntimeError(self.pkgbuild)

        if not self.is_initialized:
            self.is_initialized = self.initialize_once()

        if fetch_pkgbuild:
            self.sync_database_with_pkgbuild()

    def initialize_once(self):
        allowed_in = self.get_from_pkgbuild('_allowed_in')
        auto_sum = self.get_from_pkgbuild('_auto_sum')
        is_metapkg = self.get_from_pkgbuild('_is_metapkg') in ['True', 'yes']
        is_monitored = self.get_from_pkgbuild('_is_monitored') in ['True', 'yes']
        patterns = ['pkgname=(', 'pkgbase=']
        is_split_package = [True for pattern in patterns if pattern in self.pkgbuild]

        if '-x86_64' in self.name or '-i686' in self.name:
            self.is_iso = True

        if allowed_in:
            self.allowed_in.extend(allowed_in)
        else:
            self.allowed_in.extend(['staging', 'main'])

        if auto_sum:
            self.auto_sum = auto_sum

        if is_metapkg:
            self.is_metapkg = is_metapkg

        if is_monitored:
            service = self.get_from_pkgbuild('_monitored_service')
            monitored_type = self.get_from_pkgbuild('_monitored_type')
            project = self.get_from_pkgbuild('_monitored_project')
            repo = self.get_from_pkgbuild('_monitored_repo')

            config_items = [service, type, project, repo]

            if len([True for item in config_items if item]) == 4:
                self.is_monitored = is_monitored
                self.monitored_service = service
                self.monitored_type = monitored_type
                self.monitored_project = project
                self.monitored_repo = repo

        if is_split_package:
            self.is_split_package = True
            split_packages = self.get_split_packages()
            logger.debug(split_packages)

            if split_packages:
                self.split_packages.extend(split_packages)

        return True

    def get_split_packages(self):
        split_pkgs = self.get_from_pkgbuild('pkgname')
        logger.debug(split_pkgs)

        if self.pkgname in split_pkgs:
            split_pkgs.remove(self.pkgname)

        return split_pkgs

    def get_from_pkgbuild(self, var):
        """
        Get a variable from this package's PKGBUILD (which is stored in antergos-packages gh repo).

        :param var: (str) A variable to extract from the PKGBUILD.
        :return: (str) The variable's value after extracted from PKGBUILD.

        """

        val = ''

        if not self.pkgbuild:
            self.pkgbuild = self.fetch_pkgbuild_from_github()

        if var not in self.pkgbuild:
            logger.info('%s not found in PKGBUILD for %s.', var, self.pkgname)
            return val

        if not self._pkgbuild:
            self._pkgbuild = Pkgbuild(self.pkgbuild)
            self._pkgbuild.parse_contents()

        if var not in self._pkgbuild.values:
            logger.info('%s not found in PKGBUILD for %s.', var, self.pkgname)
            return val

        if self._pkgbuild.values[var]:
            val = self._pkgbuild.values[var]
        else:
            val = self._pkgbuild.get_value(var) or ''

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
        if 'cnchi' == self.name:
            zpath = os.path.join(dirpath, self.name + '.zip')
            gh = login(token=status.github_token)
            repo = gh.repository('antergos', 'cnchi')
            repo.archive('zipball', zpath, ref='master')
            zfile = zipfile.ZipFile(zpath, 'r')
            zfile.extractall(dirpath)
            cnchi_dir = glob('{0}/Antergos-Cnchi-*'.format(dirpath))
            logger.debug(cnchi_dir)
            new_dir = os.path.join(dirpath, 'cnchi')
            shutil.move(cnchi_dir[0], new_dir)
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

    def determine_github_path(self):
        paths = ['cinnamon/{0}'.format(self.pkgname),
                 'mate/{0}'.format(self.pkgname),
                 self.pkgname]
        gh_path = ''

        for path in paths:
            url = '{0}{1}'.format(GH_REPO_BASE_URL, path)
            req = requests.head(url, allow_redirects=True)

            try:
                req.raise_for_status()
                gh_path = path
            except Exception:
                logger.info('path: %s not found for %s', path, self.pkgname)
                continue

            break

        if not gh_path:
            logger.error('Could not determine gh_path for %s', self.pkgname)
            return False
        else:
            self.gh_path = gh_path
            return True


    @staticmethod
    def get_github_api_client():
        gh = login(token=status.github_token)
        repo = gh.repository('antergos', 'antergos-packages')

        return gh, repo

    def fetch_pkgbuild_from_github(self):
        logger.debug('fetch_pkgbuild_from_github!')
        gh, repo = self.get_github_api_client()
        pbpath = None
        target_path = None

        if not self.gh_path or not isinstance(self.gh_path, str):
            logger.debug('not self.gh_path!')
            self.determine_github_path()

        if 'PKGBUILD' not in self.gh_path:
            gh_path = repo.file_contents(self.gh_path)

            if isinstance(gh_path, UnprocessableResponseBody):
                pbpath = '{0}/PKGBUILD'.format(self.gh_path)
            elif 'symlink' == gh_path['type']:
                pbpath = os.path.join(
                    self.gh_path.rsplit('/', 1)[0],
                    gh_path['target'],
                    'PKGBUILD'
                )

            self.gh_path = pbpath

        logger.debug(pbpath)

        pbfile_contents = repo.file_contents(self.gh_path).decoded.decode('utf-8')

        if not pbfile_contents:
            logger.error(pbfile_contents)

        return pbfile_contents

    def is_package_on_github(self, name=None):
        pname = name or self.pkgname
        return pname in status.all_packages or self.determine_github_path()

    def update_pkgbuild_and_push_github(self, var=None, old_val=None, new_val=None):
        can_push = self.push_version or self.is_monitored

        if not can_push or old_val == new_val or new_val in [None, 'None']:
            logger.error('cant push to github!')
            return

        gh = login(token=status.github_token)
        repo = gh.repository('antergos', 'antergos-packages')
        pb_file = repo.file_contents(self.name + '/PKGBUILD')

        if not pb_file:
            pb_file = repo.file_contents('cinnamon/' + self.name + '/PKGBUILD')

        pb_contents = pb_file.decoded.decode('utf-8')
        search_str = '{0}={1}'.format(var, old_val)

        if 'pkgver' == var and 'pkgver=None' in pb_contents:
            search_str = '{0}={1}'.format(var, 'None')

        replace_str = '{0}={1}'.format(var, new_val)
        new_pb_contents = pb_contents.replace(search_str, replace_str)

        if 'pkgver' == var:
            commit_msg = '[ANTBS] | [updpkg] {0} {1}'.format(self.name, new_val)
            search_str = 'pkgrel={0}'.format(self.pkgrel)
            replace_str = 'pkgrel={0}'.format('1')
            new_pb_contents = new_pb_contents.replace(search_str, replace_str)
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
        version_from_tag = self.is_monitored and self.monitored_type in ['releases', 'tags']
        if not version_from_tag:
            for key in ['pkgver', 'pkgrel', 'epoch']:
                new_val = self.get_from_pkgbuild(key)

                if not new_val:
                    logger.info('unable to get %s from pkgbuild for %s', key, self.pkgname)

                if new_val and (new_val != old_vals[key] or new_val not in self.version_str):
                    changed[key] = new_val
                    setattr(self, key, new_val)

            if not any([True for x in changed if changed[x] not in [None, 'None']]):
                return self.version_str
        else:
            changed['pkgver'] = self.monitored_last_result
            setattr(self, 'pkgver', changed['pkgver'])
            self.update_pkgbuild_and_push_github('pkgver', old_vals['pkgver'], changed['pkgver'])
            gevent.sleep(8)
            self.update_pkgbuild_and_push_github('pkgrel', old_vals['pkgrel'], '1')
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
        deps = list(self.get_from_pkgbuild('depends'))
        mkdeps = list(self.get_from_pkgbuild('makedepends'))

        all_deps = deps + mkdeps
        for dep in all_deps:
            has_ver = re.search(r'^[\d\w-]+(?=\=|\>|\<)', dep)
            if has_ver:
                dep = has_ver.group(0)

            depends.append(dep)

        return depends

    def sync_pkgbuild_array_by_key(self, key_name):
        attrib = getattr(self, key_name)
        from_pbuild = set(self.get_from_pkgbuild(key_name))

        if 'pkgname' == key_name and self.is_split_package:
            attrib = getattr(self, 'split_packages')

        elif key_name in ['depends', 'makedepends']:
            from_pbuild = set(self.get_deps())

        from_db = set(attrib)
        to_remove = from_db - from_pbuild
        to_add = from_pbuild - from_db

        for old_val in to_remove:
            attrib.remove(old_val)
        for new_val in to_add:
            attrib.append(new_val)

    def sync_database_with_pkgbuild(self):
        if not self.pkgver:
            self.pkgver = self.get_from_pkgbuild('pkgver')

        if not self.pkgdesc or not self.description:
            self.pkgdesc = self.get_from_pkgbuild('pkgdesc')
            self.description = self.pkgdesc

        if not self.url:
            self.url = self.get_from_pkgbuild('url')

        if self.is_split_package:
            self.sync_pkgbuild_array_by_key('pkgname')

        self.sync_pkgbuild_array_by_key('depends')
        self.sync_pkgbuild_array_by_key('groups')


def get_pkg_object(name, fetch_pkgbuild=False):
    pkg_obj = Package(name=name, fetch_pkgbuild=fetch_pkgbuild)

    return pkg_obj
