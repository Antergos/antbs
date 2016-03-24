#!/usr/bin/python
# -*- coding: utf-8 -*-
#
# server_status.py
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

from database.base_objects import RedisHash, RedisList, RedisZSet
from utils.utilities import DateTimeStrings, Singleton


class ServerStatus(RedisHash, metaclass=Singleton):

    def __init__(self, prefix='status', key='', *args, **kwargs):
        super().__init__(prefix=prefix, key=key, *args, **kwargs)

        self.key_lists.update(
                dict(string=['current_status', 'container', 'github_token',
                             'gitlab_token', 'building_start', 'building_num', 'docker_user',
                             'docker_password', 'gpg_key', 'gpg_password', 'wp_password',
                             'bugsnag_key', 'sp_session_key', 'sp_api_id', 'sp_api_key',
                             'sp_app', 'gh_repo_url', 'request_from'],
                     bool=['status', 'idle', 'iso_flag', 'iso_building', 'iso_minimal',
                           'docker_image_building', 'repo_locked_antergos', 'repo_locked_staging'],
                     int=['building_num'],
                     list=['completed', 'failed', 'transaction_queue', 'pending_review',
                           'all_tl_events', 'build_queue', 'transactions_running', 'now_building'],
                     set=['all_packages', 'iso_pkgs', 'repos', 'review_pending'],
                     path=['APP_DIR', 'STAGING_REPO', 'MAIN_REPO', 'STAGING_64', 'STAGING_32',
                           'MAIN_64', 'MAIN_32', 'PKGBUILDS_DIR', 'BUILD_BASE_DIR']))

        super().__namespaceinit__()

        if not self or not self.status:
            self.__keysinit__()
            self.status = True
            self.current_status = 'Idle'
            self.idle = True
            self.iso_flag = False
            self.iso_building = False

    def get_repo_lock(self, repo):
        lock_key = 'antbs:misc:repo_locks:{0}'.format(repo)
        if self.db.setnx(lock_key, True):
            self.db.expire(lock_key, 300)
            return True
        return False

    def release_repo_lock(self, repo):
        lock_key = 'antbs:misc:repo_locks:{0}'.format(repo)
        self.db.delete(lock_key)

    def now_building_add(self, bnum):
        if bnum not in self.now_building:
            self.now_building.append(bnum)

    def now_building_remove(self, bnum):
        if bnum in self.now_building:
            self.now_building.remove(bnum)


class TimelineEvent(RedisHash, DateTimeStrings):

    def __init__(self, msg=None, tl_type=None, event_id=None, packages=None, prefix='timeline'):
        if not event_id and not all([msg, tl_type]):
            raise ValueError('msg and tl_type required when event_id is not provided.')

        the_id = event_id
        if not event_id:
            the_id = self.db.incr('antbs:misc:event_id:next')

        super().__init__(prefix=prefix, key=the_id)

        super().__namespaceinit__()

        self.key_lists.update(
                dict(string=['event_type', 'date_str', 'time_str', 'message'],
                     int=['event_id', 'tl_type'],
                     bool=[],
                     list=['packages'],
                     set=[]))

        self.all_keys = [item for sublist in self.key_lists.values() for item in sublist]

        if not self or not event_id:
            super().__keysinit__()
            self.event_id = the_id
            all_events = status.all_tl_events
            all_events.append(self.event_id)
            self.tl_type = tl_type
            self.message = msg
            dt = datetime.datetime.now()
            self.date_str = self.dt_date_to_string(dt)
            self.time_str = self.dt_time_to_string(dt)
            if packages:
                packages = [p for p in packages if p]
                for p in packages:
                    self.packages.append(p)


def get_timeline_object(event_id=None, msg=None, tl_type=None, packages=None, ret=True):
    tl_obj = TimelineEvent(event_id=event_id, msg=msg, tl_type=tl_type, packages=packages)
    if ret:
        return tl_obj


status = ServerStatus()
