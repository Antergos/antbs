#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# package.py
#
# Copyright 2014 Antergos
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#  GNU General Public License for more details.
#
#  You should have received a copy of the GNU General Public License
#  along with this program; if not, write to the Free Software
#  Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston,
#  MA 02110-1301, USA.

""" Package Class """

import src.logging_config
import src.redis_connection
import subprocess
import os

logger = src.logging_config.logger


class Package(object):
    db = src.redis_connection.db

    def __init__(self, name, db=db):
        self.name = name
        self.key = 'pkg:%s' % self.name
        logger.info('@@-package.py-@@ | self.key is %s' % self.key)
        if not db.exists(self.key):
            db.set('%s:%s' % (self.key, 'name'), self.name)
        self.version = db.get('%s:%s' % (self.key, 'version'))
        self.epoch = db.get('%s:%s' % (self.key, 'epoch'))
        self.depends = db.lrange('%s:%s' % (self.key, 'depends'), 0, -1)
        self.builds = db.lrange('%s:%s' % (self.key, 'builds'), 0, -1)

    def delete(self):
        self.db.delete(self.key)

    def get_from_pkgbuild(self, var=None, path=None):
        if not var or not path:
            raise KeyError
        dirpath = os.path.dirname(path)
        cmd = 'source ' + path + '; echo $' + var

        proc = subprocess.Popen(cmd, executable='/bin/bash', shell=True, cwd=dirpath, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

        out, err = proc.communicate()
        if len(out) > 0:
            out = out.strip()
            logger.info('@@-package.py-@@ | proc.out is %s' % out)
        if len(err) > 0:
            logger.error('@@-package.py-@@ | proc.err is %s' % err)

        return out

    def get_from_db(self, attr):
        if attr:
            val = db.get('%s:%s' % (self.key, attr))
            logger.info('@@-package.py-@@ | get_from_db val is %s' % val)
            return val

    def save_to_db(self, attr=None, value=None):
        if attr and value:
            db.set('%s:%s' % (self.key, attr), value)