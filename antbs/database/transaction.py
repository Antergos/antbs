#!/usr/bin/python
# -*- coding: utf-8 -*-
#
# transaction.py
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
import shutil
import subprocess
import tempfile
import gevent

from utils import (
    all_file_paths_exist,
    copy_or_symlink,
    try_run_command,
    DockerUtils,
    PacmanPackageCache,
    remove
)

from . import (
    RedisHash,
    get_build_object,
    get_pkg_object,
    status,
    get_repo_object
)

logger = status.logger
doc_util = DockerUtils(status)
doc = doc_util.doc

pkg_cache_obj = PacmanPackageCache()


class TransactionMeta(RedisHash):
    """
    This is the base class for `Transaction`(s). It simply sets up the attributes
    which are stored in redis so they can be properly accessed. This class should
    not be used directly.

    Args:
        See `Transaction` docstring.

    Attributes:
        See `Transaction` docstring.
    """

    _main_repo = get_repo_object('antergos', 'x86_64')
    _staging_repo = get_repo_object('antergos-staging', 'x86_64')
    _main_repo32 = get_repo_object('antergos', 'i686')
    _staging_repo32 = get_repo_object('antergos-staging', 'i686')

    attrib_lists = dict(
        string=['building', 'start_str', 'end_str', 'initiated_by', 'gh_sha_before', 'gh_sha_after', 'gh_patch'],
        bool=['is_running', 'is_finished', 'sync_pkgbuilds_only'],
        int=['tnum'],
        list=['queue'],
        set=['packages', 'builds', 'completed', 'failed', 'generated_pkgs'],
        path=['base_path', 'path', 'result_dir', 'cache', 'cache_i686', 'upd_repo_result']
    )

    def __init__(self, packages=None, tnum=None, base_path='/var/tmp/antbs', namespace='antbs',
                 prefix='trans', repo_queue=None):
        if not packages and not tnum:
            raise ValueError('At least one of [packages, tnum] required.')
        elif packages and tnum:
            raise ValueError('Only one of [packages, tnum] can be given, not both.')

        the_tnum = tnum
        if not tnum:
            the_tnum = self.db.incr('antbs:misc:tnum:next')

        super().__init__(namespace=namespace, prefix=prefix, key=the_tnum)

        self.__namespaceinit__()

        self._repo_queue = repo_queue
        self._internal_deps = []
        self._build_dirpaths = {}
        self._pkgvers = {}

        if not self or not self.tnum:
            self.tnum = the_tnum
            self.base_path = base_path
            self.cache = pkg_cache_obj.cache
            self.cache_i686 = pkg_cache_obj.cache_i686

            if packages:
                packages = [p for p in packages if p]

                for pkg in packages:
                    self.packages.add(pkg)

        for pkg in self.packages:
            self._build_dirpaths[pkg] = {'build_dir': '', '32bit': '', '32build': ''}
            self._pkgvers[pkg] = ''


