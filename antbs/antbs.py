#!/usr/bin/python
# -*- coding: utf-8 -*-
#
# antbs.py
#
# Copyright © 2013-2016 Antergos
#
# This file is part of The Antergos Build Server, (AntBS).
#
# AntBS is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 3 of the License, or
# (at your option) any later version.
#
# AntBS is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# The following additional terms are in effect as per Section 7 of the license:
#
# The preservation of all legal notices and author attributions in
# the material or in the Appropriate Legal Notices displayed
# by works containing it is required.
#
# You should have received a copy of the GNU General Public License
# along with AntBS; If not, see <http://www.gnu.org/licenses/>.


""" AntBS (Antergos Build Server) Main Module """

# Start ignoring PyImportSortBear as monkey patching needs to be done before
# other imports
import gevent
import gevent.monkey

gevent.monkey.patch_all()
# Stop ignoring

import glob
import json
import os
import re
from datetime import datetime, timedelta

import requests
from flask import (
    Flask, Response, abort, flash, redirect, render_template, request, stream_with_context,
    url_for)
from flask.ext.stormpath import StormpathManager, groups_required, user
from werkzeug.contrib.fixers import ProxyFix

import bugsnag
import iso
import repo_monitor
import rq_dashboard
import transaction_handler
import utils.pagination
import webhook
from bugsnag.flask import handle_exceptions
from database.base_objects import db
from database.build import get_build_object
from database.package import get_pkg_object
from database.server_status import get_timeline_object, status
from database.transaction import get_trans_object
from rq import Connection, Queue, Worker
from utils.logging_config import logger
from utils.utilities import copy_or_symlink
from utils.utilities import remove


app = transaction_queue = repo_queue = webhook_queue = w1 = w2 = w3 = None


def url_for_other_page(page):
    args = request.view_args.copy()
    args['page'] = page
    return url_for(request.endpoint, **args)


def initialize_app():
    """
    Creates flask app object, initializes settings, then returns `app`.

    """

    bugsnag.configure(api_key=status.bugsnag_key, project_root=status.APP_DIR)

    # Create the variable `app` which is an instance of the Flask class
    global app
    app = Flask(__name__)
    handle_exceptions(app)

    # Stormpath configuration
    app.config.update({'SECRET_KEY': status.sp_session_key,
                       'STORMPATH_API_KEY_ID': status.sp_api_id,
                       'STORMPATH_API_KEY_SECRET': status.sp_api_key,
                       'STORMPATH_APPLICATION': status.sp_app,
                       'STORMPATH_ENABLE_USERNAME': True,
                       'STORMPATH_REQUIRE_USERNAME': True,
                       'STORMPATH_ENABLE_REGISTRATION': False,
                       'STORMPATH_REDIRECT_URL': '/pkg_review',
                       'STORMPATH_LOGIN_TEMPLATE': 'admin/login.html',
                       'STORMPATH_COOKIE_DURATION': timedelta(days=14),
                       'STORMPATH_ENABLE_FORGOT_PASSWORD': True})

    # Create Stormpath Manager object.
    stormpath_manager = StormpathManager(app)

    # Jinja2 configuration
    global url_for_other_page
    app.jinja_options = Flask.jinja_options.copy()
    app.jinja_options['lstrip_blocks'] = True
    app.jinja_options['trim_blocks'] = True
    app.jinja_env.globals['url_for_other_page'] = url_for_other_page

    # Use gunicorn with nginx proxy
    app.wsgi_app = ProxyFix(app.wsgi_app)

    # Setup rq_dashboard (accessible at '/rq' endpoint)
    app.config.from_object(rq_dashboard.default_settings)
    app.register_blueprint(rq_dashboard.blueprint, url_prefix='/rq')

    # Setup rq (background task queue manager)
    with Connection(db):
        global transaction_queue, repo_queue, webhook_queue, w1, w2, w3
        transaction_queue = Queue('transactions')
        repo_queue = Queue('repo_update')
        webhook_queue = Queue('webook')
        w1 = Worker([transaction_queue])
        w2 = Worker([repo_queue])
        w3 = Worker([webhook_queue])


# Make `app` available to gunicorn
initialize_app()


@app.before_request
def maybe_check_for_remote_commits():
    check = repo_monitor.maybe_check_for_new_items()
    if not check:
        repo_queue.enqueue_call(repo_monitor.check_for_new_items)


