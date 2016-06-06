#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
#  repo.py
#
#  Copyright © 2015-2016 Antergos
#
#  This file is part of The Antergos Build Server, (AntBS).
#
#  AntBS is free software; you can redistribute it and/or modify
#  it under the terms of the GNU General Public License as published by
#  the Free Software Foundation; either version 3 of the License, or
#  (at your option) any later version.
#
#  AntBS is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#  GNU General Public License for more details.
#
#  The following additional terms are in effect as per Section 7 of the license:
#
#  The preservation of all legal notices and author attributions in
#  the material or in the Appropriate Legal Notices displayed
#  by works containing it is required.
#
#  You should have received a copy of the GNU General Public License
#  along with AntBS; If not, see <http://www.gnu.org/licenses/>.


import os
import tarfile
import gevent
from multiprocessing import Process
import subprocess

import re

from database.base_objects import RedisHash
from database.server_status import status
from utils.logging_config import logger
from utils.utilities import Singleton, remove
import utils.docker_util as docker_util

doc_util = docker_util.DockerUtils()
doc = doc_util.doc
PKG_EXT = '.pkg.tar.xz'
SIG_EXT = '.sig'
SCRIPTS_DIR = os.path.join(status.APP_DIR, 'scripts')


class PacmanRepo(RedisHash):
    """
    This class represents a "repo" throughout this application. It is used to
    get/set metadata about the repos that this application manages from/to the database.

    Args:
        name (str): The name of the repo (as it would be configured in pacman.conf).
        path (str): The absolute path to the repo's directory on the server.

    Attributes:
        (str)
            name: see args description above.
            path: see args description above.

        (bool)
            n/a

        (int)
            pkg_count_alpm: Total number of packages in the repo (as per alpm database).
            pkg_count_fs: Total number of packages in the repo (files found on server).

        (list)
            n/a

        (set)
            pkgs_fs: List of the package files in the repo's directory on the server (pkg names)
            pkgs_alpm: List of packages that are in the repo's alpm database file (this is what pacman sees).

    """

    def __init__(self, name=None, path=None, prefix='repo'):
        if not name:
            raise RuntimeError

        super().__init__(prefix=prefix, key=name)

        self.attrib_lists.update(
            dict(string=['name'],
                 bool=['locked'],
                 int=['pkg_count_alpm', 'pkg_count_fs'],
                 list=[],
                 set=['pkgs_fs', 'pkgs_alpm', 'packages', 'unaccounted_for'],
                 path=['path']))

        super().__namespaceinit__()

        if not self or not self.name:
            self.__keysinit__()
            self.name = name
            self.path = os.path.join(path, name)
            status.repos.add(name)

        self.sync_repo_packages_data()

    def sync_repo_packages_data(self):
        self.save_repo_state_alpm()
        self.save_repo_state_filesystem()
        self.setup_packages_manifest()
        self.get_repo_packages_unaccounted_for()

    def setup_packages_manifest(self):
        pkgs_fs = set([p.split('|')[0] for p in self.pkgs_fs if p])
        pkgs_alpm = set([p.split('|')[0] for p in self.pkgs_alpm if p])
        pkgs = list(pkgs_fs & pkgs_alpm)
        unaccounted_for = [p.split('|')[0] for p in list(pkgs_fs) + list(pkgs_alpm) if p not in pkgs]

        for pk in self.packages:
            if pk not in pkgs:
                self.packages.remove(pk)

        for pkg in pkgs:
            self.packages.add(pkg)

        for pk in self.unaccounted_for:
            if pk in pkgs:
                self.unaccounted_for.remove(pk)

        for pak in unaccounted_for:
            self.unaccounted_for.add(pak)

    def _pkgname_matches(self, pkgname, match_in):
        pattern = r'{}\|'.format(pkgname)
        return re.match(pattern, match_in)

    def _get_pkgnames(self, location):
        return [p.split('|')[0] for p in location if p]

    def get_pkgnames_filesystem(self):
        return self._get_pkgnames(self.pkgs_fs)

    def get_pkgnames_alpm(self):
        return self._get_pkgnames(self.pkgs_alpm)

    def get_pkgs_info_from_pkg_fnames(self, pkg_fnames):
        pkgs_info = {}

        for pkg_fname in pkg_fnames:
            pkgname, _pkgver, _arch = pkg_fname.rsplit('-', 2)
            pkgver = '{}-{}'.format(_pkgver, _arch.partition('.')[0])
            arch = _arch.partition('.')[-1]

            pkgs_info[pkgname] = {'pkg_fname': pkg_fname, 'pkgver': pkgver, 'arch': arch}

        return pkgs_info

    def _get_pkgver(self, pkgname, location):
        pkgs = self._get_pkgnames(location)

        if pkgname not in pkgs:
            return ''

        pkgver = [p.split('|')[1] for p in location if p and self._pkgname_matches(pkgname, p)]

        return pkgver[0] if pkgver else ''

    def get_pkgver_alpm(self, pkgname):
        return self._get_pkgver(pkgname, self.pkgs_alpm)

    def get_pkgver_filesystem(self, pkgname):
        return self._get_pkgver(pkgname, self.pkgs_fs)

    def _has_package(self, pkgname, location):
        return pkgname in self._get_pkgnames(location)

    def has_package_filesystem(self, pkgname):
        return self._has_package(pkgname, self.pkgs_fs)

    def has_package_alpm(self, pkgname):
        return self._has_package(pkgname, self.pkgs_alpm)

    def save_repo_state_filesystem(self):
        repodir = os.path.join(self.path, 'x86_64')
        pkgs = set(p for p in os.listdir(repodir) if '.pkg.' in p and not p.endswith('.sig'))

        for pkg in pkgs:
            pkg = os.path.basename(pkg)
            try:
                pkg, version, rel, suffix = pkg.rsplit('-', 3)
            except ValueError:
                logger.error("unexpected pkg: " + pkg)
                continue

            self.pkgs_fs.add('{0}|{1}-{2}'.format(pkg, version, rel))

        self.pkg_count_fs = len(self.pkgs_fs)

    def save_repo_state_alpm(self):
        repodir = os.path.join(self.path, 'x86_64')
        dbfile = os.path.join(repodir, '%s.db.tar.gz' % self.name)

        try:
            with tarfile.open(dbfile, 'r') as pacman_db:
                for pkg in pacman_db.getnames():
                    pkg = pkg.split('/', 1)[0]
                    pkgname, ver, rel = pkg.rsplit('-', 2)

                    self.pkgs_alpm.add('{0}|{1}-{2}'.format(pkgname, ver, rel))
                    self.pkg_count_alpm = len(self.pkgs_alpm)
        except Exception as err:
            logger.error(err)

    def get_repo_packages_unaccounted_for(self):
        unaccounted_for = []

        if self.unaccounted_for:
            for pkg in self.unaccounted_for:
                _pkg = dict(pkgname=pkg, fs=None, alpm=None)

                if self.has_package_filesystem(pkg):
                    _pkg['fs'] = self.get_pkgver_filesystem(pkg)

                if self.has_package_alpm(pkg):
                    _pkg['alpm'] = self.get_pkgver_alpm(pkg)

                unaccounted_for.append(_pkg)

        return unaccounted_for

    def _do_update_repo(self, pkg_fnames, is_review=False, review_result=None):
        pkg_fnames = list(pkg_fnames)
        action = 'add'

        self.sync_repo_packages_data()

        for pkg_fname in pkg_fnames:
            if pkg_fname and not is_review:
                self._add_or_remove_package_alpm_database(pkg_fname, action)

            elif pkg_fname and is_review and review_result is not None:
                action = 'add' if 'passed' == review_result else 'remove'

                self._add_or_remove_package_alpm_database(pkg_fname, action)

        self._post_update_sanity_check(pkg_fnames, action)

    def _post_update_sanity_check(self, pkg_fnames, action):
        pkgs_info = self.get_pkgs_info_from_pkg_fnames(pkg_fnames)
        all_okay = []

        self.sync_repo_packages_data()

        for pkgname, pkg_info in pkgs_info.items():
            has_pkg_alpm = self.has_package_alpm(pkgname)
            has_pkg_fs = self.has_package_filesystem(pkgname)

            if 'add' == action:
                pkgver_alpm_match = self.get_pkgver_alpm(pkgname) == pkg_info['pkgver']
                pkgver_fs_match = self.get_pkgver_filesystem(pkgname) == pkg_info['pkgver']

                if all([has_pkg_alpm, pkgver_alpm_match, has_pkg_fs, pkgver_fs_match]):
                    all_okay.append(True)
                else:
                    all_okay.append(False)

            elif 'remove' == action:
                if not any([has_pkg_alpm, has_pkg_fs]):
                    all_okay.append(True)
                else:
                    all_okay.append(False)

            if not all(all_okay):
                logger.error(
                    'Post repo update sanity check failed! {}'.format(all_okay)
                )

    def _handle_pkg_review_passed(pkg_fname):
        raise NotImplementedError()

    def _try_run_command(self, cmd, cwd):
        res = None
        success = False

        try:
            res = subprocess.check_output(
                cmd, stderr=subprocess.STDOUT, universal_newlines=True, cwd=cwd
            )
            success = True
        except subprocess.CalledProcessError as err:
            logger.error((err.output, err.stderr))
            res = err.output

        return success, res

    def _add_or_remove_package_alpm_database(self, pkg_fname, action):
        package_file = '{}{}'.format(pkg_fname, PKG_EXT)
        arch = 'x86_64' if 'i686' not in pkg_fname else 'i686'
        cwd = os.path.join(self.path, arch)
        action = 'repo-{}'.format(action)
        cmd = [os.path.join(SCRIPTS_DIR, action)]

        if 'add' == action:
            cmd.append('-R')

        cmd.extend([
            '{}.db.tar.gz'.format(self.name),
            package_file
        ])

        success, res = self._try_run_command(cmd, cwd)
        lock_not_aquired = 'Failed to acquire lockfile'
        waiting = 0

        if not success and lock_not_aquired in res:
            logger.warning(res)
            while not success and lock_not_aquired in res:
                waiting += 10
                gevent.sleep(10)
                success, res = self._try_run_command(cmd, cwd)

                if waiting > 300:
                    logger.error('repo-add script timed out!')
                    break

        if not success:
            logger.error(
                '%s package command on alpm database failed for %s! Output was: %s',
                action,
                pkg_fname,
                res
            )

    def update_repo(self, pkg_fnames, is_review=False, review_result=None):
        trans_running = status.transactions_running or status.transaction_queue
        building_saved = False
        excluded = ['Updating antergos repo database.',
                    'Updating antergos-staging repo database.',
                    'Processing developer review result.']

        if not status.idle and trans_running and status.current_status not in excluded:
            building_saved = status.current_status
        elif status.idle:
            status.idle = False

        msg = excluded[0] if 'antergos' == self.name else excluded[1]
        status.current_status = msg

        self._do_update_repo(pkg_fnames, is_review, review_result)

        trans_running = status.transactions_running or status.transaction_queue

        if building_saved and not status.idle and status.current_status == msg:
            status.current_status = building_saved

        elif status.idle and not trans_running and not status.now_building:
            status.idle = True
            status.current_status = 'Idle.'


class AntergosRepo(PacmanRepo, metaclass=Singleton):
    def __init__(self, name='antergos', *args, **kwargs):
        super().__init__(name=name, *args, **kwargs)


class AntergosStagingRepo(PacmanRepo, metaclass=Singleton):
    def __init__(self, name='antergos-staging', *args, **kwargs):
        super().__init__(name=name, *args, **kwargs)


def get_repo_object(name, path=None):
    if not path:
        path = status.REPO_BASE_DIR
    if 'antergos' == name:
        repo = AntergosRepo(name=name, path=path)
    elif 'antergos-staging' == name:
        repo = AntergosStagingRepo(name=name, path=path)
    else:
        raise TypeError('name must be one of [antergos, antergos-staging]')

    return repo
