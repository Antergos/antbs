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
import importlib
import __builtin__

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), os.path.pardir)))
from src.redis_connection import db
import docker
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

logger = logconf.logger
SRC_DIR = os.path.dirname(__file__) or '.'
BASE_DIR = os.path.split(os.path.abspath(SRC_DIR))[0]
DOC_DIR = os.path.join(BASE_DIR, 'docker_files')
REPO_DIR = "/opt/antergos-packages"
package = pkgclass.Package


# Initiate communication with docker_files daemon
try:
    doc = docker.Client(base_url='unix://var/run/docker.sock')
    # doc.build(path=DOC_DIR, tag="arch-devel", quiet=False, timeout=None)
except Exception as err:
    logger.error("Cant connect to Docker daemon. Error msg: %s", err)


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

def get_pkgver(pkgobj):
    pkg = pkgobj.name
    pbfile = os.path.join(REPO_DIR, pkg, 'PKGBUILD')
    pkgver = pkgobj.get_from_pkgbuild('pkgver', pbfile)
    if pkg == "cnchi-dev" and pkgver[-1] != "0":
        return False
    old_pkgver = pkgobj.pkgver
    pkgobj.save_to_db('pkgver', pkgver)
    epoch = pkgobj.get_from_pkgbuild('epoch', pbfile)
    pkgrel = pkgobj.get_from_pkgbuild('pkgrel', pbfile)
    if epoch and epoch != '' and epoch is not None:
        pkgver = epoch + ':' + pkgver
    if pkgrel and pkgrel != '' and pkgrel is not None:
        pbver = pkgver + '-' + pkgrel
        old_pkgrel = pkgrel
        if pkgver == old_pkgver:
            pkgrel = str(int(pkgrel) + 1)
        elif pkgver != pkgobj.pkgver and pkgobj.push_version == "True":
            pkgrel = 1
        else:
            pass

        pkgobj.update_and_push_github('pkgrel', old_pkgrel, pkgrel)
        pkgobj.save_to_db('pkgrel', pkgrel)

    pkgver = pkgver + '-' + str(pkgrel)
    if pkgver and pkgver != '' and pkgver is not None:
        pkgobj.save_to_db('version', pkgver)
        logger.info('@@-build_pkg.py-@@ | pkgver is %s' % pkgver)
    else:
        pkgver = pkgobj.get_from_db('version')
    del pkgobj
    return pkgver

def get_deps(pkg):
    depends = []
    pbfile = os.path.join(REPO_DIR, pkg, 'PKGBUILD')
    with open(pbfile) as PKGBUILD:
        for line in PKGBUILD:
            if line.startswith("depends") or line.startswith("makedepends"):
                dep_line = line.split('=', 1)
                dep_str = dep_line[1].rstrip()
                for c in ['(', ')', "'", '"']:
                    dep_str = dep_str.replace(c, '')
                deps = dep_str.split(' ')
                for dep in deps:
                    depends.append(dep)
            else:
                continue
    return depends


def check_deps(packages):
    # TODO: Come up with more versitile solution. This will only help in the most basic situations.
    pkgs = packages
    queued = db.lrange('queue', 0, -1)
    matches = []
    for pkg in pkgs:
        deps = db.lrange('pkg:%s:deps' % pkg, 0, -1)
        if set(deps).intersection(set(queued)):
            logger.info('CHECK DEPS: %s added to matches.' % pkg)
            matches.append(pkg)
            continue
    return set(matches)


