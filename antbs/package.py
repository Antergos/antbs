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

from github3 import login
from gitlab import Gitlab
import shutil
import time

from utils.logging_config import logger
from utils.redis_connection import db, RedisObject,  RedisList, RedisZSet
from utils.server_status import status


REPO_DIR = "/var/tmp/antergos-packages"
GITLAB_TOKEN = status.gitlab_token


class PackageMeta(RedisObject):
    """
    This is the base class for ::class:`Package`. It initalizes the fields for
    the package metadata that is stored in the database. You should not this
    class directly.

    """

    def __init__(self, name=None, *args, **kwargs):
        super(PackageMeta, self).__init__()

        self.key_lists = dict(
            redis_string=['name', 'pkgname', 'version_str', 'pkgver', 'epoch', 'pkgrel', 'short_name', 'path', 'pbpath',
                          'description', 'pkgdesc', 'build_path', 'success_rate', 'failure_rate', 'git_url', 'git_name',
                          'gh_repo', 'gh_project', 'iso_md5', 'iso_url', 'url', 'pkgbuild'],
            redis_string_bool=['push_version', 'autosum', 'saved_commit', 'is_iso'],
            redis_string_int=['pkg_id'],
            redis_list=['allowed_in', 'builds', 'tl_events'],
            redis_zset=['depends', 'groups', 'makedepends'])

        self.all_keys = [item for sublist in self.key_lists.values() for item in sublist]
        self.all_keys.append('_build')

        if not name:
            raise AttributeError

        self.namespace = 'antbs:pkg:%s:' % name
        self.prefix = self.namespace[:-1]
        self.name = name


