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


def run_docker_clean(pkg=None):
    """

    :param pkg:
    :return:
    """
    try:
        doc.remove_container(pkg, v=True)
    except Exception:
        pass
    return True


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


def check_deps(source):
    # # TODO: This still needs to be improved.
    """perform topological sort on elements.

    :param source:
    :arg source: list of ``(name, [list of dependancies])`` pairs
    :returns: list of names, with dependancies listed first
    """
    pending = [(name, set(deps)) for name, deps in source]  # copy deps so we can modify set in-place
    emitted = []
    try:
        while pending:
            next_pending = []
            next_emitted = []
            for entry in pending:
                name, deps = entry
                deps.difference_update(emitted)  # remove deps we emitted last pass
                if deps:  # still has deps? recheck during next pass
                    next_pending.append(entry)
                else:  # no more deps? time to emit
                    yield name
                    emitted.append(name)  # <-- not required, but helps preserve original ordering
                    next_emitted.append(name)  # remember what we emitted for difference_update() in next pass
            if not next_emitted:  # all entries have unmet deps, one of two things is wrong...
                logger.error("cyclic or missing dependancy detected: %r", next_pending)
                raise ValueError
            pending = next_pending
            emitted = next_emitted
    except ValueError as err:
        logger.error(err)


def process_package_queue():
    """

    :return: :raise ValueError:

    """

    if status.hook_queue is None:
        raise ValueError('the_queue cannot be None')
    all_deps = []

    if not db.exists('BUILD_REPO_UPDATED'):
        if db.setnx('BUILD_REPO_LOCK', True):
            db.expire('BUILD_REPO_LOCK', 300)

            if os.path.exists(REPO_DIR):
                try:
                    subprocess.check_output(['git', 'reset', '--soft', 'origin/master'], cwd=REPO_DIR)
                    subprocess.check_output(['git', 'pull'], cwd=REPO_DIR)
                    db.setex('BUILD_REPO_UPDATED', 120, True)
                except subprocess.CalledProcessError as err:
                    logger.error(err.output)
            else:
                try:
                    subprocess.check_output(
                        ['git',
                         'clone',
                         'http://github.com/antergos/antergos-packages.git'], cwd='/opt')
                    subprocess.check_output(['chmod', '-R', 'a+rw', REPO_DIR], cwd='/opt')
                except subprocess.CalledProcessError as err:
                    logger.error(err.output)
            db.delete('BUILD_REPO_LOCK')

        else:
            while not db.exists('BUILD_REPO_UPDATED') and db.exists('BUILD_REPO_LOCK'):
                time.sleep(1)

    for pkg in status.hook_queue:
        # logger.info(pkg)
        if not pkg:
            continue
        pkg_obj = package.get_pkg_object(name=pkg)
        version = pkg_obj.get_version()
        if not version:
            status.hook_queue.remove(pkg_obj.name)
            if 'cnchi-dev' != pkg:
                logger.error('pkgbuild path is not valid for %s', pkg_obj.name)
            continue
        else:
            pkg_obj.version_str = version

        logger.info('Updating pkgver in database for %s to %s' % (pkg_obj.name, version))
        status.current_status = 'Updating pkgver in database for %s to %s' % (pkg_obj.name, version)

        depends = pkg_obj.get_deps()
        if depends:
            all_deps.append(depends)

        if not pkg_obj.build_path:
            paths = [os.path.join('/opt/antergos-packages/', pkg),
                     os.path.join('/opt/antergos-packages/cinnamon', pkg)]
            for p in paths:
                if os.path.exists(p):
                    pkg_obj.build_path = p
                    break
        if 'cnchi' in pkg:
            logger.info('cnchi package detected.')
            src = os.path.join('/var/tmp/antergos-packages/', pkg, 'cnchi')
            dest = os.path.join('/opt/antergos-packages/', pkg)
            remove(os.path.join(dest, 'cnchi'))
            shutil.move(src, dest)
            status.current_status = 'Fetching latest translations for %s from Transifex.' % pkg
            logger.info(status.current_status)
            cnchi_dir = '/opt/antergos-packages/%s' % pkg
            fetch_and_compile_translations(translations_for=["cnchi"], pkg_obj=pkg_obj)
            remove(os.path.join(cnchi_dir, 'cnchi/.git'))
            subprocess.check_output(['tar', '-cf', 'cnchi.tar', 'cnchi'],
                                    cwd='/opt/antergos-packages/%s' % pkg)
        elif 'numix-icon-theme-square' == pkg:
            src = os.path.join('/var/tmp/antergos-packages/', pkg, pkg + '.zip')
            dest = os.path.join('/opt/antergos-packages/', pkg)
            shutil.move(src, dest)

    return all_deps


