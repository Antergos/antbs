#!/usr/bin/python
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
from github3 import login

logger = src.logging_config.logger


class Package(object):

    db = src.redis_connection.db
    gh_user = db.get('ANTBS_GITHUB_TOKEN')

    def __init__(self, name, db=db):
        self.name = name
        self.key = 'pkg:%s' % self.name
        logger.info('@@-package.py-@@ | self.key is %s' % self.key)
        if not db.exists(self.key):
            db.set(self.key, True)
            db.set('%s:%s' % (self.key, 'name'), self.name)
            db.set('%s:%s' % (self.key, 'push_version'), "False")
        self.version = self.get_from_db('version')
        self.epoch = self.get_from_db('epoch')
        self.depends = self.get_from_db('depends')
        self.builds = self.get_from_db('builds')
        self.push_version = self.get_from_db('push_version')
        self.pkgrel = self.get_from_db('pkgrel')
        self.pkgver = self.get_from_db('pkgver')
        self.saved_commit = self.get_from_db('saved_commit')
        self.tl_event = self.get_from_db('tl_event')

    def delete(self):
        self.db.delete(self.key)

    def get_from_db(self, attr):
        if attr:
            if self.db.type('string'):
                val = self.db.get('%s:%s' % (self.key, attr))
            elif self.db.type('list'):
                val = self.db.lrange('%s:%s' % (self.key, attr), 0, -1)
            else:
                val = ''
            logger.info('@@-package.py-@@ | get_from_db %s is %s' % (attr, val))
            return val

    def save_to_db(self, attr=None, value=None):
        if attr and value:
            if self.push_version and self.push_version == "True" and attr == "pkgver":
                old = self.get_from_db(attr)
                if old != value:
                    self.update_and_push_github(attr, old, value)

            self.db.set('%s:%s' % (self.key, attr), value)
            return self.db.get('%s:%s' % (self.key, attr))

    def get_from_pkgbuild(self, var=None, path=None):
        if not var or not path:
            raise KeyError
        parse = open(path).read()
        dirpath = os.path.dirname(path)
        if var == "pkgver" and 'pkgname=cnchi-dev' in parse:
            if "info" in sys.modules:
                del(sys.modules["info"])
            if "/tmp/cnchi/src" not in sys.path:
                sys.path.append('/tmp/cnchi/src')
            import info

            out = info.CNCHI_VERSION
            out = out.replace('"', '')
            del(info.CNCHI_VERSION)
            del(sys.modules["info"])
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
                if 'numix-icon-theme' not in parse:
                    giturl = re.search('(?<=git\\+).+(?="|\')', parse)
                    giturl = giturl.group(0)
                    pkgdir, pkgbuild = os.path.split(path)
                    if self.name == 'pamac-dev':
                        gitnm = 'pamac'
                    else:
                        gitnm = self.name
                    subprocess.check_call(['git', 'clone', giturl, gitnm], cwd=pkgdir)

                cmd = 'source ' + path + '; ' + var

            proc = subprocess.Popen(cmd, executable='/bin/bash', shell=True, cwd=dirpath, stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE)
            out, err = proc.communicate()

        if len(out) > 0:
            out = out.strip()
            logger.info('@@-package.py-@@ | proc.out is %s' % out)
        if len(err) > 0:
            logger.error('@@-package.py-@@ | proc.err is %s' % err)

        return out

    def update_and_push_github(self, var=None, old_val=None, new_val=None):
        if self.push_version != "True" or old_val == new_val:
            return
        gh = login(token=self.gh_user)
        repo = gh.repository('antergos', 'antergos-packages')
        tf = repo.file_contents(self.name + '/PKGBUILD')
        content = tf.decoded
        search_str = '%s=%s' % (var, old_val)
        replace_str = '%s=%s' % (var, new_val)
        content = content.replace(search_str, replace_str)
        ppath = os.path.join('/opt/antergos-packages/', self.name, '/PKGBUILD')
        with open(ppath, 'w') as pbuild:
            pbuild.write(content)
        pbuild.close()
        commit = tf.update('[ANTBS] | Updated %s to %s in PKGBUILD for %s' % (var, new_val, self.name), content)
        if commit and commit['commit'] is not None:
            try:
                logger.info('@@-package.py-@@ | commit hash is %s' % commit['commit'].sha)
            except AttributeError:
                pass
            return True
        else:
            logger.error('@@-package.py-@@ | commit failed')
            return False