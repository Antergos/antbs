#!/usr/bin/python
# -*- coding: utf-8 -*-
#
# package.py
#
# Copyright 2013-2015 Antergos
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
# You should have received a copy of the GNU General Public License
# along with AntBS; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston,
# MA 02110-1301, USA.

""" Package Class """

import subprocess
import os
import re
import sys

from github3 import login
import shutil

from utils.logging_config import logger
from utils.redis_connection import db, RedisObject,  RedisList, RedisZSet
from utils.server_status import status

REPO_DIR = "/var/tmp/antergos-packages"


class PackageMeta(RedisObject):
    """
    This is the base class for ::class:`Package`. It initalizes the fields for
    the package metadata that is stored in the database. You should not this
    class directly.

    """

    def __init__(self, *args, **kwargs):
        super(PackageMeta, self).__init__()

        self.key_lists = dict(
            redis_string=['name', 'pkgname', 'version_str', 'pkgver', 'epoch', 'pkgrel', 'short_name', 'path', 'pbpath',
                          'description', 'pkgdesc', 'build_path', 'success_rate', 'failure_rate'],
            redis_string_bool=['push_version', 'autosum', 'saved_commit', 'is_iso'],
            redis_string_int=['pkg_id'],
            redis_list=['allowed_in', 'builds', 'tl_events'],
            redis_zset=['depends', 'groups', 'makedepends'])

        self.all_keys = [item for sublist in self.key_lists.values() for item in sublist]

        name = kwargs.get('name')
        if not name:
            raise AttributeError

        self.namespace = 'antbs:pkg:%s:' % name
        self.name = name


