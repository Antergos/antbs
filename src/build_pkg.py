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
from pygments.styles import get_style_by_name

logger = logging.getLogger(__name__)
logging.config.dictConfig(logconf.log_config)
SRC_DIR = os.path.dirname(__file__) or '.'
BASE_DIR = os.path.split(os.path.abspath(SRC_DIR))[0]
DOC_DIR = os.path.join(BASE_DIR, 'docker')
REPO_DIR = "/opt/antergos-packages"


def get_pkgver(package):
    pkg = package
    pkgver = None
    pbfile = os.path.join(REPO_DIR, pkg, 'PKGBUILD')
    with open(pbfile) as PKGBUILD:
        for line in PKGBUILD:
            if line.startswith("pkgver") and not line.startswith("pkgver()"):
                l = line.split('=')
                logging.info('line is %s' % l)
                pkgver = l[1].strip('\n')
            elif line.startswith("pkgver()"):
                proc = subprocess.Popen(['bash', '-c', '. ' + pbfile + '; pkgver'], stdout=subprocess.PIPE)
                getver = proc.communicate()
                pkgver = getver[0]
            else:
                continue
    return pkgver


def get_deps(package):
    pkg = package
    depends = None
    pbfile = os.path.join(REPO_DIR, pkg, 'PKGBUILD')
    with open(pbfile) as PKGBUILD:
        for line in PKGBUILD:
            if line.startswith("depends") or line.startswith("makedepends"):
                deps = line.split('=')
                depends = deps[1].rstrip()
            else:
                continue
        try:
            return list(depends)
        except Exception:
            return [depends]


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


def handle_hook():

    iso_flag = db.get('isoFlag')
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
    else:
        logger.info('Pulling changes from github.')
        subprocess.call(['git', 'clone', 'http://github.com/lots0logs/antergos-packages.git'], cwd='/opt')
        subprocess.call(['chmod', '-R', '777', 'antergos-packages'], cwd='/opt')
        # Check database to see if packages exist and add them if necessary.
        packages = db.lrange('queue', 0, -1)
        logger.info('Checking database for packages.')
        for package in packages:
            version = get_pkgver(package)
            depends = get_deps(package)
            if not db.exists('pkg:%s' % package):
                logger.info('%s not found in database, adding entry..' % package)
                db.set('pkg:%s' % package, True)
                db.set('pkg:%s:name' % package, package)
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
        build_pkgs()


def db_filter_and_add(output=None, this_log=None):
    if output is None or this_log is None:
        return
    nodup = set()
    part2 = None
    filtered = []
    for line in output:
        if not line or line == '':
            continue
        line = line.rstrip()
        end = line[20:]
        if end not in nodup:
            nodup.add(end)
            line = line.replace("can't", "can not")
            #line = line.replace('"', '\\"')
            if len(line) > 210:
                part1 = line[:210]
                part2 = line[211:]
                filtered.append(part1)
                #db.rpush('%s:content' % this_log, part1)
                continue
            elif part2:
                #db.rpush('%s:content' % this_log, part2)
                filtered.append(part2)
                part2 = None
                continue
            else:
                filtered.append(line)
    filtered_string = '\n '.join(filtered)
                #db.rpush('%s:content' % this_log, line)
    pretty = highlight(filtered_string, BashLexer(), HtmlFormatter(style='monokai', linenos='inline',
                                                                   prestyles="background:#272822;color:#fff;"))
    db.set('%s:content' % this_log, pretty)



