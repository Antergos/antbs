#!/usr/bin/python
# -*- coding: utf-8 -*-
#
# build_pkg.py
#
# Copyright 2014-2015 Antergos
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


""" Monitor commit activity on 3rd-party repos. Schedule a build when new commits are detected. """

from utils.logging_config import logger
from utils.redis_connection import db
from utils.server_status import status
import webhook
from github3 import login
from gitlab import Gitlab
import json

GITLAB_TOKEN = status.gitlab_token
GITHUB_TOKEN = status.github_token
ITEMS_HASH = db.hgetall('antbs:monitor:list') or False
logger.debug(type(ITEMS_HASH))
MONITOR_ITEMS = ITEMS_HASH if ITEMS_HASH else None


def maybe_check_for_new_items():
    """


    :return:
    """
    return db.exists('FEED_CHECKED')


def check_for_new_items():
    """


    """
    db.set('FEED_CHECKED', 'True')
    db.expire('FEED_CHECKED', 900)
    build_pkgs = []
    for service, project_list in MONITOR_ITEMS.iteritems():
        projects = project_list.split(',')
        for project in projects:
            if not project or project == '':
                continue
            res = None
            if 'github' == service:
                project = project.split('/')
                res = check_github_repo(project=project[0], repo=project[1])
            elif 'gitlab' == service:
                res = check_gitlab_repo(project_id=project)

            if res:
                build_pkgs = build_pkgs + res

    if len(build_pkgs) > 0:
        add_to_build_queue(build_pkgs)


def add_to_build_queue(pkgs=None):
    """

    :param pkgs:
    :return:
    """
    if pkgs is None:
        return False
    req = dict(method='POST', args={})

    wh = webhook.Webhook(req)

    wh.is_numix = True
    wh.repo = 'antergos-packages'
    wh.changes = pkgs

    wh.process_changes()


def check_github_repo(project=None, repo=None):
    """

    :param project:
    :param repo:
    :return:
    """
    new_items = []
    gh = login(token=GITHUB_TOKEN)
    key = 'antbs:monitor:github:%s:%s' % (project, repo)
    last_id = db.get(key) or ''
    gh_repo = gh.repository(project, repo)
    commits = gh_repo.commits()
    releases = [r for r in gh_repo.releases()]
    latest = None
    if repo not in ['scudcloud']:
        try:
            commit = commits.next()
            latest = commit.sha
        except StopIteration:
            pass
    else:
        try:
            release = releases[0]
            latest = release.name
            db.set('ANTBS_SCUDCLOUD_RELEASE_TAG', latest.replace('v', ''))
        except Exception as err:
            logger.error(err)

    if latest != last_id:
        if 'pamac' == repo:
            repo = 'pamac-dev'
        db.set(key, latest)
        new_items.append([repo])

    return new_items


def check_gitlab_repo(project_id=None):
    """

    :param project_id:
    :return:
    """
    new_items = []
    gl = Gitlab('https://gitlab.com', GITLAB_TOKEN)
    gl.auth()
    nxsq = gl.Project(id=project_id)
    key = 'antbs:monitor:gitlab:%s' % project_id
    last_updated = db.get(key)
    events = nxsq.Event()

    for event in events:
        if event.action_name == 'pushed to':
            if event.created_at != last_updated:
                db.set(key, event.created_at)
                new_items.append(['numix-icon-theme-square'])
                new_items.append(['numix-icon-theme-square-kde'])

            break

    return new_items
