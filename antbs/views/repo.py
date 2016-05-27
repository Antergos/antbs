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

from flask import Blueprint, abort
from flask.ext.stormpath import user

from . import (
    try_render_template,
    get_paginated,
    get_build_object,
    get_repo_object,
    get_pkg_object,
    package_in_group,
    status
)

repo_view = Blueprint('repo', __name__)


def get_repo_packages(repo_name=None, group=None, page=1):
    if repo_name is None:
        abort(500)

    pkgs = []
    bld_obj = None
    repo_obj = get_repo_object(repo_name)

    if user.is_authenticated():
        rev_pending = []
    else:
        rev_pending = []

    if not repo_obj.packages:
        return pkgs, rev_pending

    if group:
        repo_packages = [p for p in repo_obj.packages if package_in_group(p, group)]
    else:
        repo_packages = repo_obj.packages

    repo_packages = get_paginated(repo_packages, 25, page)

    for pkg in repo_packages:
        if 'dummy' in pkg or 'grub-zfs' in pkg:
            continue

        pkg_obj = get_pkg_object(pkg)

        try:
            bnum = pkg_obj.builds[0]
            if bnum:
                bld_obj = get_build_object(bnum=bnum)
        except Exception:
            continue

        pkg_obj._build = bld_obj
        pkgs.append(pkg_obj)

    return pkgs, rev_pending


@repo_view.route('/repo/<name>/packages/<group>')
@repo_view.route('/repo/<name>/packages')
def get_repo_packages(name=None, group=None):
    if not name or name not in status.repos or (group and group not in status.package_groups):
        abort(404)

    packages, rev_pending = get_repo_packages(name, group)

    return try_render_template("repos/repo_pkgs.html", repo_packages=packages,
                               rev_pending=rev_pending, name=name)

