#!/usr/bin/env python
#  -*- coding: utf-8 -*-
#
#  gunicorn_conf.py
#
#  Copyright © 2016 Antergos
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

import logging
import redis

_db = redis.StrictRedis(unix_socket_path='/var/run/redis/redis.sock', decode_responses=True)
_logger = logging.getLogger('antbs')

_namespace = 'antbs'
_status_key = '{}:status'.format(_namespace)
_monitor_key = '{}:monitor'.format(_namespace)
_monitor_key = '{}:github'.format(_monitor_key)


# Gunicorn Settings
pid = '/run/gunicorn/pid'
workers = 8
worker_class = 'gevent'
errorlog = '-'


def on_starting(server):
    _db.hset(_monitor_key, 'check_is_running', False)
    _db.hset(_status_key, 'repos_syncing', False)

