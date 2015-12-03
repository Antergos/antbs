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

""" Repo Class """

import re
import os
import glob
import tarfile
from utils.logging_config import logger
from utils.server_status import status
from utils.redis_connection import RedisObject, RedisZSet, RedisList, db


class Repo(RedisObject):
    """
    This class represents a "repo" throughout this application. It is used to
    get and set metadata about repos that this application manages from/to the database.

    Args:
        :param name: (str) The name of the repo (as it would be configured in pacman.conf).
        :param path: (str) The absolute path to the repo's directory on the server.

    Attributes:
        (str)
            name: see args description above.
            path: see args description above.


        (bool)
            n/a

        (int)
            pkg_count: Total number of packages in the repo.

        (list)
            n/a

        (set)
            all_pkgs: List of the packages in the repo (pkg names)

    """

    def __init__(self, name=None, path=None):
        if not name:
            raise RuntimeError

        super(Repo, self).__init__()
        super(Repo, self).__namespaceinit__('repo', name)

        self.key_lists.update(dict(
                redis_string=['name', 'path'],
                redis_string_bool=[],
                redis_string_int=['pkg_count'],
                redis_list=[],
                redis_zset=['all_pkgs', 'unexpected_pkgs']))

        self.all_keys = [item for sublist in self.key_lists.values() for item in sublist]

        if not self:
            self.__keysinit__()
            self.name = name
            if not self.path:
                self.path = path
            self.sync_with_pacman_db()

    def sync_with_pacman_db(self):
        repodir = os.path.join(self.path, 'x86_64')
        dbfile = os.path.join(repodir, '%s.db.tar.gz' % self.name)
        pkgs = set(p for p in os.listdir(repodir) if '.pkg.' in p and not p.endswith('.sig'))
        print(pkgs)
        parsed_pkgs = dict()

        for pkg in pkgs:
            print(pkg)
            pkg = os.path.basename(pkg)
            print(pkg)
            try:
                pkg, version, rel, suffix = pkg.rsplit('-', 3)
                print(pkg, version, rel, suffix)
            except ValueError:
                logger.error("unexpected pkg: " + pkg)
                continue
            pkgver = version + '-' + rel
            try:
                parsed_pkgs[pkg].append((pkg, pkgver))
            except KeyError:
                parsed_pkgs[pkg] = [(pkg, pkgver)]

        with tarfile.open(dbfile, 'r') as pacman_db:
            for pkg in pacman_db.getnames():
                pkg = pkg.split('/', 1)[0]
                self.all_pkgs.add(pkg.rsplit('-', 2)[0])

        unexpected = sorted([x[0] for x in parsed_pkgs.values() if x[0] not in self.all_pkgs])

        setattr(self, 'unexpected_pkgs', unexpected)








