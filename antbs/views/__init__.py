#!/usr/bin/env python
#  -*- coding: utf-8 -*-
#
#  __init__.py
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

from flask import render_template, abort
from jinja2 import TemplateNotFound

from ..database.package import get_pkg_object
from ..database.repo import get_repo_object
from ..database.build import get_build_object
from ..database.server_status import status


def try_render_template(*args, **kwargs):
    try:
        return render_template(*args, **kwargs)
    except TemplateNotFound:
        abort(500)


def get_paginated(item_list, per_page, page):
    if len(item_list) < 1:
        return item_list, 0

    page -= 1
    items = list(item_list)

    items.reverse()

    paginated = [items[i:i + per_page] for i in range(0, len(items), per_page)]
    all_pages = len(paginated)

    if all_pages and page <= all_pages:
        this_page = paginated[page]
    elif all_pages and page > all_pages:
        this_page = paginated[-1]
    else:
        this_page = paginated[0]

    return this_page, all_pages


def match_pkgname_with_build_number(bnum=None, match=None):
    if not bnum or not match:
        return False

    bld_obj = get_build_object(bnum=bnum)

    if bld_obj:
        return match in bld_obj.pkgname

    return False


def package_in_group(pkg=None, group=None):
    if not pkg or not group:
        return False

    pkg_obj = get_pkg_object(pkg)

    if pkg_obj:
        return group in pkg_obj.groups

    return False