@app.context_processor
def inject_global_template_variables():
    return dict(
        idle=status.idle,
        current_status=status.current_status,
        now_building=status.now_building,
        rev_pending=status.pending_review,
        user=user
    )


def get_live_build_output(bnum):
    psub = db.pubsub()
    psub.subscribe('live:build_output:{0}'.format(bnum))
    last_line_key = 'tmp:build_log_last_line:{0}'.format(bnum)
    first_run = True
    keep_alive = 0
    while True:
        message = psub.get_message()
        if message and (message['data'] == '1' or message['data'] == 1):
            if first_run:
                message['data'] = db.get(last_line_key)
                first_run = False
            else:
                message['data'] = '...'

            yield 'event: build_output\ndata: {0}\n\n'.format(message['data']).encode('UTF-8')

        elif keep_alive > 600:
            keep_alive = 0
            yield ':'.encode('UTF-8')

        keep_alive += 1
        gevent.sleep(.05)

    psub.close()


def get_live_status_updates():
    last_event = None
    keep_alive = 0
    while True:
        if status.idle and 'Idle' != last_event:
            last_event = 'Idle'
            yield 'event: status\ndata: {0}\n\n'.format('Idle').encode('UTF-8')
        elif not status.idle and status.current_status != last_event:
            last_event = status.current_status
            yield 'event: status\ndata: {0}\n\n'.format(status.current_status).encode('UTF-8')
        elif keep_alive > 15:
            keep_alive = 0
            yield ':'.encode('UTF-8')

        keep_alive += 1
        gevent.sleep(1)


def get_paginated(item_list, per_page, page, timeline):
    if len(item_list) < 1:
        return [], []
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


def match_pkg_name_build_log(bnum=None, match=None):
    if not bnum or not match:
        return False
    pname = get_build_object(bnum=bnum)
    logger.info(bnum)
    if pname:
        return match in pname.pkgname
    else:
        return False


# @cache.memoize(timeout=900, unless=cache_buster)
def get_build_info(page=None, build_status=None, logged_in=False, search=None):
    """
    Get paginated list of build objects.

    :param (int) page: Page number.
    :param (str) build_status: Only include builds of this status (completed, failed, etc).
    :param (bool) logged_in: Was the request made by a logged-in user?
    :param (str) search: Filter list to include builds where "search" string is found in pkgname.

    :return (list) pkglist, (int) all_pages, (list) rev_pending:

    """
    if page is None or build_status is None:
        abort(500)

    if 'antergos' in build_status:
        build_status = 'completed'

    pkg_list = []
    rev_pending = []
    all_builds = None
    all_pages = 0

    try:
        all_builds = getattr(status, build_status)
    except Exception as err:
        logger.error('GET_BUILD_INFO - %s', err)
        abort(500)

    if all_builds:
        if search is not None:
            search_all_builds = [x for x in all_builds if
                                 x is not None and match_pkg_name_build_log(x, search)]
            all_builds = search_all_builds

        if all_builds:
            builds, all_pages = get_paginated(all_builds, 10, page, False)
            for bnum in builds:
                if int(bnum) < 2227:
                    try:
                        bld_obj = get_build_object(bnum=bnum)
                    except Exception as err:
                        logger.error(err)
                        continue

                    pkg_list.append(bld_obj)

                else:
                    trans_obj = get_trans_object(tnum=bnum)
                    trans_blds = [get_build_object(bnum=bnum) for bnum in trans_obj.builds]
                    pkg_list.extend(trans_blds)

            if logged_in:
                for bld_obj in pkg_list:
                    if bld_obj.review_status == "pending":
                        rev_pending.append(bld_obj)

    return pkg_list, int(all_pages), rev_pending


# @cache.memoize(timeout=900, unless=cache_buster)
def get_repo_info(repo=None, logged_in=False):
    if repo is None:
        abort(500)
    container = dict(pkgs=[])

    if logged_in:
        p, a, rev_pending = get_build_info(1, repo, logged_in)
    else:
        rev_pending = []

    path_part = os.path.join('/srv/antergos.info/repo', repo, 'x86_64')
    all_packages = glob.glob('{0}/***.pkg.tar.xz'.format(path_part))

    if all_packages:
        for pkg in all_packages:
            p = pkg
            pkg = re.search(r'^(\w|-)+(\D|r3|g4|qt5)(?=-\d(\w|\.|-|_)*)', os.path.basename(pkg))
            if pkg:
                pkg = pkg.group(0)
            else:
                logger.error(p)
                continue
            if 'dummy' in pkg or 'grub-zfs' in pkg:
                continue
            pkg_obj = get_pkg_object(pkg)
            bld_obj = dict(review_status='', review_dev='', review_date='')
            try:
                bnum = pkg_obj.builds[0]
                if bnum:
                    bld_obj = get_build_object(bnum=bnum)
            except Exception:
                continue

            pkg_obj._build = bld_obj if isinstance(bld_obj, dict) else bld_obj.__jsonable__()
            container["pkgs"].append(pkg_obj.__jsonable__())

    return container, rev_pending


