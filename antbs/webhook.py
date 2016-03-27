#!/usr/bin/python
# -*- coding: utf-8 -*-
#
# webhook.py
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


"""Webhook Handler Module"""

import ast
import datetime
import ipaddress
import json
import os
import shutil

import requests

import transaction_handler as builder
from database import package
from database.base_objects import db
from database.installation import AntergosInstallation, AntergosInstallationUser
from database.server_status import get_timeline_object, status
from database.transaction import get_trans_object
from rq import Connection, Queue, Worker
from utils.logging_config import logger

with Connection(db):
    queue = Queue('transactions')
    w = Worker([queue])


def rm_file_or_dir(src):
    """

    :param src:
    :return:
    """
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


class WebhookMeta:
    """
    This is the base class for `Webhook`. It simply initializes attributes.

    Attributes:
        is_authorized (bool): The request is from an authorized sender.
        is_monitor (bool): The request is internel (repo_monitor.py).
        is_cnchi (bool): The request is from Cnchi Installer.
        request (dict): The Flask request.
        is_manual (bool): The request was made by a human.
        manual_trans_index (int): The db list index of the github trans that is to be processed.
        is_numix (bool): The request is for a numix icon repo.
        is_github (bool): The request is from Github.
        is_gitlab (bool): The request is from Gitlab.
        changes (list): Packages for which changes were made to their PKGBUILD in git commit.
        repo (string): The name of the github repo that triggered the webhook.
        payload (dict): The webhook data payload.
        full_name (string): The github repo's full name. eg. "org/repo"
        pusher (string): The name of the pusher.
        commits (list): List of commits in the payload.
        result (string): Result string to send as response to the request.
        building (string): The name of the package being built if a build is running currently.

    """

    def __init__(self):
        super().__init__()
        self.attrib_lists = dict(bool=['is_authorized', 'is_monitor', 'is_cnchi', 'is_manual',
                                       'is_numix', 'is_github', 'is_gitlab'],
                                 int=['manual_hook_index'],
                                 dict=['payload'],
                                 list=['changes', 'commits'],
                                 string=['repo', 'full_name', 'pusher', 'result', 'building'])

        self.all_attribs = [item for sublist in self.attrib_lists.values() for item in sublist]

        for attrib in self.all_attribs:
            if attrib in self.attrib_lists['bool']:
                setattr(self, attrib, False)
            elif attrib in self.attrib_lists['int']:
                setattr(self, attrib, 0)
            elif attrib in self.attrib_lists['dict']:
                setattr(self, attrib, dict())
            elif attrib in self.attrib_lists['list']:
                setattr(self, attrib, [])
            elif attrib in self.attrib_lists['string']:
                setattr(self, attrib, '')

        self.repo = 'antergos-packages'
        self.full_name = 'Antergos/antergos-packages'
        self.request = dict(args={})


