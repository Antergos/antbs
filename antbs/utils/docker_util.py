#!/usr/bin/python
# -*- coding: utf-8 -*-
#
# docker_util.py
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
# The following additional terms are in effect as per Section 7 of the license:
#
# The preservation of all legal notices and author attributions in
# the material or in the Appropriate Legal Notices displayed
# by works containing it is required.
#
# You should have received a copy of the GNU General Public License
# along with AntBS; If not, see <http://www.gnu.org/licenses/>.

""" Docker Utilities """

import subprocess
import os
import shutil
import docker
from logging_config import logger
from redis_connection import db
from server_status import status

doc_user = status.docker_user
doc_pass = status.docker_password

SRC_DIR = os.path.dirname(__file__) or '.'
DOC_DIR = os.path.abspath(os.path.join(SRC_DIR, '..', 'build/docker'))
BUILD_DIR = os.path.abspath(os.path.join(DOC_DIR, '..'))


class DockerUtils(object):
    # Initiate communication with build daemon
    try:
        doc = docker.Client(base_url='unix://var/run/docker.sock', version='auto')
        # doc.build(path=DOC_DIR, tag="arch-devel", quiet=False, timeout=None)
    except Exception as err:
        logger.error("Cant connect to Docker daemon. Error msg: %s", err)
        raise RuntimeError

    def __init__(self):
        self.cache_dir = '/var/tmp/pkg_cache'
        self.cache_i686 = '/var/tmp/pkg_cache_i686'
        self.result_dir = '/tmp/pkgver_result'
        if os.path.exists(self.result_dir):
            shutil.rmtree(self.result_dir, ignore_errors=True)
        os.mkdir(self.result_dir)

    def create_pkgs_host_config(self, pkgbuild_dir, result=None):
        """

        :param cache_i686:
        :param cache:
        :param pkgbuild_dir:
        :param result:
        :return:
        """
        binds = {
            self.cache_dir:
                {
                    'bind': '/var/cache/pacman',
                    'ro': False
                },
            self.cache_i686:
                {
                    'bind': '/var/cache/pacman_i686',
                    'ro': False
                },
            BUILD_DIR:
                {
                    'bind': '/makepkg',
                    'ro': False
                },
            '/srv/antergos.info/repo/antergos-staging':
                {
                    'bind': '/staging',
                    'ro': False
                },
            '/srv/antergos.info/repo/antergos':
                {
                    'bind': '/antergos',
                    'ro': False
                },
            pkgbuild_dir:
                {
                    'bind': '/pkg',
                    'ro': False
                },
            '/root/.gnupg':
                {
                    'bind': '/root/.gnupg',
                    'ro': False
                },
        }
        if 'pkgver' not in result:
            binds['/var/tmp/32bit'] = {'bind': '/32bit', 'ro': False}
            binds['/var/tmp/32build'] = {'bind': '/32build', 'ro': False}

        binds[result] = {'bind': '/result', 'ro': False}

        pkgs_hconfig = self.doc.create_host_config(binds=binds,
                                                   restart_policy={"MaximumRetryCount": 2,
                                                                   "Name": "on-failure"},
                                                   privileged=True, cap_add=['ALL'],
                                                   mem_limit='2G', memswap_limit='-1')

        return pkgs_hconfig

    def create_repo_update_host_config(self):
        """


        :return:
        """
        repos_hconfig = self.doc.create_host_config(
            binds={
                BUILD_DIR:
                    {
                        'bind': '/makepkg',
                        'ro': False
                    },
                '/srv/antergos.info/repo/antergos':
                    {
                        'bind': '/main',
                        'ro': False
                    },
                '/srv/antergos.info/repo/antergos-staging':
                    {
                        'bind': '/staging',
                        'ro': False
                    },
                '/root/.gnupg':
                    {
                        'bind': '/root/.gnupg',
                        'ro': False
                    },
                '/tmp/result':
                    {
                        'bind': '/result',
                        'ro': False
                    }
            }, privileged=True, cap_add=['ALL'], mem_limit='2G', memswap_limit='-1')

        return repos_hconfig

    def maybe_build_base_devel(self):
        """


        :return:
        """
        if db.exists('antbs:docker-images:base-devel:built-today'):
            return True

        # No image was built in the past 24 hours, let's build one.
        status.current_status = 'Docker images are stale. Building new images.'
        build_script = os.path.join(DOC_DIR, 'base-devel.sh')
        build_it = False
        try:
            build_it = subprocess.check_output([build_script])
        except subprocess.CalledProcessError as err:
            logger.error('@@-docker_util.py-@@ | Image build script failed with error: %s',
                         err.output)
            return False
        except shutil.Error as err2:
            logger(err2)

        if build_it:
            try:
                # Image was built successfully. Push it to docker hub.
                self.push_to_hub('antergos/archlinux-base-devel')
            except Exception:
                pass
            mpkg = self.build_makepkg()
            if not mpkg:
                return False
            db.setex('antbs:docker-images:base-devel:built-today', 84600, 'True')
            return True
        else:
            return False

    def maybe_build_mkarchiso(self):
        """


        :return:
        """
        if db.exists('antbs:docker-images:mkarchiso:built-today'):
            return True

        # No image was built in the past 24 hours, let's build one.
        status.current_status = 'Docker images are stale. Building new images.'

        archiso = self.build_mkarchiso()

        if not archiso or archiso is None:
            return False

        db.setex('antbs:docker-images:mkarchiso:built-today', 84600, 'True')

        return True

    def build_makepkg(self):
        """


        :return:
        """
        dockerfile = os.path.join(DOC_DIR, 'makepkg')
        try:
            build_it = [line for line in
                        self.doc.build(dockerfile, 'antergos/makepkg', quiet=False, nocache=True,
                                       rm=True,
                                       stream=True, forcerm=True)]
            if build_it:
                self.push_to_hub('antergos/makepkg')
        except Exception as err:
            logger.error('@@-docker_util.py-@@ | Building makepkg failed with error: %s', err)
            return False

        return True

    def build_mkarchiso(self):
        """


        :return:
        """
        dockerfile = '/opt/archlinux-mkarchiso'
        shutil.rmtree(os.path.join(dockerfile, 'antergos-iso'), ignore_errors=True)
        try:
            build_it = [line for line in
                        self.doc.build(dockerfile, tag='antergos/mkarchiso', quiet=False,
                                       nocache=True,
                                       rm=True, stream=True, forcerm=True)]
            if build_it:
                self.push_to_hub('antergos/mkarchiso')
        except Exception as err:
            logger.error('@@-docker_util.py-@@ | Building makepkg failed with error: %s', err)
            return False

        return True

    def push_to_hub(self, repo=None):
        """

        :param repo:
        :return:
        """
        if repo is None:
            return
        try:
            self.doc.login(username=doc_user, password=doc_pass, email='dustin@falgout.us')
            response = [line for line in self.doc.push(repo, stream=True, insecure_registry=True)]
            if not response:
                logger.info('Pushing to Docker hub might not have completed successfully.')
            else:
                logger.info(response)
        except Exception as err:
            logger.error('Pushing to docker hub failed with error: %s', err)

    def get_pkgver_inside_container(self, pkg_obj):
        dirpath = os.path.dirname(pkg_obj.pbpath)
        hconfig = self.create_pkgs_host_config(dirpath, self.result_dir)
        hconfig.pop('restart_policy', None)
        build_env = ['_ALEXPKG=False', '_GET_PKGVER_ONLY=True', 'srcdir=/pkg']
        try:
            container = self.doc.create_container("antergos/makepkg",
                                                  command="/makepkg/build.sh ",
                                                  volumes=['/var/cache/pacman', '/makepkg',
                                                           '/antergos', '/pkg', '/root/.gnupg',
                                                           '/staging', '/result'],
                                                  environment=build_env, cpuset='0-3',
                                                  name=pkg_obj.pkgname + '-pkgver',
                                                  host_config=hconfig)
            if container.get('Warnings') and container.get('Warnings') != '':
                logger.error(container.get('Warnings'))
        except Exception as err:
            logger.error('Create container failed. Error Msg: %s' % err)
            raise RuntimeError

        try:
            self.doc.start(container.get('Id'))
            result = self.doc.wait(container.get('Id'))

            if result == 0:
                version = [v for v in os.listdir(self.result_dir) if v][0]
                if version:
                    self.doc.remove_container(container.get('Id'), v=True)
                    return version
        except Exception as err:
            logger.error('Failed to get pkgver from inside container. err is: %s', err)

        raise RuntimeError