def handle_hook_set_server_status(first=True, saved_status=False, early_exit=False):
    if first:
        saved = False
        if not status.idle and 'Idle' not in status.current_status:
            saved = status.current_status
        else:
            status.idle = False

        status.current_status = 'Build hook was triggered. Checking docker images.'

        return saved

    elif early_exit:
        if not saved_status:
            status.idle = True
            status.current_status = 'Idle'
        else:
            status.current_status = saved_status

        return

    else:




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


def update_main_repo(rev_result=None, bld_obj=None, is_review=False, rev_pkgname=None,
                     is_action=False, action=None, action_pkg=None):
    """

    :param rev_result:
    :param bld_obj:
    :param is_review:
    :param rev_pkgname:
    :return:
    """
    if rev_result:
        repo = 'antergos'
        repodir = 'main'
        if rev_result == 'staging':
            rev_result = ''
            repo = 'antergos-staging'
            repodir = 'staging'
        result = '/tmp/result'
        if os.path.exists(result):
            shutil.rmtree(result)
        os.mkdir(result, 0o777)
        if rev_pkgname is not None:
            pkgname = rev_pkgname
        else:
            pkgname = bld_obj.pkgname
        command = "/makepkg/build.sh"
        pkgenv = ["_PKGNAME=%s" % pkgname, "_RESULT=%s" % rev_result, "_UPDREPO=True",
                  "_REPO=%s" % repo, "_REPO_DIR=%s" % repodir]
        building_saved = False
        if not status.idle and status.current_status != 'Updating repo database.':
            building_saved = status.current_status
        else:
            status.idle = False
        status.current_status = 'Updating repo database.'
        container = None
        run_docker_clean("update_repo")
        hconfig = docker_utils.DockerUtils().create_repo_update_host_config()
        try:
            container = doc.create_container("antergos/makepkg", command=command,
                                             name="update_repo", environment=pkgenv,
                                             volumes=['/makepkg', '/root/.gnupg', '/main',
                                                      '/result', '/staging'],
                                             host_config=hconfig)
            db.set('update_repo_container', container.get('Id'))
            doc.start(container.get('Id'))
            if not is_review:
                stream_process = Process(target=publish_build_ouput,
                                         kwargs=dict(container=container.get('Id'),
                                                     bld_obj=bld_obj,
                                                     upd_repo=True))
                stream_process.start()
            result = doc.wait(container.get('Id'))
            if not is_review:
                stream_process.join()
            if result != 0:
                logger.error('update repo failed. exit status is: %s', result)
            else:
                doc.remove_container(container, v=True)
            db.set('antbs:misc:cache_buster:flag', True)
        except Exception as err:
            result = 1
            logger.error('Start container failed. Error Msg: %s' % err)

        if not status.idle:
            if building_saved:
                status.current_status = building_saved
            else:
                status.idle = True
                status.current_status = 'Idle'
        if result != 0:
            return False
        else:
            return True


