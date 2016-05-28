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

from views import *

repo_view = Blueprint('repo', __name__)


def get_repo_packages(repo_name=None, group=None, page=1):
    if repo_name is None:
        abort(500)

    pkgs = []
    bld_obj = None
    all_pages = 0
    repo_obj = get_repo_object(repo_name)

    if user.is_authenticated():
        rev_pending = []
    else:
        rev_pending = []

    if not repo_obj.packages:
        return pkgs, rev_pending

    if group:
        repo_packages = [p for p in list(repo_obj.packages.sort()) if package_in_group(p, group)]
    else:
        repo_packages = sorted(list(repo_obj.packages.sort()))

    repo_packages, all_pages = get_paginated(repo_packages, 10, page, reverse=False)

    for pkg in repo_packages:
        if 'dummy' in pkg or 'grub-zfs' in pkg:
            continue

        try:
            pkg_obj = get_pkg_object(pkg)
            bnum = pkg_obj.builds[0]
            if bnum:
                bld_obj = get_build_object(bnum=bnum)
        except Exception:
            continue

        pkg_obj._build = bld_obj
        pkgs.append(pkg_obj)

    return pkgs, rev_pending, all_pages


###
##
#   Views Start Here
##
###

@repo_view.route('/<name>/packages/<group>')
@repo_view.route('/<name>/packages/<page>')
@repo_view.route('/<name>/packages')
def repo_packages_listing(name=None, group=None, page=1):
    if not name or name not in status.repos or (group and group not in status.package_groups):
        abort(404)

    packages, rev_pending, all_pages = get_repo_packages(name, group, page)
    pagination = Pagination(page, 10, all_pages)

    return try_render_template("repos/listing.html", repo_packages=packages,
                               pagination=pagination, all_pages=all_pages,
                               rev_pending=rev_pending, name=name, group=group)


@repo_view.route('/browse/<goto>')
@repo_view.route('/browse')
def repo_browser(goto=None):
    building = status.now_building
    release = False
    testing = False
    main = False
    template = "repo_browser/repo_browser.html"
    if goto == 'release':
        release = True
    elif goto == 'testing':
        testing = True
    elif goto == 'main':
        main = True
        template = "repo_browser/repo_browser_main.html"

    return try_render_template(template, building=building, release=release, testing=testing,
                               main=main)