def handle_hook(first=False, last=False):
    db.set('idle', 'False')
    iso_flag = db.get('isoFlag')
    #pull_from = db.get('pullFrom')
    pull_from = 'antergos'

    if iso_flag == 'True':
        db.set('isoBuilding', 'True')
        db.lrem('queue', 0, 'antergos-iso')
        archs = ['x86_64', 'i686']
        for arch in archs:
            db.rpush('queue', 'antergos-iso-%s' % arch)
            version = datetime.datetime.now().strftime('%Y.%m.%d')
            if not db.exists('pkg:antergos-iso-%s' % arch):
                db.set('pkg:antergos-iso-%s' % arch, True)
                db.set('pkg:antergos-iso-%s:name' % arch, 'antergos-iso-%s' % arch)
            db.set('pkg:antergos-iso-%s:version' % arch, version)
        build_iso()
        db.set('isoFlag', 'False')
        db.set('isoBuilding', 'False')
        return True

    elif first:
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

        # Check database to see if packages exist and add them if necessary.
        packages = db.lrange('queue', 0, -1)
        logger.info('Checking database for packages.')
        db.set('building', 'Checking database for queued packages')
        nxsq = 'numix-icon-theme-square'

        subprocess.call(['chmod', '-R', '777', 'antergos-packages'], cwd='/opt')
        for pack in packages:
            # if 'numix-icon-theme' == package:
            # logger.info('cloning repo for %s' % package)
            # subprocess.check_call(['git', 'clone', '/var/repo/NX', nxsq],
            # cwd='/opt/antergos-packages/numix-icon-theme')
            # subprocess.check_call(['tar', '-cf', nxsq + '.tar', nxsq],
            #                           cwd='/opt/antergos-packages/numix-icon-theme')
            try:
                if 'numix-icon-theme' == pack:
                    subprocess.call(['git', 'clone', '/var/repo/NX', 'numix-icon-theme'],
                                    cwd='/opt/antergos-packages/numix-icon-theme')
                    logger.info('Creating tar archive for %s' % pack)
                    subprocess.check_call(['tar', '-cf', 'numix-icon-theme.tar', 'numix-icon-theme'],
                                          cwd='/opt/antergos-packages/numix-icon-theme')
                elif 'numix-icon-theme-square' == pack:
                    subprocess.call(['git', 'clone', '/var/repo/NXSQ', nxsq],
                                    cwd='/opt/antergos-packages/numix-icon-theme-square')
                    logger.info('Creating tar archive for %s' % pack)
                    subprocess.check_call(['tar', '-cf', nxsq + '.tar', nxsq],
                                          cwd='/opt/antergos-packages/numix-icon-theme-square')
                elif 'numix-icon-theme-square-kde' == pack:
                    subprocess.call(['git', 'clone', '/var/repo/NXSQ', nxsq + '-kde'],
                                    cwd='/opt/antergos-packages/numix-icon-theme-square-kde')
                    logger.info('Creating tar archive for %s' % pack)
                    subprocess.check_call(['tar', '-cf', nxsq + '-kde.tar', nxsq + '-kde'],
                                          cwd='/opt/antergos-packages/numix-icon-theme-square-kde')
                elif 'numix-frost-themes' == pack:
                    logger.info('Copying numix-frost source file into build directory.')
                    subprocess.check_call(
                        ['cp', '/opt/numix/numix-frost.zip', os.path.join(REPO_DIR, 'numix-frost-themes')],
                        cwd='/opt/numix')
                elif 'cnchi-dev' == pack:
                    logger.info('Copying cnchi-dev source file into build directory.')
                    shutil.copy('/srv/antergos.org/cnchi.tar', os.path.join(REPO_DIR, pack))
            except subprocess.CalledProcessError as err:
                logger.error(err.output)

            pack = package(pack, db)
            version = get_pkgver(pack)
            if not version:
                return False
            depends = get_deps(pack.name)
            # if not db.exists('pkg:%s' % package):
            #     logger.info('%s not found in database, adding entry..' % package)
            #     db.set('building', '%s not found in database, adding entry..' % package)
            #     db.set('pkg:%s' % package, True)
            #     db.set('pkg:%s:name' % package, package)
            if depends is not None:
                db.delete('pkg:%s:deps' % pack.name)
                for dep in depends:
                    db.rpush('pkg:%s:deps' % pack.name, dep)
            logger.info('Updating pkgver in databse for %s to %s' % (pack.name, version))
            db.set('building', 'Updating pkgver in databse for %s to %s' % (pack.name, version))
            #pack.save_to_db('version', version)

            logger.info('All queued packages are in the database, checking deps to determine build order.')
            db.set('building', 'Determining build order based on pkg dependancies')
            check = check_deps(packages)
            if len(check) > 0:
                for c in check:
                    logger.info('%s depends on a pkg in this build. Moving it to the end of the queue.' % c)
                    db.lrem('queue', 0, c)
                    db.rpush('queue', c)
            logger.info('Check deps complete. Starting build_pkgs')
            db.set('building', 'Check deps complete. Starting build container.')
            del pack
    try:
        subprocess.check_call(['git', 'pull'], cwd='/opt/antergos-packages')
    except subprocess.CalledProcessError as err:
        logger.error(err.output)

    logger.info('[FIRST IS SET]: %s' % first)
    logger.info('[LAST IS SET]: %s' % last)
    if iso_flag == 'False':
        build_pkgs(last)


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
        pkgenv = ["_PKGNAME=%s" % pkg, "_RESULT=%s" % rev_result, "_UPDREPO=True", "_REPO=%s" % repo, "_REPO_DIR=%s" % repodir]
        db.set('building', 'Updating repo database.')
        container = None
        run_docker_clean("update_repo")
        try:
            container = doc.create_container("antergos/makepkg", command=command,
                                             name="update_repo", environment=pkgenv,
                                             volumes=['/makepkg', '/root/.gnupg', '/main', '/result'])
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
        except Exception as err:
            logger.error('Start container failed. Error Msg: %s' % err)

        doc.remove_container(container)
        db.set('idle', 'True')
        db.set('building', 'Idle')
        db.delete('repo-count-staging')
        db.delete('repo-count-main')


