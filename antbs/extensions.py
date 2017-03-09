#!/usr/bin/env python
#  -*- coding: utf-8 -*-
#
#  extensions.py
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

""" Extensions are instantiated here to avoid circular imports with views and create_app(). """

from werkzeug.contrib.fixers import ProxyFix
from werkzeug.local import LocalProxy
from flask import (
    request,
    url_for,
    _request_ctx_stack,
)
from flask_classful import (
    FlaskView,
    route,
)
import rq_dashboard

from utils import (
    AntBSDebugToolbar,
    get_current_user,
)

debug_toolbar = AntBSDebugToolbar()
current_user = LocalProxy(lambda: get_current_user())


def url_for_other_page(page):
    args = request.view_args.copy()
    args['page'] = page
    return url_for(request.endpoint, **args)
