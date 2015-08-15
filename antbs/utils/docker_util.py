#!/usr/bin/python
# -*- coding: utf-8 -*-
#
# docker_util.py
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

""" Docker Utilities """

import subprocess
import os
import shutil

import docker
from docker.utils import create_host_config

from logging_config import logger
from redis_connection import db
from server_status import status

doc_user = status.docker_user
doc_pass = status.docker_password

SRC_DIR = os.path.dirname(__file__) or '.'
DOC_DIR = os.path.abspath(os.path.join(SRC_DIR, '..', 'build/docker'))
BUILD_DIR = os.path.abspath(os.path.join(DOC_DIR, '..'))
# logger.debug([('SRC_DIR', SRC_DIR), ('DOC_DIR', DOC_DIR), ('BUILD_DIR', BUILD_DIR)])


# Initiate communication with build daemon
try:
    doc = docker.Client(base_url='unix://var/run/docker.sock', version='auto')
    # doc.build(path=DOC_DIR, tag="arch-devel", quiet=False, timeout=None)
except Exception as err:
    logger.error("Cant connect to Docker daemon. Error msg: %s", err)


def create_pkgs_host_config(cache, pkgbuild_dir, result):
    """

    :param cache:
    :param pkgbuild_dir:
    :param result:
    :return:
    """
    pkgs_hconfig = create_host_config(
        binds={
            cache:
                {
                    'bind': '/var/cache/pacman',
                    'ro': False
                },
            BUILD_DIR:
                {
                    'bind': '/makepkg',
                    'ro': False
                },
            '/srv/antergos.info/repo/iso/testing/uefi/antergos-staging':
                {
                    'bind': '/staging',
                    'ro': False
                },
            '/srv/antergos.info/repo/antergos':
                {
                    'bind': '/main',
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
            '/var/tmp/32bit':
                {
                    'bind': '/32bit',
                    'ro': False
                },
            '/var/tmp/32build':
                {
                    'bind': '/32build',
                    'ro': False
                },
            result:
                {
                    'bind': '/result',
                    'ro': False
                }
        },
        restart_policy={
            "MaximumRetryCount": 2,
            "Name": "on-failure"
        }, privileged=True, cap_add=['ALL'])

    return pkgs_hconfig


def create_repo_update_host_config():
    """


    :return:
    """
    repos_hconfig = create_host_config(
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
            '/srv/antergos.info/repo/iso/testing/uefi/antergos-staging/':
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
        }, privileged=True, cap_add=['ALL'])

    return repos_hconfig


def maybe_build_base_devel():
    """


    :return:
    """
    if db.exists('antbs:docker-images:base-devel:built-today'):
        return True

    # No image was built in the past 24 hours, let's build one.
    build_script = os.path.join(DOC_DIR, 'base-devel.sh')
    build_it = False
    try:
        build_it = subprocess.check_output([build_script])
        shutil.rmtree('/opt/antergos-packages')
    except subprocess.CalledProcessError as err:
        logger.error('@@-docker_util.py-@@ | Image build script failed with error: %s', err.output)
        return False
    except shutil.Error as err2:
        logger(err2)

    if build_it:
        # Image was built successfully. Push it to docker hub.
        push_to_hub('antergos/archlinux-base-devel')
        mpkg = build_makepkg()
        if not mpkg:
            return False
        db.psetex('antbs:docker-images:base-devel:built-today', 304800000, 'True')
        return True
    else:
        return False


def maybe_build_mkarchiso():
    """


    :return:
    """
    if db.exists('antbs:docker-images:mkarchiso:built-today'):
        return True

    archiso = build_mkarchiso()

    if not archiso or archiso is None:
        return False

    db.psetex('antbs:docker-images:mkarchiso:built-today', 304800000, 'True')

    return True


def build_makepkg():
    """


    :return:
    """
    dockerfile = os.path.join(DOC_DIR, 'makepkg')
    try:
        build_it = [line for line in
                    doc.build(dockerfile, 'antergos/makepkg', quiet=False, nocache=True, rm=True,
                              stream=True, forcerm=True)]
        if build_it:
            push_to_hub('antergos/makepkg')
    except Exception as err:
        logger.error('@@-docker_util.py-@@ | Building makepkg failed with error: %s', err)
        return False

    return True


def build_mkarchiso():
    """


    :return:
    """
    dockerfile = '/opt/archlinux-mkarchiso'
    try:
        build_it = [line for line in
                    doc.build(dockerfile, tag='antergos/mkarchiso', quiet=False, nocache=True,
                              rm=True,
                              stream=True, forcerm=True)]
        if build_it:
            push_to_hub('antergos/mkarchiso')
    except Exception as err:
        logger.error('@@-docker_util.py-@@ | Building makepkg failed with error: %s', err)
        return False

    return True


def push_to_hub(repo=None):
    """

    :param repo:
    :return:
    """
    if repo is None:
        return
    try:
        doc.login(username=doc_user, password=doc_pass, email='dustin@falgout.us')
        response = [line for line in doc.push(repo, stream=True, insecure_registry=True)]
        if not response:
            logger.info('Pushing to Docker hub might not have completed successfully.')
        else:
            logger.info(response)
    except Exception as err:
        logger.error('Pushing to docker hub failed with error: %s', err)