def redirect_url(default='homepage'):
    return request.args.get('next') or request.referrer or url_for(default)


def set_pkg_review_result(bnum=None, dev=None, result=None):
    if not any([bnum, dev, result]):
        abort(500)

    errmsg = dict(error=True, msg=None)
    dt = datetime.now().strftime("%m/%d/%Y %I:%M%p")

    try:
        bld_obj = get_build_object(bnum=bnum)
        pkg_obj = get_pkg_object(name=bld_obj.pkgname)
        if pkg_obj and bld_obj:
            allowed = pkg_obj.allowed_in
            if 'main' not in allowed and result == 'passed':
                msg = '{0} is not allowed in main repo.'.format(pkg_obj.pkgname)
                errmsg.update(error=True, msg=msg)
                return errmsg
            else:
                bld_obj.review_dev = dev
                bld_obj.review_date = dt
                bld_obj.review_status = result

        if result == 'skip':
            errmsg = dict(error=False, msg=None)
            return errmsg

        pkg_files_64 = glob.glob('{0}/{1}-***'.format(status.STAGING_64, pkg_obj.pkgname))
        pkg_files_32 = glob.glob('{0}/{1}***'.format(status.STAGING_32, pkg_obj.pkgname))
        pkg_files = pkg_files_64 + pkg_files_32

        if pkg_files:
            for f in pkg_files_64:
                logger.debug('f in pkg_files_64 fired!')
                if result == 'passed':
                    copy_or_symlink(f, status.MAIN_64)
                    copy_or_symlink(f, '/tmp')
                if result != 'skip':
                    os.remove(f)
            for f in pkg_files_32:
                if result == 'passed':
                    copy_or_symlink(f, status.MAIN_32)
                    copy_or_symlink(f, '/tmp')
                if result != 'skip':
                    os.remove(f)
            if result and result != 'skip':
                repo_queue.enqueue_call(transaction_handler.process_dev_review,
                                        (result, None, True, bld_obj.pkgname), timeout=9600)
                errmsg = dict(error=False, msg=None)

        else:
            logger.error('While moving to main, no packages were found to move.')
            err = 'While moving to main, no packages were found to move.'
            errmsg = dict(error=True, msg=err)

    except (OSError, Exception) as err:
        logger.error('Error while moving to main: %s', err)
        err = str(err)
        errmsg = dict(error=True, msg=err)

    return errmsg


# @cache.memoize(timeout=900, unless=cache_buster)
def get_timeline(tlpage=None):
    if not tlpage:
        tlpage = 1
    timeline = []
    for event_id in status.all_tl_events[1000:-1]:
        event = get_timeline_object(event_id=event_id)
        timeline.append(event)
    this_page, all_pages = get_paginated(timeline, 6, tlpage, True)

    return this_page, all_pages


# @cache.memoize(timeout=900, unless=cache_buster)
def get_build_history_chart_data(pkg_obj=None):
    if pkg_obj is None:
        builds = status.completed + status.failed
        chart_data = db.get('antbs:misc:charts:home:heatmap') or False
    else:
        builds = pkg_obj.builds
        chart_data = pkg_obj.heat_map
        if chart_data:
            chart_data = json.loads(chart_data)
            all_builds = sum([int(num) for num in
                              [chart_data[key]['builds'] for key in chart_data]])
            if len(pkg_obj.builds) > all_builds:
                chart_data = '{}'

    timestamps = {}

    if not chart_data or '{}' == chart_data:
        chart_data = dict()
        builds = [b for b in builds if b]
        for bld in builds:
            bld_obj = get_build_object(bnum=bld)
            if not bld_obj.end_str:
                continue
            dt = datetime.strptime(bld_obj.end_str, "%m/%d/%Y %I:%M%p")
            key = dt.strftime("%s")
            if not chart_data.get(key, False):
                chart_data[key] = dict(month=dt.month, day=dt.day, year=dt.year, builds=1,
                                       timestamp=key)
            else:
                chart_data[key]['builds'] += 1

        if pkg_obj is None:
            db.setex('antbs:misc:charts:home:heatmap', 10800, json.dumps(chart_data))
        else:
            pkg_obj.heatmap = json.dumps(chart_data)
    elif isinstance(chart_data, str):
        chart_data = json.loads(chart_data)

    for key in chart_data:
        timestamps[key] = chart_data[key]['builds']

    return chart_data, timestamps