class Webhook(WebhookMeta):
    """
    This class handles the processing of all Webhooks.

    Args:
        request (flask.request): The flask request or a dict-like object.

    Attributes:
       See `WebhookMeta` class.

    """

    def __init__(self, request=None):
        if not request:
            raise ValueError(
                'request is required to create a {0} instance.'.format(self.__class__.__name__)
            )

        super().__init__()

        if isinstance(request, dict):
            self.is_monitor = True

        self.request = request
        self.building = status.now_building

        if self.is_monitor or self.is_from_authorized_sender():

            if self.is_manual:
                self.process_manual()

            elif self.is_cnchi and self.request.args.get('result', None) is None:
                self.process_cnchi_start()

            elif self.is_cnchi and self.request.args.get('result', None) is not None:
                install_id = self.request.args.get('install_id', None)
                result = self.request.args.get('result', None)

                if install_id is not None and result is not None:
                    logger.debug('Cnchi install_id {0} result is {1}'.format(install_id, result))
                    result = AntergosInstallation.bool_string_helper(result)
                    logger.debug(result)
                    self.process_cnchi_end(install_id, result)

            if self.is_github:
                self.process_github()

            if len(self.changes) > 0:
                self.process_changes()
        else:
            if not self.result:
                self.result = 'Nothing to see here, move along ...'

    def is_from_authorized_sender(self):
        """
        Determine if the request sender is authorized to send us webhooks.

        Returns:
            bool: The request is from an authorized sender.

        """

        manual_flag = int(self.request.args.get('phab', '0'))
        gitlab = self.request.headers.get('X-Gitlab-Event', '')
        cnchi = self.request.args.get('cnchi', False)
        cnchi_version = self.request.headers.get('X-Cnchi-Installer', False)

        if manual_flag and manual_flag > 0:
            if self.request.args.get('token') == db.get('ANTBS_MANUAL_TOKEN'):
                self.is_manual = True
                self.is_authorized = True
                self.manual_trans_index = manual_flag

        elif cnchi and cnchi_version and db.get('CNCHI_TOKEN_NEW') == cnchi:
            self.is_cnchi = cnchi_version
            self.is_authorized = True

        elif '' != gitlab and 'Push Hook' == gitlab:
            self.is_gitlab = True
            self.is_authorized = True
            self.changes = [['numix-icon-theme-square']]
        else:
            if not db.exists('GITHUB_HOOK_IP_BLOCKS'):
                # Store the IP address blocks that github uses for webhook requests.
                hook_blocks = requests.get('https://api.github.com/meta').text
                db.setex('GITHUB_HOOK_IP_BLOCKS', 42300, hook_blocks)
                hook_blocks = json.loads(hook_blocks)['hooks']
            else:
                hook_blocks = json.loads(db.get('GITHUB_HOOK_IP_BLOCKS'))['hooks']

            for block in hook_blocks:
                ip = ipaddress.ip_address(self.request.remote_addr)
                if ipaddress.ip_address(ip) in ipaddress.ip_network(block):
                    # the remote_addr is within the network range of github
                    self.is_github = True
                    self.is_authorized = True
                    break

            if self.request.headers.get('X-GitHub-Event') == "ping":
                self.result = json.dumps({'msg': 'Hi!'})
            elif self.request.headers.get('X-GitHub-Event') != "push":
                self.result = json.dumps({'msg': "wrong event type"})

        return self.is_authorized

    def process_manual(self):
        index = self.manual_trans_index
        try:
            key = db.lrange('antbs:github:payloads:index', -index, -index)
            logger.info(key)
            logger.info(key[0])
            self.payload = db.hgetall(key[0])
        except Exception as err:
            logger.error(err)
            self.result = 500
            return
        self.commits = ast.literal_eval(self.payload['commits'])
        self.is_github = True

    def process_github(self):
        if self.is_manual:
            return
        self.payload = json.loads(self.request.data.decode('UTF-8'))
        # Save payload in the database temporarily in case we need it later.
        dt = datetime.datetime.now().strftime("%m%d%Y-%I%M")
        key = 'antbs:github:payloads:{0}'.format(dt)
        if db.exists(key):
            for i in range(1, 5):
                tmp = '{0}:{1}'.format(key, i)
                if not db.exists(tmp):
                    key = tmp
                    break
        db.hmset(key, self.payload)
        db.rpush('antbs:github:payloads:index', key)
        db.expire(key, 172800)

        self.full_name = self.payload['repository']['full_name']
        self.repo = self.payload['repository']['name']
        self.pusher = self.payload['pusher']['name']
        self.commits = self.payload['commits']

        if self.repo == 'numix-icon-theme':
            rate_limit = True
            if self.repo not in status.queue and self.repo != status.building:
                if not db.exists('numix-commit-flag'):
                    self.changes.append(['numix-icon-theme'])
                    self.is_numix = True
                    db.setex('numix-commit-flag', 3600, 'True')
                    rate_limit = False

            if rate_limit:
                msg = 'RATE LIMIT IN EFFECT FOR numix-icon-theme'
                logger.info(msg)
                self.result = json.dumps({'msg': msg})
            else:
                self.repo = 'antergos-packages'

        elif self.repo == 'cnchi-dev':
            self.changes.append(['cnchi-dev'])
            self.is_cnchi = True

        elif self.pusher != "antbs":
            for commit in self.commits:
                self.changes.append(commit['modified'])
                self.changes.append(commit['added'])

    def process_changes(self):
        tpl = 'Webhook triggered by <strong>{0}.</strong> Packages added to the build queue: {1}'

        if self.repo == "antergos-packages":
            logger.debug("Build hook triggered. Updating build queue.")
            has_pkgs = False
            no_dups = []

            for changed in self.changes:
                if len(changed) > 0:
                    for item in changed:
                        if item and self.is_gitlab or self.is_numix or self.is_cnchi:
                            pak = item
                        elif item and "PKGBUILD" in item:
                            pak, pkb = item.rsplit('/', 1)
                            pak = pak.rsplit('/', 1)[-1]
                        else:
                            pak = None

                        if pak and 'antergos-iso' != pak:
                            logger.info('Adding %s to the build queue.' % pak)
                            no_dups.append(pak)
                            status.all_packages.add(pak)
                            has_pkgs = True

            if has_pkgs:
                the_pkgs = list(set(no_dups))
                last_pkg = the_pkgs[-1]
                html = []
                if len(the_pkgs) > 1:
                    html.append('<ul class="hook-pkg-list">')
                for p in the_pkgs:
                    if p:
                        if len(the_pkgs) > 1:
                            item = '<li>{0}</li>'.format(p)
                        else:
                            item = '<strong>{0}</strong>'.format(p)
                        html.append(item)
                    if p == last_pkg:
                        if self.is_gitlab:
                            source = 'Gitlab'
                            tltype = 2
                        else:
                            source = 'Github'
                            tltype = 1
                        if len(the_pkgs) > 1:
                            html.append('</ul>')

                        the_pkgs_str = ''.join(html)
                        tl_event = get_timeline_object(msg=tpl.format(source, the_pkgs_str),
                                                       tl_type=tltype,
                                                       packages=the_pkgs)
                        p_obj = package.get_pkg_object(name=p)
                        events = p_obj.tl_events
                        events.append(tl_event.event_id)
                        del p_obj

                trans_obj = get_trans_object(the_pkgs)
                status.queue.append(trans_obj.tnum)
                queue.enqueue_call(builder.handle_hook, timeout=84600)

            if not self.result:
                self.result = json.dumps({'msg': 'OK!'})

    def process_cnchi_start(self):
        """
        Generate installation ID then save it along with the clients ip in result variable.

        :return: None
        """

        namespace = 'cnchi' + self.is_cnchi[2:4]
        client_ip = self.request.remote_addr

        install = AntergosInstallation(namespace=namespace, ip=client_ip)
        user = AntergosInstallationUser(namespace=namespace,
                                        ip=client_ip,
                                        install_id=install.install_id)

        self.result = json.dumps({'id': install.install_id, 'ip': user.ip_address})

    def process_cnchi_end(self, install_id, result):
        """ Record install result (success/failure). """

        namespace = 'cnchi' + self.is_cnchi[2:4]
        client_ip = self.request.remote_addr

        install = AntergosInstallation(namespace=namespace, install_id=install_id)
        user = AntergosInstallationUser(namespace=namespace, ip=client_ip)

        if result:
            user.installs_completed.add(install_id)
            install.completed = True
        else:
            user.installs_failed.add(install_id)

        install.set_installation_ended()

        self.result = json.dumps({'msg': 'Ok!'})
