#!/usr/bin/env python
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
import __builtin__

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), os.path.pardir)))
from src.redis_connection import db
import docker
import subprocess
import logging
import logging.config
import src.logging_config as logconf
import datetime
import shutil
from pygments import highlight
from pygments.lexers import BashLexer
from pygments.formatters import HtmlFormatter
import re
import time
from multiprocessing import Process

logger = logging.getLogger(__name__)
logging.config.dictConfig(logconf.log_config)
SRC_DIR = os.path.dirname(__file__) or '.'
BASE_DIR = os.path.split(os.path.abspath(SRC_DIR))[0]
DOC_DIR = os.path.join(BASE_DIR, 'docker_files')
REPO_DIR = "/opt/antergos-packages"


# Initiate communication with docker_files daemon
try:
    doc = docker.Client(base_url='unix://var/run/docker.sock')
    # doc.build(path=DOC_DIR, tag="arch-devel", quiet=False, timeout=None)
except Exception as err:
    logger.error("Cant connect to Docker daemon. Error msg: %s", err)


def get_pkgver(package):
    pkg = package
    pkgver = None
    pbfile = os.path.join(REPO_DIR, pkg, 'PKGBUILD')
    pkgdir = os.path.join(REPO_DIR, pkg)
    logger.info('pkgdir is %s' % pkgdir)
    parse = open(pbfile).read()
    if 'git+' in parse or 'numix-icon-theme-square' in pkg:
        epoch = 'epoch=' in parse
        logger.info('parse is %s' % parse)
        giturl = re.search('(?<=git\\+).+(?="|\')', parse)
        if not giturl:
            giturl = '/var/repo/NXSQ'
        else:
            giturl = giturl.group(0)
        subprocess.check_call(['git', 'clone', giturl, pkg], cwd=pkgdir)
        rev = subprocess.check_output(['git', 'rev-list', '--count', 'HEAD'], cwd=os.path.join(pkgdir, pkg))
        short = subprocess.check_output(['git', 'rev-parse', '--short', 'HEAD'], cwd=os.path.join(pkgdir, pkg))
        pkgver = '0.r%s.%s' % (rev, short)
        if pkgver and pkgver != '0.r.':
            if epoch:
                epoch = re.search('(?<=epoch\\=)\d{1,2}', parse)
                pkgver = epoch.group(0) + ':' + pkgver
            logger.info('pkgver is %s' % pkgver)
            return pkgver
    epoch_num = None
    epoch_done = None
    arch_done = None
    pkgver_done = None
    pkgrel_done = None
    with open(pbfile) as PKGBUILD:
        for line in PKGBUILD:
            if line.startswith('arch='):
                if 'i686' in line:
                    shutil.copyfile(pbfile, os.path.join(pkgdir, 'PKGBUILD32'))
                arch_done = True
                if epoch_done and pkgver_done:
                    break
                else:
                    continue
            elif line.startswith('epoch='):
                epoch = line.split('=')
                epoch_num = epoch[1].strip('\n')
                epoch_done = True
                if arch_done and pkgver_done and pkgrel_done:
                    break
                else:
                    continue
            elif line.startswith("pkgver") and not line.startswith("pkgver()"):
                l = line.split('=')
                logger.info('line is %s' % l)
                pkgver = l[1].strip('\n')
                pkgver_done = True
                if epoch_done and arch_done and pkgrel_done:
                    break
                else:
                    continue
            elif line.startswith('pkgrel='):
                pkgrel = line.split('=')
                pkgrel_num = pkgrel[1].strip('\n')
                pkgrel_done = True
                if arch_done and pkgver_done and epoch_done:
                    break
                else:
                    continue

    if epoch_num:
        pkgver = epoch_num + ':' + pkgver
    if pkgrel_num:
        pkgver = pkgver + '-' + pkgrel_num
    else:
        pkgver += '-1'

    return pkgver


