#  -*- coding: utf-8 -*-
#
#  repo_meta.py
#
#  Copyright © 2016-2017 Antergos
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

from database import RedisHash, status


class PacmanRepoMetadata(RedisHash):
    """
    This is the base class for ::class:`PacmanRepo`. It initializes the fields for
    the package metadata that is stored in the database. You should not use this
    class directly.
    
    Attributes:
        attrib_lists (dict[str, list[str]])
    """

    attrib_lists = dict(
        string=[
            'alpm_db',
            'arch',
            'name'
        ],

        bool=[
            'locked'
        ],

        int=[
            'pkg_count_alpm',
            'pkg_count_fs'
        ],

        list=[],

        path=[
            'alpm_db_path',
            'path'
        ],

        set=[
            'packages',
            'pkgs_alpm',
            'pkgs_fs',
            'pkgnames',
            'unaccounted_for'
        ]
    )

    def __init__(self, name, arch, path=None, prefix='repo', *args, **kwargs):
        key = '{}:{}'.format(name, arch)

        super().__init__(prefix=prefix, key=key, *args, **kwargs)

        self.__namespaceinit__()

        if not self or not self.name:
            if path is None:
                raise ValueError('path cannot be None when adding a new repo to the database!')

            self.name = name
            self.arch = arch
            self.path = os.path.join(path, name, arch)
            self.alpm_db = '{}.db.tar.gz'.format(name)
            self.alpm_db_path = os.path.join(path, name, arch, self.alpm_db)
            status.repos.add(name)