class Package(PackageMeta):
    """
    This class represents a "package" throughout this application. It is used to
    get and set package data from/to the database as well as from PKGBUILDs.

    Args:
        :param name: (str) The name of the package, AKA the pkgname.

    Attributes:
        (str)
            name, pkgname, pkgver, epoch, pkgrel, description, pkgdesc,
            version_str: The package's version including pkgrel for displaying on the frontend.,
            short_name: Optional name to use on frontend instead of the pkgname.,
            path: Absolute path to the package's directory (subdir of antergos-packages directory),
            pbpath: Absolute path to the package's PKGBUILD file.,
            build_path: Absolute path to the the package's build directory.,
            success_rate: The package's rate of successful builds.,
            failure_rate: The package's rate of build failures.
        
        (bool)
            push_version: Should we automatically update the version and push to Github (for pkgrel bumps)?,
            autosum: Does the package's PKGBUILD download checksums when makepkg is called?,
            saved_commit: When making changes to be pushed to github, do we have a saved commit not yet pushed?
            is_iso: Is this a dummy package for building an install iso image?

        (int)
            pkg_id: ID assigned to the package when it is added to our database for the first time.

        (list)
            allowed_in: The repos that the package is allowed to be in.,
            builds: The IDs of all builds (coompleted & failed) for the package.,
            tl_events: The IDs of all timeline events that include this package.

        (set)
            depends, groups, makedepends

    """

    def __init__(self, name):
        super(Package, self).__init__(self, name=name)

        self.maybe_update_pkgbuild_repo()

        if not self or not self.pkg_id and os.path.exists(os.path.join(REPO_DIR, name)):
            # Package is not in the database, so it must be new. Let's initialize it.
            for key in self.all_keys:
                if key in self.key_lists['redis_string'] and key != 'name':
                    setattr(self, key, '')
                elif key in self.key_lists['redis_string_bool']:
                    setattr(self, key, False)
                elif key in self.key_lists['redis_string_int']:
                    setattr(self, key, 0)
                elif key in self.key_lists['redis_list']:
                    setattr(self, key, RedisList.as_child(self, key, str))
                elif key in self.key_lists['redis_zset']:
                    setattr(self, key, RedisZSet.as_child(self, key, str))
            self.pkgname = name
            next_id = db.incr('antbs:misc:pkgid:next')
            self.pkg_id = next_id
            all_pkgs = status.all_packages()
            all_pkgs.add(self.name)

            if '-x86_64' in self.name or '-i686' in self.name:
                self.is_iso = True
            else:
                self.is_iso = False

    def get_from_pkgbuild(self, var=None):
        """
        Get a variable from the package's PKGBUILD (which is stored in antergos-packages gh repo).

        :param var: (str) A variable to extract from the PKGBUILD.
        :return: (str) The variable's value after extracted from PKGBUILD.

        """
        if var is None:
            logger.error('get_from_pkgbuild var is none')
            return ''
        self.maybe_update_pkgbuild_repo()
        path = None
        paths = [os.path.join('/var/tmp/antergos-packages/', self.name),
                 os.path.join('/var/tmp/antergos-packages/deepin_desktop', self.name),
                 os.path.join('/var/tmp/antergos-packages/cinnamon', self.name)]
        for p in paths:
            if os.path.exists(p):
                path = os.path.join(p, 'PKGBUILD')
                if p == paths[0] and 'cinnamon' != self.pkgname and len(self.allowed_in()) == 0:
                    self.allowed_in().append('main')
                break
        else:
            logger.error('get_from_pkgbuild cant determine pkgbuild path')

        parse = open(path).read()
        dirpath = os.path.dirname(path)

        if var in ['source', 'depends', 'makedepends', 'arch']:
            cmd = 'source ' + path + '; echo ${' + var + '[*]}'
        else:
            cmd = 'source ' + path + '; echo ${' + var + '}'

        if var == "pkgver" and ('git+' in parse or 'cnchi' in self.name or 'git://' in parse):
            giturl = re.search('(?<=git\\+).+(?="|\')', parse)
            if giturl:
                giturl = giturl.group(0)
            else:
                giturl = re.search('(?<="|\')git:.+(?="|\')', parse)
                if giturl:
                    giturl = giturl.group(0)
                else:
                    giturl = ''
            gitnm = self.name
            if self.name == 'pamac-dev':
                gitnm = 'pamac'
            elif self.name == 'cnchi-dev':
                gitnm = 'cnchi'
                giturl = 'http://github.com/lots0logs/cnchi-dev.git'
            elif self.name == 'cnchi':
                giturl = 'http://github.com/antergos/cnchi.git'

            if os.path.exists(os.path.join(dirpath, gitnm)):
                shutil.rmtree(os.path.join(dirpath, gitnm), ignore_errors=True)
            try:
                subprocess.check_output(['git', 'clone', giturl, gitnm], cwd=dirpath)
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
        """


        """
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

    def update_and_push_github(self, var=None, old_val=None, new_val=None):
        """

        :param var:
        :param old_val:
        :param new_val:
        :return:
        """
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
        """


        :return:
        """
        changed = []
        for key in ['pkgver', 'pkgrel', 'epoch']:
            old_val = getattr(self, key)
            new_val = self.get_from_pkgbuild(key)
            if new_val != old_val:
                changed.append((key, new_val))
                setattr(self, key, new_val)

        if not changed:
            return self.version_str

        version = self.pkgver
        if self.epoch and self.epoch != '' and self.epoch is not None:
            version = self.epoch + ':' + version

        version = version + '-' + self.pkgrel
        if version and version != '' and version is not None:
            self.version_str = version
            # logger.info('@@-package.py-@@ | pkgver is %s' % pkgver)
        else:
            version = self.version_str

        return version

    def get_deps(self):
        """


        :return:
        """
        depends = []
        deps = self.get_from_pkgbuild('depends').split()
        logger.info('deps are %s', deps)
        mkdeps = self.get_from_pkgbuild('makedepends').split()
        queue = status.queue

        for dep in deps:
            has_ver = re.search('^[\d\w]+(?=\=|\>|\<)', dep)
            if has_ver is not None:
                dep = has_ver.group(0)
                if dep in status.all_packages() and dep in queue:
                    depends.append(dep)

                self.depends().add(dep)

        for mkdep in mkdeps:
            has_ver = re.search('^[\d\w]+(?=\=|\>|\<)', mkdep)
            if has_ver is not None:
                mkdep = has_ver.group(0)
                if mkdep in status.all_packages() and mkdep in queue:
                    depends.append(mkdep)

                self.makedepends().add(mkdep)

        res = (self.name, depends)

        return res


def get_pkg_object(name=None):
    """

    :param name:
    :return:
    """
    if not name:
        logger.debug('name is required to get package object.')
        return False
    pkg_obj = Package(name=name)
    return pkg_obj
