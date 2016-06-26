#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
#  mirror.py
#
#  Copyright © 2016 Antergos
#
#  This file is part of The Antergos Build Server, (AntBS).
#
#  AntBS is free software; you can redistribute it and/or modify
#  it under the terms of the GNU General Public License as published by
#  the Free Software Foundation; either version 3 of the License, or
#  (at your option) any later version.
#
#  AntBS is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#  GNU General Public License for more details.
#
#  The following additional terms are in effect as per Section 7 of the license:
#
#  The preservation of all legal notices and author attributions in
#  the material or in the Appropriate Legal Notices displayed
#  by works containing it is required.
#
#  You should have received a copy of the GNU General Public License
#  along with AntBS; If not, see <http://www.gnu.org/licenses/>.

import re
import os
import gevent
from urllib.parse import urlparse

from database.base_objects import RedisHash
from database.server_status import status
from utils.logging_config import logger
from utils.utilities import Singleton, remove
import utils.docker_util as docker_util

doc_util = docker_util.DockerUtils()
doc = doc_util.doc


class RepoMirror(RedisHash):
    """
    This class represents a "repo mirror" throughout this application. It is used to
    get/set metadata about the mirrors for the repos that this application manages
    from/to the database.

    Args:
        url (str): See `RepoMirror.url`

    Attributes:
        (str)
            url: The mirror's complete URL (as it appears in antergos-mirrorlist).
            domain: The domain portion of the repo's url (as it appears in antergos-mirrorlist).

        (bool)
            is_synced: Whether or not the mirror is fully synced with mirrorbrain server.

        (int)
            pkg_count: Total number of packages on the mirror (available for download).
            last_sync: When the mirror last synced with mirrorvrain server in Unix time.
            mnum: The mirror's unique id (assigned when mirror is initially added to database)

        (list)
            n/a

        (set)
            packages: Packages currently avaialble on the mirror (pkgname|url strings)
            missing_pkgs: Packages that should be available on the mirror but are not.
            extra_pkgs: Packages that are available on the mirror but should not be.
            protocols: The network protocols this mirror supports.
            all_urls: All urls for this mirror.

    """

    attrib_lists = dict(
        string=['url', 'domain', 'port', 'protocol'],

        bool=['is_synced', 'is_initialized'],

        int=['pkg_count', 'last_sync', 'mnum'],

        list=[],

        set=['pkgs', 'missing_pkgs', 'extra_pkgs', 'protocols', 'all_urls'],

        path=[]
    )

    def __init__(self, mnum=None, url=None, domain=None, prefix='mirror'):
        if not any(True for arg in [url, domain, mnum] if arg):
            raise ValueError('At least one of [url, domain, mnum] is required.')

        if not mnum:
            if not url:
                raise ValueError('url is required when mnum is None.')

            sep = ':'
            mnum = self.db.incr('{}{}{}{}{}'.format('antbs', sep, 'misc', sep, 'mnum'))

        domain = urlparse(url).replace('www.', '')
        port = re.match(r':\d+', domain.networkloc)

        if port:
            _domain = domain.replace(port.group(0), '')
        else:
            _domain = domain.networkloc

        super().__init__(prefix=prefix, key=domain)

        super().__namespaceinit__()

        if not self or not self.mnum:
            # This is a new mirror. Let's add it.
            self.domain = _domain
            self.url = url
            self.port = port if port else ''
            self.protocol = domain.scheme

            status.mirrors.add(domain)

            if not self.is_initialized:
                self.initialize_once()

    def setup_packages_manifest(self):
        pkgs_fs = set([p.split('|')[0] for p in self.pkgs_fs if p])
        pkgs_alpm = set([p.split('|')[0] for p in self.pkgs_alpm if p])
        pkgs = list(pkgs_fs & pkgs_alpm)
        unaccounted_for = [p.split('|')[0] for p in list(pkgs_fs) + list(pkgs_alpm) if p not in pkgs]

        for pk in self.packages:
            if pk not in pkgs:
                self.packages.remove(pk)

        for pkg in pkgs:
            self.packages.add(pkg)

        for pk in self.unaccounted_for:
            if pk in pkgs:
                self.unaccounted_for.remove(pk)

        for pak in unaccounted_for:
            self.unaccounted_for.add(pak)

    def _pkgname_matches(self, pkgname, match_in):
        pattern = r'{}\|'.format(pkgname)
        return re.match(pattern, match_in)

    def _get_pkgnames(self, location):
        return [p.split('|')[0] for p in location if p]

    def get_pkgnames_filesystem(self):
        return self._get_pkgnames(self.pkgs_fs)

    def get_pkgnames_alpm(self):
        return self._get_pkgnames(self.pkgs_alpm)

    def _get_pkgver(self, pkgname, location):
        pkgs = self._get_pkgnames(location)

        if pkgname not in pkgs:
            return ''

        pkgver = [p.split('|')[1] for p in location if p and self._pkgname_matches(pkgname, p)]

        logger.debug(pkgver)

        return pkgver[0] or ''

    def get_pkgver_alpm(self, pkgname):
        return self._get_pkgver(pkgname, self.pkgs_alpm)

    def get_pkgver_filesystem(self, pkgname):
        return self._get_pkgver(pkgname, self.pkgs_fs)

    def _has_package(self, pkgname, location):
        return pkgname in self._get_pkgnames(location)

    def has_package_filesystem(self, pkgname):
        return self._has_package(pkgname, self.pkgs_fs)

    def has_package_alpm(self, pkgname):
        return self._has_package(pkgname, self.pkgs_alpm)

    def save_repo_state_filesystem(self):
        repodir = os.path.join(self.path, 'x86_64')
        pkgs = set(p for p in os.listdir(repodir) if '.pkg.' in p and not p.endswith('.sig'))

        for pkg in pkgs:
            pkg = os.path.basename(pkg)
            try:
                pkg, version, rel, suffix = pkg.rsplit('-', 3)
            except ValueError:
                logger.error("unexpected pkg: " + pkg)
                continue

            self.pkgs_fs.add('{0}|{1}-{2}'.format(pkg, version, rel))

        self.pkg_count_fs = len(self.pkgs_fs)

    def save_repo_state_alpm(self):
        repodir = os.path.join(self.path, 'x86_64')
        dbfile = os.path.join(repodir, '%s.db.tar.gz' % self.name)

        with tarfile.open(dbfile, 'r') as pacman_db:
            for pkg in pacman_db.getnames():
                pkg = pkg.split('/', 1)[0]
                pkgname, ver, rel = pkg.rsplit('-', 2)

                self.pkgs_alpm.add('{0}|{1}-{2}'.format(pkgname, ver, rel))

        self.pkg_count_alpm = len(self.pkgs_alpm)

    def get_repo_packages_unaccounted_for(self):
        unaccounted_for = []

        if self.unaccounted_for:
            for pkg in self.unaccounted_for:
                _pkg = dict(pkgname=pkg, fs=None, alpm=None)

                if self.has_package_filesystem(pkg):
                    _pkg['fs'] = self.get_pkgver_filesystem(pkg)

                if self.has_package_alpm(pkg):
                    _pkg['alpm'] = self.get_pkgver_alpm(pkg)

                unaccounted_for.append(_pkg)

        return unaccounted_for

    def update_repo(self, bld_obj=False, pkg_obj=False, action=False, review_result=False,
                    result_dir='/tmp/update_repo_result'):

        if not any([bld_obj, review_result]):
            raise ValueError('at least one of [bld_obj, is_review] required.')

        repodir = 'staging' if 'staging' in self.name else 'main'
        trans_running = status.transactions_running or status.transaction_queue
        building_saved = False
        excluded = ['Updating antergos repo database.',
                    'Updating antergos-staging repo database.',
                    'Processing developer review result.']

        if not status.idle and trans_running and status.current_status not in excluded:
            building_saved = status.current_status
        elif status.idle:
            status.idle = False

        status.current_status = excluded[0] if 'main' == repodir else excluded[1]

        if os.path.exists(result_dir):
            remove(result_dir)

        os.mkdir(result_dir, 0o777)

        command = "/makepkg/build.sh"
        pkgenv = ["_PKGNAME={0}".format(bld_obj.pkgname),
                  "_PKGVER={0}".format(bld_obj.pkgver),
                  "_RESULT={0}".format(review_result),
                  "_UPDREPO=True",
                  "_REPO={0}".format(self.name),
                  "_REPO_DIR={0}".format(repodir)]

        doc_util.do_docker_clean("update_repo")

        hconfig = doc_util.get_host_config('repo_update', result_dir)
        volumes = ['/makepkg', '/root/.gnupg', '/main', '/result', '/staging']

        try:
            container = doc.create_container("antergos/makepkg", command=command,
                                             name="update_repo", environment=pkgenv,
                                             volumes=volumes, host_config=hconfig)

            cont = container.get('Id')
            bld_obj.repo_container = cont
            doc.start(cont)
            if not review_result:
                stream_process = Process(target=bld_obj.publish_build_output,
                                         kwargs=dict(upd_repo=True))
                stream_process.start()

            result = doc.wait(cont)
            if not review_result:
                stream_process.join()

            if int(result) != 0:
                logger.error('update repo failed. exit status is: %s', result)
            else:
                doc.remove_container(container, v=True)

        except Exception as err:
            result = 1
            logger.error('Start container failed. Error Msg: %s' % err)

        if not status.idle:
            if building_saved:
                status.current_status = building_saved
            elif not status.transactions_running and not status.now_building:
                status.idle = True
                status.current_status = 'Idle.'

        return result == 0


class AntergosRepo(PacmanRepo, metaclass=Singleton):
    def __init__(self, name='antergos', *args, **kwargs):
        super().__init__(name=name, *args, **kwargs)


class AntergosStagingRepo(PacmanRepo, metaclass=Singleton):
    def __init__(self, name='antergos-staging', *args, **kwargs):
        super().__init__(name=name, *args, **kwargs)


def get_repo_object(name, path=None):
    if not path:
        path = status.REPO_BASE_DIR
    if 'antergos' == name:
        repo = AntergosRepo(name=name, path=path)
    elif 'antergos-staging' == name:
        repo = AntergosStagingRepo(name=name, path=path)
    else:
        raise TypeError('name must be one of [antergos, antergos-staging]')

    return repo
