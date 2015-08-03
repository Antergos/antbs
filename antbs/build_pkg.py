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
    if the_queue is not None:
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
                    shutil.copy2('/var/tmp/antergos-packages/cnchi-dev/cnchi-dev.tar', '/opt/antergos-packages/cnchi-dev')
                elif pkg == 'cnchi-dev':
                    shutil.copy2('/var/tmp/antergos-packages/cnchi/cnchi.tar', '/opt/antergos-packages/cnchi')
                status.current_status = 'Fetching latest translations for %s from Transifex.' % pkg
                get_latest_translations()

            if depends and len(the_queue) > 1:
                all_deps.append(depends)
        logger.info('@@-build_pkg.py-@@ 189 | all_deps before topsort: %s' % all_deps)
        return all_deps


def handle_hook(first=False, last=False):
    status.idle = False
    pull_from = 'antergos'
    packages = status.queue()

    if os.path.exists(REPO_DIR):
        remove(REPO_DIR)
    try:
        subprocess.check_call(
            ['git', 'clone', 'http://github.com/antergos/antergos-packages.git'],
            cwd='/opt')
        subprocess.check_call(['chmod', '-R', 'a+rw', REPO_DIR], cwd='/opt')
    except subprocess.CalledProcessError as err:
        logger.error(err)

    if status.iso_flag:
        status.iso_flag = False
        status.current_status = 'Building docker image.'
        status.iso_building = True
        image = docker_utils.maybe_build_mkarchiso()
        db.lrem('queue', 0, 'antergos-iso')
        db.lrem('queue', 0, 'antergos-iso.openbox')
        if image:
            archs = ['x86_64', 'i686']
            if db.get('isoMinimal') == 'True':
                iso_name = 'antergos-iso-minimal-'
            else:
                iso_name = 'antergos-iso-'
            for arch in archs:
                db.rpush('queue', iso_name + arch)
                version = datetime.datetime.now().strftime('%Y.%m.%d')
                pkgobj = package.get_pkg_object(iso_name + arch)
                pkgobj.save_to_db('version', version)
            build_iso()
        db.set('isoBuilding', 'False')
        db.set('isoMinimal', 'False')
        db.set('idle', "True")
        return True

    elif first and not status.iso_flag:
        status.current_status = 'Building docker image.'
        image = docker_utils.maybe_build_base_devel()
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
        logger.debug((packages, status.iso_flag))
        status.current_status = 'Check deps complete. Starting build container.'

    if not status.iso_flag and len(packages) > 0:
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
        logger.info('All builds completed.')


