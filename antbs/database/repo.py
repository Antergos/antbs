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
from redis.lock import Lock

from database.base_objects import RedisHash
from database.server_status import status
from utils.logging_config import logger
from utils.utilities import Singleton, remove, try_run_command, MyLock
import utils.docker_util as docker_util

doc_util = docker_util.DockerUtils()
doc = doc_util.doc
PKG_EXT = '.pkg.tar.xz'
SIG_EXT = '.sig'
DB_EXT = '.db.tar.gz'
SCRIPTS_DIR = os.path.join(status.APP_DIR, 'scripts')

main_repo = staging_repo = None


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

    def __init__(self, name, path=None, prefix='repo'):
        super().__init__(prefix=prefix, key=name)

        self.attrib_lists.update(
            dict(string=['name', 'alpm_db'],
                 bool=['locked'],
                 int=['pkg_count_alpm', 'pkg_count_fs'],
                 list=['pkgnames'],
                 set=['pkgs_fs', 'pkgs_alpm', 'packages', 'unaccounted_for'],
                 path=['path', 'path64', 'path32', 'alpm_db_path64', 'alpm_db_path32']))

        super().__namespaceinit__()

        if not self or not self.name:
            self.__keysinit__()
            self.name = name
            self.path = os.path.join(path, name)
            status.repos.add(name)

        _lock_name = '{}:_lock'.format(self.full_key)
        self._lock = MyLock(self.db, _lock_name)

    def sync_repo_packages_data(self):
        with self._lock:
            self.locked = True

            self._determine_current_repo_state_alpm()
            self._determine_current_repo_state_fs()
            self._process_current_repo_states()

        self.locked = False

    def _process_current_repo_states(self):
        pkgs_fs = set(self.pkgs_fs)
        pkgs_alpm = set(self.pkgs_alpm)
        accounted_for = list(pkgs_fs & pkgs_alpm)
        unaccounted_for = list(pkgs_fs - pkgs_alpm) + list(pkgs_alpm - pkgs_fs)

        self.packages.remove_range(0, -1)
        self.unaccounted_for.remove_range(0, -1)
        self.pkgnames.remove_range(0, -1)

        for pkg in accounted_for:
            self.packages.add(pkg)

        for pkg in unaccounted_for:
            self.unaccounted_for.add(pkg)

        self.pkgnames.extend(self._get_pkgnames(accounted_for))

    def _get_pkgnames(self, location):
        return [p.split('|')[0] for p in location if p]

    def get_pkgnames_filesystem(self):
        return self._get_pkgnames(self.pkgs_fs)

    def get_pkgnames_alpm(self):
        return self._get_pkgnames(self.pkgs_alpm)

    def _get_pkgvers(self, pkgname, location):
        pkgs = self._get_pkgnames(location)

        if pkgname not in pkgs:
            return []

        pkgvers = [p.split('|')[1] for p in location if p and p.split('|')[0] == pkgname]

        return pkgvers

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

    def _has_package(self, pkgname, location):
        return pkgname in self._get_pkgnames(location)

    def has_package_filesystem(self, pkgname):
        return self._has_package(pkgname, self.pkgs_fs)

    def has_package_alpm(self, pkgname):
        return self._has_package(pkgname, self.pkgs_alpm)

    def _determine_current_repo_state_fs(self):
        pkgs = set(p for p in os.listdir(self.path64) if '.pkg.' in p and not p.endswith('.sig'))

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

    def _determine_current_repo_state_alpm(self):
        self.pkgs_alpm.remove_range(0, -1)

        try:
            with tarfile.open(self.alpm_db_path64, 'r') as alpm_db:
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

    def _get_packages_unaccounted_for_info(self):
        unaccounted_for = {}

        for pkg in self.unaccounted_for:
            pkgname = pkg.rsplit('|')[0]
            unaccounted_for[pkgname] = dict(fs=[], alpm=[])

            if self.has_package_filesystem(pkgname):
                unaccounted_for[pkgname]['fs'].extend(self.get_pkgvers_filesystem(pkgname))

            if self.has_package_alpm(pkgname):
                unaccounted_for[pkgname]['alpm'].append(self.get_pkgver_alpm(pkgname))

        return unaccounted_for

    @staticmethod
    def _pkgver_is_greater_than(pkgver, compare_to):
        return parse_version(pkgver) > parse_version(compare_to)

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

    def _process_repo_packages_unaccounted_for(self):
        unaccounted_for = self._get_packages_unaccounted_for_info()
        logger.debug(unaccounted_for)
        add_to_db = []
        rm_from_db = []
        rm_from_fs = []

        for pkgname, versions in unaccounted_for:
            if not versions['fs'] and not versions['alpm']:
                logger.error('nothing to compare')
                continue

            if versions['fs'] and not versions['alpm']:
                latest = ''
                latest_fs = self._compare_pkgvers(versions['fs'])
                in_db_now = self.get_pkgver_alpm(pkgname)

                if latest_fs and in_db_now:
                    latest = self._compare_pkgvers([latest_fs[0], in_db_now])
                elif latest_fs and not in_db_now:
                    latest = latest_fs

                if latest[0] != in_db_now:
                    add_to_db.append((pkgname, latest[0]))

                for pkgver in versions['fs']:
                    if pkgver != latest[0]:
                        rm_from_fs.append((pkgname, pkgver))

            elif versions['alpm'] and not versions['fs']:
                for pkgver in versions['alpm']:
                    rm_from_db.append((pkgname, pkgver))

        logger.error(
            [('add_to_db', add_to_db), ('rm_from_db', rm_from_db), ('rm_from_fs', rm_from_fs)]
        )

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

        success, res = try_run_command(cmd, cwd)
        lock_not_aquired = 'Failed to acquire lockfile'
        waiting = 0

        if not success and lock_not_aquired in res:
            logger.warning(res)
            while not success and lock_not_aquired in res:
                waiting += 10
                gevent.sleep(10)
                success, res = try_run_command(cmd, cwd)

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

    def _update_repo(self):
        self.sync_repo_packages_data()

        if self.pkgs_alpm != self.pkgs_fs:
            with self._lock:
                self.locked = True
                self._process_repo_packages_unaccounted_for()

            self.locked = False

        self._post_update_sanity_check(pkg_fnames, action)

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


class AntergosRepo(PacmanRepo, metaclass=Singleton):
    def __init__(self, name='antergos', *args, **kwargs):
        super().__init__(name=name, *args, **kwargs)


class AntergosStagingRepo(PacmanRepo, metaclass=Singleton):
    def __init__(self, name='antergos-staging', *args, **kwargs):
        super().__init__(name=name, *args, **kwargs)


def get_repo_object(name, path=None):
    global main_repo, staging_repo

    path = path if path else status.REPO_BASE_DIR

    if main_repo is None:
        main_repo = AntergosRepo(path=path)
        main_repo.sync_repo_packages_data()

    if staging_repo is None:
        staging_repo = AntergosStagingRepo(path=path)
        staging_repo.sync_repo_packages_data()

    if 'antergos' == name:
        return main_repo
    elif 'antergos-staging' == name:
        return staging_repo
    else:
        raise TypeError('name must be one of [antergos, antergos-staging]')
