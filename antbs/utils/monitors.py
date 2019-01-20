#!/usr/bin/python
# -*- coding: utf-8 -*-
#
# monitors.py
#
# Copyright © 2016-2017 Antergos
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

import xml.etree.ElementTree as ET
import re
from random import randrange

import requests
from github3 import login
from gitlab import Gitlab


class PackageSourceMonitor:
    """
    Base class for all monitors.

    Class Attributes:
        logger           (Logging.Handler) Current logging handler (status.logger)
        status           (ServerStatus)    Current ServerStatus instance.

    """

    status = None
    logger = None

    def __init__(self, status):
        if self.status is None:
            self.status = status
            self.logger = status.logger
            self.latest = None

    @staticmethod
    def _empty(value):
        return value in ['', 'None', None, False]

    @staticmethod
    def _matches_pattern(pattern, latest):
        matches = False
        pattern = pattern or '.'

        if not latest:
            return matches

        matches = pattern in latest

        if not matches and pattern.startswith('/') and pattern.endswith('/'):
            # Regular Expression
            pattern = pattern[1:-1]
            matches = re.search(pattern, latest)

        return matches

    def package_source_changed(self, pkg_obj):
        last_result = pkg_obj.mon_last_result
        return not self._empty(self.latest) and self.latest != last_result


class WebMonitor(PackageSourceMonitor):
    """
    Base class for monitors which watch a remote HTTP resource for changes.

    Attributes:
        changed         (bool) Whether or not the current etag equals the one provided.
        url             (str)  The url for the monitored web resource.
        remote_resource (dict) Remote resource content and metadata.

    See Also:
        PackageSourceMonitor.__doc__()
    """
    def __init__(self, url, last_etag, status):
        super().__init__(status)

        self.url = url
        self.files = {}
        self.remote_resource = {}
        self.etag = self._get_etag()
        self.changed = self.etag != last_etag

        if self.changed:
            self.download_remote_resource()

    def _get_etag(self):
        try:
            req = requests.head(self.url)
            req.raise_for_status()
        except Exception as err:
            self.logger.exception(err)
            return ''

        return req.headers.get('ETag', str(randrange(0, 1000000)))

    def _process_remote_resource(self):
        raise NotImplementedError

    def download_remote_resource(self):
        try:
            resource = requests.get(self.url)
            resource.raise_for_status()
        except Exception as err:
            self.logger.exception(err)
            return

        if resource:
            self.remote_resource['text'] = resource.text
            self.remote_resource['etag'] = resource.headers['ETag']
            self.remote_resource['lines'] = resource.text.split('\n')

        self._process_remote_resource()


class CheckSumsMonitor(WebMonitor):
    """
    Monitors a remote HTTP resource containing a list of files and their checksums.

    Attributes:
        files (dict) Files listed in the monitored resource.

    See Also:
        WebMonitor.__doc__
    """
    @staticmethod
    def _get_file_extension_with_compression_type(file):
        parts = file.partition('.tar.')
        return '{}{}'.format(parts[1], parts[2])

    def _get_pkgname_and_pkgver_from_file_name(self, file):
        extension = self._get_file_extension_with_compression_type(file)
        file = file.replace(extension, '')

        if '-' == file[-2]:
            # Hyphens are not allowed in pkgver
            file[-2] = '_'

        return file.rsplit('-', 1)

    def _process_remote_resource(self):
        for line in self.remote_resource['lines']:
            line = line.strip()

            if not line:
                continue

            checksum, file = line.split()
            name, version = self._get_pkgname_and_pkgver_from_file_name(file)

            self.files[name] = {
                'version': version,
                'checksum': checksum
            }

    def get_latest_version_for_package(self, pkg_obj):
        return '' if pkg_obj.pkgname not in self.files else self.files[pkg_obj.pkgname]['version']

    def package_source_changed(self, pkg_obj, result=None):
        if not self.changed:
            return False

        self.latest = self.get_latest_version_for_package(pkg_obj)
        return super().package_source_changed(pkg_obj)


class GithubMonitor(PackageSourceMonitor):

    def __init__(self, token, project=None, repo=None, mon_type='release', status=None):
        super().__init__(status)
        self.type = mon_type
        self.gh = login(token=token)
        self.project_name = project
        self.repo_name = repo
        self.last_etag = None
        self.etag = None
        self.repo = None
        self.changed = False

        if project and repo:
            self.set_repo(project=project, repo=repo)

    def _get_latest(self, what_to_get, pkg_obj=None):
        if self.repo is None:
            self._repo_not_set_error()

        git_item = getattr(self.repo, what_to_get)
        res = git_item(etag=pkg_obj.mon_etag)
        items_checked = 0
        pattern = pkg_obj.mon_match_pattern or '.'
        # self.logger.debug([git_item, res, pattern])

        def _get_next_item():
            _latest = etag = ''

            try:
                item = res.next()
                etag = item.etag

                if 'commits' == what_to_get:
                    _latest = item.sha
                elif 'releases' == what_to_get:
                    _latest = item.tag_name if not item.prerelease else ''
                elif 'tags' == what_to_get:
                    _latest = str(item)

            except StopIteration:
                pass
            except Exception as err:
                self.logger.exception(err)

            return _latest, etag

        latest, etag = _get_next_item()

        if not latest or (pattern and not self._matches_pattern(pattern, latest)):
            while not latest or (pattern and not self._matches_pattern(pattern, latest)):
                latest, etag = _get_next_item()
                items_checked += 1

                if items_checked > 50:
                    break

        if latest and pattern and pattern.startswith('/') and pattern.endswith('/'):
            pattern = pattern[1:-1]
            matches = re.search(pattern, latest)

            try:
                latest = matches.group(1)
            except IndexError:
                latest = matches.group(0)
            except Exception:
                pass

        if 'commits' != what_to_get and latest.startswith('v'):
            latest = latest[1:]

        pkg_obj.mon_etag = etag
        return latest

    @staticmethod
    def _repo_not_set_error():
        raise AttributeError('repo is not set!')

    def get_latest_commit(self, pattern=None):
        return self._get_latest('commits', pattern)

    def get_latest_release(self, pattern=None):
        return self._get_latest('releases', pattern)

    def get_latest_tag(self, pattern=None):
        return self._get_latest('tags', pattern)

    def package_source_changed(self, pkg_obj, change_type=None, change_id=None):
        change_type = change_type or pkg_obj.mon_type
        self.latest = change_id or self._get_latest(change_type, pkg_obj)
        return super().package_source_changed(pkg_obj)

    def set_repo(self, project=None, repo=None):
        self.project_name = project if project is not None else self.project_name
        self.repo_name = repo if repo is not None else self.repo_name

        if not (self.project_name and self.repo_name):
            raise ValueError('Both project and repo are required in order to set repo!')

        self.repo = self.gh.repository(self.project_name, self.repo_name)


