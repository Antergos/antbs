#!/usr/bin/env python
#  -*- coding: utf-8 -*-
#
#  repo.py
#
#  Copyright © 2016  Antergos
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

from . import *


class RepoView(FlaskView):

    def _get_repo_packages(self, repo_name=None, filter=None, filter_by=None, page=1):
        if repo_name is None:
            abort(500)

        pkgs = []
        bld_obj = None
        all_pages = 0
        repo_obj = get_repo_object(repo_name, 'x86_64')

        if current_user.is_authenticated:
            rev_pending = []
        else:
            rev_pending = []

        if not repo_obj.pkgnames and not repo_obj.locked:
            repo_obj.update_repo()
            return pkgs, rev_pending, all_pages

        if filter and 'group' == filter:
            repo_packages = [p for p in sorted(repo_obj.pkgnames) if package_in_group(p, filter_by)]

        elif filter and 'search' == filter:
            repo_packages = [p for p in sorted(repo_obj.pkgnames) if filter_by in p]

        else:
            repo_packages = [p for p in sorted(repo_obj.pkgnames)]

        packages, all_pages = get_paginated(repo_packages, 10, page, reverse=False)

        for pkg in packages:
            if 'dummy' in pkg or 'grub-zfs' in pkg:
                continue

            try:
                pkg_obj = get_pkg_object(pkg)
            except Exception as err:
                logger.error(err)
                continue

            try:
                bnum = pkg_obj.builds[-1]
                if bnum:
                    bld_obj = get_build_object(bnum=bnum)
            except Exception:
                continue

            pkg_obj._build = bld_obj
            pkgs.append(pkg_obj)

        return pkgs, rev_pending, all_pages

    @route('/<name>/packages/<filter>/<filter_by>')
    @route('/<name>/packages/<filter>/<filter_by>/<int:page>')
    @route('/<name>/packages/<int:page>')
    @route('/<name>/packages')
    def repo_packages_listing(self, name=None, filter=None, filter_by=None, page=1):
        name_ok = name and name in status.repos
        filter_ok = 'search' == filter or ('group' == filter and filter_by in status.package_groups)

        if not name_ok or (filter and not filter_ok):
            abort(404)

        packages, rev_pending, all_pages = self._get_repo_packages(name, filter, filter_by, page)
        _pagination = Pagination(page, 10, all_pages)
        columns_info_obj = ColumnsInfo(current_user)
        _columns_info = columns_info_obj.columns_info

        return try_render_template('repo/packages.html', objs=packages,
                                   pagination=_pagination, all_pages=all_pages,
                                   columns_info=_columns_info, rev_pending=rev_pending,
                                   table_heading=name)

    @route('/browse/<goto>')
    @route('/browse')
    def browse(self, goto=None):
        # TODO: This needs a rewrite.
        building = status.now_building
        release = False
        testing = False
        main = False
        template = "repo/repo_browser.html"
        if goto == 'release':
            release = True
        elif goto == 'testing':
            testing = True
        elif goto == 'main':
            main = True
            template = "repo/repo_browser_main.html"

        return try_render_template(
            template, building=building, release=release, testing=testing, main=main
        )

