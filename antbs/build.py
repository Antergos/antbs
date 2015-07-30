#!/usr/bin/python
# -*- coding: utf-8 -*-
#
# build.py
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

""" Build Class - Represents a single build """

import datetime

from utils.redis_connection import db, RedisObject


class Build(RedisObject):
    """ This class represents a "build" throughout the build server app. It is used to
    get and set build data to the database. """

    def __init__(self, pkg_obj=None, bnum=None):
        if not pkg_obj:
            raise AttributeError

        super(Build, self).__init__()

        self.all_keys = dict(
            redis_string=['pkgname', 'pkgver', 'epoch', 'pkgrel', 'path', 'build_path', 'start_str', 'end_str',
                          'version_str', 'container', 'review_status', 'review_dev', 'review_date'],
            redis_string_bool=['failed', 'completed'],
            redis_string_int=['pkgid'],
            redis_list=['log'],
            redis_zset=[])

        if not bnum:
            next_bnum = db.incr('antbs:misc:bnum:next')
            self.namespace = 'antbs:build:%s:' % next_bnum
            self.bnum = next_bnum

        key_lists = ['redis_string', 'redis_string_bool', 'redis_string_int', 'redis_list', 'redis_zset']
        for key_list_name in key_lists:
            key_list = self.all_keys[key_list_name]
            for key in key_list:
                if key_list_name.endswith('string'):
                    value = getattr(pkg_obj, key, '')
                    setattr(self, key, value)
                elif key_list_name.endswith('bool'):
                    value = getattr(pkg_obj, key, False)
                    setattr(self, key, value)
                elif key_list_name.endswith('int'):
                    value = getattr(pkg_obj, key, 0)
                    setattr(self, key, value)
                elif key_list_name.endswith('list'):
                    value = getattr(pkg_obj, key, [])
                    setattr(self, key, value)
                elif key_list_name.endswith('zset'):
                    value = getattr(pkg_obj, key, [])
                    setattr(self, key, value)

    @staticmethod
    def datetime_to_string(dt):
        return dt.strftime("%m/%d/%Y %I:%M%p")

