#!/usr/bin/python
# -*- coding: utf-8 -*-
#
# transaction_handler.py
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
# The following additional terms are in effect as per Section 7 of the license:
#
# The preservation of all legal notices and author attributions in
# the material or in the Appropriate Legal Notices displayed
# by works containing it is required.
#
# You should have received a copy of the GNU General Public License
# along with AntBS; If not, see <http://www.gnu.org/licenses/>.


""" Transaction Handler Module: This is the point-of-entry for RQ Workers. """

from rq import (
    Connection,
    Queue,
    Worker,
    get_current_job
)

from database import (
    get_repo_object,
    db,
    status,
    get_trans_object
)

from utils import (
    DockerUtils,
    set_server_status
)

logger = status.logger
doc_utils = DockerUtils(status)
doc = doc_utils.doc

with Connection(db):
    transaction_queue = Queue('transactions')
    repo_queue = Queue('update_repo')
    w1 = Worker([transaction_queue])
    w2 = Worker([repo_queue])


def handle_hook():
    saved_status = set_server_status(first=True)

    logger.debug('calling maybe build docker image')

    if not status.iso_flag:
        image = doc_utils.maybe_build_base_devel()
    else:
        status.iso_flag = False
        image = doc_utils.maybe_build_mkarchiso()

    if not image:
        set_server_status(first=False, saved_status=saved_status)
        return False

    if status.transaction_queue:
        tnum = status.transaction_queue.lpop()
        transaction = get_trans_object(tnum=tnum, repo_queue=repo_queue)

        # Store this transaction's number and packages on the RQ job object.
        # We do this so that our custom exception handler can access the data
        # if an exception is raised while running this transaction.
        with Connection(db):
            current_job = get_current_job()
            current_job.meta.update(dict(tnum=transaction.tnum, packages=transaction.packages))
            current_job.save()

        transaction.start()

    set_server_status(first=False, saved_status=saved_status)

    if not status.transaction_queue and not status.transactions_running:
        status.idle = True
        status.building = 'Idle'
        status.container = ''
        status.building_num = ''
        status.building_start = ''
        status.iso_building = False
        logger.info('All builds completed.')


def update_repo_databases():
    with Connection(db):
        current_job = get_current_job()
        if 'update_repo' != current_job.origin:
            logger.error('Only the repo worker can update repos!')
            return

    saved_status = set_server_status(True, is_review=True)
    repos = [get_repo_object(repo, arch) for arch in ['x86_64', 'i686'] for repo in status.repos]

    with status.repos_syncing_lock():
        for antergos_repo in repos:
            antergos_repo.update_repo()

    set_server_status(False, saved_status)