def db_filter_and_add(output=None, this_log=None):
    if output is None or this_log is None:
        return
    nodup = set()
    part2 = None
    filtered = []
    for line in output:
        if not line or line == '' or "Antergos Automated Build Server" in line or "--passphrase" in line:
            continue
        line = line.rstrip()
        end = line[20:]
        if end not in nodup:
            nodup.add(end)
            line = re.sub('(?<=[\w\d])\'(?=[\w\d]+)', '', line)
            bad_date = re.search(r"\d{4}-\d{2}-[\d\w:\.]+Z{1}", line)
            if bad_date:
                line = line.replace(bad_date.group(0), datetime.datetime.now().strftime("%m/%d/%Y %I:%M%p"))
            if len(line) > 210:
                part1 = line[:210]
                part2 = line[211:]
                filtered.append(part1)
                filtered.append(datetime.datetime.now().strftime("%m/%d/%Y %I:%M%p") + ' ' + part2)
                # db.rpush('%s:content' % this_log, part1)
            else:
                filtered.append(line)

    filtered_string = '\n '.join(filtered)
    # db.rpush('%s:content' % this_log, line)
    # filtered_string = filtered_string.decode('utf-8')
    pretty = highlight(filtered_string, BashLexer(), HtmlFormatter(style='monokai', linenos='inline',
                                                                   prestyles="background:#272822;color:#fff;",
                                                                   encoding='utf-8'))
    db.set('%s:content' % this_log, pretty.decode('utf-8'))


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
        end = line[20:]
        if end not in nodup:
            nodup.add(end)
            line = re.sub('(?<=[\w\d])\'(?=[\w\d]+)', '', line)
            if line[-1:] == "'" or line[-1:] == '"':
                line = line[:-1]
            line = re.sub('(?<=[\w\d])\' (?=[\w\d]+)', ' ', line)
            # bad_date = re.search(r"\d{4}-\d{2}-[\d\w:\.]+Z{1}", line)
            # if bad_date:
            # line = line.replace(bad_date.group(0), datetime.datetime.now().strftime("%m/%d/%Y %I:%M%p"))
            line = '[%s]: %s' % (datetime.datetime.now().strftime("%m/%d/%Y %I:%M%p"), line)
            content.append(line)
            db.publish('build-output', line)
            db.set('build_log_last_line', line)

    if upd_repo:
        db.publish('build-output', 'ENDOFLOG')
    content = '\n '.join(content)

    log_exists = db.get('%s:content' % this_log)
    if log_exists and log_exists != '':
        content = content + log_exists
        pretty = highlight(content, BashLexer(), HtmlFormatter(style='monokai', linenos='inline',
                                                               prestyles="background:#272822;color:#fff;",
                                                               encoding='utf-8'))
        db.set('%s:content' % this_log, pretty.decode('utf-8'))
    else:
        db.set('%s:content' % this_log, content)


