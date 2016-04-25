#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
#  repo.py
#
#  Copyright © 2015 Antergos
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


import os
import tarfile
import gevent
from multiprocessing import Process

from database.base_objects import RedisHash
from database.server_status import status
from utils.logging_config import logger
from utils.utilities import Singleton, remove
import utils.docker_util as docker_util

doc_util = docker_util.DockerUtils()
doc = doc_util.doc


class PacmanRepo(RedisHash, metaclass=Singleton):
    """
    This class represents a "repo" throughout this application. It is used to
    get/set metadata about the repos that this application manages from/to the database.

    Args:
        name (str): The name of the repo (as it would be configured in pacman.conf).
        path (str): The absolute path to the repo's directory on the server.

    Attributes:
        (str)
            name: see args description above.
            path: see args description above.

        (bool)
            n/a

        (int)
            pkg_count_alpm: Total number of packages in the repo (as per alpm database).
            pkg_count_fs: Total number of packages in the repo (files found on server).

        (list)
            n/a

        (set)
            pkgs_fs: List of the package files in the repo's directory on the server (pkg names)
            pkgs_alpm: List of packages that are in the repo's alpm database file (this is what pacman sees).

    """

    def __init__(self, name=None, path='/srv/antergos.info/repo', prefix='repo'):
        if not name:
            raise RuntimeError

        super().__init__(prefix=prefix, key=name)

        self.key_lists.update(
                dict(string=['name'],
                     bool=['locked'],
                     int=['pkg_count_alpm', 'pkg_count_fs'],
                     list=[],
                     set=['pkgs_fs', 'pkgs_alpm'],
                     path=['path']))

        self.all_keys = [item for sublist in self.key_lists.values() for item in sublist]

        super().__namespaceinit__()

        if not self or not self.name:
            self.__keysinit__()
            self.name = name
            self.path = os.path.join(path, name)
            status.repos.add(name)

        self.sync_with_alpm_db()
        self.sync_with_filesystem()

    def sync_with_filesystem(self):
        repodir = os.path.join(self.path, 'x86_64')
        pkgs = set(p for p in os.listdir(repodir) if '.pkg.' in p and not p.endswith('.sig'))
        parsed_pkgs = []

        for pkg in pkgs:
            pkg = os.path.basename(pkg)
            try:
                pkg, version, rel, suffix = pkg.rsplit('-', 3)
            except ValueError:
                logger.error("unexpected pkg: " + pkg)
                continue
            pkgver = version + '-' + rel
            parsed_pkgs.append('{0}|{1}'.format(pkg, pkgver))

        self.pkgs_fs = parsed_pkgs
        self.pkg_count_fs = len(parsed_pkgs)

    def sync_with_alpm_db(self):
        repodir = os.path.join(self.path, 'x86_64')
        dbfile = os.path.join(repodir, '%s.db.tar.gz' % self.name)
        pkgs = []

        with tarfile.open(dbfile, 'r') as pacman_db:
            for pkg in pacman_db.getnames():
                pkg = pkg.split('/', 1)[0]
                pkgname, ver = pkg.rsplit('-', 2)
                pkgs.append('{0}|{1}'.format(pkgname, ver))

        self.pkgs_alpm = pkgs
        self.pkg_count_alpm = len(pkgs)

    def update_repo(self, review_result=None, bld_obj=None, is_review=False, rev_pkgname=None,
                    is_action=False, action=None, action_pkg=None, result_dir=None,
                    publish_build_output=None):
        if not review_result:
            raise ValueError('review_result cannot be None.')
        elif not any([bld_obj, is_review]):
            raise ValueError('at least one of [bld_obj, is_review] required.')

        container = None
        rev_result = review_result
        repodir = 'staging' if 'staging' in self.name else 'main'

        if self.locked:
            while self.locked:
                gevent.sleep(2)

        self.locked = True

        building_saved = False
        if not status.idle and status.current_status != 'Updating repo database.':
            building_saved = status.current_status
        else:
            status.idle = False
        status.current_status = 'Updating repo database.'

        if os.path.exists(result_dir):
            remove(result_dir)
        os.mkdir(result_dir, 0o777)

        if rev_pkgname is not None:
            pkgname = rev_pkgname
        else:
            pkgname = bld_obj.pkgname

        command = "/makepkg/build.sh"
        pkgenv = ["_PKGNAME={0}".format(pkgname),
                  "_RESULT={0}".format(review_result),
                  "_UPDREPO=True",
                  "_REPO={0}".format(self.name),
                  "_REPO_DIR={0}".format(repodir)]

        self.do_docker_clean("update_repo")
        hconfig = doc_util.get_host_config('repo_update', self.upd_repo_result)

        try:
            container = doc.create_container("antergos/makepkg", command=command,
                                             name="update_repo", environment=pkgenv,
                                             volumes=['/makepkg', '/root/.gnupg', '/main',
                                                      '/result', '/staging'],
                                             host_config=hconfig)

            cont = container.get('Id')
            doc.start(cont)
            if not is_review:
                stream_process = Process(target=publish_build_output,
                                         kwargs=dict(container=cont,
                                                     bld_obj=bld_obj,
                                                     upd_repo=True,
                                                     tnum=self.tnum))
                stream_process.start()

            result = doc.wait(cont)
            if not is_review:
                stream_process.join()

            if result != 0:
                logger.error('update repo failed. exit status is: %s', result)
            else:
                doc.remove_container(container, v=True)

        except Exception as err:
            result = 1
            logger.error('Start container failed. Error Msg: %s' % err)

        if is_review:
            if not status.idle:
                if building_saved:
                    status.current_status = building_saved
                else:
                    status.idle = True
                    status.current_status = 'Idle.'

        if result != 0:
            return False
        else:
            return True


def get_repo_object(name, path=None):
    return PacmanRepo(name=name, path=path)

