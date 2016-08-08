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
import re

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
        bool=['checked_recently', 'check_is_running'],
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

        self.repo_obj = self.staging_repo_obj = self.gh = None

    def _get_latest_release_tag_commit(self, gh_repo, what_to_get, pattern=None):
        git_item = getattr(gh_repo, what_to_get)
        res = git_item()
        latest = ''
        items_checked = 0

        def _get_next_item():
            _latest = ''
            try:
                item = res.next()

                if 'commits' == what_to_get:
                    _latest = item.sha
                elif 'releases' == what_to_get:
                    _latest = item.tag_name if not item.prerelease else ''
                elif 'tags' == what_to_get:
                    _latest = str(item)

            except StopIteration:
                pass
            except Exception as err:
                logger.exception(err)

            return _latest

        latest = _get_next_item()

        if not latest or (pattern and not self._matches_pattern(pattern, latest)):
            while not latest or (pattern and not self._matches_pattern(pattern, latest)):
                latest = _get_next_item()
                items_checked += 1

                if items_checked > 5:
                    break

        logger.debug(latest)
        return latest

    @staticmethod
    def _get_repo_objects(sync_repos):
        repo = get_repo_object('antergos', 'x86_64')
        staging_repo = get_repo_object('antergos-staging', 'x86_64')
        repo32 = get_repo_object('antergos', 'i686')
        staging_repo32 = get_repo_object('antergos-staging', 'i686')

        if sync_repos:
            repo.update_repo()
            staging_repo.update_repo()
            repo32.update_repo()
            staging_repo32.update_repo()
            status.repos_synced_recently = (True, 600)

        return repo, staging_repo

    def _matches_pattern(self, pattern, latest):
        matches = False

        if not pattern or not latest:
            return matches

        matches = pattern in latest

        if not matches and pattern.startswith('/') and pattern.endswith('/'):
            # Regular Expression
            pattern = pattern[1:-1]
            matches = re.fullmatch(pattern, latest)
            logger.debug('matches is %s', matches)

        return matches

    def _package_version_in_repos(self, pkgname, latest):
        version_in_repo = version_in_staging = None

        if self.repo_obj.has_package_alpm(pkgname):
            version_in_repo = self.repo_obj.get_pkgver_alpm(pkgname)
            version_in_repo, pkgrel = version_in_repo.rsplit('-', 1)
            logger.debug([self.repo_obj.name, version_in_repo, latest])

        if self.staging_repo_obj.has_package_alpm(pkgname):
            version_in_staging = self.staging_repo_obj.get_pkgver_alpm(pkgname)
            version_in_staging, pkgrel = version_in_staging.rsplit('-', 1)
            logger.debug([self.staging_repo_obj.name, version_in_repo, latest])

        in_repo = version_in_repo is not None and version_in_repo == latest
        in_staging = version_in_staging is not None and version_in_staging == latest

        logger.debug([pkgname, in_repo, in_staging])
        return in_repo or in_staging

    def _maybe_override_build(self, pkg_obj, latest):
        build_override = None

        if 'redis-desktop-manager' == pkg_obj.pkgname and re.search(r'-\d$', latest):
            latest = latest.replace('-', '.')
        elif 'package-query' == pkg_obj.pkgname and '1.8' == latest:
            build_override = False

        return build_override, latest

    def _sync_packages_list(self):
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
        if self.gh is None:
            self.gh = login(token=GITHUB_TOKEN)

        project = pkg_obj.mon_project
        repo = pkg_obj.mon_repo
        last_result = pkg_obj.mon_last_result
        mon_pattern = pkg_obj.mon_match_pattern
        gh_repo = self.gh.repository(project, repo)
        pattern = '.' if not mon_pattern else mon_pattern
        in_mate_group = any([g for g in ['mate', 'mate-extra'] if g in pkg_obj.groups])
        is_mate_pkg = in_mate_group or 'mate-' in pkg_obj.pkgname

        pkg_obj.mon_last_checked = self.datetime_to_string(datetime.now())

        latest = self._get_latest_release_tag_commit(gh_repo, pkg_obj.mon_type, pattern)

        if not latest and is_mate_pkg:
            latest = self._get_latest_release_tag_commit(gh_repo, 'tags', pattern)

        if not latest or latest in ['None']:
            logger.error(
                '%s - latest: %s, last_result: %s, pkgver: %s',
                pkg_obj.pkgname, latest, last_result, pkg_obj.pkgver
            )
            return build_pkgs

        if latest.startswith('v') and 'commits' != pkg_obj.mon_type:
            latest = latest[1:]

        build_override, latest = self._maybe_override_build(pkg_obj, latest)

        do_build = build_override if build_override is not None else latest != pkg_obj.pkgver

        pkg_obj.mon_last_result = latest

        if do_build or not self._package_version_in_repos(pkg_obj, latest):
            build_pkgs.append(pkg_obj.name)

            if pkg_obj.mon_type in ['releases', 'tags']:
                pkg_obj.update_pkgbuild_and_push_github('pkgver', pkg_obj.pkgver, latest)

        return build_pkgs

    def check_gitlab_repo_for_changes(self, pkg_obj, build_pkgs):
        gl = Gitlab('https://gitlab.com', GITLAB_TOKEN)
        gl.auth()
        project_id = pkg_obj.mon_project
        repo = pkg_obj.mon_repo
        project = gl.projects.get(project_id)
        last_result = pkg_obj.mon_last_result
        events = project.events.list()

        for event in events:
            if event.action_name == 'pushed to':
                if event.created_at != last_result:
                    pkg_obj.mon_last_result = event.created_at
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

    def check_repos_for_changes(self, check_github, sync_repos, webhook):
        self.repo_obj, self.staging_repo_obj = self._get_repo_objects(sync_repos)

        if not check_github:
            return

        self.checked_recently = (True, 3600)

        build_pkgs = []
        quiet_down_noisy_loggers()
        self._sync_packages_list()

        logger.info('Checking github repos for changes...')

        for pkg in self.packages:
            pkg_obj = get_pkg_object(name=pkg, fetch_pkgbuild=True)

            if 'github' == pkg_obj.mon_service:
                build_pkgs = self.check_github_repo_for_changes(pkg_obj, build_pkgs)
            elif 'gitlab' == pkg_obj.mon_service:
                build_pkgs = self.check_gitlab_repo_for_changes(pkg_obj, build_pkgs)

            gevent.sleep(0.5)

        build_pkgs = [p for p in build_pkgs if p]

        if len(build_pkgs) > 0:
            self.add_to_build_queue(build_pkgs, webhook)

        if self.db.exists('antbs:misc:iso-release:do_check'):
            version = self.db.get('antbs:misc:iso-release:do_check')
            self.check_mirror_for_iso(version)


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


def check_repos_for_changes(check_github, sync_repos, webhook):
    monitor_obj = get_monitor_object('github')

    monitor_obj.check_repos_for_changes(check_github, sync_repos, webhook)

    if check_github:
        monitor_obj.check_is_running = False

    if sync_repos:
        status.repos_syncing = False