class Package(PackageMeta):
    """
    This class represents a "package" throughout this application. It is used to
    get and set package data from/to the database as well as from PKGBUILDs.

    Args:
        :param name: (str) The name of the package, AKA the pkgname.

    Attributes:
        (str)
            name, pkgname, pkgver, epoch, pkgrel, description, pkgdesc: self explanatory (see `man PKGBUILD`)
            version_str: The package's version including pkgrel for displaying on the frontend.
            short_name: Optional name to use on frontend instead of the pkgname.
            path: Absolute path to the package's directory (subdir of antergos-packages directory)
            pbpath: Absolute path to the package's PKGBUILD file.
            build_path: Absolute path to the the package's build directory.
            success_rate: The package's rate of successful builds.
            failure_rate: The package's rate of build failures.
        
        (bool)
            push_version: Should we automatically update the version and push to Github (for pkgrel bumps)?
            autosum: Does the package's PKGBUILD download checksums when makepkg is called?
            saved_commit: When making changes to be pushed to github, do we have a saved commit not yet pushed?
            is_iso: Is this a dummy package for building an install iso image?

        (int)
            pkg_id: ID assigned to the package when it is added to our database for the first time.

        (list)
            allowed_in: The repos that the package is allowed to be in.
            builds: The IDs of all builds (coompleted & failed) for the package.
            tl_events: The IDs of all timeline events that include this package.

        (set)
            depends, groups, makedepends

    """

    def __init__(self, name):
        super(Package, self).__init__(name=name)

        if not self or (not self.pkg_id and os.path.exists(os.path.join(REPO_DIR, name))):
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
            all_pkgs = status.all_packages
            all_pkgs.add(self.name)

            if '-x86_64' in self.name or '-i686' in self.name:
                self.is_iso = True

            if 'pycharm' in self.name:
                self.autosum = True

            self.determine_pbpath()

        self.pkgbuild = ''

        if not self.pkgname:
            self.pkgname = self.name
        if not self.pkg_id:
            next_id = db.incr('antbs:misc:pkgid:next')
            self.pkg_id = next_id
        if not self.pkgver:
            self.get_from_pkgbuild('pkgver')
        if not self.pkgdesc or not self.description:
            self.pkgdesc = self.description = self.get_from_pkgbuild('pkgdesc')
        if not self.url:
            self.url = self.get_from_pkgbuild('url')
        if not self.depends:
            self.get_deps()
        if not self.groups:
            self.groups = self.get_from_pkgbuild('groups')

    def get_from_pkgbuild(self, var=None):
        """
        Get a variable from the package's PKGBUILD (which is stored in antergos-packages gh repo).

        :param var: (str) A variable to extract from the PKGBUILD.
        :return: (str) The variable's value after extracted from PKGBUILD.

        """
        if var is None:
            logger.error('get_from_pkgbuild var is none')
            raise ValueError

        if 'dummy-' in self.name:
            return 'n/a'

        self.maybe_update_pkgbuild_repo()

        if not self.pbpath:
            self.determine_pbpath()

        if not self.pkgbuild:
            self.pkgbuild = open(self.pbpath).read()

        dirpath = os.path.dirname(self.pbpath)

        if var in ['source', 'depends', 'makedepends', 'arch']:
            cmd = 'cd ' + dirpath + '; source ./PKGBUILD; echo ${' + var + '[*]}'
        else:
            cmd = 'cd ' + dirpath + '; source ./PKGBUILD; echo ${' + var + '}'

        if var == "pkgver":
            exclude = ['numix-icon-theme', 'plymouth']
            use_container = ['zfs', 'spl', 'zfs-utils', 'spl-utils', 'broadcom-wl']
            git_source = 'git+' in self.pkgbuild or 'git://' in self.pkgbuild
            if (git_source and self.name not in exclude) or 'cnchi' in self.name:
                if 'http' not in self.git_url or not self.git_name:
                    self.determine_git_repo_info()
                self.prepare_package_source(dirpath=dirpath)

            if self.name in use_container:
                from utils.docker_util import DockerUtils
                pkgver = DockerUtils().get_pkgver_inside_container(self)
                return pkgver

            if 'numix-icon-theme' in self.name:
                self.prepare_package_source(dirpath=dirpath)

        proc = subprocess.Popen(cmd, executable='/bin/bash', shell=True, cwd=dirpath,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        out, err = proc.communicate()

        if len(out) > 0:
            out = out.strip()
            # logger.info('proc.out is %s' % out)
        if len(err) > 0:
            logger.error('proc.err is %s', err)

        os.unsetenv('srcdir')

        return out

    def prepare_package_source(self, dirpath=None):
        if not dirpath:
            logger.error('dirpath cannot be None')
            raise ValueError

        if 'numix-icon-theme-square' == self.name:
            zpath = os.path.join(dirpath, self.name + '.zip')
            gl = Gitlab('https://gitlab.com', GITLAB_TOKEN)
            gl.auth()
            nxsq = gl.Project(id='61284')
            source = nxsq.archive()
            with open(zpath, 'wb') as fd:
                fd.write(source)
            return

        os.putenv('srcdir', dirpath)

        if os.path.exists(os.path.join(dirpath, self.git_name)):
            shutil.rmtree(os.path.join(dirpath, self.git_name), ignore_errors=True)
        try:
            res = subprocess.check_output(['/usr/bin/git', 'clone', self.git_url, self.git_name], cwd=dirpath)
            logger.info(res)
        except subprocess.CalledProcessError as err:
            logger.error(err)

    def determine_git_repo_info(self):
        if not self.git_url or not self.git_url.endswith('.git'):
            source = self.get_from_pkgbuild('source')
            logger.info(source)
            url_match = re.search("((https*)|(git:)).+\.git", source)
            if url_match:
                logger.info('url_match is: %s', url_match)
                self.git_url = url_match.group(0)
            else:
                self.git_url = ''

        logger.info('2. self.git_url is: %s', self.git_url)
        if not self.git_name:
            self.git_name = self.name
            if self.name == 'pamac-dev':
                self.git_name = 'pamac'
            elif self.name == 'cnchi-dev':
                self.git_name = 'cnchi'
                self.git_url = 'http://github.com/lots0logs/cnchi-dev.git'
            elif self.name == 'cnchi':
                self.git_url = 'http://github.com/antergos/cnchi.git'

    def determine_pbpath(self):
        path = None
        paths = [os.path.join('/var/tmp/antergos-packages/', self.pkgname),
                 os.path.join('/var/tmp/antergos-packages/cinnamon/', self.pkgname)]
        self.maybe_update_pkgbuild_repo()
        for p in paths:
            logger.info(p)
            if os.path.exists(p):
                ppath = os.path.join(p, 'PKGBUILD')
                logger.info(ppath)
                if os.path.exists(ppath) and not ('cinnamon' == self.pkgname and paths[0] == p):
                    self.pbpath = ppath
                    if p == paths[0] and 'cinnamon' != self.pkgname and len(self.allowed_in) == 0:
                        self.allowed_in.append('main')
                    break
        else:
            logger.error('get_from_pkgbuild cant determine pkgbuild path for %s', self.name)
            if 'dummy-' not in self.name:
                raise ValueError

    @staticmethod
    def maybe_update_pkgbuild_repo():
        """


        """
        if not db.exists('PKGBUILD_REPO_UPDATED') or not os.path.exists('/var/tmp/antergos-packages'):
            if db.setnx('PKGBUILD_REPO_LOCK', True):
                db.expire('PKGBUILD_REPO_LOCK', 150)

                if os.path.exists('/var/tmp/antergos-packages'):
                    shutil.rmtree('/var/tmp/antergos-packages')
                try:
                    subprocess.check_call(['git', 'clone', 'http://github.com/antergos/antergos-packages'], cwd='/var/tmp')
                    db.setex('PKGBUILD_REPO_UPDATED', 350, True)
                except subprocess.CalledProcessError as err:
                    logger.error(err)
                    db.delete('PKGBUILD_REPO_UPDATED')

                db.delete('PKGBUILD_REPO_LOCK')
            else:
                while not db.exists('PKGBUILD_REPO_UPDATED') and db.exists('PKGBUILD_REPO_LOCK'):
                    time.sleep(2)

    def update_and_push_github(self, var=None, old_val=None, new_val=None):
        """

        :param var:
        :param old_val:
        :param new_val:
        :return:

        """
        if not self.push_version or old_val == new_val:
            return
        gh = login(token=status.github_token)
        repo = gh.repository('antergos', 'antergos-packages')
        tf = repo.file_contents(self.name + '/PKGBUILD')
        content = tf.decoded
        search_str = '%s=%s' % (var, old_val)
        if 'pkgver=None' in content:
            search_str = '%s=%s' % (var, 'None')
        replace_str = '%s=%s' % (var, new_val)
        content = content.replace(search_str, replace_str)
        ppath = os.path.join('/var/tmp/antergos-packages/', self.name, '/PKGBUILD')
        with open(ppath, 'w') as pbuild:
            pbuild.write(content)
        pbuild.close()
        commit = tf.update(
            '[ANTBS] | Updated %s to %s in PKGBUILD for %s' % (var, new_val, self.name), content)
        if commit and commit['commit'] is not None:
            try:
                logger.info('commit hash is %s', commit['commit'].sha)
            except AttributeError:
                logger.error('commit failed. commit=%s | content=%s', commit, content)
            return True
        else:
            logger.error('commit failed')
            return False

    def get_version(self):
        """


        :return:

        """
        changed = {}
        old_vals = {}
        if self.name not in ['scudcloud', 'yaourt', 'package-query']:
            for key in ['pkgver', 'pkgrel', 'epoch']:
                old_val = str(getattr(self, key))
                old_vals[key] = old_val
                new_val = str(self.get_from_pkgbuild(key))
                if new_val != old_val:
                    changed[key] = new_val
                    setattr(self, key, new_val)

            if 'cnchi-dev' == self.name and self.pkgver[-1] != '0' and self.pkgver[-1] != '5':
                return False

            if not changed:
                return self.version_str
        else:
            old_val = self.pkgver
            key = 'antbs:monitor:github:%s:%s' % (self.gh_project, self.gh_repo)
            changed['pkgver'] = db.get(key)
            setattr(self, 'pkgver', changed['pkgver'])
            self.update_and_push_github('pkgver', old_val, self.pkgver)

        version = changed.get('pkgver', self.pkgver)

        if changed.get('epoch', False):
            version = changed['epoch'] + ':' + version
        elif self.epoch:
            version = self.epoch + ':' + version

        if changed.get('pkgrel', False):
            version = version + '-' + changed['pkgrel']
        elif self.pkgrel:
            version = version + '-' + self.pkgrel
        else:
            version = version + '-' + '1'

        if version and len(version) > 2:
            setattr(self, 'version_str', version)
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

        all_deps = deps + mkdeps
        for dep in all_deps:
            has_ver = re.search('^[\d\w-]+(?=\=|\>|\<)', dep)
            if has_ver:
                dep = has_ver.group(0)
            if dep in status.all_packages and (dep in status.queue or dep in status.hook_queue):
                depends.append(dep)
                if dep in deps:
                    self.depends.add(dep)
                elif dep in mkdeps:
                    self.makedepends.add(dep)

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
