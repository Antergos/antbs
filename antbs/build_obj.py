#!/usr/bin/python
# -*- coding: utf-8 -*-
#
# build_obj.py
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

from utils.redis_connection import db, RedisObject, RedisList, RedisZSet
from utils.logging_config import logger


class BuildObject(RedisObject):
    """ This class represents a "build" object throughout the build server app. It is used to
    get and set build data to the database. """

    def __init__(self, pkg_obj=None, bnum=None):
        if not pkg_obj and not bnum:
            raise AttributeError

        super(BuildObject, self).__init__()

        self.key_lists = dict(
            redis_string=['pkgname', 'pkgver', 'epoch', 'pkgrel', 'path', 'build_path', 'start_str', 'end_str',
                          'version_str', 'container', 'review_status', 'review_dev', 'review_date', 'log_str'],
            redis_string_bool=['failed', 'completed'],
            redis_string_int=['pkgid', 'bnum'],
            redis_list=['log'],
            redis_zset=[])

        self.all_keys = [item for sublist in self.key_lists.values() for item in sublist]

        if not self:
            next_bnum = db.incr('antbs:misc:bnum:next')
            self.namespace = 'antbs:build:%s:' % next_bnum
            self.bnum = next_bnum
            for key in self.all_keys:
                if key in self.key_lists['redis_string']:
                    value = getattr(pkg_obj, key, '')
                    setattr(self, key, value)
                elif key in self.key_lists['redis_bool']:
                    value = getattr(pkg_obj, key, False)
                    setattr(self, key, value)
                elif key in self.key_lists['redis_int']:
                    value = getattr(pkg_obj, key, 0)
                    setattr(self, key, value)
                elif key in self.key_lists['redis_list']:
                    setattr(self, key, RedisList.as_child(self, key, str))
                elif key in self.key_lists['redis_zset']:
                    setattr(self, key, RedisZSet.as_child(self, key, str))
        else:
            self.namespace = 'antbs:build:%s:' % bnum

    @staticmethod
    def datetime_to_string(dt):
        return dt.strftime("%m/%d/%Y %I:%M%p")


def get_build_object(bnum=None, pkg_obj=None):
    if not pkg_obj and not bnum:
        logger.debug('build number is required to get build object.')
        raise AttributeError
    bld_obj = BuildObject(bnum=bnum, pkg_obj=pkg_obj)
    return bld_obj
