#!/usr/bin/python
# -*- coding: utf-8 -*-
#
# server_status.py
#
# Copyright 2014-2015 Antergos
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston,
# MA 02110-1301, USA.

""" A singleton class for the server status """

from utils.redis_connection import RedisObject
from utils.logging_config import logger


class Singleton(RedisObject):
    def __init__(self, *args, **kwargs):
        super(Singleton, self).__init__()
        globals()[self.__class__.__name__] = self

    def __call__(self):
        return self


class ServerStatus(Singleton):

    namespace = 'antbs:status:'

    def __init__(self, *args, **kwargs):
        super(ServerStatus, self).__init__(self, *args, **kwargs)

        self.all_keys = dict(redis_string=['current_status', 'now_building', 'container', 'github_token',
                                           'gitlab_token', 'building_start'],
                             redis_string_bool=['status', 'idle'],
                             redis_string_int=['building_num'],
                             redis_list=['completed', 'failed', 'queue', 'pending_review'],
                             redis_zset=['all_packages', 'all_tl_events'])


# Check if this is our first run, create initial tables if needed
status = ServerStatus()
idle = status.idle
if idle is not None and idle != '':
    logger.debug('idle is: %s. Db exists, no initial setup required.' % idle)
else:
    status.current_status = 'Idle'
    status.idle = True
    status.now_building = 'Idle'
    logger.debug('Initial db creation complete.')
