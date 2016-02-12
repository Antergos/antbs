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


import os, subprocess, tempfile
import shutil

from build_pkg import logger
from .base_objects import RedisHash
from .server_status import status
from .package import get_pkg_object
from utils.logging_config import logger
from utils.utilities import remove



class Transaction(RedisHash):
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

    def __init__(self, packages=None, tnum=None, base_path='/var/tmp/antbs', prefix='trans'):
        if not any([packages, tnum]):
            raise ValueError('At least one of [packages, tnum] required.')
        elif all([packages, tnum]):
            raise ValueError('Only one of [packages, tnum] can be given, not both.')

        the_tnum = tnum
        if not tnum:
            the_tnum = self.db.incr('antbs:misc:tnum:next')

        super().__init__(prefix=prefix, key=the_tnum)

        self.key_lists.update(dict(
            string=['building', 'start_str', 'end_str'],
            bool=['is_running', 'is_finished'],
            int=['tnum'],
            list=['queue'],
            zset=['packages', 'builds', 'completed', 'failed'],
            path=['base_path', 'path']
        ))

        if packages and not self:
            self.__keysinit__()
            self.tnum = the_tnum
            self.base_path = base_path

            for pkg in packages:
                self.packages.add(pkg)

            self._internal_deps = []

    def start(self):
        self.setup_transaction_directory()
        self.process_packages()


    def setup_transaction_directory(self):
        self.path = tempfile.mkdtemp(prefix=self.full_key, dir=self.base_path)

        try:
            subprocess.check_output(['git', 'clone', status.gh_repo_url], cwd=self.path)
        except subprocess.CalledProcessError as err:
            raise RuntimeError(err.output)

    def get_package_build_directory(self, pkg):
        paths = [os.path.join(self.path, pkg),
                 os.path.join(self.path, 'cinnamon', pkg)]
        pbpath = None
        for p in paths:
            if os.path.exists(p):
                pbpath = p
                break
            else:
                raise RuntimeError('Unable to determine pb_path for {0}'.format(pkg))

        return pbpath

    def handle_special_cases(self, pkg, pkg_obj):
        if 'cnchi' in pkg:
            logger.info('cnchi package detected.')
            status.current_status = 'Fetching latest translations for %s from Transifex.' % pkg
            logger.info(status.current_status)
            cnchi_dir = os.path.join(self.path, pkg)
            self.fetch_and_compile_translations(translations_for=["cnchi"], pkg_obj=pkg_obj)
            remove(os.path.join(cnchi_dir, 'cnchi/.git'))
            subprocess.check_output(['tar', '-cf', 'cnchi.tar', 'cnchi'], cwd=cnchi_dir)

        elif 'numix-icon-theme-square' == pkg:
            src = os.path.join('/var/tmp/antergos-packages/', pkg, pkg + '.zip')
            dest = os.path.join('/opt/antergos-packages/', pkg)
            shutil.move(src, dest)

    def process_packages(self):

        for pkg in self.packages:
            if not pkg:
                continue

            pbpath = self.get_package_build_directory(pkg)

            pkg_obj = get_pkg_object(name=pkg, pbpath=pbpath)
            version = pkg_obj.get_version()

            if not version:
                self.packages.remove(pkg)
                logger.debug('Skipping cnchi-dev build: {0}'.format(pkg))
                continue

            pkg_obj.version_str = version

            log_msg = 'Updating pkgver in database for {0} to {1}'.format(pkg, version)
            logger.info(log_msg)
            status.current_status = log_msg

            depends = pkg_obj.get_deps()
            intersect = list(set(depends) & set(self.packages))
            if depends and len(intersect) > 0:
                self._internal_deps.append((pkg, intersect))

            self.handle_special_cases(pkg, pkg_obj)

        pkg = None
        if self._internal_deps:
            for name in self.determine_build_order(self._internal_deps):
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
            name = pkg_obj.name

        trans = {
            "cnchi": {
                'trans_dir': "/opt/cnchi-translations/",
                'trans_files_dir': '/opt/cnchi-translations/translations/antergos.cnchi',
                'dest_dir': os.path.join(self.path, name, '/cnchi/po')
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
                    logger.error("cyclic or missing dependancy detected: %r", next_pending)
                    raise ValueError
                pending = next_pending
                emitted = next_emitted
        except ValueError as err:
            logger.error(err)


def get_trans_object(packages=None, tnum=None):
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

    trans_obj = Transaction(packages=packages, tnum=tnum)

    return trans_obj