def get_deps(package):
    pkg = package
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
    pull_from = db.get('pullFrom')

    if iso_flag == 'True':
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
        db.delete('pkg:antergos-iso:last_commit')
        return

    elif first:
        db.set('pkg_count', '0')
        gh_repo = 'http://github.com/' + pull_from + '/antergos-packages.git'
        logger.info('Pulling changes from github.')
        if os.path.exists(REPO_DIR):
            shutil.rmtree(REPO_DIR)
        try:
            subprocess.check_call(['git', 'clone', gh_repo], cwd='/opt')
        except subprocess.CalledProcessError as err:
            logger.error(err.output)

        # Check database to see if packages exist and add them if necessary.
        packages = db.lrange('queue', 0, -1)
        logger.info('Checking database for packages.')
        nxsq = 'numix-icon-theme-square'

        subprocess.call(['chmod', '-R', '777', 'antergos-packages'], cwd='/opt')
        subprocess.call(['tar', '-cf', nxsq + '.tar', nxsq], cwd='/opt/antergos-packages/numix-icon-theme-square')
        for package in packages:
            # if 'numix-icon-theme' == package:
            # logger.info('cloning repo for %s' % package)
            # subprocess.check_call(['git', 'clone', '/var/repo/NX', nxsq],
            #                           cwd='/opt/antergos-packages/numix-icon-theme')
            #     subprocess.check_call(['tar', '-cf', nxsq + '.tar', nxsq],
            #                           cwd='/opt/antergos-packages/numix-icon-theme')
            version = get_pkgver(package)
            depends = get_deps(package)
            try:
                if 'numix-icon-theme-square' == package:
                    logger.info('Creating tar archive for %s' % package)
                    subprocess.check_call(['tar', '-cf', nxsq + '.tar', nxsq],
                                          cwd='/opt/antergos-packages/numix-icon-theme-square')
                elif 'numix-icon-theme-square-kde' == package:
                    logger.info('Creating tar archive for %s' % package)
                    subprocess.check_call(['tar', '-cf', nxsq + '-kde.tar', nxsq + '-kde'],
                                          cwd='/opt/antergos-packages/numix-icon-theme-square-kde')
                elif 'numix-frost-themes' == package:
                    logger.info('Copying numix-frost source file into build directory.')
                    subprocess.check_call(
                        ['cp', '/opt/numix/numix-frost.zip', os.path.join(REPO_DIR, 'numix-frost-themes')],
                        cwd='/opt/numix')
            except subprocess.CalledProcessError as err:
                logger.error(err.output)

            if not db.exists('pkg:%s' % package):
                logger.info('%s not found in database, adding entry..' % package)
                db.set('pkg:%s' % package, True)
                db.set('pkg:%s:name' % package, package)
            db.delete('pkg:%s:deps' % package)
            for dep in depends:
                db.rpush('pkg:%s:deps' % package, dep)
            logger.info('Updating pkgver in databse for %s to %s' % (package, version))
            db.set('pkg:%s:version' % package, version)
        logger.info('All queued packages are in the database, checking deps to determine build order.')
        check = check_deps(packages)
        if len(check) > 0:
            for c in check:
                logger.info('%s depends on a pkg in this build. Moving it to the end of the queue.' % c)
                db.lrem('queue', 0, c)
                db.rpush('queue', c)
        logger.info('Check deps complete. Starting build_pkgs')
    logger.info('[FIRST IS SET]: %s' % first)
    logger.info('[LAST IS SET]: %s' % last)
    build_pkgs(last)


