#!/usr/bin/python
# -*- coding: utf-8 -*-
#
# build_obj.py
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
            redis_string_int=['pkg_id', 'bnum'],
            redis_list=['log'],
            redis_zset=[])

        self.all_keys = [item for sublist in self.key_lists.values() for item in sublist]

        if not bnum:
            next_bnum = db.incr('antbs:misc:bnum:next')
            self.namespace = 'antbs:build:%s:' % next_bnum
            self.prefix = self.namespace[:-1]
            for key in self.all_keys:
                if key in self.key_lists['redis_string']:
                    value = getattr(pkg_obj, key, '')
                    setattr(self, key, value)
                elif key in self.key_lists['redis_string_bool']:
                    value = getattr(pkg_obj, key, False)
                    setattr(self, key, value)
                elif key in self.key_lists['redis_string_int']:
                    value = getattr(pkg_obj, key, 0)
                    setattr(self, key, value)
                elif key in self.key_lists['redis_list']:
                    setattr(self, key, RedisList.as_child(self, key, str))
                elif key in self.key_lists['redis_zset']:
                    setattr(self, key, RedisZSet.as_child(self, key, str))
            self.bnum = next_bnum
            self.failed = False
            self.completed = False
        else:
            self.namespace = 'antbs:build:%s:' % bnum
            self.prefix = self.namespace[:-1]

    @staticmethod
    def datetime_to_string(dt):
        """

        :param dt:
        :return:
        """
        return dt.strftime("%m/%d/%Y %I:%M%p")


def get_build_object(pkg_obj=None, bnum=None):
    """

    :param pkg_obj:
    :param bnum:
    :return: :raise AttributeError:
    """
    if not pkg_obj and not bnum:
        logger.debug('bnum or pkg_obj is required to get build object.')
        raise AttributeError
    bld_obj = BuildObject(pkg_obj=pkg_obj, bnum=bnum)
    return bld_obj
