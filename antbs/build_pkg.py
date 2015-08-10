#!/usr/bin/python
# -*- coding: utf-8 -*-
#
# build_pkg.py
#
# Copyright 2013 Antergos
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
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston,
# MA 02110-1301, USA.

""" Build packages when triggered by /hook """

import os
import sys

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), os.path.pardir)))
from utils.redis_connection import db
import utils.docker_util as docker_utils
import subprocess
import utils.logging_config as logconf
import datetime
import shutil
from pygments import highlight
from pygments.lexers import BashLexer
from pygments.formatters import HtmlFormatter
import re
import time
from multiprocessing import Process
import package
from rq import get_current_job
from utils.server_status import status, Timeline
import build_obj
import utils.sign_pkgs as sign_pkgs

SRC_DIR = os.path.dirname(__file__) or '.'
BASE_DIR = os.path.split(os.path.abspath(SRC_DIR))[0]
DOC_DIR = os.path.join(BASE_DIR, 'build')
REPO_DIR = "/opt/antergos-packages"
doc = docker_utils.doc
create_host_config = docker_utils.create_host_config
logger = logconf.logger


def remove(src):
    if src != str(src):
        return True
    if os.path.isdir(src):
        try:
            shutil.rmtree(src)
        except Exception as err:
            logger.error(err)
            return True
    elif os.path.isfile(src):
        try:
            os.remove(src)
        except Exception as err:
            logger.error(err)
            return True
    else:
        return True


def run_docker_clean(pkg=None):
    try:
        doc.remove_container(pkg, v=True)
    except Exception:
        pass
    return True


def truncate_middle(s, n):
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
                logger.error("cyclic or missing dependancy detected: %r" % (next_pending,))
                pass
            pending = next_pending
            emitted = next_emitted
    except ValueError as err:
        logger.error(err)


def process_package_queue(the_queue=None):
    if the_queue is None:
        raise ValueError('the_queue cannot be None')
    all_deps = []
    for pkg in the_queue:
        if pkg == '':
            continue
        pkg_obj = package.get_pkg_object(name=pkg)
        version = pkg_obj.get_version()
        if not version:
            continue
        logger.info('Updating pkgver in database for %s to %s' % (pkg_obj.name, version))
        status.current_status = 'Updating pkgver in databse for %s to %s' % (pkg_obj.name, version)
        depends = pkg_obj.get_deps()

        if not pkg_obj.build_path or pkg_obj.build_path == '':
            paths = [os.path.join('/opt/antergos-packages/', pkg),
                     os.path.join('/opt/antergos-packages/deepin_desktop', pkg),
                     os.path.join('/opt/antergos-packages/cinnamon', pkg)]
            for p in paths:
                if os.path.exists(p):
                    pkg_obj.build_path = p
                    break
        if 'cnchi' in pkg:
            if pkg == 'cnchi-dev':
                shutil.copytree('/var/tmp/antergos-packages/cnchi-dev', '/opt/antergos-packages/cnchi-dev/cnchi-dev')
            elif pkg == 'cnchi':
                shutil.copytree('/var/tmp/antergos-packages/cnchi', '/opt/antergos-packages/cnchi/cnchi')
            status.current_status = 'Fetching latest translations for %s from Transifex.' % pkg
            fetch_and_compile_translations(translations_for="cnchi", pkg_obj=pkg_obj)

        if depends and len(the_queue) > 1:
            all_deps.append(depends)

    return all_deps


