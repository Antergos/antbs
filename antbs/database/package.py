#!/usr/bin/python
# -*- coding: utf-8 -*-
#
# package.py
#
# Copyright © 2013-2017 Antergos
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
import zipfile
from glob import glob
from typing import Dict

import requests
from github3 import login
from github3.exceptions import UnprocessableResponseBody
from gitlab import Gitlab

from .meta.package_meta import PackageMeta
from utils import Pkgbuild
from . import (
    status
)

logger = status.logger
REPO_DIR = status.PKGBUILDS_DIR
GITLAB_TOKEN = status.gitlab_token
GH_REPO_BASE_URL = 'http://github.com/Antergos/antergos-packages/blob/master/'


class Package(PackageMeta):
    """
    This class represents a "package" throughout this application. It is used to
    get and set package data from/to the database as well as from PKGBUILDs.

    Args:
        name (str): The name of the package, AKA the pkgname.

    Attributes:
        allowed_in        (list): The repos that the package is allowed to be in (repo names).
        auto_sum          (bool): Does the package's PKGBUILD download checksums during build?
        builds            (list): The IDs of all builds (completed & failed) for the package.
        depends           (set):  See `man PKGBUILD`.
        description       (str):  See `Package.pkgdesc`
        epoch             (str):  See `man PKGBUILD`.
        failure_rate      (str):  The package's build failure rate.
        gh_path           (str):  The path to the package's PKGBUILD in `antergos-packages` repo.
        git_name          (str):  The name of the packages source repo on github.
        git_url           (str):  The url for the packages source repo on github.
        groups            (set):  See `man PKGBUILD`.
        heat_map          (str):  Package's build history heatmap data as `JSON` serialized string.
        is_initialized    (bool): Has the package been initialized? (This occurs only once).
        is_iso            (bool): Is this a dummy package for building an install iso image?
        is_metapkg        (bool): Is this a "metapkg"? (don't check/download deps during build).
        is_monitored      (bool): Is this package's repo being monitored by `RepoMonitor`.
        is_split_package  (bool): Is this package a split package (PKGBUILD w/ multiple packages).
        iso_md5           (str):  If `Package.is_iso` this is the iso image's checksum.
        iso_url           (str):  If `Package.is_iso` this is the iso image's download URL.
        makedepends       (set):  See `man PKGBUILD`.
        mon_etag          (str):  The HTTP ETag for the monitored resource.
        mon_file_url      (str):  The url of the file to monitor with `RemoteFileMonitor`.
        mon_last_checked  (str):  Time of the last check for new release by `RepoMonitor`.
        mon_last_result   (str):  Result of the last check for new release by `RepoMonitor`.
        mon_match_pattern (str):  Release results must match this pattern (substring or /regex/).
        mon_project       (str):  The name of the github project for source repo being monitored.
        mon_repo          (str):  The name of the github source repo being monitored.
        mon_service       (str):  The name of the service being monitored (currently only github).
        mon_type          (str):  The type of release result to get (releases, tags, or commits).
        mon_version_url   (str):  The url to get version from with `RemoteFileMonitor`.
        mon_version_pattern (str): The regex pattern for the version with `RemoteFileMonitor`.
        name              (str):  See `Package.pkgname`.
        pbpath            (str):  :deprecated:
        pkg_id            (int):  ID assigned to the package when it is added to the database.
        pkgbuild          (str):  This package's PKGBUILD (sourced from antergos-packages repo).
        pkgdesc           (str):  See `man PKGBUILD`.
        pkgname           (str):  See `man PKGBUILD`.
        pkgrel            (str):  See `man PKGBUILD`.
        pkgver            (str):  See `man PKGBUILD`.
        push_version      (bool): :deprecated: Use `Package.is_monitored` instead.
        repover           (str):  The version of this package that is currently in main repo.
        short_name        (str):  An alternate name to represent package on the frontend.
        split_packages    (list): If `Package.is_split_package`, list of all packages in PKGBUILD.
        stagingver        (str):  The version of this package that is currently in staging repo.
        success_rate      (str):  The package's build success rate.
        tl_events         (list): The IDs of all timeline events that include this package.
        transactions      (list): The IDs of all build transactions that include this package.
        url               (str):  See `man PKGBUILD`.
        version_str       (str):  The full version suituble for display on the frontend.

    """
    def __init__(self, name, fetch_pkgbuild=False):
        super().__init__(key=name)

        self._pkgbuild = None

        if not self.gh_path:
            self.determine_github_path()
            if not self.gh_path:
                raise RuntimeError(name)

        if fetch_pkgbuild or not self.pkgbuild:
            logger.debug('%s: Fetching pkgbuild from github..', self.pkgname)
            self.pkgbuild = self.fetch_pkgbuild_from_github()

        if not self.pkgbuild:
            raise RuntimeError(name)

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
            self.sync_repo_monitor_config()

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

    def setup_pkgbuild_parser(self):
        self._pkgbuild = Pkgbuild(self.pkgbuild)
        self._pkgbuild.parse_contents()

    def get_from_pkgbuild(self, var):
        """
        Get a variable from this package's PKGBUILD (stored on github).

        Args:
            var (str): A variable to get from the PKGBUILD.

        Returns:
            (str): The variable's value parsed from PKGBUILD.

        """

        val = ''

        if not self.pkgbuild:
            self.pkgbuild = self.fetch_pkgbuild_from_github()

        if var not in self.pkgbuild:
            logger.debug('%s not found in PKGBUILD for %s.', var, self.pkgname)
            return val

        if not self._pkgbuild:
            self.setup_pkgbuild_parser()

        if var not in self._pkgbuild.values:
            logger.debug('%s not found in parsed PKGBUILD for %s.', var, self.pkgname)
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

        if 'cnchi-dev' == self.name:
            zpath = os.path.join(dirpath, self.name + '.zip')
            gh = login(token=status.github_token)
            repo = gh.repository('antergos', 'cnchi')
            repo.archive('zipball', zpath, ref='0.14.x')
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
        paths = [
            os.path.join('antergos', 'cinnamon', self.pkgname),
            os.path.join('antergos', 'mate', self.pkgname),
            os.path.join('antergos', self.pkgname)
        ]
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
            self.gh_path = os.path.join(gh_path, 'PKGBUILD')
            return True

    @staticmethod
    def get_github_api_client(project='antergos', repo='antergos-packages'):
        gh = login(token=status.github_token)
        gh_repo = gh.repository(project, repo)

        return gh, gh_repo

    def fetch_pkgbuild_from_github(self):
        logger.debug('fetch_pkgbuild_from_github! %s', self.pkgname)
        gh, repo = self.get_github_api_client()
        pbpath = None
        target_path = None

        if not self.gh_path or not isinstance(self.gh_path, str):
            logger.debug('not self.gh_path!')
            self.determine_github_path()

        try:
            pbfile_contents = repo.file_contents(self.gh_path).decoded.decode('utf-8')
        except Exception:
            at_path = repo.file_contents(self.gh_path)

            if isinstance(at_path, UnprocessableResponseBody):
                # Path is a directory
                pbpath = os.path.join(self.gh_path, 'PKGBUILD')

            elif 'symlink' == at_path.type:
                pbpath = os.path.join(
                    self.gh_path.rsplit('/', 1)[0],
                    at_path.target,
                    'PKGBUILD'
                )

            self.gh_path = pbpath

            logger.debug(pbpath)

        pbfile_contents = repo.file_contents(self.gh_path).decoded.decode('utf-8')

        if not pbfile_contents:
            logger.error(self.pkgname)

        return pbfile_contents

    def is_package_on_github(self, name=None):
        pname = name or self.pkgname
        return pname in status.all_packages or self.determine_github_path()

    def update_pkgbuild_and_push_github(self, changes: Dict[str, tuple]) -> bool:
        change_monitored = [True for key in changes if 'monitored' in key]
        can_push = change_monitored or self.push_version or self.is_monitored
        invalid_value = [
            True for c in changes
            if any(True for n in [None, 'None', '']
                   if n == changes[c][1] or changes[c][0] == changes[c][1])
        ]

        if invalid_value or not can_push:
            logger.error('cant push to github! %s', changes)
            return False

        gh = login(token=status.github_token)
        repo = gh.repository('antergos', 'antergos-packages')
        pb_file = repo.file_contents(self.gh_path)

        pb_contents = pb_file.decoded.decode('utf-8')
        new_pb_contents = pb_contents
        msg_tpl = '[ANTBS] | [updpkg] {0} {1}'

        if '1.14' == self.mon_match_pattern:
            changes['_monitored_match_pattern'] = ('1.14', '1.16')

        if 'pkgver' in changes and not self.auto_sum:
            commit_msg = msg_tpl.format(self.pkgname, changes['pkgver'])
        else:
            commit_msg = '[ANTBS] | Updated PKGBUILD for {0}.'.format(self.pkgname)

        for key, val in changes.items():
            search_str = '{0}={1}'.format(key, val[0])
            replace_str = '{0}={1}'.format(key, val[1])

            if 'monitored' in key:
                search_str = "{0}='{1}'".format(key, val[0])
                replace_str = "{0}='{1}'".format(key, val[1])

            new_pb_contents = new_pb_contents.replace(search_str, replace_str)

            if 'pkgver' == key and '1' != self.pkgrel:
                search_str = 'pkgrel={0}'.format(self.pkgrel)
                replace_str = 'pkgrel={0}'.format('1')
                new_pb_contents = new_pb_contents.replace(search_str, replace_str)

            elif 'checksum' == key and val[0] in new_pb_contents:
                new_pb_contents = new_pb_contents.replace(val[0], val[1])

        if new_pb_contents == pb_contents:
            return True

        commit = pb_file.update(commit_msg, new_pb_contents.encode('utf-8'))

        if commit:
            logger.info('commit hash is %s', commit)
            return True
        else:
            logger.error('commit failed. commit=%s | content=%s', commit, new_pb_contents)
            return False

    def get_version_str(self):
        # TODO: This is still garbage. Rewrite and simplify!
        changed = {'epoch': False, 'pkgrel': False, 'pkgver': False}
        old_vals = {'pkgver': self.pkgver, 'pkgrel': self.pkgrel, 'epoch': self.epoch}
        version_from_tag = self.is_monitored and self.mon_type in ['releases', 'tags', 'file']
        version_from_commit = self.is_monitored and 'commits' == self.mon_type
        is_mate_pkg = self.is_monitored and 'mate-desktop' == self.mon_service

        if not any([version_from_tag, version_from_commit, is_mate_pkg]):
            for key in changed:
                new_val = self.get_from_pkgbuild(key)

                if not new_val or 'None' in new_val:
                    logger.info('unable to get %s from pkgbuild for %s', key, self.pkgname)

                elif new_val and (new_val != old_vals[key] or new_val not in self.version_str):
                    changed[key] = new_val

        elif version_from_tag or is_mate_pkg:
            if not self.mon_last_result:
                self.mon_last_result = self.get_from_pkgbuild('pkgver')

            if self.auto_sum and self.mon_last_result.replace('|', '.') != old_vals['pkgver']:
                _pkgver, _buildver = self.mon_last_result.split('|')
                changed['_pkgver'] = _pkgver
                changed['_buildver'] = _buildver
                changed['pkgver'] = self.mon_last_result.replace('|', '.')
                changed['pkgrel'] = '1'
            elif 'pamac-dev' == self.pkgname:
                # Hack -- fix later.
                changed['pkgver'] = get_pkg_object('pamac').pkgver
                self.update_pkgbuild_and_push_github(
                    {'pkgver': (old_vals['pkgver'], changed['pkgver'])}
                )
                changed['pkgrel'] = '1'
            elif self.mon_last_result != old_vals['pkgver']:
                changed['pkgver'] = self.mon_last_result
                changed['pkgrel'] = '1'

        elif version_from_commit:
            cmd = ['/usr/bin/makepkg', '--packagelist']
            pkgver = ''
            pkgbuild_dir = os.path.join(status.PKGBUILDS_DIR, 'antergos', self.pkgname)
            tmp_dir = os.path.join('/tmp', self.pkgname)
            pkgbuild = os.path.join(tmp_dir, 'PKGBUILD')

            if os.path.exists(tmp_dir):
                shutil.rmtree(tmp_dir)

            shutil.copytree(pkgbuild_dir, tmp_dir)

            with open(pkgbuild, 'w') as pkgbuild:
                pkgbuild.write(self.pkgbuild)

            try:
                pkglist = subprocess.check_output(cmd, cwd=tmp_dir, universal_newlines=True)
                pkg = pkglist.split('\n')[0]
                name, pkgver, pkgrel, arch = pkg.split('-')
            except Exception as err:
                logger.exception(err)
                return self.version_str

            if not pkgver:
                return self.version_str

            changed['pkgver'] = pkgver
            changed['pkgrel'] = '1'

        changes = [k for k in changed if changed[k] is not False]
        if not changes:
            is_valid = self.version_str and 'None' not in self.version_str
            return self.version_str if is_valid else self.pkgver

        for key in changes:
            if changed[key] is False:
                raise ValueError
            setattr(self, key, changed[key])

        version_str = '{0}-{1}'.format(self.pkgver, self.pkgrel)

        if self.epoch:
            version_str = '{0}:{1}'.format(self.epoch, version_str)

        if version_str and len(version_str) > 2 and 'None' not in version_str:
            self.version_str = version_str
        else:
            raise ValueError(version_str)

        if 'cnchi-dev' == self.name and self.pkgver[-1] not in ['0', '5']:
            if not self.db.exists('CNCHI-DEV-OVERRIDE'):
                version_str = False
            else:
                self.db.delete('CNCHI-DEV-OVERRIDE')

        return version_str

    def get_deps(self, makedepends=False):
        depends = []

        if makedepends:
            deps = list(self.get_from_pkgbuild('makedepends'))
        else:
            deps = list(self.get_from_pkgbuild('depends'))

        for dep in deps:
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

        elif key_name == 'depends':
            from_pbuild = set(self.get_deps())
        elif key_name == 'makedepends':
            from_pbuild = set(self.get_deps(makedepends=True))

        from_db = set(attrib)
        to_remove = from_db - from_pbuild
        to_add = from_pbuild - from_db

        for old_val in to_remove:
            attrib.remove(old_val)
        for new_val in to_add:
            attrib.append(new_val)

    def sync_repo_monitor_config(self):
        # TODO: Come up with more robust solution for repo monitor metadata
        is_monitored = self.get_from_pkgbuild('_is_monitored') in ['True', 'yes']

        if not is_monitored:
            self.is_monitored = False
            self.db.zrem(status.MONITOR_PKGS_KEY, self.pkgname)
            return

        service = self.get_from_pkgbuild('_monitored_service')
        mon_type = self.get_from_pkgbuild('_monitored_type')
        project = self.get_from_pkgbuild('_monitored_project')
        repo = self.get_from_pkgbuild('_monitored_repo')
        pattern = self.get_from_pkgbuild('_monitored_match_pattern')
        file_url = self.get_from_pkgbuild('_monitored_file_url')
        ver_url = self.get_from_pkgbuild('_monitored_version_url')
        ver_pattern = self.get_from_pkgbuild('_monitored_version_pattern')

        self.is_monitored = True
        self.mon_service = service
        self.mon_type = mon_type
        self.mon_project = project
        self.mon_repo = repo
        self.mon_match_pattern = pattern
        self.mon_file_url = file_url
        self.mon_version_url = ver_url
        self.mon_version_pattern = ver_pattern
        self.db.zadd(status.MONITOR_PKGS_KEY, 1, self.pkgname)

    def sync_database_with_pkgbuild(self):
        if 'None' in self.version_str and 'None' not in self.pkgver:
            self.version_str = self.pkgver
        elif 'None' in self.version_str:
            raise ValueError('version_str and pkgver cannot be None')

        self.pkgdesc = self.get_from_pkgbuild('pkgdesc')
        self.description = self.pkgdesc
        self.url = self.get_from_pkgbuild('url')

        if self.is_split_package:
            self.sync_pkgbuild_array_by_key('pkgname')

        self.sync_repo_monitor_config()
        self.sync_pkgbuild_array_by_key('depends')
        self.sync_pkgbuild_array_by_key('makedepends')
        self.sync_pkgbuild_array_by_key('groups')


def get_pkg_object(name, fetch_pkgbuild=False):
    pkg_obj = Package(name=name, fetch_pkgbuild=fetch_pkgbuild)

    return pkg_obj
