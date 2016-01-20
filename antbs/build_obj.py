#!/usr/bin/python
# -*- coding: utf-8 -*-
#
# build_obj.py
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


""" Build Class - Represents a single build """

import datetime

from utils.redis_connection import RedisObject
from utils.logging_config import logger


class BuildObject(RedisObject):
    """
    This class represents a "build" throughout the build server app. It is used
    to get and set build data to the database.

    Args:
        pkg_obj (Package): Create a new build for this package.
        bnum (int): Get an existing build identified by its `bnum`.

    Attributes:
        (str)
            pkgname, pkgver, epoch, pkgrel: self explanatory (see `man PKGBUILD`)
            version_str: The package's version including pkgrel for displaying on the frontend.
            path: Absolute path to the package's directory (subdir of antergos-packages directory)
            build_path: Absolute path to the the package's build directory.
            start_str: The build's start timestamp.
            end_str: The build's end timestamp.
            container: The build's Docker container ID.
            review_status: The build's developer review status.
            review_dev: The developer who reviewed the build result.
            review_date: The review's timestamp.
            log_str: The build log, fully processed into HTML for display on the front-end.


        (bool)
            failed: The build failed (Only one of `failed` and `completed` can be `True`)
            completed: The build completed (Only one of `failed` and `completed` can be `True`)

        (int)
            bnum: ID assigned to the build.
            pkg_id: ID of the package that this build is for.

        (list)
            log: The build log, unprocessed, stored as lines in a list.

    Raises:
        ValueError: If both `pkg_obj` and `bnum` are Falsey.

    """
    def __init__(self, pkg_obj=None, bnum=None, prefix='build'):
        if not any([pkg_obj, bnum]):
            raise ValueError

        the_bnum = bnum
        if not bnum:
            the_bnum = self.db.incr('antbs:misc:bnum:next')

        super().__init__(prefix=prefix, key=the_bnum)

        self.key_lists.update(
                dict(string=['pkgname', 'pkgver', 'epoch', 'pkgrel', 'path', 'build_path',
                             'start_str', 'end_str', 'version_str', 'container',
                             'review_status', 'review_dev', 'review_date', 'log_str'],
                     bool=['failed', 'completed'],
                     int=['pkg_id', 'bnum'],
                     ist=['log'],
                     zset=[]))

        self.all_keys = [item for sublist in self.key_lists.values() for item in sublist]

        if not self or not bnum:
            self._keysinit_()
            self.bnum = the_bnum
            self.failed = False
            self.completed = False

            for key in pkg_obj.all_keys:
                if key in self.all_keys:
                    setattr(self, key, getattr(pkg_obj, key))

    @staticmethod
    def datetime_to_string(dt):
        """
        Converts a datetime to a string.

        Args:
            dt (datetime.datetime): `datetime` to be converted.

        Returns:
            str: The datetime string.

        """
        return dt.strftime("%m/%d/%Y %I:%M%p")


def get_build_object(pkg_obj=None, bnum=None):
    """
    Gets an existing build or creates a new one.

    Args:
        pkg_obj (Package): Create a new build for this package.
        bnum (int): Get an existing build identified by `bnum`.

    Returns:
        BuildObject: A fully initiallized `BuildObject`.

    Raises:
        ValueError: If both `pkg_obj` and `bnum` are Falsey.

    """
    if not pkg_obj and not bnum:
        logger.debug('bnum or pkg_obj is required to get build object.')
        raise ValueError
    bld_obj = BuildObject(pkg_obj=pkg_obj, bnum=bnum)
    return bld_obj
