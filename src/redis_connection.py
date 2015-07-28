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
# import redis
import walrus as DB
import logging

logger = logging.getLogger(__name__)

# db = redis.StrictRedis(unix_socket_path='/var/run/redis/redis.sock')
db = DB.Database(unix_socket_path='/var/run/redis/redis.sock', db=2)


class BuildServerStatus(DB.Model):
    database = db
    namespace = 'antbs:status'
    index_separator = ':'
    status = DB.BooleanField(primary_key=True, default=False)

    idle = DB.BooleanField(default=True)
    current_status = DB.TextField()

    all_packages = DB.ZSetField()

    now_building = DB.TextField()
    container = DB.TextField()
    building_num = DB.IntegerField()
    building_start = DB.DateTimeField()

    completed = DB.ListField()
    failed = DB.ListField()
    queue = DB.ListField()
    pending_review = DB.ListField()



# Check if this is our first run, create initial tables if needed
try:
    status = BuildServerStatus.get(BuildServerStatus.status is True)
    logger.debug('Db exists, no initial setup required.')
except ValueError:
    logger.debug('First run detected. Created initial db entries.')

    status = BuildServerStatus.create(status=True)

    logger.debug('Initial db creation complete.')
