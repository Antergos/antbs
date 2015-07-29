#!/usr/bin/python
# -*- coding: utf-8 -*-
#
# redis_connection.py
#
# Copyright 2013-2015 Antergos
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#  GNU General Public License for more details.
#
#  You should have received a copy of the GNU General Public License
#  along with this program; if not, write to the Free Software
#  Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston,
#  MA 02110-1301, USA.

""" Database module """

import redis

db = redis.StrictRedis(unix_socket_path='/var/run/redis/redis.sock')


class RedisObject(object):
    all_keys = dict(
        redis_string=[],
        redis_string_bool=[],
        redis_string_int=[],
        redis_list=[],
        redis_zset=[])

    database = db
    namespace = 'antbs:'

    def __getattribute__(self, attrib):
        if attrib in ['all_keys', 'namespace', 'database'] or '__' in attrib:
            return super(RedisObject, self).__getattribute__(attrib)

        akeys = super(RedisObject, self).__getattribute__('all_keys').values()
        akeys = [item for sublist in akeys for item in sublist]
        if attrib not in akeys:
            return super(RedisObject, self).__getattribute__(attrib)

        key_name = self.namespace + attrib

        all_keys = super(RedisObject, self).__getattribute__('all_keys')

        if attrib in all_keys['redis_string']:
            return db.get(key_name) if db.exists(key_name) else ''

        elif attrib in all_keys['redis_string_bool']:
            return bool(db.get(key_name)) if db.exists(key_name) else ''

        elif attrib in all_keys['redis_string_int']:
            return int(db.get(key_name)) if db.exists(key_name) else ''

        elif attrib in all_keys['redis_list']:
            return db.lrange(key_name, 0, -1) if db.exists(key_name) else []

        elif attrib in all_keys['redis_zset']:
            return db.zrange(key_name, 0, -1) if db.exists(key_name) else []

    def __setattr__(self, attrib, value, score=None):
        if attrib in ['all_keys', 'namespace', 'database'] or '__' in attrib:
            super(RedisObject, self).__setattr__(attrib, value)
            return
        akeys = super(RedisObject, self).__getattribute__('all_keys').values()
        akeys = [item for sublist in akeys for item in sublist]
        if attrib not in akeys and '__' not in attrib:
            super(RedisObject, self).__setattr__(attrib, value)
            return

        key_name = self.namespace + attrib

        all_keys = super(RedisObject, self).__getattribute__('all_keys')

        if attrib in all_keys['redis_string']:
            db.set(key_name, value)

        elif attrib in all_keys['redis_string_bool']:
            db.set(key_name, str(value))

        elif attrib in all_keys['redis_string_int']:
            db.set(key_name, int(value))

        elif attrib in all_keys['redis_list']:
            if not isinstance(value, list):
                raise ValueError(type(value))
            db.rpush(key_name, value)

        elif attrib in all_keys['redis_zset']:
            if not isinstance(value, str):
                raise ValueError
            db.zadd(key_name, 1, value)