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

import src.logging_config as logconf
from src.redis_connection import db
from github3 import login
from gitlab import Gitlab

GITLAB_TOKEN = db.get('ANTBS_GITLAB_TOKEN')
GITHUB_TOKEN = db.get('ANTBS_GITHUB_TOKEN')


def maybe_check_for_new_items():
    return not db.exists('FEED_CHECKED')


def check_for_new_items():
    new_items = []
    gh = login(username='lots0logs', token=GITHUB_TOKEN)
    last_id = db.get('ANTBS_GITHUB_LAST_EVENT') or ''
    events = gh.events.repos.list(user="numixproject", repo="numix-icon-theme")
    events = events.all()
    latest = events[0]

    if latest.get('id') != last_id:
        db.set('ANTBS_GITHUB_LAST_EVENT', latest.get('id'))
        new_items.append('numix-icon-theme')

    gl = Gitlab('https://gitlab.com/api', private_token=GITLAB_TOKEN)
    gl.auth()
    nxsq = gl.Project(id='61284')
    last_updated = db.get('ANTBS_GITLAB_LAST_UPDATED')
    events = nxsq.Event()

    for event in events:
        if event.get('action_name') == 'pushed to':
            if event.get('created_at') != last_updated:
                db.set('ANTBS_GITLAB_LAST_UPDATED', event.get('created_at'))
                new_items.append('numix-icon-theme-square')
                new_items.append('numix-icon-theme-square-kde')

            break

    db.setex('FEED_CHECKED', 900, 'True')

    return new_items