def build_pkgs(last=False):
    # Create our tmp directories
    result = os.path.join("/tmp", "result")
    cache = os.path.join("/tmp", "pkg_cache")
    for d in [result, cache]:
        if os.path.exists(d):
            shutil.rmtree(d)
        os.mkdir(d, 0o777)
    pkglist = db.lrange('queue', 0, -1)
    pkglist1 = ['1']
    in_dir_last = len([name for name in os.listdir(result)])
    db.set('pkg_count', in_dir_last)
    for i in range(len(pkglist1)):
        pkg = db.lpop('queue')
        db.set('now_building', pkg)
        db.set('building', 'Building %s with makepkg' % pkg)
        failed = False
        if pkg is None or pkg == '':
            continue
        logger.info('Building %s' % pkg)
        version = db.get('pkg:%s:version' % pkg)
        db.incr('build_number')
        dt = datetime.datetime.now().strftime("%m/%d/%Y %I:%M%p")
        build_id = db.get('build_number')
        db.set('building_num', build_id)
        this_log = 'build_log:%s' % build_id
        db.set(this_log, True)
        db.rpush('pkg:%s:build_logs' % pkg, build_id)
        db.set('%s:start' % this_log, dt)
        db.set('building_start', dt)
        db.set('%s:pkg' % this_log, pkg)
        db.set('%s:version' % this_log, version)
        pkgdir = os.path.join(REPO_DIR, pkg)
        pkg_deps = db.lrange('pkg:%s:deps' % pkg, 0, -1)
        pkg_deps_str = ' '.join(pkg_deps)
        logger.info('pkg_deps_str is %s' % pkg_deps_str)
        run_docker_clean(pkg)
        try:
            container = doc.create_container("antergos/makepkg", command="/makepkg/build.sh " + pkg_deps_str,
                                             name=pkg, volumes=['/var/cache/pacman', '/makepkg', '/repo', '/pkg',
                                                                '/root/.gnupg', '/staging', '/32bit', '/32build',
                                                                '/result'])
        except Exception as err:
            logger.error('Create container failed. Error Msg: %s' % err)
            failed = True
            continue
        db.set('container', container.get('Id'))
        dirs = ['/tmp/32build', '/tmp/32bit']
        for d in dirs:
            if not os.path.exists(d):
                os.mkdir(d)
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
                '/srv/antergos.info/repo/iso/testing/uefi/antergos-staging/':
                    {
                        'bind': '/staging',
                        'ro': False
                    },
                '/srv/antergos.info/repo/antergos/':
                    {
                        'bind': '/main',
                        'ro': False
                    },
                pkgdir:
                    {
                        'bind': '/pkg',
                        'ro': False
                    },
                '/root/.gnupg':
                    {
                        'bind': '/root/.gnupg',
                        'ro': False
                    },
                '/tmp/32bit':
                    {
                        'bind': '/32bit',
                        'ro': False
                    },
                '/tmp/32build':
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
            result = doc.wait(container)
            if result is not 0:
                failed = True
                logger.error('[CONTAINER EXIT CODE] Container %s exited. Return code was %s' % (pkg, result))
            else:
                logger.info('[CONTAINER EXIT CODE] Container %s exited. Return code was %s' % (pkg, result))
        except Exception as err:
            logger.error('Start container failed. Error Msg: %s' % err)
            failed = True
            continue
        # db.publish('build-ouput', 'ENDOFLOG')
        # stream = doc.logs(container, stdout=True, stderr=True, timestamps=True)
        # log_stream = stream.split('\n')
        # db_filter_and_add(log_stream, this_log)

        #in_dir = len([name for name in os.listdir(result)])
        #last_count = int(db.get('pkg_count'))
        #logger.info('last count is %s %s' % (last_count, type(last_count)))
        #logger.info('in_dir is %s %s' % (in_dir, type(in_dir)))
        pkgs2sign = None
        if not failed:
            db.publish('build-output', 'Signing package..')
            pkgs2sign = glob.glob('/srv/antergos.info/repo/iso/testing/uefi/antergos-staging/x86_64/%s-**.xz' % pkg)
            pkgs2sign32 = glob.glob('/srv/antergos.info/repo/iso/testing/uefi/antergos-staging/i686/%s-**.xz' % pkg)
            pkgs2sign = pkgs2sign + pkgs2sign32
            logger.info('[PKGS TO SIGN] %s' % pkgs2sign)
            if pkgs2sign is not None:
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
                log_string = db.get('%s:content' % this_log)
                if log_string and log_string != '':
                    pretty = highlight(log_string, BashLexer(), HtmlFormatter(style='monokai', linenos='inline',
                                                                           prestyles="background:#272822;color:#fff;",
                                                                           encoding='utf-8'))
                    db.set('%s:content' % this_log, pretty.decode('utf-8'))

        if not failed:
            db.publish('build-output', 'Build completed successfully!')
            #db.incr('pkg_count', (in_dir - last_count))
            db.rpush('completed', build_id)
            db.set('%s:result' % this_log, 'completed')
            db.set('%s:review_stat' % this_log, '1')
        else:
            logger.error('No package found after container exit.')
            if pkgs2sign is not None:
                for p in pkgs2sign:
                    remove(p)
            db.set('%s:result' % this_log, 'failed')
            db.rpush('failed', build_id)
        doc.remove_container(container)
        end = datetime.datetime.now().strftime("%m/%d/%Y %I:%M%p")
        db.set('%s:end' % this_log, end)
        try:
            db_caches = db.scan_iter(match='*_cache*', count=3)
            for db_cache in db_caches:
                logger.info('[REPO COUNT CACHE KEY FOUND]: %s' % db_cache)
                db.delete(db_cache)
        except Exception as err:
            logger.error(err)

        db.set('container', '')
        db.set('building_num', '')
        db.set('building_start', '')
        db.delete('repo-count-staging')
        db.delete('repo-count-main')

        if last:
            db.set('idle', "True")
            db.set('building', 'Idle')
            for f in [result, cache, '/opt/antergos-packages', '/tmp/32bit', '/tmp/32build']:
                remove(f)
        else:
            db.set('building', 'Starting next build in queue...')

        if not failed:
            return True

    logger.info('Build completed. Repo has been updated.')


