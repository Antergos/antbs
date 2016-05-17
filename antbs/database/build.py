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

import gevent
from pygments import highlight
from pygments.formatters import HtmlFormatter
from pygments.lexers import BashLexer

from database.base_objects import RedisHash
from utils.logging_config import logger
from utils.docker_util import DockerUtils
from utils.utilities import CustomSet
from datetime import datetime


doc = DockerUtils().doc


class Build(RedisHash):
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
            tnum: ID of the transaction that this build is a part of.

        (list)
            log: The build log, unprocessed, stored as lines in a list.

    Raises:
        ValueError: If both `pkg_obj` and `bnum` are Falsey.

    """

    def __init__(self, pkg_obj=None, bnum=None, tnum=None, prefix='build'):
        if not pkg_obj and not bnum:
            raise ValueError

        the_bnum = bnum
        if not bnum:
            the_bnum = self.db.incr('antbs:misc:bnum:next')

        super().__init__(prefix=prefix, key=the_bnum)

        self.attrib_lists.update(
                dict(string=['pkgname', 'pkgver', 'epoch', 'pkgrel', 'path', 'build_path',
                             'start_str', 'end_str', 'version_str', 'container', 'review_status',
                             'review_dev', 'review_date', 'log_str', 'pkg_id', 'bnum', 'tnum',
                             'repo_container'],
                     bool=['failed', 'completed', 'is_iso'],
                     int=[],
                     list=['log'],
                     set=[],
                     path=[]))

        self.__namespaceinit__()

        if pkg_obj and (not self or not self.bnum):
            self.__keysinit__()

            for key in pkg_obj.all_keys:
                if key in self.all_attribs:
                    val = getattr(pkg_obj, key)
                    value = False if 'is_iso' == key and '' == val else val
                    setattr(self, key, value)

            self.bnum = the_bnum
            self.tnum = tnum
            self.failed = False
            self.completed = False

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

    def publish_build_output(self, upd_repo=False):
        if not self.container or (upd_repo and not self.repo_container):
            logger.error('Unable to publish build output. (Container is None)')
            return

        container = self.container if not upd_repo else self.repo_container

        output = doc.logs(container=container, stream=True)
        nodup = CustomSet()
        content = []
        live_output_key = 'live:build_output:{0}'.format(self.bnum)
        last_line_key = 'tmp:build_log_last_line:{0}'.format(self.bnum)
        for line in output:
            line = line.decode('UTF-8').rstrip()
            if not line or 'makepkg]# PS1="' in line:
                continue
            end = line[25:]
            if nodup.add(end):
                line = line.replace("'", '')
                line = line.replace('"', '')
                line = '[{0}]: {1}'.format(datetime.now().strftime("%m/%d/%Y %I:%M%p"), line)

                content.append(line)
                self.db.publish(live_output_key, line)
                self.db.setex(last_line_key, 3600, line)

        result_ready = self.completed != self.failed
        if not result_ready:
            while not result_ready:
                result_ready = self.completed != self.failed
                gevent.sleep(2)

        if upd_repo or self.failed:
            self.db.publish(live_output_key, 'ENDOFLOG')

        existing = True
        if len(self.log) < 1 and not self.failed and not self.is_iso:
            existing = False

        for line in content:
            self.log.rpush(line)

        if existing:
            log_content = '\n '.join(self.log)
            self.log_str = highlight(log_content,
                                     BashLexer(),
                                     HtmlFormatter(style='monokai',
                                                   linenos='inline',
                                                   prestyles="background:#272822;color:#fff;"))


def get_build_object(pkg_obj=None, bnum=None, tnum=None):
    """
    Gets an existing build or creates a new one.

    Args:
        pkg_obj (Package): Create a new build for this package.
        bnum (int): Get an existing build identified by `bnum`.

    Returns:
        Build: A fully initiallized `Build`.

    Raises:
        ValueError: If both `pkg_obj` and `bnum` are Falsey or Truthy.

    """
    if not any([pkg_obj, bnum]):
        raise ValueError('At least one of [pkg_obj, bnum] required.')
    elif all([pkg_obj, bnum]):
        raise ValueError('Only one of [pkg_obj, bnum] can be given, not both.')

    bld_obj = Build(pkg_obj=pkg_obj, bnum=bnum, tnum=tnum)

    return bld_obj
