#!/usr/bin/python
# -*- coding: utf-8 -*-
#
# package.py
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
# You should have received a copy of the GNU General Public License
# along with AntBS; If not, see <http://www.gnu.org/licenses/>.


import os
import re
import shutil
import subprocess
import time
import zipfile

from gitlab import Gitlab

from database.base_objects import RedisHash
from database.server_status import status
from gevent import sleep
from github3 import login
from utils.logging_config import logger

REPO_DIR = "/var/tmp/antergos-packages"
GITLAB_TOKEN = status.gitlab_token


class PackageMeta(RedisHash):
    """
    This is the base class for ::class:`Package`. It initalizes the fields for
    the package metadata that is stored in the database. You should not use this
    class directly.

    """

    def __init__(self, namespace='antbs', prefix='pkg', key='', *args, **kwargs):

        super().__init__(namespace=namespace, prefix=prefix, key=key, *args, **kwargs)

        self.key_lists.update(
                dict(string=['name', 'pkgname', 'version_str', 'pkgver', 'epoch', 'pkgrel',
                             'short_name', 'path', 'pbpath', 'description', 'pkgdesc',
                             'build_path', 'success_rate', 'failure_rate', 'git_url',
                             'git_name', 'gh_repo', 'gh_project', 'iso_md5', 'iso_url',
                             'url', 'pkgbuild', 'heatmap'],
                     bool=['push_version', 'autosum', 'saved_commit', 'is_iso',
                           'is_metapkg', 'is_monitored'],
                     int=['pkg_id'],
                     list=['allowed_in', 'builds', 'tl_events'],
                     set=['depends', 'groups', 'makedepends']))

        self.__namespaceinit__()

        self.all_keys.append('_build')

        if not self or (not self.pkg_id and os.path.exists(os.path.join(REPO_DIR, key))):
            # Package is not in the database, so it must be new. Let's initialize it.
            self.__keysinit__()
            self.pkgname = self.name = key
            next_id = self.db.incr('antbs:misc:pkgid:next')
            self.pkg_id = next_id

            status.all_packages.add(self.name)

            if '-x86_64' in self.name or '-i686' in self.name:
                self.is_iso = True
            if 'pycharm' in self.name or 'intellij' in self.name or 'clion-eap' == self.name:
                self.autosum = True
            if 'yes' == self.get_from_pkgbuild('_is_metapkg'):
                self.is_metapkg = True
            if 'yes' == self.get_from_pkgbuild('_is_monitored'):
                self.is_monitored = True

    def get_from_pkgbuild(self, item):
        raise NotImplementedError('Subclass must implement this method')


