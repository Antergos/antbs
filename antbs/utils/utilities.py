#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
#  utilities.py
#
#  Copyright © 2016 Antergos
#
#  This file is part of Antergos Build Server, (AntBS).
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

""" Various utility classes, metaclasses, and functions. """

import glob
import logging
import os
import shutil
import subprocess

import gevent
from redis.exceptions import LockError


class Singleton(type):
    _instance = None

    def __call__(cls, *args, **kwargs):
        if not cls._instance:
            cls._instance = super(Singleton, cls).__call__(*args, **kwargs)
        return cls._instance


class DateTimeStrings:

    @staticmethod
    def dt_date_to_string(dt):
        return dt.strftime("%m/%d/%Y")

    @staticmethod
    def dt_time_to_string(dt):
        return dt.strftime("%I:%M%p")

    @staticmethod
    def dt_to_string(dt):
        return dt.strftime("%m/%d/%Y %I:%M%p")


class PacmanPackageCache(metaclass=Singleton):

    def __init__(self, cache_dir='/var/tmp/pkg_cache/pkg'):
        self.cache = cache_dir
        self.cache_i686 = cache_dir.replace('cache', 'cache_i686')
        self.all_caches = [self.cache, self.cache_i686]
        self.doing_cache_cleanup = False

    def maybe_do_cache_cleanup(self):
        if self.doing_cache_cleanup:
            waiting = 0
            while self.doing_cache_cleanup:
                if waiting > 300:
                    break
                gevent.sleep(5)
                waiting += 5
            return

        self.doing_cache_cleanup = True

        for cache_dir in self.all_caches:
            if not os.path.exists(cache_dir):
                os.mkdir(cache_dir, mode=0o777)
            elif os.path.exists(cache_dir):
                already_checked = []
                for path, dir_name, pkg_files in os.walk(cache_dir):
                    for pkg_file in pkg_files:
                        try:
                            pkg, version, rel, suffix = pkg_file.rsplit('-', 3)
                        except ValueError:
                            logging.error('value error for %s', pkg_file)
                            continue
                        # Use globbing to check for multiple versions of the package.
                        all_versions = glob.glob('{0}/{1}***.xz'.format(cache_dir, pkg))
                        if pkg in already_checked:
                            # We've already handled all versions of this package.
                            continue
                        elif len(all_versions) < 2:
                            # There is only one version of the package in this cache dir, keep it.
                            already_checked.append(pkg)
                            continue
                        elif pkg not in already_checked and len(all_versions) > 1:
                            # There are multiple versions of the package. Determine the latest.
                            newest = max(glob.iglob('{0}/{1}**.xz'.format(cache_dir, pkg)),
                                         key=os.path.getctime)
                            logging.debug(newest)
                            logging.debug(all_versions)
                            for package_file in all_versions:
                                if package_file != newest:
                                    # This file is not the newest. Remove it.
                                    remove(package_file)

        self.doing_cache_cleanup = False


class CustomSet(set):

    def add(self, item):
        added = item not in self
        super().add(item)
        return added


class RQWorkerCustomExceptionHandler:
    status = None
    logger = None

    def __init__(self, status, logger):
        if self.status is None:
            self.status = status
        if self.logger is None:
            self.logger = logger

    def handle_worker_exception(self, job, exc_type, exc_value, traceback):
        tnum = job.meta.get('tnum', 0)
        packages = job.meta.get('packages', [])
        bnum = job.meta.get('building_num', 0)

        running = self.status.transactions_running and tnum in self.status.transactions_running
        building = self.status.now_building and bnum in self.status.now_building

        self.logger.exception('%s | %s | %s | %s', job, exc_type, exc_value, traceback)

        if running:
            self.status.transactions_running.remove(tnum)

        if building:
            self.status.now_building.remove(bnum)

        if not self.status.transactions_running and not self.status.now_building:
            self.status.idle = True
            self.status.current_status = ''


class MyLock:
    def __init__(self, redis_client, key):
        self.lock = redis_client.lock(key, blocking_timeout=300, thread_local=False)
        self.locked = False

    def __enter__(self):
        if self.lock.acquire(blocking=True):
            self.locked = True
            return self
        else:
            raise LockError('Cannot release an unlocked lock')

    def __exit__(self, type, value, tb):
        if self.locked:
            self.lock.release()


def truncate_middle(s, n):
    if len(s) <= n:
        # string is already short-enough
        return s
    # half of the size, minus the 3 .'s
    n_2 = int(n) / 3 - 3
    # whatever's left
    n_1 = n - n_2 - 3
    return '{0}...{1}'.format(s[:n_1], s[-n_2:])


def remove(src):
    if not isinstance(src, str):
        raise ValueError('src must be of type(str), type({0}) given.'.format(type(src)))

    if os.path.isdir(src):
        try:
            shutil.rmtree(src)
        except Exception as err:
            logging.error(err)

    elif os.path.isfile(src):
        try:
            os.remove(src)
        except Exception as err:
            logging.error(err)


def copy_or_symlink(src, dst):
    """
    Copies the file at `src` to `dst`. If `src` is a symlink the link will be
    followed to get the file that will be copied. If `dst` is a symlink then it will
    be removed.

    Args:
        src (str): The path to the file that will be copied.
        dst (str): The path to where the src file should be copied to.

    """

    if os.path.islink(src):
        linkto = os.readlink(src)
        os.symlink(linkto, dst)
    else:
        try:
            shutil.copyfile(src, dst)
        except shutil.SameFileError:
            if os.path.islink(dst):
                os.unlink(dst)
                shutil.copyfile(src, dst)
        except Exception as err:
            logging.error(err)


def symlink(src, dst):
    """
    Creates a symbolic link at `dst` to the file at `src`. If `src` is a symlink the
    link will be followed to get the actual file that will be linked at `dst`. If `dst`
    is a symlink then it will be removed.

    Args:
        src (str): The path to the file that will be linked.
        dst (str): The path at which the link to the file at `src` should be created.

    """

    if os.path.islink(src):
        src = os.readlink(src)

    if os.path.islink(dst):
        os.unlink(dst)

    os.symlink(src, dst)


def quiet_down_noisy_loggers():
    noisy_loggers = ["github3",
                     "requests",
                     "stormpath.http"]

    for logger_name in noisy_loggers:
        noisy_logger = logging.getLogger(logger_name)
        noisy_logger.setLevel(logging.ERROR)


def try_run_command(cmd, cwd):
    """
    Tries to run command and then returns the result (success/fail)
    and any output that is captured.

    Args:
        cmd (list): Command to run as a list. See `subprocess` docs for details.
        cwd (str): Set the current working directory to use when running command.

    Returns:
        success (bool): Whether or not the command returned successfully (exit 0)
        res (str): The output that was captured.

    """

    res = None
    success = False

    try:
        res = subprocess.check_output(
            cmd, stderr=subprocess.STDOUT, universal_newlines=True, cwd=cwd
        )
        success = True
    except subprocess.CalledProcessError as err:
        logging.exception((err.output, err.stderr))
        res = err.output

    return success, res


def get_build_queue(status_obj, get_transaction):
    if not status_obj.transactions_running and not status_obj.transaction_queue:
        return []

    queued = []
    running = [t for t in status_obj.transactions_running if t]
    waiting = [t for t in status_obj.transaction_queue if t]
    all_transactions = running + waiting

    for tnum in all_transactions:
        trans_obj = get_transaction(tnum=tnum)

        if trans_obj.queue:
            queued.extend(trans_obj.queue)

    return queued


def all_file_paths_exist(paths):
    return not any(True for p in paths if not os.path.exists(p))