def build_iso():
    iso_arch = ['x86_64', 'i686']
    in_dir_last = len([name for name in os.listdir('/srv/antergos.info/repo/iso/testing')])
    db.set('pkg_count_iso', in_dir_last)
    for arch in iso_arch:
        db.incr('build_number')
        dt = datetime.datetime.now().strftime("%m/%d/%Y %I:%M%p")
        build_id = db.get('build_number')
        this_log = 'build_log:%s' % build_id
        db.set('%s:start' % this_log, dt)
        db.set('building_num', build_id)
        db.set(this_log, True)
        db.set('building_start', dt)
        logger.info('Building antergos-iso-%s' % arch)
        db.set('building', 'Building: antergos-iso-%s' % arch)
        db.lrem('queue', 0, 'antergos-iso-%s' % arch)
        db.set('%s:pkg' % this_log, 'antergos-iso-%s' % arch)
        db.rpush('pkg:antergos-iso-%s:build_logs' % arch, build_id)

        flag = '/srv/antergos.info/repo/iso/testing/.ISO32'
        if arch is 'i686':
            if not os.path.exists(flag):
                open(flag, 'a').close()
        else:
            if os.path.exists(flag):
                os.remove(flag)
        nm = 'antergos-iso-%s' % arch
        # Initiate communication with docker daemon
        run_docker_clean(nm)
        try:
            doc = docker.Client(base_url='unix://var/run/docker.sock', timeout=10)
            iso_container = doc.create_container("antergos/mkarchiso",
                                                 volumes=['/antergos-iso/configs/antergos/out', '/var/run/dbus',
                                                          '/start', '/sys/fs/cgroup'], tty=True,
                                                 name=nm, cpu_shares=512)
            db.set('container', iso_container.get('Id'))
        except Exception as err:
            logger.error("Cant connect to Docker daemon. Error msg: %s", err)
            break

        try:
            doc.start(iso_container, privileged=True, binds={
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
                        'bind': '/antergos-iso/configs/antergos/out',
                        'ro': False
                    },
                '/srv/antergos.info/repo/antergos':
                    {
                        'bind': '/srv/antergos.info/repo/antergos',
                        'ro': True
                    },
                '/sys/fs/cgroup':
                    {
                        'bind': '/sys/fs/cgroup',
                        'ro': True
                    }
            })

            cont = db.get('container')
            stream_process = Process(target=publish_build_ouput, args=(cont, this_log))
            stream_process.start()
            doc.wait(iso_container)

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
            logger.error('antergos-iso-%s not found after container exit.' % arch)
            failed = True
            db.set('%s:result' % this_log, 'failed')
            db.rpush('failed', build_id)
        db.set('idle', 'True')
        db.set('isoFlag', 'False')
        db.set('isoBuilding', 'False')

    try:
        shutil.rmtree('/opt/antergos-packages')
    except Exception:
        pass
    db.set('idle', "True")
    db.set('building', 'Idle')
    db.set('container', '')
    db.set('building_num', '')
    db.set('building_start', '')
    logger.info('All iso builds completed.')