class Package(PackageMeta):
    """
    This class represents a "package" throughout this application. It is used to
    get and set package data from/to the database as well as from PKGBUILDs.

    Args:
        name (str): The name of the package, AKA the pkgname.

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
            is_metapkg: Is this a "metapkg" (don't check/build dependencies).
            is_monitored: Are we monitoring this package's releases with `RepoMonitor`?

        (int)
            pkg_id: ID assigned to the package when it is added to our database for the first time.

        (list)
            allowed_in: The repos that the package is allowed to be in (repo names).
            builds: The IDs of all builds (coompleted & failed) for the package.
            tl_events: The IDs of all timeline events that include this package.

        (set)
            depends, groups, makedepends

    """

    def __init__(self, name, pbpath=None):
        super().__init__(key=name)

        if not pbpath:
            self.determine_pbpath()
        else:
            self.pbpath = pbpath

        if os.path.isdir(pbpath):
            self.pbpath = os.path.join(pbpath, 'PKGBUILD')

        if os.path.exists(self.pbpath):
            self.pkgbuild = open(self.pbpath).read()

    def get_from_pkgbuild(self, var=None):
        """
        Get a variable from this package's PKGBUILD (which is stored in antergos-packages gh repo).

        :param var: (str) A variable to extract from the PKGBUILD.
        :return: (str) The variable's value after extracted from PKGBUILD.

        """
        if var is None:
            logger.error('get_from_pkgbuild var is none')
            raise ValueError
        if 'dummy-' in self.name:
            return 'n/a'

        self.maybe_update_pkgbuild_repo()

        if not self.pkgbuild:
            setattr(self, 'pkgbuild', open(self.pbpath).read())

        dirpath = os.path.dirname(self.pbpath)

        if var in ['source', 'depends', 'makedepends', 'arch']:
            cmd = 'cd ' + dirpath + '; source ./PKGBUILD; echo ${' + var + '[*]}'
        else:
            cmd = 'cd ' + dirpath + '; source ./PKGBUILD; echo ${' + var + '}'

        if var == "pkgver":
            exclude = ['plymouth']
            use_container = []
            git_source = 'git+' in self.pkgbuild or 'git://' in self.pkgbuild
            if (git_source and self.name not in exclude) or 'cnchi' in self.name:
                if not self.git_url or 'http' not in self.git_url or not self.git_name:
                    self.determine_git_repo_info()

                self.prepare_package_source(dirpath=dirpath)

            if 'cnchi-dev' == self.name:
                cmd = 'mv Antergos*** cnchi; /usr/bin/python cnchi/cnchi/info.py'

            if self.name in use_container:
                from utils.docker_util import DockerUtils
                pkgver = DockerUtils().get_pkgver_inside_container(self)
                return pkgver

        proc = subprocess.Popen(cmd, executable='/bin/bash', shell=True, cwd=dirpath,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        out, err = proc.communicate()

        if len(out) > 0:
            out = out.decode('UTF-8').strip()
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
            nxsq = gl.projects(id='61284')
            source = nxsq.archive()
            with open(zpath, 'wb') as fd:
                fd.write(source)
            return
        if 'cnchi-dev' == self.name:
            zpath = os.path.join(dirpath, self.name + '.zip')
            gh = login(token=status.github_token)
            repo = gh.repository('antergos', 'cnchi')
            repo.archive('zipball', zpath, ref='master')
            zfile = zipfile.ZipFile(zpath, 'r')
            zfile.extractall(dirpath)
            return

        os.putenv('srcdir', dirpath)

        if os.path.exists(os.path.join(dirpath, self.git_name)):
            shutil.rmtree(os.path.join(dirpath, self.git_name), ignore_errors=True)
        try:
            res = subprocess.check_output(['/usr/bin/git', 'clone', self.git_url, self.git_name],
                                          cwd=dirpath)
            logger.info(res)
        except subprocess.CalledProcessError as err:
            logger.error(err.output)

    def determine_git_repo_info(self):
        if not self.git_url or not self.git_url.endswith('.git'):
            source = self.get_from_pkgbuild('source')
            url_match = re.search(r'((https*)|(git:)).+\.git', source)
            if url_match:
                logger.info('url_match is: %s', url_match)
                setattr(self, 'git_url', url_match.group(0))
            else:
                setattr(self, 'git_url', '')

        if not self.git_name:
            setattr(self, 'git_name', self.name)
            if self.name == 'pamac-dev':
                setattr(self, 'git_name', 'pamac')
            elif self.name == 'cnchi-dev':
                setattr(self, 'git_name', 'cnchi')
                setattr(self, 'git_url', 'http://github.com/antergos/cnchi.git')
            elif self.name == 'cnchi':
                setattr(self, 'git_url', 'http://github.com/antergos/cnchi.git')

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
                if os.path.exists(ppath) and ('cinnamon' != self.pkgname and paths[0] == p):
                    self.pbpath = ppath
                    if p == paths[0] and 'cinnamon' != self.pkgname and len(self.allowed_in) == 0:
                        self.allowed_in.append('main')
                    break
        else:
            msg = 'cant determine pkgbuild path for {0}'.format(self.name)
            logger.error(msg)
            if 'dummy-' not in self.name:
                raise ValueError(msg)

    def maybe_update_pkgbuild_repo(self):
        if not self.db.exists('PKGBUILD_REPO_UPDATED') or not os.path.exists(status.PKGBUILDS_DIR):
            if self.db.setnx('PKGBUILD_REPO_LOCK', True):
                self.db.expire('PKGBUILD_REPO_LOCK', 150)

                if os.path.exists(status.PKGBUILDS_DIR):
                    shutil.rmtree(status.PKGBUILDS_DIR)
                try:
                    subprocess.check_call(
                            ['git', 'clone', 'http://github.com/antergos/antergos-packages'],
                            cwd='/var/tmp')
                    self.db.setex('PKGBUILD_REPO_UPDATED', 350, True)
                except subprocess.CalledProcessError as err:
                    logger.error(err)
                    self.db.delete('PKGBUILD_REPO_UPDATED')

                self.db.delete('PKGBUILD_REPO_LOCK')
            else:
                while not self.db.exists('PKGBUILD_REPO_UPDATED') and self.db.exists('PKGBUILD_REPO_LOCK'):
                    sleep(2)

    def update_and_push_github(self, var=None, old_val=None, new_val=None):
        if not self.push_version or old_val == new_val:
            return
        gh = login(token=status.github_token)
        repo = gh.repository('antergos', 'antergos-packages')
        pb_file = repo.file_contents(self.name + '/PKGBUILD')
        pb_contents = pb_file.decoded.decode('utf-8')

        search_str = '{0}={1}'.format(var, old_val)
        if 'pkgver=None' in pb_contents:
            search_str = '{0}={1}'.format(var, 'None')

        replace_str = '{0}={1}'.format(var, new_val)
        new_pb_contents = pb_contents.replace(search_str, replace_str)

        with open(self.pbpath, 'w') as pbuild:
            pbuild.write(new_pb_contents)

        pbuild.close()

        if 'pkgver' == var:
            commit_msg = '[ANTBS] | [updpkg] {0} {1}'.format(self.name, new_val)
        else:
            commit_msg = '[ANTBS] | Updated {0} to {1} in PKGBUILD for {2}.'.format(var, new_val,
                                                                                    self.name)
        commit = pb_file.update(commit_msg, new_pb_contents.encode('utf-8'))

        if commit and 'commit' in commit:
            try:
                logger.info('commit hash is %s', commit['commit'].sha)
            except AttributeError:
                logger.error('commit failed. commit=%s | content=%s', commit, new_pb_contents)
            return True
        else:
            logger.error('commit failed')
            return False

    def get_version(self):
        changed = {'epoch': None, 'pkgrel': None, 'pkgver': None}
        old_vals = {}
        version_from_tag = self.is_monitored and 'pkgver()' not in self.pkgbuild
        if not version_from_tag:
            for key in ['pkgver', 'pkgrel', 'epoch']:
                old_val = getattr(self, key)
                old_vals[key] = old_val
                new_val = self.get_from_pkgbuild(key)

                if new_val != old_val:
                    changed[key] = new_val
                    setattr(self, key, new_val)

            if not any([x for x in changed if changed[x] is not None]):
                return self.version_str
        else:
            old_val = self.pkgver
            key = 'antbs:monitor:github:{0}:{1}'.format(self.gh_project, self.gh_repo)
            changed['pkgver'] = self.db.get(key)
            setattr(self, 'pkgver', changed['pkgver'])
            self.update_and_push_github('pkgver', old_val, changed['pkgver'])
            time.sleep(10)
            self.update_and_push_github('pkgrel', self.pkgrel, '1')
            setattr(self, 'pkgrel', '1')
            changed['pkgrel'] = '1'

        version = changed.get('pkgver', self.pkgver) or self.pkgver

        if changed['epoch']:
            version = '{0}:{1}'.format(changed['epoch'], version)
        elif self.epoch:
            version = '{0}:{1}'.format(self.epoch, version)

        if changed['pkgrel']:
            version = '{0}-{1}'.format(version, changed['pkgrel'])
        elif self.pkgrel:
            version = '{0}-{1}'.format(version, self.pkgrel)
        else:
            version = '{0}-{1}'.format(version, '1')

        if version and len(version) > 2:
            setattr(self, 'version_str', version)
        else:
            version = self.version_str

        if 'cnchi-dev' == self.name and self.pkgver[-1] not in ['0', '5']:
            if not self.db.exists('CNCHI-DEV-OVERRIDE'):
                version = False
            else:
                self.db.delete('CNCHI-DEV-OVERRIDE')

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
            has_ver = re.search(r'^[\d\w-]+(?=\=|\>|\<)', dep)
            if has_ver:
                dep = has_ver.group(0)

            depends.append(dep)

            if dep in deps:
                self.depends.add(dep)
            elif dep in mkdeps:
                self.makedepends.add(dep)

        return depends

    def sync_database_with_pkgbuild(self):
        if not self.pkgname:
            setattr(self, 'pkgname', self.name)
        if not self.pkg_id:
            next_id = self.db.incr('antbs:misc:pkgid:next')
            setattr(self, 'pkg_id', next_id)
        if not self.pkgver:
            self.get_from_pkgbuild('pkgver')
        if not self.pkgdesc or not self.description:
            setattr(self, 'pkgdesc', self.get_from_pkgbuild('pkgdesc'))
            setattr(self, 'description', self.pkgdesc)
        if not self.url:
            setattr(self, 'url', self.get_from_pkgbuild('url'))
        if not self.depends:
            self.get_deps()
        if not self.groups:
            setattr(self, 'groups', self.get_from_pkgbuild('groups'))


def get_pkg_object(name=None, pbpath=None):
    if not name:
        raise ValueError('name is required to get package object.')

    path = pbpath
    if not path:
        path = os.path.join('/var/tmp/antergos-packages', name, 'PKGBUILD')

    pkg_obj = Package(name=name, pbpath=path)

    return pkg_obj
