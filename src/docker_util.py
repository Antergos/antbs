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

import src.logging_config
import src.redis_connection
import subprocess
import os
import docker
from docker.utils import create_host_config

logger = src.logging_config.logger
db = src.redis_connection.db

doc_user = db.get('docker-images:username')
doc_pass = db.get('docker-images:password')

SRC_DIR = os.path.dirname(__file__) or '.'
BASE_DIR = os.path.split(os.path.abspath(SRC_DIR))[0]
DOC_DIR = os.path.join(BASE_DIR, 'build/docker')

# Initiate communication with build daemon
try:
    doc = docker.Client(base_url='unix://var/run/docker.sock', version='auto')
    # doc.build(path=DOC_DIR, tag="arch-devel", quiet=False, timeout=None)
except Exception as err:
    logger.error("Cant connect to Docker daemon. Error msg: %s", err)


def maybe_build_base_devel():

    if db.exists('docker-images:base-devel:built-today'):
        return True

    # No image was built in the past 24 hours, let's build one.
    build_script = os.path.join(DOC_DIR, 'base-devel.sh')
    try:
        build_it = subprocess.check_output([build_script])
    except subprocess.CalledProcessError as err:
        logger.error('@@-docker_util.py-@@ | Image build script failed with error: %s', err.output)
        return False

    if build_it:
        # Image was built successfully. Push it to docker hub.
        push_to_hub('antergos/archlinux-base-devel')
        mpkg = build_makepkg()
        if not mpkg:
            return False
        db.psetex('docker-images:base-devel:built-today', 304800000, 'True')
        return True
    else:
        return False


def maybe_build_mkarchiso():
    if db.exists('docker-images:mkarchiso:built-today'):
        return True

    archiso = build_mkarchiso()

    if not archiso or archiso is None:
        return False

    db.psetex('docker-images:mkarchiso:built-today', 304800000, 'True')

    return True


def build_makepkg():
    dockerfile = os.path.join(DOC_DIR, 'makepkg')
    try:
        build_it = [line for line in doc.build(dockerfile, 'antergos/makepkg', quiet=False, nocache=True, rm=True,
                                               stream=True, forcerm=True)]
        if build_it:
            push_to_hub('antergos/makepkg')
    except Exception as err:
        logger.error('@@-docker_util.py-@@ | Building makepkg failed with error: %s', err)
        return False

    return True


def build_mkarchiso():
    dockerfile = '/opt/archlinux-mkarchiso'
    try:
        build_it = [line for line in doc.build(dockerfile, tag='antergos/mkarchiso', quiet=False, nocache=True, rm=True,
                                               stream=True, forcerm=True)]
        if build_it:
            push_to_hub('antergos/mkarchiso')
    except Exception as err:
        logger.error('@@-docker_util.py-@@ | Building makepkg failed with error: %s', err)
        return False

    return True


def push_to_hub(repo=None):

    if repo is None:
        return
    try:
        doc.login(username=doc_user, password=doc_pass, email='dustin@falgout.us')
        response = [line for line in doc.push(repo, stream=True, insecure_registry=True)]
        if not response:
            return False
        else:
            logger.info(response)
    except Exception as err:
        logger.error('@@-docker_util.py-@@ | Pushing to docker hub failed with error: %s', err)