@app.errorhandler(404)
def page_not_found(e):
    return render_template('error/404.html'), 404


@app.errorhandler(500)
def internal_error(e):
    if e is not None:
        logger.error(e)
    return render_template('error/500.html'), 500


@app.errorhandler(400)
def flask_error(e):
    if e is not None:
        logger.error(e)
    return render_template('error/500.html'), 400


@app.route("/timeline/<int:tlpage>")
@app.route("/")
# @cache.memoize(timeout=900, unless=cache_buster)
def homepage(tlpage=None):
    if tlpage is None:
        tlpage = 1
    check_stats = ['build_queue', 'completed', 'failed']
    tl_events, all_pages = get_timeline(tlpage)

    if tlpage > all_pages:
        abort(404)

    build_history, timestamps = get_build_history_chart_data()

    stats = {}
    for stat in check_stats:
        builds = getattr(status, stat)
        res = len(builds) or '0'
        if stat != "build_queue":
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
        else:
            stats[stat] = res

    main_repo = glob.glob('/srv/antergos.info/repo/antergos/x86_64/*.pkg.tar.xz')
    staging_repo = glob.glob('/srv/antergos.info/repo/antergos-staging/x86_64/*.pkg.tar.xz')

    for repo in [main_repo, staging_repo]:
        if repo:
            filtered = []

            if '-staging' not in repo[0]:
                repo_name = 'repo_main'
            else:
                repo_name = 'repo_staging'

            for file_path in repo:
                new_fp = os.path.basename(file_path)
                if 'dummy-package' not in new_fp:
                    filtered.append(new_fp)

            stats[repo_name] = len(set(filtered))
        else:
            stats['repo_staging'] = 0

    return render_template("overview.html", stats=stats, user=user, tl_events=tl_events,
                           all_pages=all_pages, page=tlpage, build_history=build_history,
                           timestamps=timestamps)


@app.route("/building")
@app.route("/building/<int:bnum>")
def building(bnum=None):
    bld = bnum
    start = ''
    if status.now_building and not status.idle:
        if not bld:
            bld = status.now_building[0]

        try:
            bld_obj = get_build_object(bnum=bld)
        except Exception:
            bld_obj = None

        if bld_obj:
            start = bld_obj.start_str
            ver = bld_obj.version_str
            container = bld_obj.container

    if not start:
        start = ver = container = ''

    return render_template("building.html", container=container, bnum=bld, start=start,
                           ver=ver, idle=status.idle)


@app.route('/get_log')
@app.route("/get_log/<int:bnum>")
def get_log(bnum):
    bld = bnum
    if status.idle or not status.now_building:
        abort(404)

    if not bld:
        bld = status.now_building[0]

    if not bld:
        abort(404)

    headers = {
        'Content-Type': 'text/event-stream',
        'Cache-Control': 'no-cache',
    }
    return Response(get_live_build_output(bld), direct_passthrough=True,
                    mimetype='text/event-stream', headers=headers)


@app.route('/hook', methods=['POST', 'GET'])
def hooked():
    hook = webhook.Webhook(request)
    if hook.result is int:
        abort(hook.result)
    else:
        return json.dumps(hook.result)


@app.route('/scheduled')
def scheduled():
    builds = []
    if status.build_queue and len(status.build_queue) > 0:
        for bnum in status.build_queue:
            try:
                bld_obj = get_build_object(bnum=bnum)
                builds.append(bld_obj)
            except ValueError as err:
                logger.error(err)

    return render_template("builds/scheduled.html")


@app.route('/builds/<build_status>/search/<name>')
@app.route('/builds/<build_status>/search/<name>/<int:page>')
@app.route('/builds/<build_status>/<int:page>')
@app.route('/builds/<build_status>')
def list_builds(build_status=None, page=None, name=None):
    if not build_status or build_status not in ['completed', 'failed']:
        abort(404)
    is_logged_in = user.is_authenticated()
    if page is None:
        page = 1

    builds, all_pages, rev_pending = get_build_info(page, build_status, is_logged_in, name)
    pagination = utils.pagination.Pagination(page, 10, all_pages)
    tpl = "builds/{0}.html".format(build_status)

    return render_template(tpl, builds=builds, all_pages=all_pages, pagination=pagination,
                           build_status=build_status)


