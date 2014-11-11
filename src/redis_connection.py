#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# redis_connection.py
#
# Copyright 2013 Antergos
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
import logging

logger = logging.getLogger(__name__)

db = redis.StrictRedis(unix_socket_path='/var/run/redis.sock')


def init_db():
    logger.debug('First run detected. Created initial db entries.')

    status = {'idle': True}

    for key, value in status.items():
        db.set(key, value)
    db.set('ran_once', True)
    logger.debug('Initial db creation complete.')

# Check if this is our first run, create initial tables if needed
if db.exists('ran_once') == 0:
    init_db()
else:
    logger.debug('Db exists, no initial setup required.')




