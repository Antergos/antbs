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

from views import *


class HomeView(FlaskView):
    route_base = '/'

    def _get_timeline(self, tlpage=1):
        timeline = []
        start_at = len(status.all_tl_events) - 300

        for event_id in status.all_tl_events[start_at:-1]:
            event = get_timeline_object(event_id=event_id)
            timeline.append(event)

        this_page, all_pages = get_paginated(timeline, 6, tlpage)

        return this_page, all_pages

    def _get_number_of_packages_in_repo(self, repo_name):
        main_repo = get_repo_object('antergos', 'x86_64')
        staging_repo = get_repo_object('antergos-staging', 'x86_64')

        return len(main_repo.pkgs_alpm) if 'antergos' == repo_name else len(staging_repo.pkgs_alpm)

    def before_request(self, name, tlpage=None):
        monitor = get_monitor_object('github')
        do_sync = do_check = False

        if not monitor.check_is_running and not monitor.checked_recently:
            monitor.check_is_running = True
            do_check = True

        if not status.repos_syncing and not status.repos_synced_recently:
            status.repos_syncing = True
            do_sync = True

        repo_queue.enqueue_call(
            check_repos_for_changes, args=(do_check, do_sync, Webhook), timeout=9600
        )

    @route('/timeline/<int:tlpage>')
    @route('/')
    def index(self, tlpage=None):
        if tlpage is None:
            tlpage = 1

        check_stats = ['completed', 'failed']
        tl_events, all_pages = self._get_timeline(tlpage)

        if tlpage > all_pages:
            abort(404)

        build_history, timestamps = get_build_history_chart_data()
        stats = {
            'build_queue': len(get_build_queue(status, get_trans_object)),
            'repo_main': self._get_number_of_packages_in_repo('antergos'),
            'repo_staging': self._get_number_of_packages_in_repo('antergos-staging')
        }

        for stat in check_stats:
            builds = getattr(status, stat)
            res = len(builds) or '0'
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

        return try_render_template(
            'home.html', stats=stats, tl_events=tl_events, all_pages=all_pages, page=tlpage,
            build_history=build_history, timestamps=timestamps
        )

