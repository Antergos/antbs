#!/usr/bin/python
# -*- coding: utf-8 -*-
#
# transaction_handler.py
#
# Copyright © 2013-2017 Antergos
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
    Monitors activity on 3rd-party repos and schedules builds
    when new commits|tags|releases are detected.
"""

from datetime import datetime
import re
import xml.etree.ElementTree as ET

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

from utils import (
    quiet_down_noisy_loggers,
    CheckSumsMonitor,
    GithubMonitor,
    RemoteFileMonitor,
    set_server_status
)

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
        string=['name', 'mate_last_etag'],
        bool=['checked_recently', 'check_is_running', 'et_stats_checked_today'],
        int=[],
        list=[],
        set=['packages'],
        path=[]
    )
    can_expire = ['checked_recently', 'et_stats_checked_today']

    def __init__(self, name):
        super().__init__(prefix='monitor', key=name)

        self.__namespaceinit__()

        if not self or not self.name:
            self.name = name

        self.repo_obj = self.staging_repo_obj = self.gh = self.mate = self.remote_file = None

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

    def _get_antergos_packages_repo_head_sha(self):
        self.gh.set_repo('antergos', 'antergos-packages')
        latest_commit = [c for c in self.gh.repo.commits(number=1)]
        return '' if not latest_commit else latest_commit[0].sha

    @staticmethod
    def _handle_custom_xml_special_cases(elements, pkg_obj):
        result = None

        for element in elements:
            if pkg_obj.auto_sum:
                # JetBrains IDE
                version = element.get('version')
                build = element.get('fullNumber')

                if version and build:
                    result = '{}.{}'.format(version, build)

                    break

        return result

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

        return in_repo or in_staging

    # def process_custom_xml_elements(self, elements, pkg_obj):
    #     result = self._handle_custom_xml_special_cases(elements, pkg_obj)
    #
    #     if result:
    #         return result
    #
    #     for index, item in enumerate(elements):

    @staticmethod
    def _maybe_override_build(pkg_obj, latest):
        build_override = None

        if 'redis-desktop-manager' == pkg_obj.pkgname and re.search(r'-\d$', latest):
            latest = latest.replace('-', '.')
        elif 'package-query' == pkg_obj.pkgname and '1.8' == latest:
            build_override = False
        elif 'pamac-dev' == pkg_obj.pkgname and latest == pkg_obj.mon_last_result:
            build_override = False
        elif 'beta' in pkg_obj.mon_last_result or 'alpha' in pkg_obj.mon_last_result:
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
    def add_to_build_queue(pkgs, whook, before, after):
        req = dict(method='POST', args={})
        wh = whook(req)

        wh.is_numix = True
        wh.repo = 'antergos-packages'
        wh.changes = [pkgs]
        wh.payload = dict(before=before, after=after)

        wh.process_changes()

    # def check_custom_xml_for_changes(self, pkg_obj, build_pkgs):
    #     url = pkg_obj.mon_type
    #     req = requests.head(url)
    #
    #     if req.headers['ETag'] == pkg_obj.mon_etag:
    #         return build_pkgs
    #
    #     pkg_obj.mon_etag = req.headers['ETag']
    #
    #     try:
    #         xml_data = requests.get(url).text
    #         root = ET.fromstring(xml_data)
    #
    #         releases = root.findall(pkg_obj.mon_xpath)
    #         latest_release = None if not releases else releases[0]
    #
    #         if latest_release and pkg_obj.mon_match_pattern:
    #             latest
    #         version = latest_release.get('version')
    #         build = latest_release.get('fullNumber')
    #
    #         if ' ' in version:
    #             version = version.split(' ')[0]
    #
    #         latest = '{}|{}'.format(version, build)
    #
    #         if pkg_obj.mon_last_reselt and latest != pkg_obj.mon_last_result:
    #             build_pkgs.append(pkg_obj.pkgname)
    #
    #             pkg_obj.update_pkgbuild_and_push_github({
    #                 '_pkgver': (pkg_obj._pkgver, version),
    #                 '_buildver': (pkg_obj._buildver, build)
    #             })
    #
    #         pkg_obj.mon_last_result = latest
    #
    #     except Exception as err:
    #         logger.exception(err)
    #
    #     return build_pkgs

    def check_github_repo_for_changes(self, pkg_obj):
        if self.gh is None:
            self.gh = GithubMonitor(token=GITHUB_TOKEN, status=status)

        self.gh.set_repo(pkg_obj.mon_project, pkg_obj.mon_repo)

        return self.gh.package_source_changed(pkg_obj)

    @staticmethod
    def check_gitlab_repo_for_changes(pkg_obj, build_pkgs):
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

    def check_mate_desktop_server_for_changes(self, pkg_obj):
        if self.mate is None:
            url = 'http://pub.mate-desktop.org/releases/1.18/SHA1SUMS'
            # url = 'http://pub.mate-desktop.org/releases/{}/SHA1SUMS'.format()
            self.mate = CheckSumsMonitor(url, self.mate_last_etag, status=status)
            self.mate_last_etag = self.mate.etag

        return self.mate.package_source_changed(pkg_obj)

    def check_remote_http_resource_for_changes(self, pkg_obj, ):
        if self.remote_file is None:
            self.remote_file = RemoteFileMonitor(pkg_obj, status)

        return self.remote_file.package_source_changed(pkg_obj)

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

        saved_status = set_server_status(first=True, is_monitor=True)

        build_pkgs = []
        first = list(self.packages)[0]
        last = list(self.packages)[-1]
        before = ''
        after = ''

        quiet_down_noisy_loggers()
        self._sync_packages_list()

        for pkg in self.packages:
            pkg_obj = get_pkg_object(name=pkg, fetch_pkgbuild=True)
            changed = monitor_obj = False

            if pkg_obj.is_split_package:
                continue

            if 'github' == pkg_obj.mon_service:
                changed = self.check_github_repo_for_changes(pkg_obj)
                monitor_obj = self.gh

                if pkg == first:
                    before = self._get_antergos_packages_repo_head_sha()
                elif pkg == last:
                    after = self._get_antergos_packages_repo_head_sha()

            elif 'gitlab' == pkg_obj.mon_service:
                changed = self.check_gitlab_repo_for_changes(pkg_obj, build_pkgs)
                monitor_obj = ''

            elif 'http' == pkg_obj.mon_service:
                changed = self.check_remote_http_resource_for_changes(pkg_obj)
                monitor_obj = self.remote_file

            elif 'mate-desktop' == pkg_obj.mon_service:
                changed = self.check_mate_desktop_server_for_changes(pkg_obj)
                monitor_obj = self.mate

            pkg_obj.mon_last_checked = self.datetime_to_string(datetime.now())

            logger.debug(
                '%s - latest: %s, last_result: %s, pkgver: %s',
                pkg_obj.pkgname, monitor_obj.latest, pkg_obj.mon_last_result, pkg_obj.pkgver
            )

            if changed:
                build_pkgs = self.process_package_source_change(pkg_obj, monitor_obj, build_pkgs)

            elif not pkg_obj.mon_last_result:
                pkg_obj.mon_last_result = monitor_obj.latest

            gevent.sleep(0.5)

        build_pkgs = [p for p in build_pkgs if p]

        set_server_status(first=False, saved_status=saved_status)

        if len(build_pkgs) > 0:
            self.add_to_build_queue(build_pkgs, webhook, before, after)

        if self.db.exists(status.iso_release_check_key):
            version = self.db.get(status.iso_release_check_key)
            self.check_mirror_for_iso(version)

        self.checked_recently = (True, 3600)

    def process_package_source_change(self, pkg_obj, monitor_obj, build_pkgs):
        if monitor_obj.latest.startswith('v') and 'commits' != pkg_obj.mon_type:
            monitor_obj.latest = monitor_obj.latest[1:]

        build_override, monitor_obj.latest = self._maybe_override_build(pkg_obj, monitor_obj.latest)

        do_build = build_override if build_override is not None else monitor_obj.latest != pkg_obj.pkgver

        pkg_obj.mon_last_result = monitor_obj.latest

        if not do_build and build_override is None:
            do_build = not self._package_version_in_repos(pkg_obj.pkgname, monitor_obj.latest)

        if do_build:
            build_pkgs.append(pkg_obj.pkgname)

            if 'commits' == pkg_obj.mon_type:
                version_str = pkg_obj.get_version_str()
                latest = version_str.split('-')[0]
            else:
                latest = monitor_obj.latest

            changes = {'pkgver': (pkg_obj.pkgver, latest)}

            pkg_obj.update_pkgbuild_and_push_github(changes)

        return build_pkgs


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

    status.cleanup_all_packages_list(get_pkg_object)
    monitor_obj.check_repos_for_changes(check_github, sync_repos, webhook)

    if check_github:
        monitor_obj.check_is_running = False

    if sync_repos:
        status.repos_syncing = False

