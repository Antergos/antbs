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
# GNU General Public License for more details.
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
import re
import sys

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

    def get_from_db(self, attr):
        if attr:
            val = self.db.get('%s:%s' % (self.key, attr))
            logger.info('@@-package.py-@@ | get_from_db val is %s' % val)
            return val

    def save_to_db(self, attr=None, value=None):
        if attr and value:
            self.db.set('%s:%s' % (self.key, attr), value)
            return self.db.get('%s:%s' % (self.key, attr))

    def get_from_pkgbuild(self, var=None, path=None):
        if not var or not path:
            raise KeyError
        parse = open(path).read()
        dirpath = os.path.dirname(path)
        if var == "pkgver" and 'cnchi-dev' in parse:
            if 'info' not in sys.modules:
                if '/tmp/cnchi/src' not in sys.path:
                    sys.path.append('/tmp/cnchi/src')
                try:
                    import info
                except Exception as err:
                    logger.error(err)
            else:
                reload('info')

            out = info.CNCHI_VERSION
            err = []
        #elif var == "pkgver" and 'cnchi-dev' not in parse and ('git+' in parse or 'numix-icon-theme-square' in parse):
            # giturl = re.search('(?<=git\\+).+(?="|\')', parse)
            # if not giturl:
            #     giturl = os.path.join(dirpath, 'numix-icon-theme-square')
            # else:
            #     giturl = giturl.group(0)
            # gitcmd = 'git clone ' + giturl + ' pkgver'
            # subprocess.check_output(gitcmd, cwd=dirpath, shell=True, executable='/bin/bash')
            # rev = subprocess.check_output(['git', 'rev-list', '--count', 'HEAD'], cwd=os.path.join(dirpath, 'pkgver'))
            # short = subprocess.check_output(['git', 'rev-parse', '--short', 'HEAD'], cwd=os.path.join(dirpath, 'pkgver'))
            # out = '0.r%s.%s' % (rev.strip(), short.strip())
            # err = []
            # if not len(out) > 0:
            #     err = 'Failed to determine pkgver from git revisions'
        else:
            cmd = 'source ' + path + '; echo $' + var
            if var == "pkgver" and ('git+' in parse or 'numix-icon-theme' in parse):
                cmd = 'source ' + path + '; ' + var

            proc = subprocess.Popen(cmd, executable='/bin/bash', shell=True, cwd=dirpath, stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE)
            out, err = proc.communicate()

        if len(out) > 0:
            out = out.strip()
            out = self.save_to_db(var, out)
            logger.info('@@-package.py-@@ | proc.out is %s' % out)
        if len(err) > 0:
            logger.error('@@-package.py-@@ | proc.err is %s' % err)

        return out