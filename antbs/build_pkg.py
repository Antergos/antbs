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
import subprocess
import utils.logging_config as logconf
import datetime
import shutil
import glob
from pygments import highlight
from pygments.lexers import BashLexer
from pygments.formatters import HtmlFormatter
import re
import time
from multiprocessing import Process
from rq import Connection, Queue, get_current_job
from database.server_status import status, TimelineEvent
from database import build, package
import utils.sign_pkgs as sign_pkgs
from utils.utilities import remove

SRC_DIR = os.path.dirname(__file__) or '.'
BASE_DIR = os.path.split(os.path.abspath(SRC_DIR))[0]
DOC_DIR = os.path.join(BASE_DIR, 'build')
REPO_DIR = "/opt/antergos-packages"
doc_utils = docker_utils.DockerUtils()
doc = doc_utils.doc
logger = logconf.logger

with Connection(db):
    build_queue = Queue('build_queue')
    repo_queue = Queue('repo_queue')
    hook_queue = Queue('hook_queue')





def truncate_middle(s, n):
    """

    :param s:
    :param n:
    :return:
    """
    if len(s) <= n:
        # string is already short-enough
        return s
    # half of the size, minus the 3 .'s
    n_2 = int(n) / 3 - 3
    # whatever's left
    n_1 = n - n_2 - 3
    return '{0}...{1}'.format(s[:n_1], s[-n_2:])




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
        handle_hook_set_server_status(first=False, early_exit=True, saved_status=saved_status)
        return False

    logger.info('Processing packages.')
    status.current_status = 'Processing packages.'

    all_deps = process_package_queue()

    logger.info('All queued packages are in the database, checking deps to determine build order.')
    status.current_status = 'Determining build order based on package dependencies.'

    if all_deps:
        topsort = check_deps(all_deps)
        logger.info('Check deps complete. Starting build_package')
        status.current_status = 'Check deps complete. Starting build container.'
        for p in topsort:
            status.hook_queue.remove(p)
            status.hook_queue.append(p)

    for p in range(len(status.hook_queue)):
        pkg = status.hook_queue.lpop()
        if pkg and pkg not in status.queue:
            status.queue.rpush(pkg)
            build_queue.enqueue_call(build_pkg_handler, timeout=84600)

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














def build_iso(pkg_obj=None):
    """

    :param pkg_obj:
    :return:
    """
    status.iso_building = True

    bld_obj = process_and_save_build_metadata(pkg_obj=pkg_obj)
    build_id = bld_obj.bnum

    fetch_and_compile_translations(translations_for=["cnchi_updater", "antergos-gfxboot"])

    flag = '/srv/antergos.info/repo/iso/testing/.ISO32'
    minimal = '/srv/antergos.info/repo/iso/testing/.MINIMAL'

    if 'i686' in pkg_obj.name:
        if not os.path.exists(flag):
            open(flag, 'a').close()
    else:
        if os.path.exists(flag):
            os.remove(flag)

    if 'minimal' in pkg_obj.name:
        out_dir = '/out'
        if not os.path.exists(minimal):
            open(minimal, 'a').close()
    else:
        out_dir = '/out'
        if os.path.exists(minimal):
            os.remove(minimal)

    in_dir_last = len([name for name in os.listdir('/srv/antergos.info/repo/iso/testing')])
    db.set('pkg_count_iso', in_dir_last)

    # Create docker host config dict
    hconfig = doc.create_host_config(privileged=True, cap_add=['ALL'],
                                     binds={
                                         '/opt/archlinux-mkarchiso':
                                             {
                                                 'bind': '/start',
                                                 'ro': False
                                             },
                                         '/run/dbus':
                                             {
                                                 'bind': '/var/run/dbus',
                                                 'ro': False
                                             },
                                         '/srv/antergos.info/repo/iso/testing':
                                             {
                                                 'bind': out_dir,
                                                 'ro': False
                                             }},
                                     restart_policy={
                                         "MaximumRetryCount": 2,
                                         "Name": "on-failure"},
                                     mem_limit='2G',
                                     memswap_limit='-1')
    iso_container = {}
    try:
        iso_container = doc.create_container("antergos/mkarchiso", command='/start/run.sh',
                                             name=pkg_obj.name, host_config=hconfig, cpuset='0-3')
        if iso_container.get('Warnings', False) and iso_container.get('Warnings') != '':
            logger.error(iso_container.get('Warnings'))
    except Exception as err:
        logger.error('Create container failed. Error Msg: %s' % err)
        bld_obj.failed = True
        return False

    bld_obj.container = iso_container.get('Id')
    status.container = bld_obj.container
    open('/opt/archlinux-mkarchiso/first-run', 'a').close()

    try:
        doc.start(bld_obj.container)
        cont = bld_obj.container
        stream_process = Process(target=publish_build_ouput, kwargs=dict(container=cont, bld_obj=bld_obj, is_iso=True))
        stream_process.start()
        result = doc.wait(cont)
        inspect = doc.inspect_container(cont)
        if result != 0:
            if inspect['State'].get('Restarting', False) or inspect.get('RestartCount', 0) != 2:
                while inspect['State'].get('Restarting', False) or inspect.get('RestartCount', 0) != 2:
                    time.sleep(5)
                    inspect = doc.inspect_container(cont)

        if inspect['State'].get('ExitCode', 1) == 1:
            bld_obj.failed = True
            logger.error('[CONTAINER EXIT CODE] Container %s exited. Return code was %s' % (pkg_obj.name, result))
        else:
            bld_obj.completed = True
            logger.info('[CONTAINER EXIT CODE] Container %s exited. Return code was %s' % (pkg_obj.name, result))
    except Exception as err:
        logger.error('Start container failed. Error Msg: %s' % err)
        bld_obj.failed = True
        return False

    stream_process.join()
    in_dir = len([name for name in os.listdir('/srv/antergos.info/repo/iso/testing')])
    last_count = int(db.get('pkg_count_iso'))
    if in_dir > last_count:
        bld_obj.completed = True
        tlmsg = 'Build <a href="/build/%s">%s</a> for <strong>%s</strong> was successful.' % (
            build_id, build_id, pkg_obj.name)
        TimelineEvent(msg=tlmsg, tl_type=4)
        completed = status.completed
        completed.rpush(bld_obj.bnum)
    else:
        bld_obj.failed = True
        bld_obj.completed = False
        tlmsg = 'Build <a href="/build/%s">%s</a> for <strong>%s</strong> failed.' % (build_id, build_id, pkg_obj.name)
        TimelineEvent(msg=tlmsg, tl_type=5)
        failed = status.failed
        failed.rpush(build_id)

    bld_obj.end_str = datetime.datetime.now().strftime("%m/%d/%Y %I:%M%p")

    if not bld_obj.failed:
        remove('/opt/archlinux-mkarchiso/antergos-iso')
        run_docker_clean(pkg_obj.name)
        db.set('antbs:misc:cache_buster:flag', True)
        return True
    return False
