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

from src.logging_config import logger
import subprocess
import os
import re
import sys
from github3 import login
from src.redis_connection import db

REPO_DIR = "/var/tmp/antergos-packages"


class Package(object):
    db = db
    gh_user = db.get('ANTBS_GITHUB_TOKEN')
    db.setnx('pkg:id:next', 0)

    def __init__(self, name):
        if not name:
            logger.error(
                '@@-package.py-@@ 46| A pkg name is required to init an object on this class')
            return
        self.name = name
        self.key = 'pkg:%s' % self.name
        self.pkgname = self.name
        all_keys = dict(
            keys_of_type_str=['name', 'pkgname', 'pkgid', 'push_version', 'autosum', 'depends',
                              'version', 'pkgver', 'epoch', 'push_version', 'pkgrel',
                              'saved_commit', 'success_rate', 'failure_rate', 'short_name',
                              'path', 'pbpath', 'description', 'pkgdesc', 'allowed_in', 'build_path'],
            keys_of_type_list=['tl_event', 'build_logs', 'builds'],
            keys_of_type_set=['depends', 'groups'])

        if not db.exists(self.key):
            db.set(self.key, True)

            for ktype, keys in all_keys.items():
                if ktype.endswith('str'):
                    for key in keys:
                        if key != 'name':
                            db.set('%s:%s' % (self.key, key), '')
                elif ktype.endswith('list'):
                    for key in keys:
                        db.delete('%s:%s' % (self.key, key))
                        db.rpush('%s:%s' % (self.key, key), '')
                elif ktype.endswith('set'):
                    for key in keys:
                        db.delete('%s:%s' % (self.key, key))
                        db.sadd('%s:%s' % (self.key, key), '')

            db.incr('pkg:id:next')
            pkgid = db.get('pkg:id:next')
            db.sadd('pkgs:all', self.name)
            db.set('%s:%s' % (self.key, 'pkgid'), pkgid)
            db.set('%s:%s' % (self.key, 'name'), self.name)
            db.set('%s:%s' % (self.key, 'push_version'), "False")
            db.set('%s:%s' % (self.key, 'autosum'), "False")

        key_lists = [all_keys['keys_of_type_str'], all_keys['keys_of_type_list'], all_keys['keys_of_type_set']]
        for key_list in key_lists:
            for key in key_list:
                setattr(self, key, self.get_from_db(key))

        self.check_update_pkgbuild_repo()

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

            if self.db.type(key) == 'string' or (self.db.type(key) == 'none' and ktype is None):
                self.db.set(key, value)

            elif self.db.type(key) == 'list' or ktype == 'list':
                self.db.rpush(key, value)

            elif self.db.type(key) == 'set' or ktype == 'set':
                self.db.sadd(key, value)

            return value

    def get_from_pkgbuild(self, var=None):
        if var is None:
            logger.error('get_from_pkgbuild var is none')
        self.check_update_pkgbuild_repo()
        path = None
        paths = [os.path.join('/var/tmp/antergos-packages/', self.name),
                 os.path.join('/var/tmp/antergos-packages/deepin_desktop', self.name),
                 os.path.join('/var/tmp/antergos-packages/cinnamon', self.name)]
        for p in paths:
            if os.path.exists(p):
                path = os.path.join(p, 'PKGBUILD')
                break
        else:
            logger.error('get_from_pkgbuild cant determine pkgbuild path')
        self.save_to_db('path', path)
        parse = open(path).read()
        dirpath = os.path.dirname(path)
        if var == "pkgver" and self.name == 'cnchi-dev':
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
            if var in ['source', 'depends', 'makedepends', 'arch']:
                cmd = 'source ' + path + '; echo ${' + var + '[*]}'
            else:
                cmd = 'source ' + path + '; echo ${' + var + '}'
            logger.info('@@-package.py-@@ 88 | FIRED3! %s' % cmd)
            if var == "pkgver" and ('git+' in parse or 'numix-icon-theme' in self.name):
                if 'numix-icon-theme' not in self.name:
                    giturl = re.search('(?<=git\\+).+(?="|\')', parse)
                    giturl = giturl.group(0)
                    pkgdir, pkgbuild = os.path.split(path)
                    if self.name == 'pamac-dev':
                        gitnm = 'pamac'
                    else:
                        gitnm = self.name
                    try:
                        subprocess.check_output(['git', 'clone', giturl, gitnm], cwd=pkgdir)
                    except subprocess.CalledProcessError as err:
                        logger.error(err.output)

                cmd = 'source ' + path + '; ' + var

            proc = subprocess.Popen(cmd, executable='/bin/bash', shell=True, cwd=dirpath,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            out, err = proc.communicate()

        if len(out) > 0:
            out = out.strip()
            logger.info('@@-package.py-@@ | proc.out is %s' % out)
        if len(err) > 0:
            logger.error('@@-package.py-@@ | proc.err is %s', err)

        return out

    @staticmethod
    def check_update_pkgbuild_repo():
        if not db.exists('pkgbuild_repo_cached') or not os.path.exists('/var/tmp/antergos-packages'):
            db.setex('pkgbuild_repo_cached', 1800, "True")
            try:
                if not os.path.exists('/var/tmp/antergos-packages'):
                    subprocess.check_call(
                        ['git', 'clone', 'http://github.com/antergos/antergos-packages'],
                        cwd='/var/tmp')
                else:
                    subprocess.check_call(['git', 'reset', '--hard', 'origin/master'],
                                          cwd='/var/tmp/antergos-packages')
                    subprocess.check_call(['git', 'pull'], cwd='/var/tmp/antergos-packages')
            except subprocess.CalledProcessError as err:
                logger.error(err)
                db.delete('pkgbuild_repo_cached')

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
        commit = tf.update(
            '[ANTBS] | Updated %s to %s in PKGBUILD for %s' % (var, new_val, self.name), content)
        if commit and commit['commit'] is not None:
            try:
                logger.info('@@-package.py-@@ | commit hash is %s', commit['commit'].sha)
            except AttributeError:
                pass
            return True
        else:
            logger.error('@@-package.py-@@ | commit failed')
            return False

    def get_version(self):
        for key in ['pkgver', 'pkgrel', 'epoch']:
            old_val = getattr(self, key)
            new_val = self.get_from_pkgbuild(key)
            if new_val != old_val:
                self.save_to_db(key, new_val, 'string')

        if self.name == "cnchi-dev" and self.pkgver[-1] != "0":
            event = self.tl_event
            results = db.scan_iter('timeline:%s:*' % event, 100)
            for k in results:
                db.delete(k)
            db.lrem('timeline:all', 0, event)
            return False

        version = self.pkgver
        if self.epoch and self.epoch != '' and self.epoch is not None:
            version = self.epoch + ':' + version

        version = version + '-' + self.pkgrel
        if version and version != '' and version is not None:
            self.save_to_db('version', version, 'string')
            # logger.info('@@-package.py-@@ | pkgver is %s' % pkgver)
        else:
            version = self.version

        return version

    def get_deps(self):
        depends = []
        pbfile = self.pbpath
        deps = self.get_from_pkgbuild('depends').split()
        logger.info('@@-package.py-@@ 250| deps are %s', deps)
        mkdeps = self.get_from_pkgbuild('makedepends').split()
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
        repodir = '/opt/antergos-packages'
        pbfile = None
        path = None
        pkgbuild_dir = os.path.join(repodir, self.name)
        if not os.path.exists(os.path.join(pkgbuild_dir, 'PKGBUILD')):
            pkgbuild_dir = os.path.join(repodir, 'deepen_desktop')
            if not os.path.exists(os.path.join(pkgbuild_dir, 'PKGBUILD')):
                pkgbuild_dir = os.path.join(repodir, 'cinnamon')
                if not os.path.exists(os.path.join(pkgbuild_dir, 'PKGBUILD')):
                    raise Exception

        if os.path.exists(pkgbuild_dir):
            path = pkgbuild_dir
            logger.info('@@-package.py-@@ 281| path is %s', path)
            pbfile = os.path.join(pkgbuild_dir, 'PKGBUILD')
            logger.info('@@-package.py-@@ 281| path is %s', pbfile)

        self.save_to_db('pbpath', pbfile)
        self.save_to_db('path', path)

        return pbfile

# self.pkgid = self.get_from_db('pkgid')
# self.version = self.get_from_db('version')
# self.epoch = self.get_from_db('epoch')
# self.depends = self.get_from_db('depends')
# self.groups = self.get_from_db('groups')
# self.builds = self.get_from_db('build_logs')
# self.push_version = self.get_from_db('push_version')
# self.pkgrel = self.get_from_db('pkgrel')
# self.pkgver = self.get_from_db('pkgver')
# self.saved_commit = self.get_from_db('saved_commit')
# self.tl_event = self.get_from_db('tl_event')
# self.autosum = self.get_from_db('autosum')
# self.depends = self.get_from_db('depends')
# self.success_rate = self.get_from_db('success_rate')
# self.failure_rate = self.get_from_db('failure_rate')
# self.short_name = self.get_from_db('short_name')
# self.path = self.get_from_db('path')
# self.pbpath = self.get_from_db('pbpath')
# self.schema_v1 = self.get_from_db('schema_v1')
# self.description = self.get_from_db('description')
