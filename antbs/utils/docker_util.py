#!/usr/bin/python
# -*- coding: utf-8 -*-
#
# docker_util.py
#
# Copyright © 2013-2017 Antergos
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

import os
import shutil
import subprocess
import time

import docker

from . import Singleton


class DockerUtils(metaclass=Singleton):
    doc = _db = _status = _logger = _app_dir = _src_dir = _doc_dir = _build_dir = gpg_dir = None

    def __init__(self, _status=None):
        self.is_building_images = False
        self.result_dir = '/tmp/pkgver_result'

        if os.path.exists(self.result_dir):
            shutil.rmtree(self.result_dir)

        os.mkdir(self.result_dir)

        if self._db is None:
            self._db = _status.db
            self._status = _status
            self._logger = _status.logger
            self._app_dir = _status.APP_DIR
            self._src_dir = _status.APP_DIR
            self._doc_dir = os.path.join(self._src_dir, 'build/docker')
            self._build_dir = os.path.join(self._src_dir, 'build')
            self.gpg_dir = _status.GNUPG_DIR

        self.cache_dir = self._status.PKG_CACHE_DIR
        self.cache_i686 = self._status.PKG_CACHE_DIR32

        if self.doc is None:
            # Initiate communication with docker daemon
            try:
                self._doc = docker.Client(base_url='unix://var/run/docker.sock', version='auto')
            except Exception as err:
                self._logger.error("Cant connect to Docker daemon. Error msg: %s", err)
                raise RuntimeError

            self.doc = self._doc

    def do_docker_clean(self, pkg=None):
        try:
            self.doc.remove_container(pkg, v=True)
        except Exception:
            pass

    def get_host_config(self, config_for, *args, **kwargs):
        host_configs = {
            'packages': self.create_pkgs_host_config,
            'repo_update': self.create_repo_update_host_config
        }
        if config_for in host_configs:
            return host_configs[config_for](*args, **kwargs)

    def create_pkgs_host_config(self, pkgbuild_dir, result_dir=None, cache_dir_x86_64=None,
                                cache_dir_i686=None, _32build=None, _32bit=None):
        """

        :param cache_i686:
        :param cache:
        :param pkgbuild_dir:
        :param result_dir:
        :return:
        """
        required_args = [result_dir, _32build, _32bit, pkgbuild_dir]
        if any([True for arg in required_args if arg is None]):
            raise ValueError('All of {0} are required (cannot be None).'.format(required_args))

        cache_dir = cache_dir_x86_64 or self.cache_dir
        cache_i686 = cache_dir_i686 or self.cache_i686

        binds = {
            cache_dir:
                {
                    'bind': '/var/cache/pacman',
                    'ro': False
                },
            cache_i686:
                {
                    'bind': '/var/cache/pacman_i686',
                    'ro': False
                },
            self._build_dir:
                {
                    'bind': '/makepkg',
                    'ro': False
                },
            self._status.STAGING_REPO:
                {
                    'bind': '/staging',
                    'ro': False
                },
            self._status.MAIN_REPO:
                {
                    'bind': '/main',
                    'ro': False
                },
            pkgbuild_dir:
                {
                    'bind': '/pkg',
                    'ro': False
                },
            self.gpg_dir:
                {
                    'bind': '/root/.gnupg',
                    'ro': False
                },
            self._status.TRANSIFEXRC:
                {
                    'bind': '/tmp/antbs/.transifexrc',
                    'ro': False,
                },
        }
        if 'pkgver' not in result_dir:
            binds[_32bit] = {'bind': '/32bit', 'ro': False}
            binds[_32build] = {'bind': '/32build', 'ro': False}

        binds[result_dir] = {'bind': '/result', 'ro': False}

        pkgs_hconfig = self.doc.create_host_config(
            binds=binds, privileged=True, mem_limit='5G', memswap_limit='-1', cpuset_cpus='0-2'
        )
        return pkgs_hconfig

    def create_repo_update_host_config(self, result_dir='/tmp/result'):
        """


        :return:
        """
        repos_hconfig = self.doc.create_host_config(
            binds={
                self._build_dir:
                    {
                        'bind': '/makepkg',
                        'ro': False
                    },
                self._status.MAIN_REPO:
                    {
                        'bind': '/main',
                        'ro': False
                    },
                self._status.STAGING_REPO:
                    {
                        'bind': '/staging',
                        'ro': False
                    },
                self.gpg_dir:
                    {
                        'bind': '/root/.gnupg',
                        'ro': False
                    },
                result_dir:
                    {
                        'bind': '/result',
                        'ro': False
                    }
            }, mem_limit='1G', memswap_limit='-1')

        return repos_hconfig

    def create_unprivileged_host_config(self, pbpath, tmp_dir):
        script_path = os.path.join(self._build_dir, 'get_from_pkgbuild.sh')
        binds = {
            pbpath: {
                'bind': '/PKGBUILD',
                'ro': True
            },
            script_path: {
                'bind': '/get_from_pkgbuild.sh',
                'ro': True
            },
            tmp_dir: {
                'bind': '/output',
                'ro': False
            }
        }
        hconfig = self.doc.create_host_config(
            binds=binds,
            mem_limit='1G'
        )

        return hconfig

    def do_image_build_finished(self, result):
        self._status.docker_image_building = False
        return result

    def maybe_build_base_devel(self):
        """


        :return:
        """
        if self._db.exists('antbs:docker-images:base-devel:built-today'):
            return True
        elif self._status.docker_image_building:
            wait = self._status.docker_image_building
            self._logger.debug('waiting for docker image')
            waiting = 0
            while wait and waiting < 300:
                waiting += 10
                time.sleep(10)
                wait = self._status.docker_image_building

            return True

        # No image was built in the past 24 hours, let's build one.
        self._status.docker_image_building = True
        self._logger.debug('building new docker images')
        self._status.current_status = 'Docker images are stale. Building new images.'
        build_script = os.path.join(self._doc_dir, 'base-devel.sh')
        build_it = False
        try:
            build_it = subprocess.check_output([build_script])
        except subprocess.CalledProcessError as err:
            self._logger.exception('Image build script failed with error: %s', err.output)
            return self.do_image_build_finished(False)
        except shutil.Error as err2:
            self._logger.exception(err2)

        if build_it:
            try:
                # Image was built successfully. Push it to docker hub.
                self.push_to_hub('antergos/archlinux-base-devel')
            except Exception:
                pass
            mpkg = self.build_makepkg()
            if not mpkg:
                return self.do_image_build_finished(False)
            self._db.setex('antbs:docker-images:base-devel:built-today', 84600, 'True')
            return self.do_image_build_finished(True)
        else:
            return self.do_image_build_finished(False)

    def maybe_build_mkarchiso(self):
        """


        :return:
        """
        if self._db.exists('antbs:docker-images:mkarchiso:built-today'):
            return True

        # No image was built in the past 24 hours, let's build one.
        self._status.current_status = 'Docker images are stale. Building new images.'

        archiso = self.build_mkarchiso()

        if not archiso:
            return False

        self._db.setex('antbs:docker-images:mkarchiso:built-today', 84600, 'True')

        return True

    def build_makepkg(self):
        """


        :return:
        """
        dockerfile = os.path.join(self._doc_dir, 'makepkg')
        try:
            build_it = [line for line in
                        self.doc.build(dockerfile,
                                       'antergos/makepkg',
                                       quiet=False,
                                       nocache=True,
                                       rm=True,
                                       stream=True,
                                       forcerm=True)]
            if build_it:
                self.push_to_hub('antergos/makepkg')
        except Exception as err:
            self._logger.exception('Building makepkg failed with error: %s', err)
            return False

        return True

    def build_mkarchiso(self):
        shutil.rmtree(os.path.join(self._status.MKARCHISO_DIR, 'antergos-iso'), ignore_errors=True)

        try:
            build_it = [line for line in
                        self.doc.build(self._status.MKARCHISO_DIR,
                                       tag='antergos/mkarchiso',
                                       quiet=False,
                                       nocache=True,
                                       rm=True,
                                       stream=True,
                                       forcerm=True)]
            if build_it:
                self.push_to_hub('antergos/mkarchiso')
        except Exception as err:
            self._logger.error(
                '@@-docker_util.py-@@ | Building makepkg failed with error: %s', err
            )
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
            self.doc.login(username=self._status.docker_user,
                           password=self._status.docker_password, email=self._status.email)
            response = [line for line in self.doc.push(repo, stream=True, insecure_registry=True)]
            if not response:
                self._logger.info('Pushing to Docker hub might not have completed successfully.')
        except Exception as err:
            self._logger.exception('Pushing to docker hub failed with error: %s', err)

    def get_pkgbuild_generates(self, pkgname, build_env='', result_dir='/tmp'):
        name = '{0}-PKGBUILD_GENERATES'.format(pkgname)

        try:
            container = self.doc.create_container(
                command='/makepkg/build.sh',
                volumes=['/var/cache/pacman', '/makepkg', '/antergos',
                         '/pkg', '/root/.gnupg', '/staging', '/32bit',
                         '/32build', '/result',
                         '/var/cache/pacman_i686'],
                environment=build_env,
                cpuset='0-3',
                name=name,
                host_config=self.create_unprivileged_host_config('', '')
            )

            if container.get('Warnings', False):
                self._logger.error(container.get('Warnings'))

        except Exception as err:
            self._logger.error('Create container failed. Error Msg: %s' % err)
            raise RuntimeError

        container_id = container.get('Id')

        try:
            self.doc.start(container_id)
            result = self.doc.wait(container_id)
            output = os.listdir(result_dir)

            if not output or result != 0:
                return ''

            with open(output.pop()) as output_file:
                contents = output_file.readlines()
                pkgs = [p.strip() for p in contents if p]

                if pkgs:
                    self.do_docker_clean(pkgname)

                return pkgs

        except Exception as err:
            self._logger.error('Failed get packages generated by pkgbuild. error was: %s', err)

        raise RuntimeError