def update_main_repo(rev_result=None, bld_obj=None):
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
        command = "/makepkg/build.sh"
        pkgenv = ["_PKGNAME=%s" % bld_obj.pkgname, "_RESULT=%s" % rev_result, "_UPDREPO=True", "_REPO=%s" % repo,
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
            stream_process = Process(target=publish_build_ouput,
                                     kwargs=dict(container=container.get('Id'), bld_obj=bld_obj, upd_repo=True))
            stream_process.start()
            result = doc.wait(container.get('Id'))
            stream_process.join()
            if result != 0:
                logger.err('update repo failed. exit status is: %s', result)
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


def publish_build_ouput(container=None, bld_obj=None, upd_repo=False):
    if not container or not bld_obj:
        logger.error('Unable to publish build output. (Container is None)')
        return
    # proc = subprocess.Popen(['docker', 'logs', '--follow', container], stdout=subprocess.PIPE)
    # output = iter(proc.stdout.readline, '')
    output = doc.logs(container, stream=True)
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
            line = re.sub('(?<=[\w\d])(( \')|(\' )(?=[\w\d]+))|(\'\n)', ' ', line)
            line = '[%s]: %s' % (datetime.datetime.now().strftime("%m/%d/%Y %I:%M%p"), line)
            if len(line) > 150:
                line = truncate_middle(line, 150)
            content.append(line)
            db.publish('build-output', line)
            db.set('build_log_last_line', line)

    if upd_repo:
        db.publish('build-output', 'ENDOFLOG')

    log = bld_obj.log()

    existing = True
    logger.debug(log)
    if not log or len(log) < 1:
        existing = False

    for line in content:
        log.rpush(line)

    if existing:
        log_content = '\n '.join(log)
        pretty = highlight(log_content, BashLexer(), HtmlFormatter(style='monokai', linenos='inline',
                                                                   prestyles="background:#272822;color:#fff;",
                                                                   encoding='utf-8'))
        bld_obj.log_str = pretty


def get_latest_translations():
    # Get translations for Cnchi
    trans_dir = "/opt/cnchi-translations/"
    trans_files_dir = os.path.join(trans_dir, "translations/antergos.cnchi")
    dest_dir = '/opt/antergos-packages/cnchi/cnchi/po'
    if not os.path.exists(dest_dir):
        dest_dir = '/opt/antergos-packages/cnchi-dev/cnchi/po'
        if not os.path.exists(dest_dir):
            logger.error('cnchi po directory not found.')
    else:
        try:
            subprocess.check_call(['tx', 'pull', '-a', '-r', 'antergos.cnchi', '--minimum-perc=50'],
                                  cwd=trans_dir)
            for f in os.listdir(trans_files_dir):
                shutil.copy(f, dest_dir)
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
                    if os.stat(pfile).st_mtime < (dtime - 7 * 86400) or status.all_packages().ismember(pname):
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
            status.current_status = 'Building %s with makepkg' % pkg
            bld_obj = build_obj.get_build_object(pkg_obj=pkg_info)
            bld_obj.start_str = datetime.datetime.now().strftime("%m/%d/%Y %I:%M%p")
            status.building_num = bld_obj.bnum
            status.building_start = bld_obj.start_str
            build_id = bld_obj.bnum
            tlmsg = 'Build <a href="/build/%s">%s</a> for <strong>%s</strong> started.' % (build_id, build_id, pkg)
            Timeline(msg=tlmsg, tl_type=3)
            pbuilds = pkg_info.builds()
            pbuilds.append(build_id)
            pkg_deps = pkg_info.depends() or []
            pkg_deps_str = ' '.join(pkg_deps)
            run_docker_clean(pkg)

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
                bld_obj.completed = False
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
                    bld_obj.completed = False
                    logger.error('[CONTAINER EXIT CODE] Container %s exited. Return code was %s' % (pkg, result))
                else:
                    logger.info('[CONTAINER EXIT CODE] Container %s exited. Return code was %s' % (pkg, result))
                    bld_obj.failed = False
                    bld_obj.completed = True
            except Exception as err:
                logger.error('Start container failed. Error Msg: %s' % err)
                bld_obj.failed = True
                bld_obj.completed = False
                continue

            repo_updated = False
            if not bld_obj.failed:
                db.publish('build-output', 'Updating staging repo database..')
                repo_updated = update_main_repo(rev_result='staging', bld_obj=bld_obj)

            if repo_updated:
                tlmsg = 'Build <a href="/build/%s">%s</a> for <strong>%s</strong> was successful.' % (build_id, build_id, pkg)
                Timeline(msg=tlmsg, tl_type=4)
                completed = status.completed()
                completed.rpush(build_id)
            else:
                tlmsg = 'Build <a href="/build/%s">%s</a> for <strong>%s</strong> failed.' % (build_id, build_id, pkg)
                Timeline(msg=tlmsg, tl_type=5)

                failed = status.failed()
                failed.rpush(build_id)

            bld_obj.end_str = datetime.datetime.now().strftime("%m/%d/%Y %I:%M%p")

            if not bld_obj.failed:
                db.set('antbs:misc:cache_buster:flag', True)
                return True

    return False


def build_iso():
    iso_arch = ['x86_64', 'i686']
    in_dir_last = len([name for name in os.listdir('/srv/antergos.info/repo/iso/testing')])
    if in_dir_last is None:
        in_dir_last = "0"
    db.set('pkg_count_iso', in_dir_last)
    is_minimal = db.get('isoMinimal')
    if is_minimal == 'True':
        iso_name = 'antergos-iso-minimal-'
    else:
        iso_name = 'antergos-iso-'
    for arch in iso_arch:
        if db.exists('iso:one:arch') and arch == 'x86_64':
            continue
        pkgobj = package.get_pkg_object(iso_name + arch)
        failed = False
        db.incr('build_number')
        dt = datetime.datetime.now().strftime("%m/%d/%Y %I:%M%p")
        build_id = db.get('build_number')
        pkgobj.save_to_db('builds', build_id, 'list')
        this_log = 'build_log:%s' % build_id
        db.set('%s:start' % this_log, dt)
        db.set('building_num', build_id)
        db.hset('now_building', 'build_id', build_id)
        db.hset('now_building', 'key', this_log)
        db.hset('now_building', 'pkg', pkgobj.name)
        db.set(this_log, True)
        db.set('building_start', dt)
        logger.info('Building %s' % pkgobj.name)
        db.set('building', 'Building: %s' % pkgobj.name)
        db.lrem('queue', 0, pkgobj.name)
        db.set('%s:pkg' % this_log, pkgobj.name)
        db.set('%s:version' % this_log, pkgobj.version)

        flag = '/srv/antergos.info/repo/iso/testing/.ISO32'
        minimal = '/srv/antergos.info/repo/iso/testing/.MINIMAL'
        if arch is 'i686':
            if not os.path.exists(flag):
                open(flag, 'a').close()
        else:
            if os.path.exists(flag):
                os.remove(flag)
        if is_minimal == "True":
            out_dir = '/out'
            if not os.path.exists(minimal):
                open(minimal, 'a').close()
        else:
            out_dir = '/out'
            if os.path.exists(minimal):
                os.remove(minimal)
        # Get and compile translations for updater script
        # TODO: Move this into its own method.
        trans_dir = "/opt/antergos-iso-translations/"
        trans_files_dir = os.path.join(trans_dir, "translations/antergos.cnchi_updaterpot")
        dest_dir = '/srv/antergos.info/repo/iso/testing/trans'
        if not os.path.exists(dest_dir):
            os.mkdir(dest_dir)
        try:
            subprocess.check_call(['tx', 'pull', '-a', '-r', 'antergos.cnchi_updaterpot', '--minimum-perc=50'],
                                  cwd=trans_dir)
            for r, d, f in os.walk(trans_files_dir):
                for tfile in f:
                    logger.info('tfile is %s' % tfile)
                    logger.info('tfile cut is %s' % tfile[:-2])
                    mofile = tfile[:-2] + 'mo'
                    logger.info('mofile is %s' % mofile)
                    subprocess.check_call(['msgfmt', '-v', tfile, '-o', mofile], cwd=trans_files_dir)
                    os.rename(os.path.join(trans_files_dir, mofile), os.path.join(dest_dir, mofile))
        except subprocess.CalledProcessError as err:
            logger.error(err.output)
        except Exception as err:
            logger.error(err)

        nm = iso_name + arch
        # Initiate communication with docker daemon
        run_docker_clean(nm)
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
        try:
            iso_container = doc.create_container("antergos/mkarchiso", command='/start/run.sh', tty=True,
                                                 name=nm, host_config=hconfig, cpuset='0-3')
            db.set('container', iso_container.get('Id'))
        except Exception as err:
            logger.error("Cant connect to Docker daemon. Error msg: %s", err)
            failed = True
            break

        try:
            doc.start(iso_container, privileged=True, cap_add=['ALL'], binds={
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
                    },
            })

            cont = db.get('container')
            stream_process = Process(target=publish_build_ouput, args=(cont, this_log))
            stream_process.start()
            result = doc.wait(cont)
            result2 = None
            if result is not 0:
                doc.restart(cont)
                stream_process2 = Process(target=publish_build_ouput, args=(cont, this_log))
                stream_process2.start()
                result2 = doc.wait(cont)
                if result2 is not 0:
                    # failed = True
                    # db.set('build_failed', "True")
                    logger.error('[CONTAINER EXIT CODE] Container %s exited. Return code was %s' % (nm, result))
            if result is 0 or (result2 and result2 is 0):
                logger.info('[CONTAINER EXIT CODE] Container %s exited. Return code was %s' % (nm, result))
                db.set('build_failed', "False")

        except Exception as err:
            logger.error("Cant start container. Error msg: %s", err)
            break

        db.publish('build-output', 'ENDOFLOG')
        db.set('%s:end' % this_log, datetime.datetime.now().strftime("%m/%d/%Y %I:%M%p"))

        in_dir = len([name for name in os.listdir('/srv/antergos.info/repo/iso/testing')])
        last_count = int(db.get('pkg_count_iso'))
        if in_dir > last_count:
            db.incr('pkg_count_iso', (in_dir - last_count))
            db.rpush('completed', build_id)
            db.set('%s:result' % this_log, 'completed')
            # db.set('%s:review_stat' % this_log, '1')
        else:
            logger.error('%s not found after container exit.' % iso_name + arch)
            failed = True
            db.set('%s:result' % this_log, 'failed')
            db.rpush('failed', build_id)
        remove('/opt/archlinux-mkarchiso/antergos-iso')
        doc.remove_container(cont, v=True)
        # log_string = db.hget('%s:content' % this_log, 'content')
        # if log_string and log_string != '':
        # pretty = highlight(log_string, BashLexer(), HtmlFormatter(style='monokai', linenos='inline',
        # prestyles="background:#272822;color:#fff;",
        # encoding='utf-8'))
        # db.hset('%s:content' % this_log, 'content', pretty.decode('utf-8'))
