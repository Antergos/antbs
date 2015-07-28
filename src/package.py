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
from src.redis_connection import db, DB, status

REPO_DIR = "/var/tmp/antergos-packages"


class PackageMeta(DB.Model):
    """ This is the base class for the "package" model. It initalizes the fields for
    the package metadata that is stored in the database. """
    def __init__(self, *args, **kwargs):
        super(PackageMeta, self).__init__(*args, **kwargs)
        database = db
        namespace = 'antbs:package'
        index_separator = ':'
        gh_user = db.get('ANTBS_GITHUB_TOKEN')

        pkg_id = DB.AutoIncrementField(index=True)
        name = DB.TextField(index=True, primary_key=True)

        all_fields_dict = dict(
            TextField=['pkgname', 'pkgid', 'push_version', 'autosum', 'depends',
                       'version', 'pkgver', 'epoch', 'push_version', 'pkgrel', 'saved_commit', 'short_name',
                       'path', 'pbpath', 'description', 'pkgdesc', 'allowed_in', 'build_path'],
            ZSetField=['tl_event', 'build_logs', 'builds', 'success_rate', 'failure_rate'],
            SetField=['depends', 'groups'])

        all_fields = [all_fields_dict['TextField'], all_fields_dict['ZSetField'], all_fields_dict['SetField']]
        indexed = ['name', 'pkgname', 'pkgid', 'depends', 'version', 'pkgver', 'description', 'pkgdesc', 'allowed_in']

        for fields_list in all_fields:
            for field_type, fields in fields_list.items():
                for field in fields:
                    if field in indexed:
                        index = True
                    else:
                        index = False
                    field_type_obj = getattr(DB, field_type)
                    setattr(self, field, field_type_obj(index=index))
                    logger.debug('%s, %s' % (field, getattr(DB, field_type)))


class Package(PackageMeta):
    """ This class represents a "package" throughout the build server app. It is used to
    get and set package data to the database as well as from PKGBUILDs. """
    def __init__(self, *args, **kwargs):
        super(Package, self).__init__(*args, **kwargs)

    def get_from_pkgbuild(self, var=None):
        if var is None:
            logger.error('get_from_pkgbuild var is none')
        Package.maybe_update_pkgbuild_repo()
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
    def maybe_update_pkgbuild_repo():
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
        changed = []
        for key in ['pkgver', 'pkgrel', 'epoch']:
            old_val = getattr(self, key)
            new_val = self.get_from_pkgbuild(key)
            if new_val != old_val:
                changed.append((key, new_val))

        if not changed:
            return self.version

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
        deps = self.get_from_pkgbuild('depends').split()
        logger.info('deps are %s', deps)
        mkdeps = self.get_from_pkgbuild('makedepends').split()
        queue = status.queue

        for dep in deps:
            has_ver = re.search('^[\d\w]+(?=\=|\>|\<)', dep)
            if has_ver is not None:
                dep = has_ver.group(0)
                if dep in status.all_packages and dep in queue:
                    depends.append(dep)

                self.depends.add(dep)

        for mkdep in mkdeps:
            has_ver = re.search('^[\d\w]+(?=\=|\>|\<)', mkdep)
            if has_ver is not None:
                mkdep = has_ver.group(0)
                if mkdep in status.all_packages and mkdep in queue:
                    depends.append(mkdep)

                self.depends.add(mkdep)

        res = (self.name, depends)

        return res
