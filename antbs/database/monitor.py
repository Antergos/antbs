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

from . import (
    RedisHash,
    status,
    get_pkg_object,
    get_repo_object
)

from utils import quiet_down_noisy_loggers
import iso_utility

logger = status.logger
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
    can_expire = ['checked_recently']

    def __init__(self, name):
        super().__init__(prefix='monitor', key=name)

        self.__namespaceinit__()

        if not self or not self.name:
            self.name = name

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

            except StopIteration:
                pass
            except Exception as err:
                logger.exception(err)

            return _latest

        latest = _get_next_item()
        logger.debug(latest)

        if not latest or (must_contain and must_contain not in latest):
            while not latest or (must_contain not in latest):
                latest = _get_next_item()
                items_checked += 1

                if items_checked > 5:
                    break

        logger.debug(latest)
        return latest

    def _sync_monitored_packages_list(self):
        pkg_objs = [get_pkg_object(name=p) for p in status.all_packages if p]
        monitored = [p.pkgname for p in pkg_objs if p.is_monitored]
        new_pkgs = list(set(monitored) - set(list(self.packages)))
        rm_pkgs = list(set(list(self.packages)) - set(monitored))

        if new_pkgs:
            for pkg in new_pkgs:
                self.packages.add(pkg)

        if rm_pkgs:
            for pkg in rm_pkgs:
                self.packages.remove(pkg)

    @staticmethod
    def add_iso_versions_to_wordpress(iso_pkgs):
        bridge = iso_utility.WordPressBridge(auth=(status.docker_user, status.wp_password))
        success = []
        for iso_pkg in iso_pkgs:
            success.append(bridge.add_new_iso_version(iso_pkg))
            logger.info(success)

        return all(success)

    @staticmethod
    def add_to_build_queue(pkgs, whook):
        req = dict(method='POST', args={})
        wh = whook(req)

        wh.is_numix = True
        wh.repo = 'antergos-packages'
        wh.changes = [pkgs]

        wh.process_changes()

    def check_github_repo_for_changes(self, pkg_obj, build_pkgs):
        gh = login(token=GITHUB_TOKEN)
        project = pkg_obj.monitored_project
        repo = pkg_obj.monitored_repo
        last_result = pkg_obj.monitored_last_result
        gh_repo = gh.repository(project, repo)
        numbers_only = ['arc-icon-theme', 'gtk-theme-arc']
        must_contain = '.' if pkg_obj.pkgname not in numbers_only else '2016'
        is_mate_pkg = 'mate' in pkg_obj.groups or 'mate-extra' in pkg_obj.groups

        pkg_obj.monitored_last_checked = self.datetime_to_string(datetime.now())

        if (is_mate_pkg or 'mate-' in pkg_obj.pkgname) and pkg_obj.pkgname not in ['galculator']:
            must_contain = '1.14' if 'themes' not in pkg_obj.pkgname else '3.20'

        latest = self._get_releases_tags_or_commits(gh_repo, pkg_obj.monitored_type, must_contain)

        if not latest and ('mate' in pkg_obj.groups or 'mate-extra' in pkg_obj.groups):
            latest = self._get_releases_tags_or_commits(gh_repo, 'tags', must_contain)

        if not latest or latest in ['None']:
            logger.error(
                '%s - latest: %s, last_result: %s, pkgver: %s',
                pkg_obj.pkgname, latest, last_result, pkg_obj.pkgver
            )
            return build_pkgs

        if 'v' in latest and 'commits' != pkg_obj.monitored_type:
            latest = latest.replace('v', '')

        if self.should_build_package(pkg_obj, latest, last_result):
            pkg_obj.monitored_last_result = latest
            build_pkgs.append(pkg_obj.name)

            if latest != pkg_obj.pkgver and pkg_obj.monitored_type in ['releases', 'tags']:
                pkg_obj.update_pkgbuild_and_push_github('pkgver', pkg_obj.pkgver, latest)

        return build_pkgs

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

        if len(synced) == 2:
            success = self.add_iso_versions_to_wordpress(synced)
            if success:
                iso_utility.clean_up_after_release(version)
                self.db.delete('antbs:misc:iso-release:do_check')
            else:
                logger.error('At least one iso was not successfully added to wordpress.')

    def check_repos_for_changes(self, webhook):
        self.checked_recently = (True, 3600)

        build_pkgs = []
        quiet_down_noisy_loggers()
        self._sync_monitored_packages_list()

        logger.info('Checking github repos for changes...')

        for pkg in self.packages:
            pkg_obj = get_pkg_object(name=pkg)

            if 'github' == pkg_obj.monitored_service:
                build_pkgs = self.check_github_repo_for_changes(pkg_obj, build_pkgs)
            elif 'gitlab' == pkg_obj.monitored_service:
                build_pkgs = self.check_gitlab_repo_for_changes(pkg_obj, build_pkgs)

            gevent.sleep(1.5)

        build_pkgs = [p for p in build_pkgs if p]

        if len(build_pkgs) > 0:
            self.add_to_build_queue(build_pkgs, webhook)

        if self.db.exists('antbs:misc:iso-release:do_check'):
            version = self.db.get('antbs:misc:iso-release:do_check')
            self.check_mirror_for_iso(version)

    @staticmethod
    def should_build_package(pkg_obj, latest, last_result):
        latest_is_new = not last_result or latest != last_result
        latest_not_pkgver = latest != pkg_obj.pkgver

        if latest_is_new or latest_not_pkgver:
            return True

        repo = get_repo_object('antergos', 'x86_64')
        staging_repo = get_repo_object('antergos-staging', 'x86_64')
        in_repo = in_staging_repo = None

        if repo.has_package_alpm(pkg_obj.pkgname):
            in_repo = repo.get_pkgver_alpm(pkg_obj.pkgname)

        if staging_repo.has_package_alpm(pkg_obj.pkgname):
            in_staging_repo = staging_repo.get_pkgver_alpm(pkg_obj.pkgname)

        repo_check = in_repo is not None and in_repo != pkg_obj.pkgver
        staging_repo_check = in_staging_repo is not None and in_staging_repo != pkg_obj.pkgver

        return repo_check and not staging_repo_check


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


def check_repos_for_changes(name, webhook):
    monitor_obj = get_monitor_object(name)
    monitor_obj.check_repos_for_changes(webhook)