def handle_hook(first=False, last=False):
    status.idle = False
    packages = status.queue()

    if first:
        if not status.iso_flag:
            if os.path.exists(REPO_DIR):
                remove(REPO_DIR)
            try:
                subprocess.check_call(
                    ['git', 'clone', 'http://github.com/antergos/antergos-packages.git'],
                    cwd='/opt')
                subprocess.check_call(['chmod', '-R', 'a+rw', REPO_DIR], cwd='/opt')
            except subprocess.CalledProcessError as err:
                logger.error(err)

            image = docker_utils.maybe_build_base_devel()

        else:
            status.iso_flag = False
            image = docker_utils.maybe_build_mkarchiso()

        status.current_status = 'Building docker image.'

        if not image:
            return False

        logger.info('Checking database for packages.')
        status.current_status = 'Checking database for queued packages'

        all_deps = process_package_queue(packages)

        logger.info('All queued packages are in the database, checking deps to determine build order.')
        status.current_status = 'Determining build order by sorting package depends'
        if len(all_deps) > 1:
            topsort = check_deps(all_deps)
            check = []
            packages.delete()
            for p in topsort:
                # TODO: What if there is already a group of packages in queue prior to the current group?
                packages.append(p)

        logger.info('Check deps complete. Starting build_pkgs')
        status.current_status = 'Check deps complete. Starting build container.'

    if len(packages) > 0:
        pack = status.queue().lpop()
        if pack and pack is not None and pack != '':
            pkgobj = package.get_pkg_object(name=pack)
        else:
            return False

        rqjob = get_current_job(db)
        rqjob.meta['is_first'] = first
        rqjob.meta['is_last'] = last
        rqjob.meta['package'] = pkgobj.name
        rqjob.save()

        status.now_building = pkgobj.name

        if pkgobj.is_iso is True or pkgobj.is_iso == 'True':
            status.iso_building = True
            built = build_iso(pkgobj)
        else:
            built = build_pkgs(last, pkgobj)
        # TODO: Move this into its own method
        if built:
            completed = status.completed()
            failed = status.failed()
            blds = pkgobj.builds()
            total = len(blds)
            if total > 0:
                success = len([x for x in blds if x in completed])
                failure = len([x for x in blds if x in failed])
                if success > 0:
                    success = 100 * success / total
                else:
                    success = 0
                if failure > 0:
                    failure = 100 * failure / total
                else:
                    failure = 0
                pkgobj.success_rate = success
                pkgobj.failure_rate = failure

    if last:
        remove('/opt/antergos-packages')
        status.idle = True
        status.building = 'Idle'
        status.now_building = 'Idle'
        status.container = ''
        status.building_num = ''
        status.building_start = ''
        status.iso_building = False
        logger.info('All builds completed.')


def update_main_repo(rev_result=None, bld_obj=None, is_review=False, rev_pkgname=None):
    logger.debug('update_main_repo fired! %s', rev_result)
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
        pkgenv = ["_PKGNAME=%s" % pkgname, "_RESULT=%s" % rev_result, "_UPDREPO=True", "_REPO=%s" % repo,
                  "_REPO_DIR=%s" % repodir]
        building_saved = False
        if not status.idle:
            building_saved = status.current_status
        else:
            status.idle = False
        status.current_status = 'Updating repo database.'
        container = None
        run_docker_clean("update_repo")
        hconfig = docker_utils.create_repo_update_host_config()
        try:
            container = doc.create_container("antergos/makepkg", command=command,
                                             name="update_repo", environment=pkgenv,
                                             volumes=['/makepkg', '/root/.gnupg', '/main', '/result', '/staging'],
                                             host_config=hconfig)
            db.set('update_repo_container', container.get('Id'))
            doc.start(container.get('Id'))
            if not is_review:
                stream_process = Process(target=publish_build_ouput,
                                         kwargs=dict(container=container.get('Id'), bld_obj=bld_obj, upd_repo=True))
                stream_process.start()
            result = doc.wait(container.get('Id'))
            if not is_review:
                stream_process.join()
            if result != 0:
                logger.error('update repo failed. exit status is: %s', result)
            db.set('antbs:misc:cache_buster:flag', True)
        except Exception as err:
            result = 1
            logger.error('Start container failed. Error Msg: %s' % err)

        doc.remove_container(container, v=True)

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
    if not container or not bld_obj:
        logger.error('Unable to publish build output. (Container is None)')
        return
    # proc = subprocess.Popen(['docker', 'logs', '--follow', container], stdout=subprocess.PIPE)
    # output = iter(proc.stdout.readline, '')
    output = doc.logs(container=container, stream=True)
    nodup = set()
    content = []
    for line in output:
        # time.sleep(.10)
        if not line or line == '' or 'makepkg]# PS1="' in line:
            continue
        line = line.rstrip()
        end = line[25:]
        if end not in nodup:
            nodup.add(end)
            # line = re.sub(r'(?<=[\w\d])(( \')|(\' )(?=[\w\d]+))|(\'\n)', ' ', line)
            line = line.replace("'", '')
            line = line.replace('"', '')
            line = '[%s]: %s' % (datetime.datetime.now().strftime("%m/%d/%Y %I:%M%p"), line)
            if len(line) > 150:
                line = truncate_middle(line, 150)
            content.append(line)
            db.publish('build-output', line)
            db.set('build_log_last_line', line)

    failed = bld_obj.failed if bld_obj.completed != bld_obj.failed and not bld_obj.completed else False
    if upd_repo or failed:
        db.publish('build-output', 'ENDOFLOG')

    log = bld_obj.log()

    existing = True
    if len(log) < 1 and not failed and not is_iso:
        existing = False

    for line in content:
        log.rpush(line)

    if existing:
        log_content = '\n '.join(log)
        pretty = highlight(log_content, BashLexer(), HtmlFormatter(style='monokai', linenos='inline',
                                                                   prestyles="background:#272822;color:#fff;",
                                                                   encoding='utf-8'))
        bld_obj.log_str = pretty


