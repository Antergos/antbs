#!/usr/bin/python
# -*- coding: utf-8 -*-
#
# build_pkg.py
#
# Copyright © 2013-2015 Antergos
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


""" Build packages when triggered by /hook """

import os
import sys

from database.transaction import fetch_and_compile_translations

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), os.path.pardir)))
from database.base_objects import db
import utils.docker_util as docker_utils
from utils.logging_config import logger
from rq import Connection, Queue, get_current_job
from database.server_status import status
from database.transaction import get_trans_object

SRC_DIR = os.path.dirname(__file__) or '.'
BASE_DIR = os.path.split(os.path.abspath(SRC_DIR))[0]
DOC_DIR = os.path.join(BASE_DIR, 'build')
REPO_DIR = "/opt/antergos-packages"
doc_utils = docker_utils.DockerUtils()
doc = doc_utils.doc

with Connection(db):
    build_queue = Queue('build_queue')
    repo_queue = Queue('repo_queue')
    hook_queue = Queue('hook_queue')


def handle_hook_set_server_status(first=True, saved_status=False):
    ret = None
    if first:
        saved = False
        if not status.idle and 'Idle' not in status.current_status:
            saved = status.current_status
        else:
            status.idle = False

        status.current_status = 'Build hook was triggered. Checking docker images.'

        ret = saved

    elif not saved_status:
        status.idle = True
        status.current_status = 'Idle'
    elif saved_status and not status.idle:
        status.current_status = saved_status

    return ret


def handle_hook():

    saved_status = handle_hook_set_server_status(first=True)

    if not status.iso_flag:
        image = docker_utils.DockerUtils().maybe_build_base_devel()
    else:
        status.iso_flag = False
        image = docker_utils.DockerUtils().maybe_build_mkarchiso()

    if not image:
        handle_hook_set_server_status(first=False, saved_status=saved_status)
        return False

    if status.queue:
        tnum = status.queue.lpop()
        transaction = get_trans_object(tnum=tnum)
        transaction.start()

    handle_hook_set_server_status(first=False, saved_status=saved_status)


def build_pkg_handler():
    """


    :return:
    """
    status.idle = False
    if len(status.queue) > 0:
        pack = status.queue.lpop()
        if pack:
            pkgobj = package.get_pkg_object(name=pack)
        else:
            return False

        rqjob = get_current_job(db)
        rqjob.meta['package'] = pkgobj.name
        rqjob.save()

        status.now_building = pkgobj.name

        if pkgobj.is_iso:
            status.iso_building = True
            build_result = build_iso(pkgobj)
        else:
            build_result = build_package(pkgobj.name)

        # TODO: Move this into its own method
        if build_result is not None:
            run_docker_clean(pkgobj.pkgname)

            blds = pkgobj.builds
            total = len(blds)
            if total > 0:
                success = len([x for x in blds if x in status.completed])
                failure = len([x for x in blds if x in status.failed])
                if success > 0:
                    success = 100 * success / total
                if failure > 0:
                    failure = 100 * failure / total

                pkgobj.success_rate = success
                pkgobj.failure_rate = failure

    if not status.queue and not status.hook_queue:
        remove('/opt/antergos-packages')
        status.idle = True
        status.building = 'Idle'
        status.now_building = 'Idle'
        status.container = ''
        status.building_num = ''
        status.building_start = ''
        status.iso_building = False
        logger.info('All builds completed.')

