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
from io import TextIOWrapper
from pkg_resources import parse_version

import gevent

from . import (
    RedisHash,
    status,
    RedisSingleton
)

from utils import (
    remove,
    try_run_command,
    DockerUtils
)

logger = status.logger
doc_util = DockerUtils(status)
doc = doc_util.doc
PKG_EXT = '.pkg.tar.xz'
SIG_EXT = '.sig'
DB_EXT = '.db.tar.gz'
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

    attrib_lists = dict(
        string=['name', 'alpm_db', 'arch'],
        bool=['locked'],
        int=['pkg_count_alpm', 'pkg_count_fs'],
        list=['pkgnames'],
        set=['pkgs_fs', 'pkgs_alpm', 'packages', 'unaccounted_for'],
        path=['path', 'alpm_db_path']
    )

    def __init__(self, name, arch, path=None, prefix='repo'):
        key = '{}:{}'.format(name, arch)

        super().__init__(prefix=prefix, key=key)

        self.__namespaceinit__()

        if not self or not self.name:
            self.name = name
            self.arch = arch
            self.path = os.path.join(path, name, self.arch)
            self.alpm_db = '{}.db.tar.gz'.format(self.name)
            self.alpm_db_path = os.path.join(path, name, self.arch, self.alpm_db)
            status.repos.add(name)

        self._lock_name = '{}:_lock'.format(self.full_key)
        self.locked = False

    def _add_or_remove_package_alpm_database(self, action, pkgname=None, pkg_fname=None):
        action = 'repo-{}'.format(action)
        cmd = [os.path.join(SCRIPTS_DIR, action)]
        pkg_or_file = pkgname if 'remove' in action else pkg_fname

        if 'add' in action:
            cmd.append('-R')

        cmd.extend([self.alpm_db, pkg_or_file])
        logger.debug(cmd)

        success, res = try_run_command(cmd, self.path)
        lock_not_aquired = 'Failed to acquire lockfile'
        waiting = 0

        if not success and lock_not_aquired in res:
            logger.warning(res)
            while not success and lock_not_aquired in res:
                waiting += 10
                gevent.sleep(10)
                success, res = try_run_command(cmd, self.path)

                if waiting > 300:
                    logger.error('repo-add script timed out!')
                    break

        if not success:
            logger.error(
                '%s command on alpm database failed for %s! Output was: %s',
                action,
                pkg_fname,
                res
            )

    def _compare_pkgvers(self, pkgvers):
        if len(pkgvers) == 1:
            return pkgvers
        elif not pkgvers:
            raise ValueError('pkgvers cannot be empty.')

        _pkgvers = pkgvers
        compare_to = _pkgvers.pop()

        while len(_pkgvers) > 1:
            last_count = len(_pkgvers)
            cmp_result = [v for v in _pkgvers if self._pkgver_is_greater_than(v, compare_to)]
            this_count = len(cmp_result)

            if this_count == 0:
                _pkgvers = [compare_to]
            elif this_count == 1:
                _pkgvers = cmp_result
            elif this_count == last_count:
                compare_to = cmp_result.pop()
                _pkgvers = cmp_result

        return _pkgvers

    def _determine_current_repo_state_alpm(self):
        self.pkgs_alpm.remove_range(0, -1)

        try:
            with tarfile.open(self.alpm_db_path, 'r') as alpm_db:
                pkg_info_files = [p for p in alpm_db.getmembers() if '/desc' in p.name]

                for pkg_info_file in pkg_info_files:
                    pkg_info_bytes = alpm_db.extractfile(pkg_info_file)
                    pkg_file_name = TextIOWrapper(pkg_info_bytes).readlines()[1].strip()
                    pkg_file_name = pkg_file_name.replace('.pkg', '-pkg')

                    pkgname, ver, rel, arch, suffix = pkg_file_name.rsplit('-', 4)

                    self.pkgs_alpm.add('{0}|{1}-{2}|{3}'.format(pkgname, ver, rel, arch))

            self.pkg_count_alpm = len(self.pkgs_alpm)

        except Exception as err:
            logger.error(err)

    def _determine_current_repo_state_fs(self):
        pkgs = set(p for p in os.listdir(self.path) if '.pkg.' in p and not p.endswith('.sig'))

        self.pkgs_fs.remove_range(0, -1)

        for pkg_file_name in pkgs:
            pkg_file_name = pkg_file_name.replace('.pkg', '-pkg')

            try:
                pkg, version, rel, arch, suffix = pkg_file_name.rsplit('-', 4)
            except ValueError:
                logger.error("unexpected pkg: " + pkg_file_name)
                continue

            self.pkgs_fs.add('{0}|{1}-{2}|{3}'.format(pkg, version, rel, arch))

        self.pkg_count_fs = len(self.pkgs_fs)

    def _get_packages_unaccounted_for_info(self):
        unaccounted_for = {}

        for pkg in self.unaccounted_for:
            pkgname, pkgver, arch = self._split_pkg_info_string(pkg)
            unaccounted_for[pkgname] = dict(fs=[], alpm=[])
            fname = '{}{}'.format(pkg.replace('|', '-'), PKG_EXT)

            if self.has_package_filesystem(pkgname):
                unaccounted_for[pkgname]['fs'].append((pkg, fname))

            if self.has_package_alpm(pkgname) and pkgver == self.get_pkgver_alpm(pkgname):
                unaccounted_for[pkgname]['alpm'].append((pkg, fname))

        return unaccounted_for

    @staticmethod
    def _get_pkgnames(location):
        return [p.split('|')[0] for p in location if p]

    def _get_pkgvers(self, pkgname, location):
        pkgs = self._get_pkgnames(location)

        if pkgname not in pkgs:
            return []

        pkgvers = [p.split('|')[1] for p in location if p and p.split('|')[0] == pkgname]

        return pkgvers

    def _has_package(self, pkgname, location):
        return pkgname in self._get_pkgnames(location)

    @staticmethod
    def _pkgver_is_greater_than(pkgver, compare_to):
        return parse_version(pkgver) > parse_version(compare_to)

    def _process_current_repo_states(self):
        pkgs_fs = set(list(self.pkgs_fs))
        pkgs_alpm = set(list(self.pkgs_alpm))
        accounted_for = list(pkgs_fs & pkgs_alpm)
        unaccounted_for = list(pkgs_fs - pkgs_alpm) + list(pkgs_alpm - pkgs_fs)

        logger.debug([unaccounted_for])

        self.packages.remove_range(0, -1)
        self.unaccounted_for.remove_range(0, -1)
        self.pkgnames.remove_range(-1, -0)

        for pkg in accounted_for:
            self.packages.add(pkg)

        for pkg in unaccounted_for:
            self.unaccounted_for.add(pkg)

        for pkg in self._get_pkgnames(accounted_for):
            self.pkgnames.append(pkg)

    def _process_repo_packages_unaccounted_for(self):
        unaccounted_for = self._get_packages_unaccounted_for_info()
        add_to_db = []
        rm_from_db = []
        rm_from_fs = []

        if not unaccounted_for:
            return

        logger.debug(unaccounted_for)

        for pkgname, locations in unaccounted_for.items():
            if not locations['fs'] and not locations['alpm']:
                logger.error('nothing to compare')
                continue

            if locations['fs']:
                versions = [p_info[0].split('|')[1] for p_info in locations['fs']]
                latest = ''
                latest_fs = self._compare_pkgvers(versions)
                in_db_now = self.get_pkgver_alpm(pkgname)

                if latest_fs and in_db_now:
                    latest = self._compare_pkgvers([latest_fs[0], in_db_now])
                    latest = latest[0]
                elif latest_fs and not in_db_now:
                    latest = latest_fs[0]
                elif in_db_now:
                    latest = in_db_now

                if latest != in_db_now:
                    fname = [f[1] for f in locations['fs'] if latest in f[1]]

                    add_to_db.append(fname[0])

                filenames = [f[1] for f in locations['fs'] if latest not in f[1]]

                for fname in filenames:
                    rm_from_fs.append(fname)

            elif locations['alpm'] and not locations['fs']:
                rm_from_db.append(pkgname)

        logger.debug([
            self.name, ('add_to_db', add_to_db),
            ('rm_from_db', rm_from_db),
            ('rm_from_fs', rm_from_fs)
        ])

        return add_to_db, rm_from_db, rm_from_fs

    @staticmethod
    def _remove_package_from_filesystem(pkg_file):
        sig = '{}{}'.format(pkg_file, SIG_EXT)

        for file_name in [pkg_file, sig]:
            remove(file_name)

    @staticmethod
    def _split_pkg_info_string(pkg_info_string):
        return pkg_info_string.split('|')

    def _take_action_on_packages_unaccounted_for(self, add_to_db, rm_from_db, rm_from_fs):
        if add_to_db:
            for pkg in add_to_db:
                self._add_or_remove_package_alpm_database('add', pkg_fname=pkg)

        if rm_from_db:
            for pkg in rm_from_db:
                self._add_or_remove_package_alpm_database('remove', pkgname=pkg)

        if rm_from_fs:
            for pkg in rm_from_fs:
                self._remove_package_from_filesystem(pkg)

    def _update_repo(self):
        self.sync_repo_packages_data(release_lock_after=False)

        if self.unaccounted_for:
            if self.locked and self.db.exists(self._lock_name):
                self.db.expire(self._lock_name, 300)

                add_to_db, rm_from_db, rm_from_fs = self._process_repo_packages_unaccounted_for()

                self._take_action_on_packages_unaccounted_for(add_to_db, rm_from_db, rm_from_fs)

            else:
                logger.debug('Repo lock was released. Cannot continue!!')

        self.db.delete(self._lock_name)
        self.locked = False

    def get_pkgnames_alpm(self):
        return self._get_pkgnames(self.pkgs_alpm)

    def get_pkgnames_filesystem(self):
        return self._get_pkgnames(self.pkgs_fs)

    def get_pkgver_alpm(self, pkgname):
        pkgver = ''
        pkgvers = self._get_pkgvers(pkgname, self.pkgs_alpm)

        if pkgvers and len(pkgvers) == 1:
            pkgver = pkgvers[0]
        elif pkgvers and len(pkgvers) != 1:
            logger.error(pkgvers)

        return pkgver

    def get_pkgvers_filesystem(self, pkgname):
        return self._get_pkgvers(pkgname, self.pkgs_fs)

    def has_package_filesystem(self, pkgname):
        return self._has_package(pkgname, self.pkgs_fs)

    def has_package_alpm(self, pkgname):
        return self._has_package(pkgname, self.pkgs_alpm)

    def sync_repo_packages_data(self, release_lock_after=True):
        if not self.locked and self.db.setnx(self._lock_name, True):
            self.locked = True
            self.db.expire(self._lock_name, 300)

            self._determine_current_repo_state_alpm()
            self._determine_current_repo_state_fs()
            self._process_current_repo_states()

            if release_lock_after:
                self.db.delete(self._lock_name)
                self.locked = False

        else:
            logger.debug('repo is locked! waiting for lock to be released...')
            gevent.sleep(1)

            while self.locked:
                gevent.sleep(5)

            if release_lock_after:
                logger.debug('lock has been released!')
            else:
                if self.db.setnx(self._lock_name, True):
                    self.db.expire(self._lock_name, 300)
                    self.locked = True

    def update_repo(self):
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

        self._update_repo()

        trans_running = status.transactions_running or status.transaction_queue

        if building_saved and not status.idle and status.current_status == msg:
            status.current_status = building_saved

        elif status.idle and not trans_running and not status.now_building:
            status.idle = True
            status.current_status = 'Idle.'


def get_repo_object(name, arch, path=None):
    path = path if path else status.REPO_BASE_DIR

    if name in ['antergos', 'antergos-staging']:
        return PacmanRepo(name, arch, path=path)
    else:
        raise ValueError('name must be one of [antergos, antergos-staging]')
