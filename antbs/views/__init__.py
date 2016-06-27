#!/usr/bin/env python
#  -*- coding: utf-8 -*-
#
#  views
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

import gevent
import json
import os
from glob import glob

from flask import (
    abort,
    Blueprint,
    flash,
    redirect,
    render_template,
    request,
    Response,
    url_for
)

from rq import (
    Connection,
    Queue,
    Worker
)

from datetime import datetime, timedelta
from flask_stormpath import current_user, groups_required
from jinja2 import TemplateNotFound

from database.package import get_pkg_object
from database.repo import get_repo_object
from database.build import get_build_object
from database.server_status import status, get_timeline_object
from database.transaction import get_trans_object
from database.base_objects import db
from database.monitor import get_monitor_object, check_repos_for_changes

from utils import *

from webhook import Webhook
from transaction_handler import handle_hook, process_dev_review

import iso


# Setup rq (background task queue manager)
exc_handler = RQWorkerCustomExceptionHandler(status, logger)

with Connection(db):
    transaction_queue = Queue('transactions')
    repo_queue = Queue('update_repo')
    webhook_queue = Queue('webook')
    w1 = Worker([transaction_queue], exception_handlers=[exc_handler.handle_worker_exception])
    w2 = Worker([repo_queue])
    w3 = Worker([webhook_queue], exception_handlers=[exc_handler.handle_worker_exception])


def try_render_template(*args, **kwargs):
    try:
        return render_template(*args, **kwargs)
    except TemplateNotFound:
        abort(500)


def get_paginated(item_list, per_page, page, reverse=True):
    if len(item_list) < 1:
        return item_list, 0

    page = int(page)

    page -= 1
    items = list(item_list)

    if reverse:
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


def get_group_packages(group):
    return [p for p in status.all_packages if package_in_group(p, group)]


def redirect_url(default='homepage'):
    return request.args.get('next') or request.referrer or url_for(default)


def get_build_history_chart_data(pkg_obj=None):
    if pkg_obj is None:
        builds = status.completed + status.failed
        chart_data = db.get('antbs:misc:charts:home:heatmap') or False
    else:
        builds = pkg_obj.builds
        chart_data = pkg_obj.heat_map
        if chart_data and '_' != chart_data:
            chart_data = json.loads(chart_data)
            all_builds = sum([int(num) for num in
                              [chart_data[key]['builds'] for key in chart_data]])
            if len(pkg_obj.builds) > all_builds:
                chart_data = '[]'

    timestamps = []

    if not chart_data or chart_data in ['[]', '_']:
        chart_data = dict()
        builds = [b for b in builds if b]
        for bld in builds:
            bld_obj = get_build_object(bnum=bld)
            if not bld_obj.end_str:
                continue
            dt = datetime.strptime(bld_obj.end_str, "%m/%d/%Y %I:%M%p")
            key = dt.strftime("%m-%d-%Y")
            if not chart_data.get(key, False):
                # chart_data[key] = dict(month=dt.month, day=dt.day, year=dt.year, builds=1,
                #                      timestamp=key)
                chart_data[key] = dict(date=key, builds=1)
            else:
                if chart_data[key]['builds'] > 35:
                    continue
                chart_data[key]['builds'] += 1

        if pkg_obj is None:
            db.setex('antbs:misc:charts:home:heatmap', 10800, json.dumps(chart_data))
        else:
            pkg_obj.heatmap = json.dumps(chart_data)
    elif isinstance(chart_data, str):
        chart_data = json.loads(chart_data)

    for key in chart_data:
        timestamps.append(chart_data[key])

    return chart_data, json.dumps(timestamps)


def build_failed(bnum):
    bld_obj = get_build_object(bnum=bnum)
    return bld_obj.failed


from views.api import api_view
from views.build import build_view
from views.home import home_view
from views.live import live_view
from views.package import package_view
from views.repo import repo_view