def publish_build_ouput(container=None, bld_obj=None, upd_repo=False, is_iso=False):
    """

    :param container:
    :param bld_obj:
    :param upd_repo:
    :param is_iso:
    :return:
    """
    if not container and not bld_obj:
        logger.error('Unable to publish build output. (Container is None)')
        return
    # proc = subprocess.Popen(['docker', 'logs', '--follow', container], stdout=subprocess.PIPE)
    # output = iter(proc.stdout.readline, '')
    if 'firefox-kde' == bld_obj.pkgname:
        line = 'Skipping log output capture for %s.' % bld_obj.pkgname
        logger.info(line)
        db.publish('build-output', line)
        db.set('build_log_last_line', line)
        killed_count = 0
        # while not status.idle and killed_count < 2:
        #     time.sleep(600)
        #     output = doc.logs(container=container, tail=5)
        #     if 'Client failed to connect to the D-BUS daemon' in output:
        #         db.publish('build-output', 'Stalled build detected. Killing Xvfb...')
        #         cmd = doc.exec_create(container=container, cmd='killall Xvfb', privileged=True)
        #         if cmd.get('Id', False):
        #             res = doc.exec_start(cmd['Id'])
        #             db.publish('build-output', res)
        #             killed_count += 1

        # doc.wait(container)
        # return

    output = doc.logs(container=bld_obj.container, stream=True)
    nodup = set()
    content = []
    for line in output:
        line = line.decode('UTF-8')
        if not line or 'makepkg]# PS1="' in line:
            continue
        line = line.rstrip()
        end = line[25:]
        if end not in nodup:
            nodup.add(end)
            line = line.replace("'", '')
            line = line.replace('"', '')
            line = '[{0}]: {1}'.format(datetime.datetime.now().strftime("%m/%d/%Y %I:%M%p"), line)
            # if len(line) > 150:
            #     line = truncate_middle(line, 150)
            content.append(line)
            db.publish('build-output', line)
            db.set('build_log_last_line', line)

    result_ready = bld_obj.completed != bld_obj.failed
    if not result_ready:
        while not result_ready:
            result_ready = bld_obj.completed != bld_obj.failed
            time.sleep(2)

    if upd_repo or bld_obj.failed:
        db.publish('build-output', 'ENDOFLOG')

    existing = True
    if len(bld_obj.log) < 1 and not bld_obj.failed and not is_iso:
        existing = False

    for line in content:
        bld_obj.log.rpush(line)

    if existing:
        log_content = '\n '.join(bld_obj.log)
        bld_obj.log_str = highlight(log_content,
                                    BashLexer(),
                                    HtmlFormatter(style='monokai',
                                                  linenos='inline',
                                                  prestyles="background:#272822;color:#fff;"))


def process_and_save_build_metadata(pkg_obj=None):
    """
    Creates a new build for a package, initializes the build data, and returns a build object.

    Args:
        pkg_obj (Package): Package object for the package being built.

    Returns:
        Build: A build object.

    Raises:
        AttributeError: If `pkg_obj` is Falsey.
    """

    if not pkg_obj:
        raise AttributeError

    status.current_status = 'Building %s' % pkg_obj.name
    status.now_building = pkg_obj.name
    logger.info('Building %s' % pkg_obj.name)
    bld_obj = build.get_build_object(pkg_obj=pkg_obj)
    bld_obj.start_str = datetime.datetime.now().strftime("%m/%d/%Y %I:%M%p")
    status.building_num = bld_obj.bnum
    status.building_start = bld_obj.start_str
    build_id = bld_obj.bnum
    tlmsg = 'Build <a href="/build/%s">%s</a> for <strong>%s-%s</strong> started.' % \
            (build_id, build_id, pkg_obj.name, pkg_obj.version_str)
    TimelineEvent(msg=tlmsg, tl_type=3)
    pkg_obj.builds.append(build_id)
    run_docker_clean(pkg_obj.name)

    return bld_obj


def prepare_temp_and_cache_dirs():
    # Create our tmp directories and clean up pacman package caches
    logger.info('Preparing temp directories and cleaning package cache')
    result = '/tmp/result'
    cache = '/var/tmp/pkg_cache'
    cache_i686 = '/var/tmp/pkg_cache_i686'
    for d in [result, cache, cache_i686, '/var/tmp/32build', '/var/tmp/32bit']:
        if os.path.exists(d) and 'pkg_cache' not in d:
            # This is a temp directory that we don't want to persist across builds.
            remove(d) and os.mkdir(d, 0o777)
        elif os.path.exists(d) and 'pkg_cache' in d:
            # This is a pacman package cache directory. Let's clean it up.
            logger.info('Cleaning package cache...')
            status.current_status = 'Cleaning package cache...'
            for pcache in os.listdir(d):
                pcache = os.path.join(d, pcache)
                if not os.path.isdir(pcache):
                    logger.error('pcache is not a directory')
                    continue
                already_checked = []
                for pfile in os.listdir(pcache):
                    # Get the package name using regex.
                    pname = re.search('^([a-z]|[0-9]|-|_)+(?=-\d|r|v)', pfile)
                    if not pname or pname == '':
                        continue
                    pname = pname.group(0)
                    # Use globbing to check for multiple versions of the package.
                    all_versions = glob.glob('{0}/{1}**.xz'.format(pcache, pname))
                    if pname in already_checked:
                        # We've already handled all versions of this package.
                        continue
                    elif len(all_versions) <= 1:
                        # There is only one version of the package in this cache dir, keep it.
                        already_checked.append(pname)
                        continue
                    elif pname not in already_checked and len(all_versions) > 1:
                        # There are multiple versions of the package. Determine the latest.
                        newest = max(glob.iglob('{0}/{1}**.xz'.format(pcache, pname)),
                                     key=os.path.getctime)
                        pfile = os.path.join(pcache, pfile)
                        for package_file in all_versions:
                            if package_file != newest or status.all_packages.ismember(pname):
                                # This file is not the newest. Remove it.
                                remove(pfile)
        else:
            logger.debug(d)
            os.mkdir(d, 0o777)

    return result, cache, cache_i686


