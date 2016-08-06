#!/usr/bin/env python
#  -*- coding: utf-8 -*-
#
#  package_meta.py
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


from database import RedisHash, status


class PackageMeta(RedisHash):
    """
    This is the base class for ::class:`Package`. It initalizes the fields for
    the package metadata that is stored in the database. You should not use this
    class directly.

    """

    attrib_lists = dict(
        string=[
            'description',
            'epoch',
            'failure_rate',
            'gh_path',
            'gh_repo',
            'git_name',
            'git_url',
            'heat_map',
            'iso_md5',
            'iso_url',
            'mon_last_checked',
            'mon_last_result',
            'mon_project',
            'mon_repo',
            'mon_match_pattern',
            'mon_service',
            'mon_type',
            'name',
            'pbpath',
            'pkgbuild',
            'pkgdesc',
            'pkgname',
            'pkgrel',
            'pkgver',
            'short_name',
            'success_rate',
            'url',
            'version_antergos',
            'version_antergos_staging',
            'version_str'
        ],

        bool=[
            'auto_sum',
            'is_initialized',
            'is_iso',
            'is_metapkg',
            'is_monitored',
            'is_split_package',
            'push_version'
        ],

        int=['pkg_id'],

        list=[
            'allowed_in',
            'builds',
            'split_packages',
            'tl_events',
            'transactions'
        ],

        path=[],

        set=[
            'depends',
            'groups',
            'makedepends'
        ]
    )

    def __init__(self, namespace='antbs', prefix='pkg', key='', *args, **kwargs):
        super().__init__(namespace=namespace, prefix=prefix, key=key, *args, **kwargs)

        self.__namespaceinit__()

        if (not self or not self.pkg_id) and self.is_package_on_github(name=key):
            # Package is not in the database, so it must be new. Let's initialize it.
            self.pkgname = key
            self.name = key

            next_id = self.db.incr('antbs:misc:pkgid:next')
            self.pkg_id = next_id

            status.all_packages.add(self.name)

    def is_package_on_github(self, name=None):
        raise NotImplementedError('Subclass must implement this method')
