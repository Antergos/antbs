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
from src.redis_connection import db
import src.docker_util as docker_utils
import subprocess
import src.logging_config as logconf
import datetime
import shutil
from pygments import highlight
from pygments.lexers import BashLexer
from pygments.formatters import HtmlFormatter
import re
import time
from multiprocessing import Process
import src.package as pkgclass
import src.sign_pkgs as sign_pkgs
import glob
from rq import get_current_job

logger = logconf.logger
SRC_DIR = os.path.dirname(__file__) or '.'
BASE_DIR = os.path.split(os.path.abspath(SRC_DIR))[0]
DOC_DIR = os.path.join(BASE_DIR, 'build')
REPO_DIR = "/opt/antergos-packages"
package = pkgclass.Package
doc = docker_utils.doc
create_host_config = docker_utils.create_host_config


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
        doc.remove_container(pkg)
    except Exception:
        pass
    return True


def truncate_middle(s, n):
    if len(s) <= n:
        # string is already short-enough
        return s
    # half of the size, minus the 3 .'s
    n_2 = int(n) / 2 - 3
    # whatever's left
    n_1 = n - n_2 - 3
    return '{0}...{1}'.format(s[:n_1], s[-n_2:])


def check_deps(source):
    # # TODO: This still needs to be improved.
    """perform topo sort on elements.

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
        special_cases = [
            {'numix-icon-theme': {
                'url': 'https://github.com/numixproject/numix-icon-theme.git',
                'source': ''}
            },
            {'numix-icon-theme-square': {
                'url': 'https://gitlab.com/numix/numix-icon-theme-square.git',
                'source': ''}
            },
            {'numix-icon-theme-square-kde': {
                'callsign': 'https://gitlab.com/numix/numix-icon-theme-square.git',
                'source': ''}
            },
            {'cnchi-dev': {
                'cwd': '/srv/antergos.org/cnchi.tar',
                'callsign': '',
                'source': ''}
            },
            {'cnchi': {
                'cwd': '',
                'callsign': '',
                'source': ''}
            }]
        all_deps = []
        for pkg in the_queue:
            if pkg == '':
                continue
            special = [x for x in special_cases if pkg in x.keys()]
            # logger.info('@@-build_pkg.py-@@ | special is: %s' % special)
            if special and len(special) > 0:
                for case in special:
                    callsign = case[pkg]['url']
                    source = case[pkg]['source']
                    # logger.info('@@-build_pkg.py-@@ | callsign is: %s, source is: %s' % (callsign, source))
                    try:
                        if callsign and callsign != '':
                            subprocess.call(['git', 'clone', callsign, pkg],
                                            cwd='/opt/antergos-packages/' + pkg)
                            subprocess.call(['git', 'clone', callsign, pkg],
                                            cwd='/var/tmp/antergos-packages/' + pkg)
                            logger.info('Creating tar archive for %s' % pkg)
                            subprocess.check_call(['tar', '-cf', pkg + '.tar', pkg],
                                                  cwd='/opt/antergos-packages/' + pkg)
                        elif source and source != '':
                            logger.info('Copying numix-frost source file into build directory.')
                            subprocess.check_call(['cp', '/opt/numix/' + pkg + '.zip', os.path.join(REPO_DIR, pkg)],
                                                  cwd='/opt/numix')
                        elif 'cnchi-dev' == pkg:
                            logger.info('Copying cnchi-dev source file into build directory.')
                            shutil.copy('/srv/antergos.org/cnchi.tar', os.path.join(REPO_DIR, pkg))
                        elif 'cnchi' == pkg:
                            subprocess.check_call(['git', 'clone', 'http://github.com/antergos/cnchi.git', pkg],
                                                  cwd='/opt/antergos-packages/cnchi')
                            get_latest_translations()

                    except Exception as err:
                        logger.error(err)

            pkgobj = package(pkg, db)
            version = pkgobj.get_version()
            if not version:
                db.lrem('queue', 0, 'cnchi-dev')
                continue
            logger.info('Updating pkgver in database for %s to %s' % (pkgobj.name, version))
            db.set('building', 'Updating pkgver in databse for %s to %s' % (pkgobj.name, version))
            depends = pkgobj.get_deps()
            p, d = depends
            logger.info('@@-build_pkg.py-@@ 189 | depends before topsort: %s, %s' % (p, d))

            if len(the_queue) > 1:
                all_deps.append(depends)
        logger.info('@@-build_pkg.py-@@ 189 | all_deps before topsort: %s' % all_deps)
        return all_deps


def handle_hook(first=False, last=False):
    db.set('idle', 'False')
    iso_flag = db.get('isoFlag')
    # pull_from = db.get('pullFrom')
    pull_from = 'antergos'
    packages = db.lrange('queue', 0, -1)

    if not os.path.exists(REPO_DIR):
        try:
            subprocess.check_call(['git', 'clone', 'http://github.com/antergos/antergos-packages.git'], cwd='/opt')
        except subprocess.CalledProcessError as err:
            logger.error(err)
    else:
        try:
            subprocess.check_call(['git', 'pull'], cwd=REPO_DIR)
        except subprocess.CalledProcessError as err:
            logger.error(err)

    try:
        subprocess.check_output(['find', '/opt/antergos-packages', '-type', '-f', '-exec', 'chmod a+rw {} \;'],
                                cwd='/opt')
        subprocess.check_output(['find', '/opt/antergos-packages', '-type', '-d', '-exec', 'chmod 777 {} \;'],
                                cwd='/opt')
    except subprocess.CalledProcessError as err:
        logger.error(err.output)

    if iso_flag == 'True':
        db.set('building', 'Building docker image.')
        db.set('isoBuilding', 'True')
        image = docker_utils.maybe_build_mkarchiso()
        db.set('isoFlag', 'False')
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
                pkgobj = package(iso_name + arch, db)
                pkgobj.save_to_db('version', version)
            build_iso()
        db.set('isoBuilding', 'False')
        db.set('isoMinimal', 'False')
        db.set('idle', "True")
        return True

    elif first and iso_flag == 'False':
        db.set('building', 'Building docker image.')
        image = docker_utils.maybe_build_base_devel()
        if not image:
            return False
        gh_repo = 'http://github.com/' + pull_from + '/antergos-packages.git'
        logger.info('Pulling changes from github.')
        db.set('building', 'Pulling PKGBUILD changes from github.')
        if os.path.exists(REPO_DIR):
            shutil.rmtree(REPO_DIR)
        try:
            subprocess.check_call(['git', 'clone', gh_repo], cwd='/opt')
        except subprocess.CalledProcessError as err:
            logger.error(err.output)
            return False

        logger.info('Checking database for packages.')
        db.set('building', 'Checking database for queued packages')
        subprocess.call(['chmod', '-R', '777', 'antergos-packages'], cwd='/opt')

        all_deps = process_package_queue(packages)

        logger.info('All queued packages are in the database, checking deps to determine build order.')
        db.set('building', 'Determining build order by sorting package depends')
        if len(all_deps) > 1:
            topsort = check_deps(all_deps)
            logger.info('@@-build_pkg.py-@@ 254 | depends AFTER topsort: %s' % topsort)
            logger.info('@@-build_pkg.py-@@ 255 | queue before regen: %s' % packages)
            check = []
            db.delete('queue')
            for p in topsort:
                logger.info('@@-build_pkg.py-@@ 259 | p in topsort: %s' % p)
                db.rpush('queue', p)
                check.append(p)
            logger.debug('@@-build_pkg.py-@@ | The Queue After TopSort -> ' + ', '.join(check))

        logger.info('Check deps complete. Starting build_pkgs')
        db.set('building', 'Check deps complete. Starting build container.')

    if iso_flag == 'False' and len(packages) > 0:
        pack = db.lpop('queue')
        if pack and pack is not None and pack != '':
            pkgobj = package(pack, db)
        else:
            return False

        rqjob = get_current_job(db)
        rqjob.meta['is_first'] = first
        rqjob.meta['is_last'] = last
        rqjob.meta['package'] = pkgobj.name
        rqjob.save()

        built = build_pkgs(last, pkgobj)
        # TODO: Move this into its own method
        if built:
            completed = db.lrange('completed', 0, -1)
            failed = db.lrange('failed', 0, -1)
            success = len([x for x in pkgobj.builds if x in completed])
            failure = len([x for x in pkgobj.builds if x in failed])
            total = len(pkgobj.builds)
            if total > 0:
                if success > 0:
                    success = 100 * success / total
                else:
                    success = 0
                if failure > 0:
                    failure = 100 * failure / total
                else:
                    failure = 0
                pkgobj.save_to_db('success_rate', success)
                pkgobj.save_to_db('failure_rate', failure)
    if last:
        try:
            shutil.rmtree('/opt/antergos-packages')
        except Exception:
            pass
        db.set('idle', "True")
        db.set('building', 'Idle')
        db.set('container', '')
        db.set('building_num', '')
        db.set('building_start', '')
        logger.info('All builds completed.')


def update_main_repo(pkg=None, rev_result=None, this_log=None):
    if pkg and rev_result:
        run_docker_clean()
        repo = 'antergos'
        repodir = 'main'
        if rev_result == '2':
            rev_result = 'passed'
        elif rev_result == '3':
            rev_result = 'failed'
        elif rev_result == 'staging':
            rev_result = None
            repo = 'antergos-staging'
            repodir = 'staging'
        else:
            logger.error('[UPDATE REPO FAILED]')
            return
        db.set('idle', 'False')
        result = '/tmp/result'
        if os.path.exists(result):
            shutil.rmtree(result)
        os.mkdir(result, 0o777)
        command = "/makepkg/build.sh"
        pkgenv = ["_PKGNAME=%s" % pkg, "_RESULT=%s" % rev_result, "_UPDREPO=True", "_REPO=%s" % repo,
                  "_REPO_DIR=%s" % repodir]
        db.set('building', 'Updating repo database.')
        container = None
        run_docker_clean("update_repo")
        try:
            container = doc.create_container("antergos/makepkg", command=command,
                                             name="update_repo", environment=pkgenv,
                                             volumes=['/makepkg', '/root/.gnupg', '/main', '/result', '/staging'])
            db.set('update_repo_container', container.get('Id'))
            doc.start(container, binds={
                DOC_DIR:
                    {
                        'bind': '/makepkg',
                        'ro': True
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
            }, privileged=True)
            if this_log is None:
                this_log = 'repo_update_log'
                upd_repo = False
            else:
                upd_repo = True
            cont = db.get('update_repo_container')
            stream_process = Process(target=publish_build_ouput, args=(cont, this_log, upd_repo))
            stream_process.start()
            doc.wait(container)
            try:
                cache_keys = db.scan_iter(match='cache:*', count=100)
                for key in cache_keys:
                    logger.info('[REPO COUNT CACHE KEY FOUND]: %s' % key)
                    db.delete(key)
            except Exception as err:
                logger.error(err)
        except Exception as err:
            logger.error('Start container failed. Error Msg: %s' % err)

        doc.remove_container(container)
        db.set('idle', 'True')
        db.set('building', 'Idle')
        db.delete('repo-count-staging')
        db.delete('repo-count-main')


def publish_build_ouput(container=None, this_log=None, upd_repo=False):
    if not container or not this_log:
        logger.error('Unable to publish build output. (Container is None)')
        return
    proc = subprocess.Popen(['docker', 'logs', '--follow', container], stdout=subprocess.PIPE)
    output = iter(proc.stdout.readline, '')
    nodup = set()
    content = []
    for line in output:
        time.sleep(.10)
        if not line or line == '' or "Antergos Automated Build Server" in line or "--passphrase" in line \
                or 'makepkg]# PS1="' in line:
            continue
        line = line.rstrip()
        # if db.get('isoBuilding') == "True":
        # line = line[15:]
        end = line[25:]
        if end not in nodup:
            nodup.add(end)
            line = re.sub('(?<=[\w\d])\'(?=[\w\d]+)', '', line)
            # if line[-1:] == "'" or line[-1:] == '"':
            #     line = line[:-1]
            line = re.sub('(?<=[\w\d])\' (?=[\w\d]+)', ' ', line)
            # bad_date = re.search(r"\d{4}-\d{2}-[\d\w:\.]+Z{1}", line)
            # if bad_date:
            # line = line.replace(bad_date.group(0), datetime.datetime.now().strftime("%m/%d/%Y %I:%M%p"))
            line = '[%s]: %s' % (datetime.datetime.now().strftime("%m/%d/%Y %I:%M%p"), line)
            if len(line) > 120:
                line = truncate_middle(line, 120)
            content.append(line)
            db.publish('build-output', line)
            db.set('build_log_last_line', line)

    if upd_repo:
        db.publish('build-output', 'ENDOFLOG')
    content = '\n '.join(content)

    log_exists = db.hget('%s:content' % this_log, 'content')

    pretty = highlight(content, BashLexer(), HtmlFormatter(style='monokai', linenos='inline',
                                                           prestyles="background:#272822;color:#fff;",
                                                           encoding='utf-8'))
    if log_exists and log_exists != '':
        pretty = log_exists + pretty
    db.hset('%s:content' % this_log, 'content', pretty.decode('utf-8'))


def get_latest_translations():
    # Get translations for Cnchi
    trans_dir = "/opt/cnchi-translations/"
    trans_files_dir = os.path.join(trans_dir, "translations/antergos.cnchi")
    dest_dir = '/opt/antergos-packages/cnchi/cnchi/po'
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

    subprocess.check_call(['tar', '-cf', '/tmp/Cnchi-master.tar', '-C', '/opt/antergos-packages/cnchi', 'cnchi'])
    shutil.copy('/tmp/Cnchi-master.tar', '/opt/antergos-packages/cnchi')


def build_pkgs(last=False, pkg_info=None):
    if pkg_info is None:
        return False
    # Create our tmp directories
    result = os.path.join("/tmp", "result")
    cache = os.path.join("/var/tmp", "pkg_cache")
    for d in [result, cache]:
        if os.path.exists(d) and 'result' in d:
            shutil.rmtree(d)
            os.mkdir(d, 0o777)
        elif os.path.exists(d) and 'pkg_cache' in d:
            logger.info('@@-build_pkg.py-@@ 476 | Cleaning package cache....')
            db.set('building', 'Cleaning package cache.')
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
                    if os.stat(pfile).st_mtime < (dtime - 7 * 86400) or db.sismember('pkgs:all', pname):
                        remove(pfile)
        else:
            os.mkdir(d, 0o777)
    # pkglist = db.lrange('queue', 0, -1)
    pkglist1 = ['1']
    in_dir_last = len([name for name in os.listdir(result)])
    db.set('pkg_count', in_dir_last)
    for i in range(len(pkglist1)):
        pkg = pkg_info.name
        if pkg and pkg is not None and pkg != '':
            db.set('building', 'Building %s with makepkg' % pkg)
            failed = False
            logger.info('Building %s' % pkg)
            version = pkg_info.version
            db.incr('build_number')
            dt = datetime.datetime.now().strftime("%m/%d/%Y %I:%M%p")
            build_id = db.get('build_number')
            db.set('building_num', build_id)
            tlmsg = 'Build <a href="/build/%s">%s</a> for <strong>%s</strong> started.' % (build_id, build_id, pkg)
            logconf.new_timeline_event(tlmsg, '3')
            this_log = 'build_log:%s' % build_id
            db.hset('now_building', 'build_id', build_id)
            db.hset('now_building', 'key', this_log)
            db.hset('now_building', 'pkg', pkg)
            db.set(this_log, True)
            db.rpush('pkg:%s:build_logs' % pkg, build_id)
            db.set('%s:start' % this_log, dt)
            db.set('building_start', dt)
            db.set('%s:pkg' % this_log, pkg)
            db.set('%s:version' % this_log, version)
            pkg_deps = pkg_info.depends or []
            pkg_deps_str = ' '.join(pkg_deps)
            logger.info('pkg_deps_str is %s' % pkg_deps_str)
            run_docker_clean(pkg)
            logger.info('@@-build_pkg.py-@@ | AUTOSUMS is %s' % pkg_info.autosum)
            if pkg_info is not None and pkg_info.autosum == "True":
                build_env = ['_AUTOSUMS=True']
            else:
                build_env = ['_AUTOSUMS=False']
            if '/cinnamon' in pkg_info.path:
                build_env.append('_ALEXPKG=True')
            else:
                build_env.append('_ALEXPKG=False')
            logger.info('@@-build_pkg.py-@@ | build_env is %s' % build_env)
            try:
                container = doc.create_container("antergos/makepkg", command="/makepkg/build.sh " + pkg_deps_str,
                                                 name=pkg, volumes=['/var/cache/pacman', '/makepkg', '/repo', '/pkg',
                                                                    '/root/.gnupg', '/staging', '/32bit', '/32build',
                                                                    '/result'], environment=build_env, cpuset='0-3')
                if container.get('Warnings') and container.get('Warnings') != '':
                    logger.error(container.get('Warnings'))
            except Exception as err:
                logger.error('Create container failed. Error Msg: %s' % err)
                failed = True
                continue
            db.set('container', container.get('Id'))
            dirs = ['/var/tmp/32build', '/var/tmp/32bit']
            for d in dirs:
                if os.path.exists(d):
                    shutil.rmtree(d)
                os.mkdir(d, 0o777)
            try:
                doc.start(container, binds={
                    cache:
                        {
                            'bind': '/var/cache/pacman',
                            'ro': False
                        },
                    DOC_DIR:
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
                    pkg_info.path:
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
                }, privileged=True)
                cont = db.get('container')
                stream_process = Process(target=publish_build_ouput, args=(cont, this_log))
                stream_process.start()
                result = doc.wait(cont)
                if result is not 0:
                    failed = True
                    db.set('build_failed', "True")
                    logger.error('[CONTAINER EXIT CODE] Container %s exited. Return code was %s' % (pkg, result))
                else:
                    logger.info('[CONTAINER EXIT CODE] Container %s exited. Return code was %s' % (pkg, result))
                    db.set('build_failed', "False")
            except Exception as err:
                logger.error('Start container failed. Error Msg: %s' % err)
                failed = True
                continue
            # db.publish('build-ouput', 'ENDOFLOG')
            # stream = doc.logs(container, stdout=True, stderr=True, timestamps=True)
            # log_stream = stream.split('\n')
            # db_filter_and_add(log_stream, this_log)

            # in_dir = len([name for name in os.listdir(result)])
            # last_count = int(db.get('pkg_count'))
            # logger.info('last count is %s %s' % (last_count, type(last_count)))
            # logger.info('in_dir is %s %s' % (in_dir, type(in_dir)))
            pkgs2sign = None
            if not failed:
                db.publish('build-output', 'Signing package..')
                pkgs2sign = glob.glob(
                    '/srv/antergos.info/repo/iso/testing/uefi/antergos-staging/x86_64/%s-***.xz' % pkg)
                pkgs2sign32 = glob.glob(
                    '/srv/antergos.info/repo/iso/testing/uefi/antergos-staging/i686/%s-***.xz' % pkg)
                pkgs2sign = pkgs2sign + pkgs2sign32
                logger.info('[PKGS TO SIGN] %s' % pkgs2sign)
                if pkgs2sign is not None and pkgs2sign != []:
                    try_sign = sign_pkgs.batch_sign(pkgs2sign)
                else:
                    try_sign = False
                if try_sign:
                    db.publish('build-output', 'Signature created successfully for %s' % pkg)
                    logger.info('[SIGN PKG] Signature created successfully for %s' % pkg)
                    db.publish('build-output', 'Updating staging repo database..')
                    update_main_repo(pkg, 'staging', this_log)
                else:
                    failed = True

            if not failed:
                db.publish('build-output', 'Build completed successfully!')
                tlmsg = 'Build <a href="/build/%s">%s</a> for <strong>%s</strong> completed.' % (
                    build_id, build_id, pkg)
                logconf.new_timeline_event(tlmsg, '4')
                # db.incr('pkg_count', (in_dir - last_count))
                db.rpush('completed', build_id)
                db.set('%s:result' % this_log, 'completed')
                db.set('%s:review_stat' % this_log, '1')
            else:
                logger.error('No package found after container exit.')
                tlmsg = 'Build <a href="/build/%s">%s</a> for <strong>%s</strong> failed.' % (build_id, build_id, pkg)
                logconf.new_timeline_event(tlmsg, '5')
                if pkgs2sign is not None:
                    for p in pkgs2sign:
                        remove(p)
                        remove(p + '.sig')
                db.set('%s:result' % this_log, 'failed')
                db.rpush('failed', build_id)
                log_string = db.hget('%s:content' % this_log, 'content')
                if log_string and log_string != '':
                    pretty = highlight(log_string, BashLexer(), HtmlFormatter(style='monokai', linenos='inline',
                                                                              prestyles="background:#272822;color:#fff;",
                                                                              encoding='utf-8'))
                    db.hset('%s:content' % this_log, 'content', pretty.decode('utf-8'))
            # doc.remove_container(container)
            end = datetime.datetime.now().strftime("%m/%d/%Y %I:%M%p")
            db.set('%s:end' % this_log, end)
            db.set('container', '')
            db.set('building_num', '')
            db.set('building_start', '')
            db.delete('repo-count-staging')
            db.delete('repo-count-main')

            if last:
                db.set('idle', "True")
                db.set('building', 'Idle')
                # for f in [result, cache, '/opt/antergos-packages', '/var/tmp/32bit', '/var/tmp/32build']:
                #    remove(f)
            else:
                db.set('building', 'Starting next build in queue...')

            if not failed:
                try:
                    cache_keys = db.scan_iter(match='cache:*', count=100)
                    for key in cache_keys:
                        logger.info('[REPO COUNT CACHE KEY FOUND]: %s' % key)
                        db.delete(key)
                except Exception as err:
                    logger.error(err)
                return True

    logger.info('Build completed. Repo has been updated.')


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
        pkgobj = package(iso_name + arch, db)
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