class Transaction(TransactionMeta):
    """
    This class represents a single "build transaction" throughout the app. It is used
    to get/set transaction data from/to the database. A transaction is comprised
    of one or more builds. When a new transaction is initialized it creates its own build
    directory which it will delete once all builds are completed. This allows for
    build concurrency through multiple transactions and can be easily scaled as needed.

        Args:
            packages (list): Names of packages to build. This creates a new `Transaction`.
            tnum (int): Get an existing `Transaction` identified by its `tnum`.

        Attributes:
            tnum (int): This transaction's number or id if you prefer calling it that.
            base_path (str): Absolute path to the top-level build directory (for all transactions).
            path (str): Absolute path to this transaction's build directory.
            builds (list): This transaction's builds (list of bnums)
            is_running (bool): Whether or not the transaction is currently running.
            is_finished (bool): Whether or not the transaction is done (regardless of results)
            building (str): The name of the package currently building.
            start_str (str): The datetime string for when this transaction started.
            end_str (str): The datetime string for when this transaction ended.
            completed (list): Builds that completed successfully (list of bnums).
            failed (list): Builds that failed (list of bnums).
            internal_deps (list): List of packages that depend on package(s) in this transaction.

        Raises:
            ValueError: If both `packages` and `tnum` are Falsey.
    """

    def start(self):
        if self._repo_queue is None:
            logger.debug('self._repo_queue is: %s', self._repo_queue)
            raise AttributeError('_repo_queue is required to start a transaction.')

        status.current_status = 'Initializing build transaction.'
        self.is_running = True

        status.transactions_running.append(self.tnum)
        self.setup_transaction_directory()

        status.current_status = 'Processing packages.'

        self.process_packages()

        if self.sync_pkgbuilds_only:
            return

        status.current_status = 'Cleaning pacman package cache.'

        PacmanPackageCache().maybe_do_cache_cleanup()

        if self.queue:
            while self.queue:
                pkg = self.queue.lpop()
                build_dir = self.get_build_directory(pkg)

                if not build_dir:
                    raise RuntimeError('build_dir cannot be None.')

                pkg_obj = get_pkg_object(name=pkg)
                bld_obj = get_build_object(pkg_obj=pkg_obj, tnum=self.tnum, trans_obj=self)

                if pkg_obj.is_iso:
                    self.fetch_and_compile_translations(
                        translations_for=["cnchi_updater", "antergos-gfxboot"]
                    )
                    result = bld_obj.start(pkg_obj)
                else:
                    bld_obj = self.setup_build_directory(bld_obj, build_dir)
                    result = bld_obj.start(pkg_obj)

                logger.debug(result)
                if result in [True, False]:
                    blds = pkg_obj.builds
                    total = len(blds)

                    if total > 0:
                        success = len([x for x in blds if x in status.completed])
                        failure = len([x for x in blds if x in status.failed])

                        if success > 0:
                            success = 100 * success / total

                        if failure > 0:
                            failure = 100 * failure / total

                        pkg_obj.success_rate = success
                        pkg_obj.failure_rate = failure

                    if result is True:
                        if not pkg_obj.is_iso:
                            gevent.sleep(2)
                            self.move_files_to_staging_repo(bld_obj)
                            self._staging_repo.update_repo()
                            self._staging_repo32.update_repo()

                        self.completed.append(bld_obj.bnum)
                        doc_util.do_docker_clean(pkg_obj.name)

                    elif result is False:
                        self.failed.append(bld_obj.bnum)

                status.now_building.remove(bld_obj.bnum)

        self.is_running = False
        self.is_finished = True
        status.transactions_running.remove(self.tnum)

        remove(self.path)

    def setup_transaction_directory(self):
        path = tempfile.mkdtemp(prefix='{0}_'.format(str(self.tnum)), dir=self.base_path)
        os.chmod(path, 0o777)
        self.result_dir = os.path.join(path, 'result')
        self.upd_repo_result = os.path.join(path, 'upd_result')
        self.path = os.path.join(path, 'antergos', 'antergos-packages')

        os.mkdir(self.result_dir, mode=0o777)
        os.mkdir(self.upd_repo_result, mode=0o777)

        try:
            subprocess.check_output(['/usr/bin/git', 'clone', status.gh_repo_url], cwd=path)
        except subprocess.CalledProcessError as err:
            raise RuntimeError(err.output)

    def get_build_directory(self, pkg):
        pbpath = None
        paths = [
            os.path.join(self.path, 'mate', pkg),
            os.path.join(self.path, 'cinnamon', pkg),
            os.path.join(self.path, pkg)
        ]

        for p in paths:
            if os.path.exists(p):
                pbpath = p
                break
        else:
            raise RuntimeError('Unable to determine pb_path for {0}'.format(pkg))

        return pbpath

    def setup_build_directory(self, bld_obj, build_dir):

        self._build_dirpaths[bld_obj.pkgname].update({
            'build_dir': build_dir,
            '32bit': os.path.join(build_dir, '32bit'),
            '32build': os.path.join(build_dir, '32build'),
            'result': os.path.join(self.result_dir, bld_obj.pkgname)
        })

        for bdir, path in self._build_dirpaths[bld_obj.pkgname].items():
            if not os.path.exists(path):
                os.mkdir(path, mode=0o777)

        bld_obj.build_dir = self._build_dirpaths[bld_obj.pkgname]['build_dir']
        bld_obj._32bit = self._build_dirpaths[bld_obj.pkgname]['32bit']
        bld_obj._32build = self._build_dirpaths[bld_obj.pkgname]['32build']
        bld_obj.result_dir = self._build_dirpaths[bld_obj.pkgname]['result']

        return bld_obj

    def handle_special_cases(self, pkg, pkg_obj):
        if 'cnchi' in pkg:
            logger.info('cnchi package detected.')
            status.current_status = 'Fetching latest translations for %s from Transifex.' % pkg
            logger.info(status.current_status)
            cnchi_dir = self.get_build_directory(pkg)
            # pkg_obj.prepare_package_source(cnchi_dir)
            self.fetch_and_compile_translations(translations_for=["cnchi"], pkg_obj=pkg_obj)
            #remove(os.path.join(cnchi_dir, 'cnchi/.git'))
            #subprocess.check_output(['tar', '-cf', 'cnchi.tar', 'cnchi'], cwd=cnchi_dir)

        elif 'numix-icon-theme-square' == pkg:
            src = os.path.join('/var/tmp/antergos-packages/', pkg, pkg + '.zip')
            dest = os.path.join(self.path, pkg)
            shutil.move(src, dest)

    def move_files_to_staging_repo(self, bld_obj):
        file_count = len(bld_obj.generated_files)
        files_exist = bld_obj.generated_files and all_file_paths_exist(bld_obj.generated_files)

        if not files_exist or not (file_count % 2 == 0):
            logger.error(
                'Unable to move files to staging repo! files_exist is: %s file_count is: %s',
                files_exist,
                file_count
            )

        for pkg_file in bld_obj.generated_files:
            if 'i686' in pkg_file:
                continue

            fname = os.path.basename(pkg_file)
            staging_file = os.path.join(status.STAGING_64, fname)

            copy_or_symlink(pkg_file, status.STAGING_64, logger)
            bld_obj.staging_files.append(staging_file)

            if '-any.pkg' in pkg_file:
                src = os.path.basename(pkg_file)
                dst = '../i686/{}'.format(fname)

                success, res = try_run_command(
                    ['/bin/ln', '-srf', src, dst],
                    cwd=status.STAGING_64,
                    logger=logger
                )
                if not success:
                    logger.error(res)

        for pkg_file in bld_obj.generated_files:
            if 'x86_64' in pkg_file or '-any.pkg' in pkg_file:
                continue

            fname = os.path.basename(pkg_file)
            staging_file = os.path.join(status.STAGING_32, fname)

            copy_or_symlink(pkg_file, status.STAGING_32, logger)
            bld_obj.staging_files.append(staging_file)

    def process_packages(self):
        _pkgs = [p for p in self.packages if p]

        for pkg in self.packages:
            if not pkg:
                continue

            pbpath = self.get_build_directory(pkg)

            if not pbpath:
                raise RuntimeError('pbpath cannot be None.')

            pkg_obj = get_pkg_object(name=pkg)
            version = pkg_obj.get_version_str()

            if not version:
                self.packages.remove(pkg)
                logger.debug('Skipping cnchi-dev build: {0}'.format(pkg))
                continue

            pkg_obj.version_str = version
            self._pkgvers[pkg] = version

            log_msg = 'Updating pkgver in database for {0} to {1}'.format(pkg, version)
            logger.info(log_msg)
            status.current_status = log_msg

            depends = pkg_obj.get_deps()

            intersect = list(set(depends) & set(_pkgs))
            logger.debug((depends, intersect))
            if depends and len(intersect) > 0:
                self._internal_deps.append((pkg, intersect))
            else:
                self._internal_deps.append((pkg, []))

            self.handle_special_cases(pkg, pkg_obj)

        status.current_status = 'Using package dependencies to determine build order.'
        if self._internal_deps:
            for name in self.determine_build_order(self._internal_deps):
                if name not in self.queue:
                    self.queue.append(name)

        for pkg in self.packages:
            if pkg not in self.queue:
                self.queue.append(pkg)

    def fetch_and_compile_translations(self, translations_for=None, pkg_obj=None):
        """
        Get and compile translations from Transifex.

        :param (list) translations_for:
        :param (Package) pkg_obj:

        """

        if pkg_obj is None:
            name = ''
        else:
            name = pkg_obj.name or pkg_obj.pkgname

        pbdir = self.get_build_directory(name)
        dest_dir = os.path.join(pbdir, 'po')

        trans = {
            "cnchi": {
                'trans_dir': "/opt/cnchi-translations/",
                'trans_files_dir': '/opt/cnchi-translations/translations/antergos.cnchi',
                'dest_dir': dest_dir
            },
            "cnchi_updater": {
                'trans_dir': "/opt/antergos-iso-translations/",
                'trans_files_dir': "/opt/antergos-iso-translations/translations/antergos.cnchi_updaterpot",
                'dest_dir': '/srv/antergos.info/repo/iso/testing/trans/cnchi_updater'
            },
            "antergos-gfxboot": {
                'trans_dir': "/opt/antergos-iso-translations/",
                'trans_files_dir': '/opt/antergos-iso-translations/translations/antergos.antergos-gfxboot',
                'dest_dir': '/srv/antergos.info/repo/iso/testing/trans/antergos-gfxboot'
            }
        }

        for trans_for in translations_for:

            if not os.path.exists(trans[trans_for]['dest_dir']):
                os.mkdir(trans[trans_for]['dest_dir'])
            try:

                output = subprocess.check_output(['tx', 'pull', '-a', '--minimum-perc=50'],
                                                 cwd=trans[trans_for]['trans_dir'])

                for r, d, f in os.walk(trans[trans_for]['trans_files_dir']):
                    for tfile in f:
                        if trans_for in ['cnchi', 'antergos-gfxboot']:
                            tfile = os.path.join(r, tfile)
                            logger.debug(
                                'Copying %s to %s' % (tfile, trans[trans_for]['dest_dir']))
                            shutil.copy(tfile, trans[trans_for]['dest_dir'])
                        elif 'cnchi_updater' == trans_for:
                            mofile = tfile[:-2] + 'mo'
                            subprocess.check_call(['msgfmt', '-v', tfile, '-o', mofile],
                                                  cwd=trans[trans_for]['trans_files_dir'])
                            os.rename(os.path.join(trans[trans_for]['trans_files_dir'], mofile),
                                      os.path.join(trans[trans_for]['dest_dir'], mofile))

            except subprocess.CalledProcessError as err:
                logger.error(err.output)
            except Exception as err:
                logger.error(err)

    @staticmethod
    def determine_build_order(source):
        """
        Performs a topological sort on elements. This determines the order in which
        packages must be built based on internal (to this transaction) dependencies.

        Args:
            source (list): A list of ``(name, [list of dependancies])`` pairs.

        Returns:
            A list of names, with dependancies listed first.

        Raises:
            ValueError: When cyclic or missing dependancy detected.

        """
        # copy deps so we can modify set in-place
        pending = [(name, set(deps)) for name, deps in source]
        emitted = []
        try:
            while pending:
                next_pending = []
                next_emitted = []

                for entry in pending:
                    name, deps = entry
                    # remove deps we emitted last pass
                    deps.difference_update(emitted)

                    if deps:
                        # still has deps? recheck during next pass
                        next_pending.append(entry)
                    else:
                        # no more deps? time to emit
                        yield name
                        emitted.append(name)
                        # remember what we emitted for difference_update() in next pass
                        next_emitted.append(name)

                if not next_emitted:
                    # all entries have unmet deps, one of two things is wrong...
                    logger.error('cyclic or missing dependancy detected: %r', next_pending)
                    names = [n for n, d in source]
                    deps = [d for n, d in source]
                    missing = [m for d in deps for m in d if m not in names]
                    logger.error(names)
                    logger.error(deps)
                    logger.error(missing)

                    raise ValueError
                pending = next_pending
                emitted = next_emitted
        except ValueError as err:
            logger.error(err)


def get_trans_object(packages=None, tnum=None, repo_queue=None):
    """
    Gets an existing transaction or creates a new one.

    Args:
        packages (list): Create a new transaction with these packages.
        tnum (int): Get an existing transaction identified by `tnum`.

    Returns:
        Transaction: A fully initiallized `Transaction` object.

    Raises:
        ValueError: If both `packages` and `tnum` are Falsey or Truthy.

    """
    if not any([packages, tnum]):
        raise ValueError('At least one of [packages, tnum] required.')
    elif all([packages, tnum]):
        raise ValueError('Only one of [packages, tnum] can be given, not both.')

    trans_obj = Transaction(packages=packages, tnum=tnum, repo_queue=repo_queue)

    return trans_obj
