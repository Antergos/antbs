#!/usr/bin/python
# -*- coding: utf-8 -*-
#
# transaction_handler.py
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

# Ignore PyImportSortBear as this statement affects imports later
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), os.path.pardir)))

import utils.docker_util as docker_utils
from database.base_objects import db
from database.server_status import status
from database.transaction import get_trans_object
from rq import Connection
from rq import Queue
from rq import get_current_job
from utils.logging_config import logger


SRC_DIR = os.path.dirname(__file__) or '.'
BASE_DIR = os.path.split(os.path.abspath(SRC_DIR))[0]
DOC_DIR = os.path.join(BASE_DIR, 'build')
REPO_DIR = "/opt/antergos-packages"
doc_utils = docker_utils.DockerUtils()
doc = doc_utils.doc

with Connection(db):
    transaction_queue = Queue('transactions')
    repo_queue = Queue('repo_update')


def set_server_status(first=True, saved_status=False, is_review=False):
    ret = None
    if first:
        saved = False
        do_save = status.transactions_running and 'Idle' not in status.current_status
        if not status.idle and do_save:
            saved = status.current_status
        else:
            status.idle = False

        if is_review:
            status.current_status = 'Processing developer review result.'
        else:
            status.current_status = 'Build hook was triggered. Checking docker images.'

        ret = saved

    elif not saved_status and not status.transactions_running:
        status.idle = True
        status.current_status = 'Idle'
    elif saved_status and not status.idle:
        status.current_status = saved_status

    return ret


def handle_hook():

    saved_status = set_server_status(first=True)

    if not status.iso_flag:
        image = docker_utils.DockerUtils().maybe_build_base_devel()
    else:
        status.iso_flag = False
        image = docker_utils.DockerUtils().maybe_build_mkarchiso()

    if not image:
        set_server_status(first=False, saved_status=saved_status)
        return False

    if status.queue:
        tnum = status.queue.lpop()
        transaction = get_trans_object(tnum=tnum, repo_queue=repo_queue)
        transaction.start()

    set_server_status(first=False, saved_status=saved_status)

    if not status.queue and not status.hook_queue:
        status.idle = True
        status.building = 'Idle'
        status.container = ''
        status.building_num = ''
        status.building_start = ''
        status.iso_building = False
        logger.info('All builds completed.')


def process_dev_review(review_result, pkgname, tnum):
    saved_status = set_server_status(True, is_review=True)

    trans_obj = get_trans_object(tnum=tnum, repo_queue=repo_queue)
    trans_obj.update_repo(review_result, None, True, pkgname)

    set_server_status(False, saved_status)
