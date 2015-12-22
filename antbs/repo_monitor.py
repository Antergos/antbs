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
import requests
import package
import iso

GITLAB_TOKEN = status.gitlab_token
GITHUB_TOKEN = status.github_token
ITEMS_HASH = db.hgetall('antbs:monitor:list') or False
# logger.debug(type(ITEMS_HASH))
MONITOR_ITEMS = ITEMS_HASH if ITEMS_HASH else None


def maybe_check_for_new_items():
    """


    :return:
    """
    return db.exists('FEED_CHECKED')


def check_for_new_items():
    """


    """
    db.setex('FEED_CHECKED', 900, 'True')
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
                build_pkgs.append([res])

    build_pkgs = [p for p in build_pkgs if p]
    if len(build_pkgs) > 0:
        add_to_build_queue(build_pkgs)

    if db.exists('antbs:misc:iso-release:do_check'):
        version = db.get('antbs:misc:iso-release:do_check')
        check_mirror_for_iso(version)


def check_mirror_for_iso(version):
    synced = []
    for iso_pkg in status.iso_pkgs:
        iso_obj = package.get_pkg_object(name=iso_pkg)
        req = requests.head(iso_obj.iso_url, allow_redirects=True)

        try:
            req.raise_for_status()
            synced.append(iso_obj)
        except Exception as err:
            logger.info(err)

    if len(synced) == 4:
        success = add_iso_versions_to_wordpress(synced)
        if success:
            iso.clean_up_after_release(version)
            db.delete('antbs:misc:iso-release:do_check')


def add_iso_versions_to_wordpress(iso_pkgs):
    bridge = iso.WordPressBridge(auth=(status.docker_user, status.wp_password))
    success = []
    for iso_pkg in iso_pkgs:
        success.append(bridge.add_new_iso_version(iso_pkg))
        logger.info(success)

    return all(success)


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
    latest = None

    if repo not in ['scudcloud', 'yaourt', 'package-query']:
        commits = gh_repo.commits()
        try:
            commit = commits.next()
            latest = commit.sha
        except StopIteration:
            pass
    else:
        releases = [r for r in gh_repo.releases()]
        try:
            release = releases[0]
            latest = release.name
            latest = latest.replace('v', '')
        except Exception as err:
            logger.error(err)

    if latest != last_id:
        db.set(key, latest)
        if 'pamac' == repo:
            repo = 'pamac-dev'
        elif 'paper-gtk-theme' == repo:
            repo = 'gtk-theme-paper'
        elif repo in ['arc-theme', 'Arc-theme']:
            repo = 'gtk-theme-arc'

        new_items = repo

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
                new_items = ['numix-icon-theme-square']

            break

    return new_items
