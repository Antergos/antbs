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
import subprocess
import os
import re
import sys
from github3 import login
from src.redis_connection import db

logger = logconf.logger
REPO_DIR = "/opt/antergos-packages"


class Package(object):
    db = db
    gh_user = db.get('ANTBS_GITHUB_TOKEN')
    db.setnx('pkg:id:next', 0)

    def __init__(self, name, db=db):
        if name is None:
            logger.error('@@-package.py-@@ 46| A pkg name is required to init an object on this class')
            return
        self.name = name
        self.key = 'pkg:%s' % self.name
        # logger.debug('@@-package.py-@@ | self.key is %s' % self.key)
        if not db.exists(self.key):
            db.set(self.key, True)
            db.sadd('pkgs:all', self.name)
            db.incr('pkg:id:next')
            pkgid = db.get('pkg:id:next')
            db.set('%s:%s' % (self.key, 'pkgid'), pkgid)
            db.set('%s:%s' % (self.key, 'name'), self.name)
            db.set('%s:%s' % (self.key, 'push_version'), "False")
            db.set('%s:%s' % (self.key, 'autosum'), "False")
            db.delete('%s:%s' % (self.key, 'depends'))
            db.sadd('%s:%s' % (self.key, 'depends'), '')
        if self.name in ['pycharm-pro-eap', 'pycharm-com-eap']:
            db.set('%s:%s' % (self.key, 'autosum'), "True")
        else:
            db.set('%s:%s' % (self.key, 'autosum'), "False")
        self.pkgid = self.get_from_db('pkgid')
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
        self.path = self.get_from_db('path')
        self.pbpath = self.get_from_db('pbpath')
        self.schema_v1 = self.get_from_db('schema_v1')

        # TODO: Need to come up with a way to standardized database schema updates/changes
        if not self.db.exists('%s:%s' % (self.key, 'allowed_in')) and not self.db.exists(
                        '%s:%s' % (self.key, 'schema_v1')):
            if 'antergos-iso' in self.name:
                self.save_to_db('allowed_in', 'n/a', 'list')
            else:
                pb = self.determine_pkg_path()
                repos = self.get_from_pkgbuild('_allowed_in', pb)
                if repos and repos != '':
                    logger.info('@@-package.py-@@ 88 | FIRED!! %s' % repos)
                    repos = repos.split()
                    for r in repos:
                        self.save_to_db('allowed_in', r, 'list')
                else:
                    if self.builds:
                        logger.info('@@-package.py-@@ 88 | FIRED!!!!! %s' % self.builds)
                        self.save_to_db('allowed_in', 'main', 'list')

        self.allowed_in = self.get_from_db('allowed_in')

    def delete(self):
        self.db.delete(self.key)

    def get_from_db(self, attr=None):
        val = ''
        if attr is not None:
            key = '%s:%s' % (self.key, attr)
            if db.exists(key):
                if self.db.type(key) == 'string':
                    val = self.db.get(key)
                elif self.db.type(key) == 'list' and int(self.db.llen(key)) > 0:
                    val = self.db.lrange(key, 0, -1)
                elif self.db.type(key) == 'set' and self.db.scard(key) > 0:
                    val = self.db.smembers(key)
                    # logger.debug('@@-package.py-@@ | get_from_db %s is %s' % (attr, val))
            else:
                val = ''

        return val

    def save_to_db(self, attr=None, value=None, ktype=None):
        if attr is not None and value is not None:
            # TODO: This needs to be moved into its own method.
            if self.push_version and self.push_version == "True" and attr == "pkgver":
                if self.pkgver != value:
                    self.update_and_push_github(attr, self.pkgver, value)

            key = '%s:%s' % (self.key, attr)

            if (self.db.type(key) == 'string' or self.db.type(key) == 'none') and ktype is None:
                self.db.set(key, value)

            elif self.db.type(key) == 'list' or ktype == 'list':
                self.db.rpush(key, value)

            elif self.db.type(key) == 'set' or ktype == 'set':
                self.db.sadd(key, value)

            return value

    def get_from_pkgbuild(self, var=None, path=None):
        for i in [var, path]:
            if i is None or i == '':
                logger.error('get_from_pkgbuild path is none')
        try:
            if not os.path.exists('/var/tmp/antergos-packages'):
                subprocess.check_call(['git', 'clone', 'http://github.com/antergos/antergos-packages'], cwd='/var/tmp')
            else:
                subprocess.check_call(['git', 'pull'], cwd='/var/tmp/antergos-packages')
        except subprocess.CalledProcessError as err:
            logger.error(err)
        path = path.replace('/opt/', '/var/tmp/')
        logger.info('@@-package.py-@@ 156| get_from_pkgbuild: path is %s' % path)
        parse = open(path).read()
        dirpath = os.path.dirname(path)
        if var == "pkgver" and 'pkgname=cnchi-dev' in parse:
            if "info" in sys.modules:
                del (sys.modules["info"])
            if "/tmp/cnchi/cnchi" not in sys.path:
                sys.path.append('/tmp/cnchi/cnchi')
            if "/tmp/cnchi-dev/cnchi" not in sys.path:
                sys.path.append('/tmp/cnchi-dev/cnchi')
            import info

            out = info.CNCHI_VERSION
            out = out.replace('"', '')
            del info.CNCHI_VERSION
            del (sys.modules["info"])
            err = []
        else:
            cmd = 'source ' + path + '; echo ${' + var + '[*]}'
            logger.info('@@-package.py-@@ 88 | FIRED3! %s' % cmd)
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
            # logger.info('@@-package.py-@@ | proc.out is %s' % out)
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
            # logger.info('@@-package.py-@@ | pkgver is %s' % pkgver)
        else:
            version = self.version

        return version

    def get_deps(self):
        depends = []
        pbfile = self.pbpath
        deps = self.get_from_pkgbuild('depends', pbfile).split()
        logger.info('@@-package.py-@@ 250| deps are %s' % deps)
        mkdeps = self.get_from_pkgbuild('makedepends', pbfile).split()
        q = db.lrange('queue', 0, -1)

        for dep in deps:
            has_ver = re.search('^[\d\w]+(?=\=|\>|\<)', dep)
            if has_ver is not None:
                dep = has_ver.group(0)
            if db.sismember('pkgs:all', dep) and dep in q:
                depends.append(dep)

            self.save_to_db('depends', dep, ktype='set')

        for mkdep in mkdeps:
            has_ver = re.search('^[\d\w]+(?=\=|\>|\<)', mkdep)
            if has_ver is not None:
                mkdep = has_ver.group(0)
            if db.sismember('pkgs:all', mkdep) and mkdep in q:
                depends.append(mkdep)

            self.save_to_db('depends', mkdep, ktype='set')

        res = (self.name, depends)

        return res

    def determine_pkg_path(self):
        if (not os.path.exists(
                os.path.join(REPO_DIR, self.name)) and 'antergos-iso' not in self.name) or 'cinnamon' == self.name:
            subdir = ['deepin_desktop', 'cinnamon']
            for d in subdir:
                pdir = os.path.join(REPO_DIR, d)
                if os.path.isdir(os.path.join(pdir, self.name)):
                    self.save_to_db(d, 'True')
                    path = os.path.join(pdir, self.name)
                    logger.info('@@-package.py-@@ 281| path is %s' % path)
                    pbfile = os.path.join(path, 'PKGBUILD')
                    logger.info('@@-package.py-@@ 281| path is %s' % pbfile)
                    break

        else:
            path = os.path.join(REPO_DIR, self.name)
            pbfile = os.path.join(path, 'PKGBUILD')

        self.save_to_db('pbpath', pbfile)
        self.save_to_db('path', path)

        return pbfile