def process_and_save_build_metadata(pkg_obj=None):
    if not pkg_obj:
        raise AttributeError

    status.current_status = 'Building %s' % pkg_obj.name
    status.now_building = pkg_obj.name
    logger.info('Building %s' % pkg_obj.name)
    bld_obj = build_obj.get_build_object(pkg_obj=pkg_obj)
    bld_obj.start_str = datetime.datetime.now().strftime("%m/%d/%Y %I:%M%p")
    status.building_num = bld_obj.bnum
    status.building_start = bld_obj.start_str
    build_id = bld_obj.bnum
    tlmsg = 'Build <a href="/build/%s">%s</a> for <strong>%s</strong> started.' % (build_id, build_id, pkg_obj.name)
    Timeline(msg=tlmsg, tl_type=3)
    pbuilds = pkg_obj.builds()
    pbuilds.append(build_id)
    run_docker_clean(pkg_obj.name)

    return bld_obj


def fetch_and_compile_translations(translations_for=None, pkg_obj=None):
    """ Get and compile translations from Transifex. """

    trans = {
        "cnchi": {
            'trans_dir': "/opt/cnchi-translations/",
            'trans_files_dir': '/opt/cnchi-translations/translations/antergos.cnchi',
            'dest_dir': '/opt/antergos-packages/' + pkg_obj.name + '/cnchi/po'
        },
        "cnchi_updater": {
            'trans_dir': "/opt/antergos-iso-translations/",
            'trans_files_dir': "/opt/antergos-iso-translations/translations/antergos.cnchi_updaterpot",
            'dest_dir': '/srv/antergos.info/repo/iso/testing/trans/cnchi_updater'
        },
        "gfxboot": {
            'trans_dir': "/opt/cnchi-translations/",
            'trans_files_dir': '/opt/cnchi-translations/translations/antergos.gfxboot',
            'dest_dir': '/srv/antergos.info/repo/iso/testing/trans/gfxboot'
        }
    }

    for trans_for in translations_for:

        if not os.path.exists(trans[trans_for]['dest_dir']):
            os.mkdir(trans[trans_for]['dest_dir'])
        try:
            subprocess.check_call(['tx', 'pull', '-a', '-r', 'antergos.gfxboot', '--minimum-perc=50'],
                                  cwd=trans[trans_for]['trans_dir'])
            for r, d, f in os.walk(trans[trans_for]['trans_files_dir']):
                for tfile in f:
                    if 'cnchi' == trans_for:
                        shutil.copy(tfile, trans[trans_for]['dest_dir'])
                    elif 'cnchi_updater' == trans_for:
                        mofile = tfile[:-2] + 'mo'
                        subprocess.check_call(['msgfmt', '-v', tfile, '-o', mofile], cwd=trans[trans_for]['trans_files_dir'])
                        os.rename(os.path.join(trans[trans_for]['trans_files_dir'], mofile),
                                  os.path.join(trans[trans_for]['dest_dir'], mofile))
                    elif 'gfxboot' == trans_for:
                        trfile = tfile[:-2] + 'tr'
                        subprocess.check_call(['po2txt_helper', tfile, trfile],
                                              cwd=trans[trans_for]['trans_files_dir'])
                        os.rename(os.path.join(trans[trans_for]['trans_files_dir'], trfile),
                                  os.path.join(trans[trans_for]['dest_dir'], trfile))

        except subprocess.CalledProcessError as err:
            logger.error(err.output)
        except Exception as err:
            logger.error(err)


