#!/usr/bin/python
# -*- coding: utf-8 -*-
#
# build_pkg.py
#
# Copyright 2014-2015 Antergos
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

""" Monitor commit activity on 3rd-party repos. Schedule a build when new commits are detected. """

from utils.logging_config import logger
from utils.redis_connection import db
from utils.server_status import status
import webhook
from github3 import login
from gitlab import Gitlab

GITLAB_TOKEN = status.gitlab_token
GITHUB_TOKEN = status.github_token


def maybe_check_for_new_items():
    return db.exists('FEED_CHECKED')


def check_for_new_items():
    db.set('FEED_CHECKED', 'True')
    db.expire('FEED_CHECKED', 900)
    new_items = []
    gh = login(token=GITHUB_TOKEN)
    last_id = db.get('ANTBS_GITHUB_LAST_EVENT') or ''
    repo = gh.repository('numixproject', "numix-icon-theme")
    commits = repo.commits()
    latest = None
    try:
        commit = commits.next()
        latest = commit.sha
    except StopIteration:
        pass

    if latest != last_id:
        db.set('ANTBS_GITHUB_LAST_EVENT', latest)
        new_items.append(['numix-icon-theme'])

    gl = Gitlab('https://gitlab.com', GITLAB_TOKEN)
    gl.auth()
    nxsq = gl.Project(id='61284')
    last_updated = db.get('ANTBS_GITLAB_LAST_UPDATED')
    events = nxsq.Event()

    for event in events:
        if event.action_name == 'pushed to':
            if event.created_at != last_updated:
                db.set('ANTBS_GITLAB_LAST_UPDATED', event.created_at)
                new_items.append(['numix-icon-theme-square'])
                new_items.append(['numix-icon-theme-square-kde'])

            break

    if len(new_items) > 0:
        add_to_build_queue(new_items)


def add_to_build_queue(pkgs=None):
    if pkgs is None:
        return False
    req = dict(method='POST', args={})

    wh = webhook.Webhook(req, db)

    wh.is_numix = True
    wh.repo = 'antergos-packages'
    wh.changes = pkgs

    wh.process_changes()
