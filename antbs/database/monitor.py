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


"""
Repo Monitor Module:
    Monitors commit activity on 3rd-party repos and schedules builds
    when new commits are detected.
"""
from datetime import datetime

import gevent
import requests
from github3 import login
from gitlab import Gitlab

import iso_utility
import webhook
from database.base_objects import RedisHash
from database.server_status import status
from database.package import get_pkg_object

from utils import logger, quiet_down_noisy_loggers

GITLAB_TOKEN = status.gitlab_token
GITHUB_TOKEN = status.github_token


class Monitor(RedisHash):
    """
    Repo monitor objects represent a host/service for 3rd-party git repos that can be monitored
    for changes (like Github, Gitlab, etc).
    """

    attrib_lists = dict(
        string=['name'],
        bool=['checked_recently'],
        int=[],
        list=[],
        set=['packages'],
        path=[]
    )

    def __init__(self, name):
        super().__init__(prefix='monitor', key=name)

        self.__namespaceinit__()
        logger.debug(dir(self))
        logger.debug(self.json())

        if not self or not self.name:
            self.name = name

    def check_repos_for_changes(self):
        self.checked_recently = True
        self.expire_in('checked_recently', 930)

        build_pkgs = []
        quiet_down_noisy_loggers()
        self.check_for_new_monitored_packages()

        logger.info('Checking github repos for changes...')

        for pkg in self.packages:
            pkg_obj = get_pkg_object(name=pkg)

            if 'github' == pkg_obj.monitored_service:
                build_pkgs = self.check_github_repo_for_changes(pkg_obj, build_pkgs)
            elif 'gitlab' == pkg_obj.monitored_service:
                build_pkgs = self.check_gitlab_repo_for_changes(pkg_obj, build_pkgs)

        build_pkgs = [p for p in build_pkgs if p]

        if len(build_pkgs) > 0:
            self.add_to_build_queue(build_pkgs)

        if self.db.exists('antbs:misc:iso-release:do_check'):
            version = self.db.get('antbs:misc:iso-release:do_check')
            self.check_mirror_for_iso(version)

    def check_for_new_monitored_packages(self):
        pkg_objs = [get_pkg_object(name=p) for p in status.all_packages if p and gevent.sleep(0.4)]
        new_pkgs = [p for p in pkg_objs if p.is_monitored and p.pkgname not in self.packages]

        if new_pkgs:
            for pkg in new_pkgs:
                self.packages.add(pkg)

    def check_github_repo_for_changes(self, pkg_obj, build_pkgs):
        gh = login(token=GITHUB_TOKEN)
        project = pkg_obj.monitored_project
        repo = pkg_obj.monitored_repo
        last_result = pkg_obj.monitored_last_result
        gh_repo = gh.repository(project, repo)
        latest = None
        must_contain = '.' if 'arc-icon-theme' != repo else '2016'

        if 'mate' in pkg_obj.groups or 'mate-extra' in pkg_obj.groups:
            must_contain = '1.14'

        latest = self._get_releases_tags_or_commits(gh_repo, pkg_obj.monitored_type, must_contain)

        if not latest and ('mate' in pkg_obj.groups or 'mate-extra' in pkg_obj.groups):
            latest = self._get_releases_tags_or_commits(gh_repo, 'tags', must_contain)

        is_new = latest and latest != last_result and latest.replace('v', '') != last_result

        if is_new or (latest and not last_result):
            if 'commits' != pkg_obj.monitored_type:
                latest = latest.replace('v', '')

            pkg_obj.monitored_last_result = latest
            pkg_obj.monitored_last_checked = self.datetime_to_string(datetime.now())
            build_pkgs.append(pkg_obj.name)

            if latest != pkg_obj.pkgver and pkg_obj.monitored_type in ['releases', 'tags']:
                pkg_obj.update_pkgbuild_and_push_github('pkgver', latest)

        elif not latest:
            logger.error('latest for %s is Falsey: %s', latest, pkg_obj.name)

        return build_pkgs

    @staticmethod
    def _get_releases_tags_or_commits(gh_repo, what_to_get, must_contain=None):
        git_item = getattr(gh_repo, what_to_get)
        res = git_item()
        latest = ''
        items_checked = 0
        logger.debug([gh_repo, what_to_get, must_contain, res])

        def _get_next_item():
            _latest = ''
            try:
                item = res.next()

                if 'commits' == what_to_get:
                    _latest = item.sha
                elif 'releases' == what_to_get:
                    _latest = item.tag_name
                elif 'tags' == what_to_get:
                    _latest = str(item)
            except Exception as err:
                logger.exception(err)

            return _latest

        latest = _get_next_item()
        logger.debug(latest)

        if must_contain and must_contain not in latest:
            while must_contain not in latest:
                latest = _get_next_item()
                items_checked += 1

                if items_checked > 5:
                    break

        logger.debug(latest)
        return latest

    def check_gitlab_repo_for_changes(self, pkg_obj, build_pkgs):
        gl = Gitlab('https://gitlab.com', GITLAB_TOKEN)
        gl.auth()
        project_id = pkg_obj.monitored_project
        repo = pkg_obj.monitored_repo
        project = gl.projects.get(project_id)
        last_result = pkg_obj.monitored_last_result
        events = project.events.list()

        for event in events:
            if event.action_name == 'pushed to':
                if event.created_at != last_result:
                    pkg_obj.monitored_last_result = event.created_at
                    build_pkgs.append('numix-icon-theme-square')

                break

        return build_pkgs

    def check_mirror_for_iso(self, version):
        synced = []
        for iso_pkg in status.iso_pkgs:
            iso_obj = get_pkg_object(name=iso_pkg)
            req = requests.head(iso_obj.iso_url, allow_redirects=True)

            try:
                req.raise_for_status()
                synced.append(iso_obj)
            except Exception as err:
                logger.info(err)

        if len(synced) == 4:
            success = self.add_iso_versions_to_wordpress(synced)
            if success:
                iso_utility.clean_up_after_release(version)
                self.db.delete('antbs:misc:iso-release:do_check')
            else:
                logger.error('At least one iso was not successfully added to wordpress.')

    @staticmethod
    def add_iso_versions_to_wordpress(iso_pkgs):
        bridge = iso_utility.WordPressBridge(auth=(status.docker_user, status.wp_password))
        success = []
        for iso_pkg in iso_pkgs:
            success.append(bridge.add_new_iso_version(iso_pkg))
            logger.info(success)

        return all(success)

    @staticmethod
    def add_to_build_queue(pkgs):
        req = dict(method='POST', args={})
        wh = webhook.Webhook(req)

        wh.is_numix = True
        wh.repo = 'antergos-packages'
        wh.changes = [pkgs]

        wh.process_changes()


def get_monitor_object(name):
    """
    Gets an existing repo monitor or creates a new one.

    Args:
        name (str): Name of 3rd-party provider/service (eg. Github).

    Returns:
        Monitor: A fully initiallized `Monitor` object.

    """

    monitor_obj = Monitor(name=name)

    return monitor_obj


def check_repos_for_changes(name):
    monitor_obj = get_monitor_object(name)
    monitor_obj.check_repos_for_changes()

