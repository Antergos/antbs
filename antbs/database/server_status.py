#!/usr/bin/python
# -*- coding: utf-8 -*-
#
# server_status.py
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
# The following additional terms are in effect as per Section 7 of the license:
#
# The preservation of all legal notices and author attributions in
# the material or in the Appropriate Legal Notices displayed
# by works containing it is required.
#
# You should have received a copy of the GNU General Public License
# along with AntBS; If not, see <http://www.gnu.org/licenses/>.


""" Server Status Module (handles this application's state) """

import datetime
import contextlib

from . import RedisHash, Singleton, RedisSingleton
from logging_config import get_logger_object
from utils import DateTimeStrings


class ServerStatus(RedisHash, metaclass=RedisSingleton):

    attrib_lists = dict(
        string=['current_status', 'container', 'github_token',
                'gitlab_token', 'building_start', 'building_num', 'docker_user',
                'docker_password', 'gpg_key', 'gpg_password', 'wp_password',
                'bugsnag_key', 'sp_session_key', 'sp_api_id', 'sp_api_key',
                'sp_app', 'gh_repo_url', 'request_from', 'ANTERGOS_API_DB_KEY_NAME',
                'MONITOR_PKGS_KEY', 'smtp_pass', 'email', 'repo_lock_id', 'repo_lock_key',
                'generating_lock_id', 'ethemes_url', 'et_count_key', 'iso_release_check_key',
                'auth0_id', 'auth0_secret', 'auth0_domain'],

        bool=['status', 'idle', 'iso_flag', 'iso_building', 'iso_minimal',
              'docker_image_building', 'repo_locked_antergos', 'repo_locked_staging',
              'debug_toolbar_enabled', 'repos_synced_recently', 'repos_syncing'],

        int=['building_num'],

        list=['completed', 'failed', 'transaction_queue', 'pending_review',
              'all_tl_events', 'build_queue', 'transactions_running', 'now_building'],

        set=['all_packages', 'iso_pkgs', 'repos', 'review_pending', 'package_groups',
             'mirrors'],

        path=['APP_DIR', 'STAGING_REPO', 'MAIN_REPO', 'STAGING_64', 'STAGING_32',
              'MAIN_64', 'MAIN_32', 'PKGBUILDS_DIR', 'BUILD_BASE_DIR', 'ISO_DIR',
              'REPO_BASE_DIR', 'MKARCHISO_DIR', 'GNUPG_DIR', 'PKG_CACHE_DIR', 'PKG_CACHE_DIR32',
              'OLD_ISO_IMAGES_DIR', 'CNCHI_TRANSLATIONS_DIR', 'ISO_TRANSLATIONS_DIR',
              'ISO_TRANSLATIONS_DESTDIR', 'ANTERGOS_ISO_DIR']
    )
    can_expire = ['repos_synced_recently']
    logger = None

    def __init__(self, prefix='status', key='', *args, **kwargs):
        super().__init__(prefix=prefix, key=key, *args, **kwargs)

        super().__namespaceinit__()

        if not self or not self.status:
            self.status = True
            self.current_status = 'Idle'
            self.idle = True
            self.iso_flag = False
            self.iso_building = False

        if self.logger is None:
            self.logger = get_logger_object(self)

    def cleanup_all_packages_list(self, get_pkg_object):
        to_remove = []

        for pkg in self.all_packages:
            try:
                pkg_obj = get_pkg_object(name=pkg)
            except Exception:
                to_remove.append(pkg)

        if to_remove:
            for pkg in to_remove:
                self.all_packages.remove(pkg)

    @contextlib.contextmanager
    def repos_syncing_lock(self):
        self.repos_syncing = True
        yield
        self.repos_syncing = False


class TimelineEvent(RedisHash, DateTimeStrings):

    attrib_lists = dict(
        string=['event_type', 'date_str', 'time_str', 'message', 'tnum'],
        int=['event_id', 'tl_type'],
        bool=[],
        list=['packages'],
        set=[],
        path=[]
    )

    def __init__(self, msg=None, tl_type=None, event_id=None, packages=None, tnum='', prefix='timeline'):
        if not event_id and any(True for i in [msg, tl_type] if not i and 0 != i):
            raise ValueError('msg and tl_type required when event_id is not provided.')

        the_id = event_id
        if not event_id:
            the_id = self.db.incr('antbs:misc:event_id:next')

        super().__init__(prefix=prefix, key=the_id)
        self.__namespaceinit__()

        if not self or not self.event_id:
            self.event_id = the_id
            self.tnum = tnum
            status.all_tl_events.append(self.event_id)
            self.tl_type = tl_type
            self.message = msg
            dt = datetime.datetime.now()
            self.date_str = self.dt_date_to_string(dt)
            self.time_str = self.dt_time_to_string(dt)
            if packages:
                packages = [p for p in packages if p]
                for p in packages:
                    self.packages.append(p)

        if '/pkg/' in self.message:
            self.message = self.message.replace('/pkg/', '/package/')


def get_timeline_object(event_id=None, msg=None, tl_type=None, packages=None, ret=True, tnum=''):
    tl_obj = TimelineEvent(event_id=event_id, msg=msg, tl_type=tl_type, packages=packages, tnum=tnum)
    if ret:
        return tl_obj


status = ServerStatus()