def build_package(pkg=None):
    """

    :param pkg:
    :return:

    """
    if pkg is None:
        return False

    result, cache, cache_i686 = prepare_temp_and_cache_dirs()
    pkg_obj = package.get_pkg_object(name=pkg)

    in_dir_last = len([name for name in os.listdir(result)])
    db.setex('pkg_count', 3600, in_dir_last)

    bld_obj = process_and_save_build_metadata(pkg_obj=pkg_obj)

    if pkg_obj.autosum:
        build_env = ['_AUTOSUMS=True']
    else:
        build_env = ['_AUTOSUMS=False']
    if '/cinnamon/' in pkg_obj.pbpath:
        build_env.append('_ALEXPKG=True')
    else:
        build_env.append('_ALEXPKG=False')

    hconfig = doc_utils.create_pkgs_host_config(pkg_obj.build_path, result)
    container = {}
    try:
        container = doc.create_container("antergos/makepkg",
                                         command='/makepkg/build.sh',
                                         volumes=['/var/cache/pacman', '/makepkg', '/antergos',
                                                  '/pkg', '/root/.gnupg', '/staging', '/32bit',
                                                  '/32build', '/result', '/var/cache/pacman_i686'],
                                         environment=build_env, cpuset='0-3', name=pkg_obj.name,
                                         host_config=hconfig)
        if container.get('Warnings', False):
            logger.error(container.get('Warnings'))
    except Exception as err:
        logger.error('Create container failed. Error Msg: %s', err)
        bld_obj.failed = True

    bld_obj.container = container.get('Id', '')
    status.container = container.get('Id', '')
    stream_process = Process(target=publish_build_ouput, kwargs=dict(bld_obj=bld_obj))

    try:
        doc.start(container.get('Id', ''))
        stream_process.start()
        result = doc.wait(bld_obj.container)
        if int(result) != 0:
            bld_obj.failed = True
            logger.error('Container %s exited with a non-zero return code. Return code was %s', pkg_obj.name, result)
        else:
            logger.info('Container %s exited. Return code was %s', pkg_obj.name, result)
            bld_obj.completed = True
    except Exception as err:
        logger.error('Start container failed. Error Msg: %s' % err)
        bld_obj.failed = True

    stream_process.join()

    repo_updated = False
    if bld_obj.completed:
        logger.debug('bld_obj.completed!')
        signed = sign_pkgs.sign_packages(bld_obj.pkgname)
        if signed:
            db.publish('build-output', 'Updating staging repo database..')
            status.current_status = 'Updating staging repo database..'
            repo_updated = update_main_repo(rev_result='staging', bld_obj=bld_obj)

    if repo_updated:
        tlmsg = 'Build <a href="/build/{0}">{0}</a> for <strong>{1}</strong> was successful.'.format(
            str(bld_obj.bnum), pkg_obj.name)
        TimelineEvent(msg=tlmsg, tl_type=4)
        status.completed.rpush(bld_obj.bnum)
        bld_obj.review_status = 'pending'
    else:
        tlmsg = 'Build <a href="/build/{0}">{0}</a> for <strong>{1}</strong> failed.'.format(
            str(bld_obj.bnum), pkg_obj.name)
        TimelineEvent(msg=tlmsg, tl_type=5)
        bld_obj.failed = True
        bld_obj.completed = False

    bld_obj.end_str = datetime.datetime.now().strftime("%m/%d/%Y %I:%M%p")

    if not bld_obj.failed:
        pkg_obj = package.get_pkg_object(bld_obj.pkgname)
        last_build = pkg_obj.builds[-2] if pkg_obj.builds else None
        if not last_build:
            db.set('antbs:misc:cache_buster:flag', True)
            return True
        last_bld_obj = build.get_build_object(bnum=last_build)
        if 'pending' == last_bld_obj.review_status and last_bld_obj.bnum != bld_obj.bnum:
            last_bld_obj.review_status = 'skip'

        db.set('antbs:misc:cache_buster:flag', True)
        return True

    status.failed.rpush(bld_obj.bnum)
    return False


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
