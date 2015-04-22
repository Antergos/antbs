#!/usr/bin/python
# -*- coding: utf-8 -*-
#
# package.py
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
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
#  You should have received a copy of the GNU General Public License
#  along with this program; if not, write to the Free Software
#  Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston,
#  MA 02110-1301, USA.

""" Package Class """

import src.logging_config as logconf
import src.redis_connection
import subprocess
import os
import re
import sys
from github3 import login
from src.redis_connection import db

logger = logconf.logger


class Package(object):

    db = src.redis_connection.db
    gh_user = db.get('ANTBS_GITHUB_TOKEN')
    db.setnx('pkg:id:next', 0)

    def __init__(self, name, db=db):
        db.incr('pkg:id:next')
        self.next_id = db.get('pkg:id:next')
        self.name = name
        self.key = 'pkg:%s' % self.name
        logger.debug('@@-package.py-@@ | self.key is %s' % self.key)
        if not db.exists(self.key) or True:
            db.set(self.key, True)
            db.set('%s:%s' % (self.key, 'name'), self.name)
            db.set('%s:%s' % (self.key, 'id'), self.next_id)
            db.set('%s:%s' % (self.key, 'push_version'), "False")
            db.set('%s:%s' % (self.key, 'autosum'), "False")
            db.sadd('%s:%s' % (self.key, 'depends'), '')
        if self.name in ['pycharm-pro-eap', 'pycharm-com-eap']:
            db.set('%s:%s' % (self.key, 'autosum'), "True")
        else:
            db.set('%s:%s' % (self.key, 'autosum'), "False")
        self.version = self.get_from_db('version')
        self.epoch = self.get_from_db('epoch')
        self.depends = self.get_from_db('depends')
        self.groups = self.get_from_db('groups')
        self.builds = self.get_from_db('build_logs')
        self.push_version = self.get_from_db('push_version')
        self.pkgrel = self.get_from_db('pkgrel')
        self.pkgver = self.get_from_db('pkgver')
        self.saved_commit = self.get_from_db('saved_commit')
        self.tl_event = self.get_from_db('tl_event')
        self.autosum = self.get_from_db('autosum')
        self.depends = self.get_from_db('depends')
        self.success_rate = self.get_from_db('success_rate')
        self.failure_rate = self.get_from_db('failure_rate')
        self.short_name = self.get_from_db('short_name')

    def delete(self):
        self.db.delete(self.key)

    def get_from_db(self, attr=None):
        val = ''
        if attr is not None:
            key = '%s:%s' % (self.key, attr)
            if db.exists(key):
                if self.db.type(key) == 'string':
                    val = self.db.get(key)
                elif self.db.type(key) == 'list':
                    val = list(self.db.lrange(key, 0, -1))
                elif self.db.type(key) == 'set':
                    val = self.db.smembers(key)
                logger.debug('@@-package.py-@@ | get_from_db %s is %s' % (attr, val))
            else:
                val = ''

        return val

    def save_to_db(self, attr=None, value=None, type=None):
        if attr is not None and value is not None:
            # TODO: This needs to be moved into its own method.
            if self.push_version and self.push_version == "True" and attr == "pkgver":
                if self.pkgver != value:
                    self.update_and_push_github(attr, self.pkgver, value)

            key = '%s:%s' % (self.key, attr)

            if (self.db.type(key) == 'string' or self.db.type(key) == 'none') and type is None:
                self.db.set(key, value)

            elif self.db.type(key) == 'list':
                self.db.rpush(key, value)

            elif self.db.type(key) == 'set' or type == 'set':
                self.db.sadd(key, value)

            return value

    def get_from_pkgbuild(self, var=None, path=None):
        if var is None or path is None:
            return ''
        parse = open(path).read()
        dirpath = os.path.dirname(path)
        if var == "pkgver" and 'pkgname=cnchi-dev' in parse:
            if "info" in sys.modules:
                del(sys.modules["info"])
            if "/tmp/cnchi/cnchi" not in sys.path:
                sys.path.append('/tmp/cnchi/cnchi')
            import info
            out = info.CNCHI_VERSION
            out = out.replace('"', '')
            del(info.CNCHI_VERSION)
            del(sys.modules["info"])
            err = []
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
            #logger.info('@@-package.py-@@ | proc.out is %s' % out)
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

    def get_version(self):
        pbfile = self.get_from_db('pbpath')
        pkgver = self.get_from_pkgbuild('pkgver', pbfile)
        if self.name == "cnchi-dev" and pkgver[-1] != "0":
            event = self.tl_event
            results = db.scan_iter('timeline:%s:*' % event, 100)
            for k in results:
                db.delete(k)
            db.lrem('timeline:all', 0, event)
            return False
        old_pkgver = self.pkgver
        self.save_to_db('pkgver', pkgver)
        epoch = self.get_from_pkgbuild('epoch', pbfile)
        pkgrel = self.get_from_pkgbuild('pkgrel', pbfile)
        if pkgrel and pkgrel != '' and pkgrel is not None:
            pkgrel_upd = False
            old_pkgrel = pkgrel
            if db.exists('build:pkg:now') and db.get('build:pkg:now') == "True":
                if pkgver == old_pkgver and pkgrel == self.pkgrel:
                    pkgrel = str(int(pkgrel) + 1)
                    pkgrel_upd = True
                elif pkgver != old_pkgver and pkgrel != "1":
                    pkgrel = "1"
                    pkgrel_upd = True
                db.set('build:pkg:now', "False")

            if pkgrel_upd:
                self.update_and_push_github('pkgrel', old_pkgrel, pkgrel)

            self.save_to_db('pkgrel', pkgrel)

        if epoch and epoch != '' and epoch is not None:
            pkgver = epoch + ':' + pkgver

        version = pkgver + '-' + str(pkgrel)
        if version and version != '' and version is not None:
            self.save_to_db('version', version)
            #logger.info('@@-package.py-@@ | pkgver is %s' % pkgver)
        else:
            version = self.version

        return version

    def get_deps(self):
        depends = []
        pbfile = self.get_from_db('pbpath')
        deps = self.get_from_pkgbuild('depends', pbfile).split()
        mkdeps = self.get_from_pkgbuild('makedepends', pbfile).split()

        for dep in deps:
            has_ver = re.search('^[\d\w]+(?=\=|\>|\<)', dep)
            if has_ver is not None:
                dep = has_ver.group(0)
            if db.sismember('pkgs:all', dep):
                depends.append(dep)

            self.save_to_db('depends', dep, type='set')
        for mkdep in mkdeps:
            has_ver = re.search('^[\d\w]+(?=\=|\>|\<)', mkdep)
            if has_ver is not None:
                mkdep = has_ver.group(0)
            if db.sismember('pkgs:all', mkdep):
                depends.append(mkdep)

            self.save_to_db('depends', mkdep, type='set')

        res = (self.name, depends)
        return res