def update_main_repo(pkg=None):
    if pkg:
        db.set('idle', 'False')
        db.set('building', 'Updating Main Repo')
        result = '/tmp/result'
        if os.path.exists(result):
            shutil.rmtree(result)
        os.mkdir(result, 0o777)
        command = "/makepkg/build.sh update_repo %s" % pkg
        pkgenv = "_PKGNAME=%s" % pkg
        try:
            container = doc.create_container("antergos/makepkg", command=command,
                                             name="update_repo", environment=[pkgenv],
                                             volumes=['/makepkg', '/root/.gnupg', '/main', '/result'])
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
            this_log = 'repo_update_log'
            stream_process = Process(target=publish_build_ouput, args=(container, this_log))
            stream_process.start()
            doc.wait(container)
        except Exception as err:
            logger.error('Start container failed. Error Msg: %s' % err)
            db.set('idle', 'True')
            db.set('building', '')
            return False
            # doc.remove_container(container)
        db.set('idle', 'True')
        db.set('building', '')




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


def publish_build_ouput(container=None, this_log=None):
    if not container or not this_log:
        logging.error('Unable to publish build output. (Container is None)')
        return
    proc = subprocess.Popen(['docker', 'logs', '--follow', container], stdout=subprocess.PIPE)
    output = iter(proc.stdout.readline, '')
    nodup = set()
    content = []
    for line in output:
        time.sleep(.05)
        if not line or line == '' or "Antergos Automated Build Server" in line or "--passphrase" in line \
                or 'makepkg]# PS1="' in line:
            continue
        line = line.rstrip()
        end = line[20:]
        if end not in nodup:
            nodup.add(end)
            line = re.sub('(?<=[\w\d])\'(?=[\w\d]+)', '', line)
            # bad_date = re.search(r"\d{4}-\d{2}-[\d\w:\.]+Z{1}", line)
            #if bad_date:
            #    line = line.replace(bad_date.group(0), datetime.datetime.now().strftime("%m/%d/%Y %I:%M%p"))
            line = '[%s]: %s' % (datetime.datetime.now().strftime("%m/%d/%Y %I:%M%p"), line)
            content.append(line)
            db.publish('build-output', line)

    db.publish('build-output', 'ENDOFLOG')
    content = '\n '.join(content)
    pretty = highlight(content, BashLexer(), HtmlFormatter(style='monokai', linenos='inline',
                                                           prestyles="background:#272822;color:#fff;",
                                                           encoding='utf-8'))
    db.set('%s:content' % this_log, pretty.decode('utf-8'))


