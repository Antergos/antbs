#!/usr/bin/env python
#  -*- coding: utf-8 -*-
#
#  home.py
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

from views import (
    abort,
    Blueprint,
    check_repos_for_changes,
    datetime,
    get_build_history_chart_data,
    get_build_object,
    get_build_queue,
    get_monitor_object,
    get_paginated,
    get_timeline_object,
    glob,
    logger,
    os,
    repo_queue,
    status,
    timedelta,
    try_render_template
)

home_view = Blueprint('home', __name__)


@home_view.before_request
def maybe_check_for_remote_commits():
    monitor = get_monitor_object(name='github')

    check_expired = monitor.__is_expired__('checked_recently')

    if not monitor.checked_recently or check_expired:
        repo_queue.enqueue_call(check_repos_for_changes, args=('github',))


def get_timeline(tlpage=None):
    if not tlpage:
        tlpage = 1

    timeline = []

    for event_id in status.all_tl_events[1000:-1]:
        event = get_timeline_object(event_id=event_id)
        timeline.append(event)

    this_page, all_pages = get_paginated(timeline, 6, tlpage)

    return this_page, all_pages


###
##
#   Views Start Here
##
###

@home_view.route("/timeline/<int:tlpage>")
@home_view.route("/")
def homepage(tlpage=None):
    if tlpage is None:
        tlpage = 1

    check_stats = ['completed', 'failed']
    tl_events, all_pages = get_timeline(tlpage)

    if tlpage > all_pages:
        abort(404)

    build_history, timestamps = get_build_history_chart_data()
    stats = {'build_queue': len(get_build_queue())}

    for stat in check_stats:
        builds = getattr(status, stat)
        res = len(builds) or 0
        builds = [x for x in builds[1000:-1] if x]
        within = []
        for bnum in builds:
            try:
                bld_obj = get_build_object(bnum=bnum)
            except (ValueError, AttributeError):
                continue

            end = ''
            if bld_obj.end_str:
                end = datetime.strptime(bld_obj.end_str, '%m/%d/%Y %I:%M%p')
                end = end if (datetime.now() - end) < timedelta(hours=48) else ''

            if end:
                within.append(bld_obj.bnum)

        stats[stat] = len(within)

    main_tpl = '{0}/*.pkg.tar.xz'.format(status.MAIN_64)
    staging_tpl = '{0}/*.pkg.tar.xz'.format(status.STAGING_64)
    main_repo = glob(main_tpl)
    staging_repo = glob(staging_tpl)

    for repo in [main_repo, staging_repo]:
        if not repo:
            continue

        filtered = []

        if '-staging' not in repo[0]:
            repo_name = 'repo_main'
        else:
            repo_name = 'repo_staging'

        for file_path in repo:
            fname = os.path.basename(file_path)
            if 'dummy-package' not in fname:
                filtered.append(fname)

        stats[repo_name] = len(set(filtered))

    return try_render_template("overview.html", stats=stats, tl_events=tl_events,
                               all_pages=all_pages, page=tlpage, build_history=build_history,
                               timestamps=timestamps)