@app.route('/build/<int:num>')
# @cache.memoize(timeout=900, unless=cache_buster)
def build_info(num):
    if not num:
        abort(404)
    try:
        bld_obj = get_build_object(bnum=num)
    except Exception:
        abort(404)

    cont = status.container
    if not bld_obj.log_str:
        bld_obj.log_str = 'Unavailable'
    if cont:
        container = cont[:20]
    else:
        container = None
    res = 'completed' if bld_obj.completed else 'failed'

    return render_template("builds/build_info.html", pkg=bld_obj.pkgname, ver=bld_obj.version_str,
                           res=res, start=bld_obj.start_str, end=bld_obj.end_str,
                           bnum=bld_obj.bnum, container=container, log=bld_obj.log_str)


@app.route('/browse/<goto>')
@app.route('/browse')
# @cache.memoize(timeout=900, unless=cache_buster)
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

    return render_template(template, building=building, release=release, testing=testing,
                           main=main, user=user)


@app.route('/pkg_review/<int:page>')
@app.route('/pkg_review', methods=['POST', 'GET'])
@groups_required(['admin'])
def dev_pkg_check(page=None):
    build_status = 'completed'
    set_rev_error = False
    set_rev_error_msg = None
    review = True
    is_logged_in = user.is_authenticated()
    if page is None:
        page = 1
    if request.method == 'POST':
        payload = json.loads(request.data.decode('utf-8'))
        bnum = payload['bnum']
        dev = payload['dev']
        result = payload['result']
        if len([x for x in (bnum, dev, result) if x]) == 3:
            logger.debug('fired!')
            set_review = set_pkg_review_result(bnum, dev, result)
            if set_review.get('error'):
                set_rev_error = set_review.get('msg')
                message = dict(msg=set_rev_error)
                return json.dumps(message)
            else:
                message = dict(msg='ok')
                return json.dumps(message)

    completed, all_pages, rev_pending = get_build_info(page, build_status, is_logged_in)
    pagination = utils.pagination.Pagination(page, 10, len(rev_pending))
    return render_template("admin/pkg_review.html", completed=completed, all_pages=all_pages,
                           set_rev_error=set_rev_error, set_rev_error_msg=set_rev_error_msg,
                           user=user, rev_pending=rev_pending, pagination=pagination)


@app.route('/build_pkg_now', methods=['POST', 'GET'])
@groups_required(['admin'])
def build_pkg_now():
    if request.method == 'POST':
        pkgname = request.form['pkgname']
        dev = request.form['dev']
        if not pkgname or pkgname is None or pkgname == '':
            abort(500)
        pexists = status.all_packages.ismember(pkgname)
        if not pexists:
            try:
                pkg = get_pkg_object(name=pkgname)
                if pkg and pkg.pkg_id:
                    pexists = True
            except Exception:
                pass

        if pexists:
            is_logged_in = user.is_authenticated()
            p, a, rev_pending = get_build_info(1, 'completed', is_logged_in)
            pending = False
            logger.debug(rev_pending)
            for bnum in rev_pending:
                bld_obj = get_build_object(bnum=bnum)
                if bld_obj and pkgname == bld_obj.pkgname:
                    pending = True
                    break

            if pending:
                flash('Unable to build %s because it is in "pending review" status.' % pkgname,
                      category='error')
            else:
                if '-x86_64' in pkgname or '-i686' in pkgname:
                    status.iso_flag = True
                    if 'minimal' in pkgname:
                        status.iso_minimal = True
                    else:
                        status.iso_minimal = False

                if 'cnchi-dev' == pkgname:
                    db.set('CNCHI-DEV-OVERRIDE', True)
                status.hook_queue.rpush(pkgname)
                webhook_queue.enqueue_call(transaction_handler.handle_hook, timeout=84600)
                get_timeline_object(
                    msg='<strong>%s</strong> added <strong>%s</strong> to the build queue.' % (
                        dev, pkgname), tl_type='0')
        else:
            flash('Package not found. Has the PKGBUILD been pushed to github?', category='error')

    return redirect(redirect_url())