def build_pkgs(last=False, pkg_info=None):
    if pkg_info is None:
        return False
    # Create our tmp directories
    result = '/tmp/result'
    cache = '/var/tmp/pkg_cache'
    for d in [result, cache, '/var/tmp/32build', '/var/tmp/32bit']:
        if os.path.exists(d) and 'pkg_cache' not in d:
            shutil.rmtree(d)
            os.mkdir(d, 0o777)
        elif os.path.exists(d) and 'pkg_cache' in d:
            logger.info('@@-build_pkg.py-@@ 476 | Cleaning package cache....')
            status.current_status = 'Cleaning package cache.'
            for pcache in os.listdir(d):
                pcache = os.path.join(d, pcache)
                if not os.path.isdir(pcache):
                    logger.error('@@-build_pkg.py-@@ 479 | pcache is not a directory')
                    continue
                for pfile in os.listdir(pcache):
                    pname = re.search('^([a-z]|[0-9]|-|_)+(?=-\d|r|v)', pfile)
                    if not pname or pname == '':
                        continue
                    pname = pname.group(0)
                    pfile = os.path.join(pcache, pfile)
                    dtime = time.time()
                    if os.stat(pfile).st_mtime < (dtime - (7 * 86400)) or status.all_packages().ismember(pname):
                        remove(pfile)
        else:
            os.mkdir(d, 0o777)

    pkglist1 = ['1']
    in_dir_last = len([name for name in os.listdir(result)])
    db.set('pkg_count', in_dir_last)
    for i in range(len(pkglist1)):
        pkg = pkg_info.name
        if pkg and pkg is not None and pkg != '':
            pkgbuild_dir = pkg_info.build_path
            pkg_deps = pkg_info.depends() or []
            pkg_deps_str = ' '.join(pkg_deps) if pkg_deps else ''

            bld_obj = process_and_save_build_metadata(pkg_obj=pkg_info)
            build_id = bld_obj.bnum

            if pkg_info is not None and pkg_info.autosum == "True":
                build_env = ['_AUTOSUMS=True']
            else:
                build_env = ['_AUTOSUMS=False']
            if '/cinnamon/' in pkg_info.path:
                build_env.append('_ALEXPKG=True')
            else:
                build_env.append('_ALEXPKG=False')
            hconfig = docker_utils.create_pkgs_host_config(cache, pkgbuild_dir, result)
            try:
                container = doc.create_container("antergos/makepkg",
                                                 command="/makepkg/build.sh " + pkg_deps_str,
                                                 volumes=['/var/cache/pacman', '/makepkg', '/repo',
                                                          '/pkg', '/root/.gnupg', '/staging',
                                                          '/32bit', '/32build', '/result'],
                                                 environment=build_env, cpuset='0-3', name=pkg,
                                                 host_config=hconfig)
                if container.get('Warnings') and container.get('Warnings') != '':
                    logger.error(container.get('Warnings'))
            except Exception as err:
                logger.error('Create container failed. Error Msg: %s' % err)
                bld_obj.failed = True
                continue

            bld_obj.container = container.get('Id')
            status.container = bld_obj.container

            try:
                doc.start(container.get('Id'))
                cont = bld_obj.container
                stream_process = Process(target=publish_build_ouput, kwargs=dict(container=cont, bld_obj=bld_obj))
                stream_process.start()
                result = doc.wait(cont)
                stream_process.join()
                if result != 0:
                    bld_obj.failed = True
                    logger.error('[CONTAINER EXIT CODE] Container %s exited. Return code was %s' % (pkg, result))
                else:
                    logger.info('[CONTAINER EXIT CODE] Container %s exited. Return code was %s' % (pkg, result))
                    bld_obj.completed = True
            except Exception as err:
                logger.error('Start container failed. Error Msg: %s' % err)
                bld_obj.failed = True
                bld_obj.completed = False
                continue

            repo_updated = False
            if bld_obj.completed:
                signed = sign_pkgs.sign_packages(bld_obj.pkgname)
                if signed:
                    db.publish('build-output', 'Updating staging repo database..')
                    repo_updated = update_main_repo(rev_result='staging', bld_obj=bld_obj, )

            if repo_updated:
                tlmsg = 'Build <a href="/build/%s">%s</a> for <strong>%s</strong> was successful.' % (
                build_id, build_id, pkg)
                Timeline(msg=tlmsg, tl_type=4)
                completed = status.completed()
                completed.rpush(bld_obj.bnum)
                bld_obj.review_status = 'pending'
            else:
                tlmsg = 'Build <a href="/build/%s">%s</a> for <strong>%s</strong> failed.' % (build_id, build_id, pkg)
                Timeline(msg=tlmsg, tl_type=5)
                bld_obj.failed = True
                bld_obj.completed = False

                failed = status.failed()
                failed.rpush(build_id)

            bld_obj.end_str = datetime.datetime.now().strftime("%m/%d/%Y %I:%M%p")

            if not bld_obj.failed:
                db.set('antbs:misc:cache_buster:flag', True)
                return True

    return False