def build_pkgs(last=False):
    # Create our tmp directories
    repo = os.path.join("/tmp", "result")
    cache = os.path.join("/tmp", "pkg_cache")
    for d in [repo, cache]:
        if not os.path.exists(d):
            os.mkdir(d, 0o777)
    pkglist = db.lrange('queue', 0, -1)
    pkglist1 = ['1']
    for i in range(len(pkglist1)):
        pkg = db.lpop('queue')
        db.set('building', 'Building: %s' % pkg)
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
        try:
            doc.remove_container(pkg)
        except Exception:
            pass
        try:
            container = doc.create_container("antergos/makepkg", command="/makepkg/build.sh " + pkg_deps_str,
                                             name=pkg,
                                             volumes=['/var/cache/pacman', '/makepkg', '/repo', '/pkg', '/root/.gnupg',
                                                      '/staging', '/32bit', '/32build', '/result'])
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
                        'ro': True
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
                '/tmp/result':
                    {
                        'bind': '/result',
                        'ro': False
                    }
            }, privileged=True)
            cont = db.get('container')
            stream_process = Process(target=publish_build_ouput, args=(cont, this_log))
            stream_process.start()
            doc.wait(container)
        except Exception as err:
            logger.error('Start container failed. Error Msg: %s' % err)
            failed = True
            continue
        #db.publish('build-ouput', 'ENDOFLOG')
        # stream = doc.logs(container, stdout=True, stderr=True, timestamps=True)
        # log_stream = stream.split('\n')
        # db_filter_and_add(log_stream, this_log)

        in_dir = len([name for name in os.listdir(repo)])
        last_count = int(db.get('pkg_count'))
        logger.info('last count is %s %s' % (last_count, type(last_count)))
        logger.info('in_dir is %s %s' % (in_dir, type(in_dir)))
        if in_dir > last_count:
            db.incr('pkg_count', (in_dir - last_count))
            db.rpush('completed', build_id)
            db.set('%s:result' % this_log, 'completed')
            db.set('%s:review_stat' % this_log, '1')
        else:
            logger.error('No package found after container exit.')
            failed = True
            db.set('%s:result' % this_log, 'failed')
            db.rpush('failed', build_id)
        # doc.remove_container(container)
        end = datetime.datetime.now().strftime("%m/%d/%Y %I:%M%p")
        db.set('%s:end' % this_log, end)
        try:
            db_caches = db.scan_iter(match='*_cache*', count=3)
            for db_cache in db_caches:
                logger.info('[REPO COUNT CACHE KEY FOUND]: %s' % db_cache)
                db.delete(db_cache)
        except Exception as err:
            logger.error(err)

        if last:
            db.set('idle', "True")
            db.set('building', 'Idle')
            try:
                shutil.rmtree(repo)
                shutil.rmtree(cache)
                shutil.rmtree('/opt/antergos-packages')
                shutil.rmtree('/tmp/32bit')
                shutil.rmtree('/tmp/32build')
            except Exception:
                pass
        else:
            db.set('building', 'Starting next build in queue...')

        db.set('container', '')
        db.set('building_num', '')
        db.set('building_start', '')
        db.delete('repo-count-staging')
        db.delete('repo-count-main')

    # logger.info('Moving pkgs into repo and updating repo database')
    # try:
    #     repo_container = doc.create_container("antergos/makepkg", command="/makepkg/repo_expect.sh --repo",
    #                                           volumes=['/var/cache/pacman', '/makepkg', '/repo', '/root/.gnupg',
    #                                                    '/staging'])
    # except Exception as err:
    #     logger.error('Create container failed. Error Msg: %s' % err)
    #
    # try:
    #     doc.start(repo_container, binds={
    #         cache:
    #             {
    #                 'bind': '/var/cache/pacman',
    #                 'ro': False
    #             },
    #         DOC_DIR:
    #             {
    #                 'bind': '/makepkg',
    #                 'ro': True
    #             },
    #         repo:
    #             {
    #                 'bind': '/staging',
    #                 'ro': False
    #             },
    #         '/root/.gnupg':
    #             {
    #                 'bind': '/root/.gnupg',
    #                 'ro': False
    #             },
    #         '/srv/antergos.info/repo/iso/testing/uefi/antergos-staging/':
    #             {
    #                 'bind': '/repo',
    #                 'ro': False
    #             }
    #     })
    #     doc.wait(repo_container)
    # except Exception as err:
    #     logger.error('Start container failed. Error Msg: %s' % err)
    # # doc.remove_container(repo_container)

    logger.info('Build completed. Repo has been updated.')


def build_iso():
    iso_arch = ['x86_64', 'i686']
    db.set('pkg_count_iso', '0')
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
        db.set('building', arch)
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
        # Initiate communication with docker daemon
        try:
            doc = docker.Client(base_url='unix://var/run/docker.sock', timeout=10)
            iso_container = doc.create_container("antergos/mkarchiso",
                                                 volumes=['/var/cache/pacman', '/antergos-iso/configs/antergos/out',
                                                          '/var/run/dbus', '/start', '/sys/fs/cgroup'], tty=True,
                                                 name=['antergos-iso-%s' % arch], cpu_shares=512)
            db.set('container', iso_container.get('Id'))
        except Exception as err:
            logger.error("Cant connect to Docker daemon. Error msg: %s", err)
            break

        try:
            doc.start(iso_container, privileged=True, binds={
                '/var/cache/pacman':
                    {
                        'bind': '/var/cache/pacman',
                        'ro': False
                    },
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
            #db.set('%s:review_stat' % this_log, '1')
        else:
            logger.error('antergos-iso-%s not found after container exit.' % arch)
            failed = True
            db.set('%s:result' % this_log, 'failed')
            db.rpush('failed', build_id)

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







