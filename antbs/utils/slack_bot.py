#!/usr/bin/python
# -*- coding: utf-8 -*-
#
# transaction_handler.py
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


""" Bot utilities for Slack """

import gevent
from rq import Connection, Worker, Queue
from slackclient import SlackClient
# from github3 import login

from database.base_objects import db, RedisHash
from .logging_config import logger


ET_SLACK_IDENTIFIER = db.get('slack:misc:identifiers:et')
#gh_user = db.get('ANTBS_GITHUB_TOKEN')
#gh = login(token=gh_user)


class DispatcherBotMessage(RedisHash):
    def __init__(self, msg_id=None):
        if msg_id is None:
            msg_id = db.incr('slack:misc:next:msg_id')

        super().__init__(namespace='slack', prefix='bot:dispatcher:message', key=msg_id)

        self.attrib_lists.update(dict(
            string=['from_user', 'content'],
            bool=['delivered'],
            int=['msg_id'],
            list=[],
            set=['to_users', 'to_channels' ],
            path=[]
        ))

        self.__namespaceinit__()

        if not self or not self.msg_id:
            self.__bindattrs__()
            self.msg_id = msg_id


class DispatcherBot(RedisHash):
    _slack = None

    def __init__(self, name='dispatcher'):
        super().__init__(namespace='slack', prefix='bot', key=name)

        self.attrib_lists.update(dict(
            string=['api_key', 'name'],
            bool=[],
            int=[],
            list=[],
            set=['watching_events', 'watching_users', 'messages'],
            path=[]
        ))

        self.__namespaceinit__()

        if not self or not self.name:
            self.__bindattrs__()
            self.name = name

        if self._slack is None:
            self._slack = SlackClient(ET_SLACK_IDENTIFIER)

        if not self.watching_events:
            self.watching_events.extend(['presence_change', 'message'])

    def start(self):
        if self._slack.rtm_connect():
            while True:
                events = self._slack.rtm_read()

                if events:
                    self.handle_events(events)

                gevent.sleep(2)

        else:
            logger.error('Connection Failed!')

    def handle_events(self, events):
        events = [e for e in events if e['type'] in self.watching_events]

        for event in events:
            method = '_{}_handler'.format(event['type'])
            handler = getattr(self, method)

            try:
                handler(event)
            except Exception as err:
                logger.exception(err)

    def _presence_change_handler(self, event):
        if 'active' != event['presence']:
            return

        if not self.watching_users or event['user'] not in self.watching_users:
            return
        elif not self.messages:
            return

        for msg_id in self.messages:
            msg = DispatcherBotMessage(msg_id)

            if msg.delivered or not msg.to_users or event['user'] not in msg.to_users:
                continue

            channel = self._slack.api_call('im.open', user=event['user'])

            if self._slack.rtm_send_message(channel, msg.content):
                msg.to_users.remove(event['user'])
                self.watching_users.remove(event['user'])

            if not msg.to_users:
                msg.delivered = True










