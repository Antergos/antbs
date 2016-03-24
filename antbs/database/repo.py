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

from database.base_objects import RedisHash
from database.server_status import status
from utils.logging_config import logger


class PacmanRepo(RedisHash):
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

    def __init__(self, name=None, path=None, prefix='repo'):
        if not name:
            raise RuntimeError

        super().__init__(prefix=prefix, key=name)

        self.key_lists.update(
                dict(string=['name', 'path'],
                     bool=[],
                     int=['pkg_count_alpm', 'pkg_count_fs'],
                     list=[],
                     set=['pkgs_fs', 'pkgs_alpm']))

        self.all_keys = [item for sublist in self.key_lists.values() for item in sublist]

        super().__namespaceinit__()

        if not self or not self.name:
            self.__keysinit__()
            self.name = name
            self.path = path
            status.repos.add(name)

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
            parsed_pkgs.append((pkg, pkgver))

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
                pkgs.append((pkgname, ver))

        self.pkgs_alpm = pkgs
        self.pkg_count_alpm = len(pkgs)