def build_pkgs():
    # Initiate communication with docker daemon
    try:
        doc = docker.Client(base_url='unix://var/run/docker.sock', version='1.12', timeout=10)
        # doc.build(path=DOC_DIR, tag="arch-devel", quiet=False, timeout=None)
    except Exception as err:
        logger.error("Cant connect to Docker daemon. Error msg: %s", err)
    # Create our tmp directories
    repo = os.path.join("/tmp", "staging")
    cache = os.path.join("/tmp", "pkg_cache")
    for d in [repo, cache]:
        if not os.path.exists(d):
            os.mkdir(d, 0o777)
    db.set('pkg_count', 0)
    pkglist = db.lrange('queue', 0, -1)
    pkglist = list(set(pkglist))
    for i in range(len(pkglist)):
        failed = False
        pkg = pkglist[i]
        if pkg is None or pkg == '':
            continue
        logger.info('Building %s' % pkg)
        db.lrem('queue', 0, pkg)
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
        db.set('building', pkg)
        pkgdir = os.path.join(REPO_DIR, pkg)
        pkg_deps = list(db.lrange('pkg:%s:deps', 0, -1))
        try:
            container = doc.create_container("lots0logs/makepkg", command=["/makepkg/build.sh", pkg_deps], name=pkg,
                                             volumes=['/var/cache/pacman', '/makepkg', '/repo', '/pkg', '/root/.gnupg',
                                                      '/staging'])
        except Exception as err:
            logger.error('Create container failed. Error Msg: %s' % err)
            failed = True
            continue
        db.set('container', container.get('Id'))
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
                repo:
                    {
                        'bind': '/staging',
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
                '/srv/antergos.info/repo/iso/testing/uefi/antergos/':
                    {
                        'bind': '/repo',
                        'ro': False
                    }
            })
            doc.wait(container)
        except Exception as err:
            logger.error('Start container failed. Error Msg: %s' % err)
            failed = True
            continue

        stream = doc.logs(container, stdout=True, stderr=True, timestamps=True)
        log_stream = stream.split('\n')
        db_filter_and_add(log_stream, this_log)

        in_dir = len([name for name in os.listdir(repo)])
        last_count = int(db.get('pkg_count'))
        logger.info('last count is %s %s' % (last_count, type(last_count)))
        logger.info('in_dir is %s %s' % (in_dir, type(in_dir)))
        if in_dir > last_count:
            db.incr('pkg_count', (in_dir - last_count))
            db.rpush('completed', build_id)
            db.set('%s:result' % this_log, 'completed')
        else:
            logger.error('No package found after container exit.')
            failed = True
            db.set('%s:result' % this_log, 'failed')
        if failed:
            db.rpush('failed', build_id)
        doc.remove_container(container)
        end = datetime.datetime.now().strftime("%m/%d/%Y %I:%M%p")
        db.set('%s:end' % this_log, end)
        

    logger.info('Moving pkgs into repo and updating repo database')
    try:
        repo_container = doc.create_container("lots0logs/makepkg", command="/makepkg/repo_expect.sh",
                                              volumes=['/var/cache/pacman', '/makepkg', '/repo', '/root/.gnupg',
                                                       '/staging'])
    except Exception as err:
        logger.error('Create container failed. Error Msg: %s' % err)

    try:
        doc.start(repo_container, binds={
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
            repo:
                {
                    'bind': '/staging',
                    'ro': False
                },
            '/root/.gnupg':
                {
                    'bind': '/root/.gnupg',
                    'ro': False
                },
            '/srv/antergos.info/repo/iso/testing/uefi/antergos/':
                {
                    'bind': '/repo',
                    'ro': False
                }
        })
        doc.wait(repo_container)
    except Exception as err:
        logger.error('Start container failed. Error Msg: %s' % err)
    # doc.remove_container(container)
    try:
        shutil.rmtree(repo)
        shutil.rmtree(cache)
        shutil.rmtree('/opt/antergos-packages')
    except Exception:
        pass
    db.set('idle', "True")
    db.set('building', 'Idle')
    db.set('container', '')
    db.set('building_num', '')
    db.set('building_start', '')
    logger.info('All builds completed. Repo has been updated.')


def build_iso():
    iso_arch = ['x86_64', 'i686']

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

        flag = '/srv/antergos.org/ISO32'
        if arch is 'i686':
            if not os.path.exists(flag):
                open(flag, 'a').close()
        else:
            if os.path.exists(flag):
                os.remove(flag)
        # Initiate communication with docker daemon
        try:
            doc = docker.Client(base_url='unix://var/run/docker.sock', version='1.12', timeout=10)
            iso_container = doc.create_container("lots0logs/antergos-iso",
                                                 volumes=['/var/cache/pacman', '/antergos-iso/configs/antergos/out',
                                                          '/var/run/dbus', '/start', '/sys/fs/cgroup'], tty=True,
                                                 name=['antergos-iso-%s' % arch], cpu_shares=512)
            db.set('container', iso_container.get('Id'))
        except Exception as err:
            logger.error("Cant connect to Docker daemon. Error msg: %s", err)

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
                '/srv/antergos.org':
                    {
                        'bind': '/antergos-iso/configs/antergos/out',
                        'ro': False
                    },
                '/sys/fs/cgroup':
                    {
                        'bind': '/sys/fs/cgroup',
                        'ro': True
                    }
            })

        except Exception as err:
            logger.error("Cant start container. Error msg: %s", err)

        doc.wait(iso_container)

        stream = doc.logs(iso_container, stdout=True, stderr=True, timestamps=True)
        log_stream = stream.split('\n')
        db_filter_and_add(log_stream, this_log)

        pkg = 'antergos-iso-%s' % arch
        iso_dir = os.listdir('/srv/antergos.org/')
        if pkg in iso_dir:
            db.rpush('completed', build_id)
            db.set('%s:result' % this_log, 'completed')
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







