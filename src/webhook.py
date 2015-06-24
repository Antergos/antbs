#!/usr/bin/python
# -*- coding: utf-8 -*-
#
# build_pkg.py
#
# Copyright © 2013-2015 Antergos
#
# This file is part of AntBS
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

"""Webhook Handler Module"""

import json
import os
import subprocess
import shutil
import datetime
import build_pkg as builder
from redis_connection import db
import ipaddress
import ast
import requests
import logging_config as logger
import package as package


def rm_file_or_dir(src):
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


class Webhook(object):
    def __init__(self, request=None, db=None, queue=None):
        self.can_process = False
        if request is None or db is None or queue is None:
            logger.error('@@-webhook.py-@@ 40 | Cant process new webhook because request or db is None.')
        elif request.method == 'POST':
            self.queue = queue
            self.can_process = True
            self.db = db
            self.request = request
            self.is_manual = False
            self.is_numix = False
            self.is_github = False
            self.is_gitlab = False
            self.changes = []
            self.phab_payload = False
            self.the_queue = db.lrange('queue', 0, -1)
            self.repo = 'antergos-packages'
            self.payload = None
            self.phab_payload = False
            self.full_name = None
            self.pusher = None
            self.commits = None
            try:
                self.building = db.hget('now_building', 'pkg')
            except Exception as err:
                logger.error(err)
                self.db.delete('now_building')
                self.building = None
            self.result = None
            self.is_authorized = self.is_from_authorized_sender()

            if self.is_authorized:
                # Process Webhook
                if self.is_manual:
                    self.process_manual()
                if self.is_github:
                    self.process_github()
                if len(self.changes) > 0:
                    self.process_changes()
            else:
                if self.result is None:
                    self.result = 'Nothing to see here, move along ...'

    def is_from_authorized_sender(self):
        # Determine if the request sender is authorized to send us webhooks.
        manual = int(self.request.args.get('phab', '0'))
        gitlab = self.request.headers.get('X-Gitlab-Event') or ''
        if manual and manual > 0 and self.request.args.get('token') == db.get('ANTBS_MANUAL_TOKEN'):
            self.is_manual = True
        elif '' != gitlab and 'Push Hook' == gitlab:
            self.is_gitlab = True
            self.repo = 'antergos-packages'
            self.full_name = 'Antergos/antergos-packages'
            self.changes = [['numix-icon-theme-square', 'numix-icon-theme-square-kde']]
            logger.error(self.request.headers)
        else:
            # Store the IP address blocks that github uses for hook requests.
            hook_blocks = requests.get('https://api.github.com/meta').json()['hooks']
            for block in hook_blocks:
                ip = ipaddress.ip_address(u'%s' % self.request.remote_addr)
                if ipaddress.ip_address(ip) in ipaddress.ip_network(block):
                    # the remote_addr is within the network range of github
                    self.is_github = True
                    break
            else:
                return False

            if self.request.headers.get('X-GitHub-Event') == "ping":
                self.result = json.dumps({'msg': 'Hi!'})
                return False
            elif self.request.headers.get('X-GitHub-Event') != "push":
                self.result = json.dumps({'msg': "wrong event type"})
                return False

        return True

    def process_manual(self):

        try:
            key = db.lrange('payloads:index', -3, -3)
            self.payload = db.hgetall(key[0])
        except Exception as err:
            logger.error(err)
            self.result = 500
            return
        self.commits = ast.literal_eval(self.payload['commits'])
        self.is_github = True
        self.full_name = 'Antergos/antergos-packages'
        self.repo = 'antergos-packages'

    def process_github(self):
        self.payload = json.loads(self.request.data)
        # Save payload in the database temporarily in case we need it later.
        dt = datetime.datetime.now().strftime("%m%d%Y-%I%M")
        key = 'payloads:%s' % dt
        if db.exists(key):
            for i in range(1, 5):
                tmp = '%s:%s' % (key, i)
                if not db.exists(tmp):
                    key = tmp
                    break
        db.hmset(key, self.payload)
        db.rpush('payloads:index', key)
        db.expire(key, 172800)

        self.full_name = self.payload['repository']['full_name']
        self.repo = self.payload['repository']['name']
        self.pusher = self.payload['pusher']['name']
        self.commits = self.payload['commits']

        if self.repo == 'numix-icon-theme':
            rate_limit = True
            if 'numix-icon-theme' not in self.the_queue and 'numix-icon-theme' != self.building:
                if not db.exists('numix-commit-flag'):
                    self.changes.append(['numix-icon-theme'])
                    self.is_numix = True
                    db.setex('numix-commit-flag', 1200, 'True')
                    rate_limit = False

            if rate_limit:
                msg = 'RATE LIMIT IN EFFECT FOR numix-icon-theme'
                logger.info(msg)
                self.result = json.dumps({'msg': msg})
            else:
                self.repo = 'antergos-packages'

        elif self.repo == 'cnchi-dev':
            idle = db.get('idle')
            working = db.exists('creating-cnchi-archive-from-dev')
            check = 'cnchi-dev' != self.building or idle == "True"
            if not working and 'cnchi-dev' not in self.the_queue and check:
                db.set('creating-cnchi-archive-from-dev', 'True')
                cnchi_git = 'https://github.com/lots0logs/cnchi-dev.git'
                cnchi_clone = '/tmp/cnchi'
                git = '/tmp/cnchi/.git'
                cnchi_tar_tmp = '/tmp/cnchi.tar'
                cnchi_tar = '/srv/antergos.org/cnchi.tar'

                for f in [cnchi_clone, cnchi_tar, cnchi_tar_tmp]:
                    if os.path.exists(f):
                        rm_file_or_dir(f)
                try:
                    subprocess.check_call(['git', 'clone', cnchi_git, 'cnchi'], cwd='/tmp')
                    shutil.rmtree(git)
                    subprocess.check_call(['tar', '-cf', '/tmp/cnchi.tar', '-C', '/tmp', 'cnchi'])
                    shutil.copy('/tmp/cnchi.tar', '/srv/antergos.org/')
                except subprocess.CalledProcessError as err:
                    logger.error(err.output)

                db.delete('creating-cnchi-archive-from-dev')
        elif self.pusher != "antbs":
            for commit in self.commits:
                self.changes.append(commit['modified'])
                self.changes.append(commit['added'])

    def process_changes(self):

        if self.repo == "antergos-packages":
            logger.info("Build hook triggered. Updating build queue.")
            has_pkgs = False
            no_dups = []
            # logger.info(self.changes)

            for changed in self.changes:
                # logger.info(changed)
                if changed is not None and changed != [] and changed != '':
                    for item in changed:
                        # logger.info(item)
                        if self.is_gitlab or self.is_numix:
                            pak = item
                        else:
                            if "PKGBUILD" in item:
                                pak, pkb = item.rsplit('/', 1)
                                pak = pak.rsplit('/', 1)[-1]
                            else:
                                pak = None

                        logger.info(pak)
                        if pak is not None and pak != '' and pak != [] and pak != 'antergos-iso':
                            logger.info('Adding %s to the build queue' % pak)
                            no_dups.append(pak)
                            db.sadd('pkgs:all', pak)
                            has_pkgs = True

            if has_pkgs:
                the_pkgs = list(set(no_dups))
                first = True
                last = False
                last_pkg = the_pkgs[-1]
                p_ul = []
                if len(the_pkgs) > 1:
                    p_ul.append('<ul class="hook-pkg-list">')
                for p in the_pkgs:
                    if p in self.the_queue:
                        continue
                    if p not in self.the_queue and p is not None and p != '' and p != []:
                        self.db.rpush('queue', p)
                        if len(the_pkgs) > 1:
                            p_li = '<li>%s</li>' % p
                        else:
                            p_li = '<strong>%s</strong>' % p
                        p_ul.append(p_li)
                    if p == last_pkg:
                        last = True
                    self.queue.enqueue_call(builder.handle_hook, args=(first, last), timeout=84600)
                    if last:
                        if self.is_gitlab:
                            source = 'Gitlab'
                            tltype = 2
                        else:
                            source = 'Github'
                            tltype = 1
                        if len(the_pkgs) > 1:
                            p_ul.append('</ul>')
                        the_pkgs_str = ''.join(p_ul)
                        tl_event = logconf.new_timeline_event(
                            'Webhook triggered by <strong>%s.</strong> Packages added to'
                            ' the build queue: %s' % (source, the_pkgs_str), tltype)
                        p_obj = package.Package(p, db)
                        p_obj.save_to_db('tl_event', tl_event)
                    first = False

            if not self.result:
                self.result = json.dumps({'msg': 'OK!'})
