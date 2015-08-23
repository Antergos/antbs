#!/usr/bin/python
# -*- coding: utf-8 -*-
#
# server_status.py
#
# Copyright 2014-2015 Antergos
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


""" A singleton class for the server status """

import datetime
from redis_connection import RedisObject, db, RedisList, RedisZSet
from logging_config import logger
import os


class Singleton(RedisObject):
    """

    :param args:
    :param kwargs:
    """

    def __init__(self, *args, **kwargs):
        super(Singleton, self).__init__()
        globals()[self.__class__.__name__] = self

    def __call__(self):
        return self


class ServerStatus(Singleton):

    """

    :param args:
    :param kwargs:
    """

    def __init__(self, *args, **kwargs):
        super(ServerStatus, self).__init__(self, *args, **kwargs)

        self.namespace = 'antbs:status:'
        self.prefix = self.namespace[:-1]

        self.key_lists = dict(redis_string=['current_status', 'now_building', 'container', 'github_token',
                                            'gitlab_token', 'building_start', 'building_num', 'docker_user',
                                            'docker_password', 'gpg_key', 'gpg_password'],
                              redis_string_bool=['status', 'idle', 'iso_flag', 'iso_building', 'iso_minimal'],
                              redis_string_int=['building_num'],
                              redis_list=['completed', 'failed', 'queue', 'pending_review', 'all_tl_events',
                                          'hook_queue'],
                              redis_zset=['all_packages'])

        self.all_keys = [item for sublist in self.key_lists.values() for item in sublist]

        if not self:
            for key in self.all_keys:
                if key in self.key_lists['redis_string']:
                    setattr(self, key, '')
                elif key in self.key_lists['redis_string_bool']:
                    setattr(self, key, False)
                elif key in self.key_lists['redis_string_int']:
                    setattr(self, key, 0)
                elif key in self.key_lists['redis_list']:
                    setattr(self, key, RedisList.as_child(self, key, str))
                elif key in self.key_lists['redis_zset']:
                    setattr(self, key, RedisZSet.as_child(self, key, str))
            self.status = True
            self.current_status = 'Idle'
            self.idle = True
            self.now_building = 'Idle'
            self.iso_flag = False
            self.iso_building = False


class Timeline(RedisObject):
    """

    :param msg:
    :param tl_type:
    :param event_id:
    :raise AttributeError:
    """

    def __init__(self, msg=None, tl_type=None, event_id=None):
        if (not msg or not tl_type) and not event_id:
            raise AttributeError

        super(Timeline, self).__init__()

        self.key_lists = dict(redis_string=['event_type', 'date_str', 'time_str', 'message'],
                              redis_string_int=['event_id', 'tl_type'],
                              redis_string_bool=[],
                              redis_list=[],
                              redis_zset=[])

        self.all_keys = [item for sublist in self.key_lists.values() for item in sublist]

        if not event_id:
            next_id = db.incr('antbs:misc:event_id:next')
            self.namespace = 'antbs:timeline:%s:' % next_id
            self.prefix = self.namespace[:-1]
            self.event_id = next_id
            all_events = status.all_tl_events
            all_events.append(self.event_id)
            self.tl_type = tl_type
            self.message = msg
            dt = datetime.datetime.now()
            self.date_str = self.dt_date_to_string(dt)
            self.date_str = self.dt_time_to_string(dt)
        else:
            self.namespace = 'antbs:timeline:%s:' % event_id
            self.prefix = self.namespace[:-1]

    @staticmethod
    def dt_date_to_string(dt):
        """

        :param dt:
        :return:
        """
        return dt.strftime("%b %d")

    @staticmethod
    def dt_time_to_string(dt):
        """

        :param dt:
        :return:
        """
        return dt.strftime("%I:%M%p")


status = ServerStatus()
