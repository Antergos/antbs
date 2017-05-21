#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
#  utility_classes.py
#
#  Copyright © 2016-2017 Antergos
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

""" Various utility classes, metaclasses, and mixins """

import glob
import logging
import os

import gevent
from redis.exceptions import LockError

from . import remove


class Singleton(type):
    _instance = None

    def __call__(cls, *args, **kwargs):
        if not cls._instance:
            cls._instance = super().__call__(*args, **kwargs)

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

    doing_cache_cleanup = False

    def __init__(self, cache_dir='/var/tmp/pkg_cache/pkg'):
        self.cache = cache_dir
        self.cache_i686 = cache_dir.replace('cache', 'cache_i686')
        self.all_caches = [self.cache, self.cache_i686]

    def maybe_do_cache_cleanup(self):
        if self.doing_cache_cleanup:
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

    def __exit__(self, _type, value, tb):
        if self.locked:
            self.lock.release()


