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
from .redis_connection import RedisObject
from .logging_config import logger


class Singleton(RedisObject):

    _instance = None

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super().__new__(cls, *args, *kwargs)
        return cls._instance


class ServerStatus(Singleton):

    def __init__(self, prefix='status', key='', *args, **kwargs):
        super().__init__(prefix=prefix, key=key, *args, **kwargs)

        self.key_lists.update(
                dict(string=['current_status', 'now_building', 'container', 'github_token',
                             'gitlab_token', 'building_start', 'building_num', 'docker_user',
                             'docker_password', 'gpg_key', 'gpg_password', 'wp_password',
                             'bugsnag_key', 'sp_session_key', 'sp_api_id', 'sp_api_key',
                             'sp_app'],
                     bool=['status', 'idle', 'iso_flag', 'iso_building', 'iso_minimal'],
                     int=['building_num'],
                     list=['completed', 'failed', 'queue', 'pending_review', 'all_tl_events',
                           'hook_queue'],
                     set=['all_packages', 'iso_pkgs', 'repos']))

        self.all_keys = [item for sublist in self.key_lists.values() for item in sublist]

        super().__namespaceinit__()

        if not self or not self.status:
            self.__keysinit__()
            self.status = True
            self.current_status = 'Idle'
            self.idle = True
            self.now_building = 'Idle'
            self.iso_flag = False
            self.iso_building = False


class TimelineEvent(RedisObject):

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

    @staticmethod
    def dt_date_to_string(dt):
        return dt.strftime("%b %d")

    @staticmethod
    def dt_time_to_string(dt):
        return dt.strftime("%I:%M%p")


def get_timeline_object(event_id=None, msg=None, tl_type=None, packages=None):
    tl_obj = TimelineEvent(event_id=event_id, msg=msg, tl_type=tl_type, packages=packages)
    return tl_obj


status = ServerStatus()

