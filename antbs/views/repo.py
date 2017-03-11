#!/usr/bin/env python
#  -*- coding: utf-8 -*-
#
#  repo.py
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

from . import *


class RepoView(FlaskView):
    route_base = '/repo'

    def _get_repo_packages(self, repo_name=None, _filter=None, filter_by=None, page=1):
        if repo_name is None:
            abort(500)

        args = [arg for arg in [_filter, filter_by] if arg is not None]

        for arg in args:
            if arg in status.package_groups:
                continue
            if arg.isdigit() or not arg.isalpha():
                abort(404)

        pkgs = []
        bld_obj = None
        all_pages = 0
        repo_obj = get_repo_object(repo_name, 'x86_64')

        if current_user.is_authenticated:
            rev_pending = []
        else:
            rev_pending = []

        if not repo_obj.pkgnames and 'staging' not in repo_name:
            logger.error('Repo has no packages!')
            return pkgs, rev_pending, all_pages

        if 'group' == _filter:
            repo_packages = [
                p for p in sorted(repo_obj.pkgnames) if package_in_group(p, filter_by)
            ]

        elif 'search' == _filter:
            repo_packages = [p for p in sorted(repo_obj.pkgnames) if filter_by in p]

        elif 'monitored' == _filter:
            repo_packages = [p for p in sorted(repo_obj.pkgnames) if package_is(p, _filter)]

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

    def _filter_is_valid(self, _filter, filter_by):
        if _filter not in ['search', 'group', 'monitored']:
            return False

        if filter_by and not re.fullmatch(r'[\w-]+$', filter_by):
            return False

        if 'group' == _filter and filter_by not in status.package_groups:
            return False

        return True

    @route('/<name>/packages/<_filter>/<filter_by>/<int:page>', endpoint='repo_packages')
    @route('/<name>/packages/<_filter>/<filter_by>', endpoint='repo_packages')
    @route('/<name>/packages/<_filter>/<int:page>', endpoint='repo_packages')
    @route('/<name>/packages/<_filter>', endpoint='repo_packages')
    @route('/<name>/packages/<int:page>', endpoint='repo_packages')
    @route('/<name>/packages', endpoint='repo_packages')
    def repo_packages(self, name=None, _filter=None, filter_by=None, page=1):
        if page > 100:
            abort(404)

        if not (name and name in status.repos):
            abort(404)

        if _filter and not self._filter_is_valid(_filter, filter_by):
            abort(404)

        packages, rev_pending, all_pages = self._get_repo_packages(name, _filter, filter_by, page)
        _pagination = Pagination(page, 10, all_pages)
        columns_info_obj = ColumnsInfo(current_user, request, _filter, filter_by)
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