class GitlabMonitor(PackageSourceMonitor):

    def __init__(self, url, token, project=None, mon_type='release', status=None):
        super().__init__(status)
        self.type = mon_type
        self.project_name = project
        self.last_etag = None
        self.etag = None
        self.repo = None
        self.changed = False

        if project:
            self.set_project(project=project, url=url, token=token)

    def _get_latest(self, what_to_get, pkg_obj=None):
        if self.project is None:
            self._project_not_set_error()

        if 'releases' == what_to_get:
            what_to_get = 'tags'

        git_item = getattr(self.project, what_to_get)
        res = [i for i in git_item.list()]
        items_checked = 0
        pattern = pkg_obj.mon_match_pattern or '.'

        # self.logger.debug([git_item, res, pattern])

        def _get_next_item():
            _latest = etag = ''

            try:
                item = res.next()

                if 'commits' == what_to_get:
                    _latest = item['sha']
                    etag = item['created_at']
                elif 'releases' == what_to_get:
                    _latest = item['release']['tag_name']
                    etag = item.commit['created_at']
                elif 'tags' == what_to_get:
                    _latest = item['release']['tag_name']
                    etag = item.commit['created_at']

            except StopIteration:
                pass
            except Exception as err:
                self.logger.exception(err)

            return _latest, etag

        latest, etag = _get_next_item()

        if not latest or (pattern and not self._matches_pattern(pattern, latest)):
            while not latest or (pattern and not self._matches_pattern(pattern, latest)):
                latest, etag = _get_next_item()
                items_checked += 1

                if items_checked > 50:
                    break

        if latest and pattern and pattern.startswith('/') and pattern.endswith('/'):
            pattern = pattern[1:-1]
            matches = re.search(pattern, latest)

            try:
                latest = matches.group(1)
            except IndexError:
                latest = matches.group(0)
            except Exception:
                pass

        if 'commits' != what_to_get and latest.startswith('v'):
            latest = latest[1:]

        pkg_obj.mon_etag = etag
        return latest

    @staticmethod
    def _project_not_set_error():
        raise AttributeError('project is not set!')

    def get_latest_commit(self, pattern=None):
        return self._get_latest('commits', pattern)

    def get_latest_release(self, pattern=None):
        return self._get_latest('tags', pattern)

    def get_latest_tag(self, pattern=None):
        return self._get_latest('tags', pattern)

    def package_source_changed(self, pkg_obj, change_type=None, change_id=None):
        change_type = change_type or pkg_obj.mon_type
        self.latest = change_id or self._get_latest(change_type, pkg_obj)
        return super().package_source_changed(pkg_obj)

    def set_project(self, project=None, url=None, token=None):
        self.project_name = project if project is not None else self.project_name
        self.url = url if url is not None else self.url
        self.token = token if token is not None else self.token

        if not (self.project_name and self.url and self.token):
            raise ValueError('project, url, and token are required in order to set project!')

        self.gl = Gitlab(url, token)

        self.gl.auth()

        self.project = self.gl.projects.get(self.project_name)


class RemoteFileMonitor(WebMonitor):

    def __init__(self, pkg_obj, status):
        self.page_url = pkg_obj.mon_version_url

        super().__init__(pkg_obj.mon_file_url, pkg_obj.mon_etag, status)

    def _get_version(self, pkg_obj):
        pattern = pkg_obj.mon_version_pattern

        if '/' == pattern[0] and '/' == pattern[-1]:
            pattern = pattern[1:-1]

        matches = re.search(pattern, self.remote_resource['text'], flags=re.M)
        return '' if not matches else matches.group(1)

    def _process_remote_resource(self):
        pass

    def download_remote_resource(self):
        try:
            resource = requests.get(self.page_url)
            resource.raise_for_status()
        except Exception as err:
            self.logger.exception(err)
            return

        if resource:
            self.remote_resource['text'] = resource.text
            self.remote_resource['lines'] = resource.text.split('\n')

        self._process_remote_resource()

    def package_source_changed(self, pkg_obj, result=None):
        if not self.changed:
            return False

        self.latest = self._get_version(pkg_obj)
        return super().package_source_changed(pkg_obj)