def build_iso(pkg_obj=None):
    status.iso_building = True

    in_dir_last = len([name for name in os.listdir('/srv/antergos.info/repo/iso/testing')])
    if in_dir_last is None:
        in_dir_last = "0"
    db.set('pkg_count_iso', in_dir_last)

    bld_obj = process_and_save_build_metadata(pkg_obj=pkg_obj)
    build_id = bld_obj.bnum

    fetch_and_compile_translations(translations_for=["gfxboot", "cnchi_updater"])

    flag = '/srv/antergos.info/repo/iso/testing/.ISO32'
    minimal = '/srv/antergos.info/repo/iso/testing/.MINIMAL'

    if 'i686' in pkg_obj.name:
        if not os.path.exists(flag):
            open(flag, 'a').close()
    else:
        if os.path.exists(flag):
            os.remove(flag)

    if status.iso_minimal:
        out_dir = '/out'
        if not os.path.exists(minimal):
            open(minimal, 'a').close()
    else:
        out_dir = '/out'
        if os.path.exists(minimal):
            os.remove(minimal)

    # Create docker host config dict
    hconfig = create_host_config(privileged=True, cap_add=['ALL'],
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
                                     "Name": "on-failure"})
    iso_container = {}
    try:
        iso_container = doc.create_container("antergos/mkarchiso", command='/start/run.sh',
                                             name=pkg_obj.name, host_config=hconfig, cpuset='0-3')
        if iso_container.get('Warnings') and iso_container.get('Warnings') != '':
            logger.error(iso_container.get('Warnings'))
    except Exception as err:
        logger.error('Create container failed. Error Msg: %s' % err)
        bld_obj.failed = True
        return False

    bld_obj.container = iso_container.get('Id')
    status.container = bld_obj.container

    try:
        doc.start(bld_obj.container)
        cont = bld_obj.container
        stream_process = Process(target=publish_build_ouput, kwargs=dict(container=cont, bld_obj=bld_obj, is_iso=True))
        stream_process.start()
        result = doc.wait(cont)
        stream_process.join()
        if result != 0:
            bld_obj.failed = True
            logger.error('[CONTAINER EXIT CODE] Container %s exited. Return code was %s' % (pkg_obj.name, result))
            return False
        else:
            bld_obj.completed = True
            logger.info('[CONTAINER EXIT CODE] Container %s exited. Return code was %s' % (pkg_obj.name, result))
    except Exception as err:
        logger.error('Start container failed. Error Msg: %s' % err)
        bld_obj.failed = True
        return False

    in_dir = len([name for name in os.listdir('/srv/antergos.info/repo/iso/testing')])
    last_count = int(db.get('pkg_count_iso'))
    if in_dir > last_count:
        bld_obj.completed = True
        tlmsg = 'Build <a href="/build/%s">%s</a> for <strong>%s</strong> was successful.' % (
        build_id, build_id, pkg_obj.name)
        Timeline(msg=tlmsg, tl_type=4)
        completed = status.completed()
        completed.rpush(bld_obj.bnum)
    else:
        bld_obj.failed = True
        bld_obj.completed = False
        tlmsg = 'Build <a href="/build/%s">%s</a> for <strong>%s</strong> failed.' % (build_id, build_id, pkg_obj.name)
        Timeline(msg=tlmsg, tl_type=5)
        failed = status.failed()
        failed.rpush(build_id)
    remove('/opt/archlinux-mkarchiso/antergos-iso')
    run_docker_clean(pkg_obj.name)
    bld_obj.end_str = datetime.datetime.now().strftime("%m/%d/%Y %I:%M%p")

    if not bld_obj.failed:
        db.set('antbs:misc:cache_buster:flag', True)
        return True
    return False