@app.route('/get_status', methods=['GET'])
@app.route('/api/ajax', methods=['GET', 'POST'])
def get_status():
    if 'get_status' in request.path:
        headers = {
            'Content-Type': 'text/event-stream',
            'Cache-Control': 'no-cache',
        }
        return Response(get_live_status_updates(), direct_passthrough=True,
                        mimetype='text/event-stream', headers=headers)

    if not user.is_authenticated():
        abort(403)

    building = status.current_status
    iso_release = bool(request.args.get('do_iso_release', False))
    reset_queue = bool(request.args.get('reset_build_queue', False))
    rerun_transaction = int(request.args.get('rerun_transaction', 0))
    message = dict(msg='Ok')

    if request.method == 'POST':
        payload = json.loads(request.data.decode('UTF-8'))
        pkg = payload.get('pkg', None)
        dev = payload.get('dev', None)
        action = payload.get('result', None)

        if all(i is not None for i in (pkg, dev, action)):
            if action in ['remove']:
                transaction_queue.enqueue_call(
                    transaction_handler.update_main_repo(is_action=True, action=action, action_pkg=pkg))
            elif 'rebuild' == action:
                status.hook_queue.rpush(pkg)
                webhook_queue.enqueue_call(transaction_handler.handle_hook, timeout=84600)
                get_timeline_object(
                    msg='<strong>%s</strong> added <strong>%s</strong> to the build queue.' % (
                        dev, pkg), tl_type='0')
            return json.dumps(message)

    if iso_release:
        transaction_queue.enqueue_call(iso.iso_release_job)
        return json.dumps(message)

    elif reset_queue:
        if transaction_queue.count > 0:
            transaction_queue.empty()
        if repo_queue.count > 0:
            repo_queue.empty()
        items = len(status.queue)
        if items > 0:
            for item in range(items):
                popped = status.queue.rpop()
                logger.debug(popped)
        status.idle = True
        status.current_status = 'Idle.'
        return json.dumps(message)

    elif rerun_transaction and user.is_authenticated():
        event = get_timeline_object(event_id=rerun_transaction)
        pkgs = event.packages
        if pkgs:
            for pkg in pkgs:
                if pkg not in status.hook_queue:
                    status.hook_queue.rpush(pkg)
            webhook_queue.enqueue_call(transaction_handler.handle_hook, timeout=84600)
        return json.dumps(message)


@app.route('/issues', methods=['GET'])
# @cache.memoize(timeout=900, unless=cache_buster)
def show_issues():
    return render_template('issues.html')


@app.route('/pkg/<pkgname>', methods=['GET'])
# @cache.memoize(timeout=900, unless=cache_buster)
def get_and_show_pkg_profile(pkgname=None):
    if pkgname is None or not status.all_packages.ismember(pkgname):
        abort(404)

    pkgobj = get_pkg_object(name=pkgname)
    if '' == pkgobj.description:
        desc = pkgobj.get_from_pkgbuild('pkgdesc')
        pkgobj.description = desc
        pkgobj.pkgdesc = desc

    build_history, timestamps = get_build_history_chart_data(pkgobj)

    return render_template('package.html', pkg=pkgobj, build_history=build_history,
                           timestamps=timestamps)


@app.route('/repo_packages/<repo>')
# @cache.memoize(timeout=900, unless=cache_buster)
def repo_packages(repo=None):
    if not repo or repo not in ['antergos', 'antergos-staging']:
        abort(404)

    building = status.now_building
    packages, rev_pending = get_repo_info(repo, user.is_authenticated())
    return render_template("repos/repo_pkgs.html", building=building, repo_packages=packages,
                           rev_pending=rev_pending, user=user, name=repo)


# @app.route('/slack/overflow', methods=['post'])
# @app.route('/slack/todo', methods=['post'])
# @app.route('/slack/tableflip', methods=['post'])
def overflow():
    res = None
    token = request.values.get('token')
    if not token or '' == token:
        abort(404)
    if 'tableflip' in request.url:
        channel = request.values.get('channel_name')
        from_user = request.values.get('user_name')
        payload = {"text": "(╯°□°)╯︵ ┻━┻", "username": from_user, "icon_emoji": ":bam:",
                   "channel": '#' + channel}
        slack = 'https://hooks.slack.com/services/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'
        requests.post(slack, data=json.dumps(payload))

        return Response(status=200)

    text = request.values.get('text')
    command = request.values.get('command')

    return Response(res['msg'], content_type=res['content_type'])


if __name__ == "__main__":
    app.run(host='127.0.0.1', port=8020, debug=True, use_reloader=False)
