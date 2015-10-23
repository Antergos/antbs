#!/usr/bin/python
# -*- coding: utf-8 -*-
#
# antbs.py
#
# Copyright © 2013-2015 Antergos
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


"""Main AntBS (Antergos Build Server) Module"""

import requests

import newrelic.agent

settings = newrelic.agent.global_settings()
settings.app_name = 'AntBS'
newrelic.agent.initialize()

import json
import re
import os
import glob
import shutil
from datetime import datetime, timedelta
from rq import Queue, Connection, Worker
from flask import Flask, request, Response, abort, render_template, url_for, redirect, flash
from werkzeug.contrib.fixers import ProxyFix
import docker
from flask.ext.stormpath import StormpathManager, groups_required, user
from flask.ext.cache import Cache
import gevent
import gevent.monkey
import utils.pagination
import build_pkg as builder
from utils.redis_connection import db
from utils.server_status import status as status, Timeline as tl_event
import utils.logging_config as logconf
import package
import webhook
import utils.slack_bot as slack_bot
import build_obj
import iso

status.github_token = os.environ.get('GITHUB_TOKEN')
status.gitlab_token = os.environ.get('GITLAB_TOKEN')
status.docker_user = os.environ.get('DOCKER_USER')
status.docker_password = os.environ.get('DOCKER_PASSWORD')
status.gpg_key = os.environ.get('ANTBS_GPG_KEY')
status.gpg_password = os.environ.get('ANTBS_GPG_PASS')

import repo_monitor as repo_mon

gevent.monkey.patch_all()

SRC_DIR = os.path.dirname(__file__) or '.'
STAGING_REPO = '/srv/antergos.info/repo/iso/testing/uefi/antergos-staging'
MAIN_REPO = '/srv/antergos.info/repo/antergos'
STAGING_64 = os.path.join(STAGING_REPO, 'x86_64')
STAGING_32 = os.path.join(STAGING_REPO, 'i686')
MAIN_64 = os.path.join(MAIN_REPO, 'x86_64')
MAIN_32 = os.path.join(MAIN_REPO, 'i686')


# Create the variable `app` which is an instance of the Flask class
app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('STORMPATH_SESSION_KEY')
app.config['STORMPATH_API_KEY_ID'] = os.environ.get('STORMPATH_API_KEY_ID')
app.config['STORMPATH_API_KEY_SECRET'] = os.environ.get('STORMPATH_API_KEY_SECRET')
app.config['STORMPATH_APPLICATION'] = os.environ.get('STORMPATH_APPLICATION')
app.config['STORMPATH_ENABLE_USERNAME'] = True
app.config['STORMPATH_REQUIRE_USERNAME'] = True
app.config['STORMPATH_ENABLE_REGISTRATION'] = False
app.config['STORMPATH_REDIRECT_URL'] = '/pkg_review'
app.config['STORMPATH_LOGIN_TEMPLATE'] = 'login.html'
app.config['STORMPATH_COOKIE_DURATION'] = timedelta(days=14)
app.config['STORMPATH_ENABLE_FORGOT_PASSWORD'] = True
stormpath_manager = StormpathManager(app)
app.jinja_options = Flask.jinja_options.copy()
app.jinja_options['lstrip_blocks'] = True
app.jinja_options['trim_blocks'] = True

# Use gunicorn to proxy with nginx
app.wsgi_app = ProxyFix(app.wsgi_app)

cache = Cache(app, config={'CACHE_TYPE': 'redis', 'CACHE_REDIS_DB': 3, 'CACHE_KEY_PREFIX': 'antbs:cache:',
                           'CACHE_REDIS_URL': 'unix:///var/run/redis/redis.sock'})
cache.init_app(app)

logger = logconf.logger


def copy(src, dst):
    """

    :param src:
    :param dst:
    """
    if os.path.islink(src):
        linkto = os.readlink(src)
        os.symlink(linkto, dst)
    else:
        try:
            shutil.copy(src, dst)
        except Exception:
            pass


def remove(src):
    """

    :param src:
    :return:
    """
    if os.path.isdir(src):
        try:
            shutil.rmtree(src)
        except Exception as err:
            logger.error(err)
            return True
    elif os.path.isfile(src):
        try:
            os.remove(src)
        except Exception as err:
            logger.error(err)
            return True
    else:
        return True


def handle_worker_exception(job, exc_type, exc_value, traceback):
    # TODO: This needs some thought on how to recover instead of bailing on entire build queue
    """

    :param job:
    :param exc_type:
    :param exc_value:
    :param traceback:
    :return:
    """
    doc = docker.Client(base_url='unix://var/run/docker.sock', timeout=10)
    if job['origin'] == 'build_queue':
        container = db.get('container')
    elif job['origin'] == 'repo_queue':
        container = db.get('repo_container')
    else:
        container = ''
    queue = status.queue
    now_building = status.now_building
    try:
        doc.kill(container)
        doc.remove_container(container)
    except Exception:
        logger.error('Unable to kill container')
    if job['origin'] == 'build_queue':
        db.set('%s:result' % now_building['key'], 'failed')
        db.rpush('failed', now_building['build_id'])

        if not queue or len(queue) == 0 or queue == []:
            repo = os.path.join("/tmp", "staging")
            cache = os.path.join("/tmp", "pkg_cache")

            remove(repo)
            remove(cache)
            remove('/opt/antergos-packages')

            # db.set('idle', "True")
            status.idle = True
            # db.set('building', 'Idle')
            status.current_status = 'Idle'
            # db.hset('now_building', 'pkg', '')
            # db.set('container', '')
            # db.set('building_num', '')
            # db.set('building_start', '')

    logger.error('Caught Build Exception: %s', traceback)

    return True


with Connection(db):
    queue = Queue('build_queue')
    repo_queue = Queue('repo_queue')
    hook_queue = Queue('hook_queue')
    w1 = Worker([queue])
    w2 = Worker([repo_queue])
    w3 = Worker([hook_queue])


def url_for_other_page(page):
    """

    :param page:
    :return:
    """
    args = request.view_args.copy()
    args['page'] = page
    return url_for(request.endpoint, **args)


app.jinja_env.globals['url_for_other_page'] = url_for_other_page


def get_live_build_output():
    """


    """

    psub = db.pubsub()
    psub.subscribe('build-output')
    first_run = True
    keep_alive = 0
    while True:
        message = psub.get_message()
        if message:
            if first_run and (message['data'] == '1' or message['data'] == 1):
                message['data'] = db.get('build_log_last_line')
                first_run = False
            elif message['data'] == '1' or message['data'] == 1:
                message['data'] = '...'

            yield 'event: build_output\ndata: %s\n\n' % message['data']
        elif keep_alive > 600:
            keep_alive = 0
            yield ':'

        keep_alive += 1
        gevent.sleep(.05)

    psub.close()


def get_live_status_updates():
    """

    :return:
    """

    last_event = None
    keep_alive = 0
    while True:
        idle = status.idle
        building = status.current_status
        if idle and 'Idle' != last_event:
            last_event = 'Idle'
            yield 'event: status\ndata: %s\n\n' % 'Idle'
        elif not idle and building != last_event:
            last_event = building
            yield 'event: status\ndata: %s\n\n' % building
        elif keep_alive > 30:
            keep_alive = 0
            yield ':'

        keep_alive += 1
        gevent.sleep(1)


def get_paginated(item_list, per_page, page, timeline):
    """

    :param item_list:
    :param per_page:
    :param page:
    :param timeline:
    :return:
    """
    if len(item_list) < 1:
        return [], []
    page -= 1
    items = list(item_list)
    items.reverse()
    paginated = [items[i:i + per_page] for i in range(0, len(items), per_page)]
    all_pages = len(paginated)
    this_page = paginated[page] if not page > all_pages else paginated[-1]

    return this_page, all_pages


def match_pkg_name_build_log(bnum=None, match=None):
    """

    :param bnum:
    :param match:
    :return:
    """
    if not bnum or not match:
        return False
    pname = build_obj.get_build_object(bnum=bnum)
    logger.info(bnum)
    if pname:
        return match in pname.pkgname
    else:
        return False


def cache_buster():
    """


    :return:
    """
    if db.exists('antbs:misc:cache_buster:flag'):
        db.delete('antbs:misc:cache_buster:flag')
        return True
    elif user.is_authenticated():
        return True

    return False


@app.context_processor
def inject_idle_status():
    """


    :return:
    """
    return dict(idle=status.idle)


@cache.memoize(timeout=900, unless=cache_buster)
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
            search_all_builds = [x for x in all_builds if x is not None and match_pkg_name_build_log(x, search)]
            logger.info('search_all_builds is %s', search_all_builds)
            all_builds = search_all_builds

        if all_builds:
            builds, all_pages = get_paginated(all_builds, 10, page, False)
            for bnum in builds:
                try:
                    bld_obj = build_obj.get_build_object(bnum=bnum)
                except Exception as err:
                    logger.error('Unable to ge build object - %s' % err)
                    continue

                # all_info = dict(bnum=bld_obj.bnum, name=bld_obj.pkgname, version=bld_obj.version_str,
                #                 start=bld_obj.start_str, end=bld_obj.end_str, review_stat=bld_obj.review_status,
                #                 review_dev=bld_obj.review_dev, review_date=bld_obj.review_date)
                # pkg_list[bnum] = all_info

                pkg_list.append(bld_obj)

                if logged_in and bld_obj.review_status == "pending":
                    rev_pending.append(bld_obj)

    return pkg_list, int(all_pages), rev_pending


@cache.memoize(timeout=900, unless=cache_buster)
def get_repo_info(repo=None, logged_in=False):
    """

    :param repo:
    :param logged_in:
    :return:
    """
    if repo is None:
        abort(500)
    pkg_list = {}
    p, a, rev_pending = get_build_info(1, repo, logged_in)

    all_packages = glob.glob('/srv/antergos.info/repo/%s/x86_64/***.pkg.tar.xz' % repo)

    if all_packages is not None:
        for item in all_packages:
            # logger.info(item)
            item = item.split('/')[-1]
            item = re.search('^([a-z]|[0-9]|-|_)+(?=-\d|r|v)', item)

            item = item.group(0) or ''
            if not item or item == '':
                continue
            logger.info(item)
            pkg = package.get_pkg_object(item)
            builds = pkg.builds
            try:
                bnum = builds[0]
                bld_obj = build_obj.get_build_object(bnum=bnum)
            except Exception:
                bld_obj = None
            all_info = dict(bld_obj=bld_obj, pkg_obj=pkg)
            pkg_list[pkg.pkg_id] = all_info

    return pkg_list, rev_pending


def redirect_url(default='homepage'):
    """

    :param default:
    :return:
    """
    return request.args.get('next') or request.referrer or url_for(default)


def set_pkg_review_result(bnum=None, dev=None, result=None):
    # TODO: This is garbage. Needs rewrite.
    """

    :param bnum:
    :param dev:
    :param result:
    :return:
    """
    if any(i is None for i in (bnum, dev, result)):
        abort(500)
    errmsg = dict(error=True, msg=None)
    dt = datetime.now().strftime("%m/%d/%Y %I:%M%p")
    if result in ['0', '1', '2', '3', '4']:
        msg = 'Please clear your browser cache, refresh the page, and try again.'
        errmsg.update(error=True, msg=msg)
        return errmsg
    try:
        bld_obj = build_obj.get_build_object(bnum=bnum)
        pkg_obj = package.Package(name=bld_obj.pkgname)
        if pkg_obj and build_obj:
            allowed = pkg_obj.allowed_in
            if 'main' not in allowed and result == 'passed':
                msg = '%s is not allowed in main repo.' % pkg_obj.pkgname
                errmsg.update(error=True, msg=msg)
                return errmsg
            else:
                bld_obj.review_dev = dev
                bld_obj.review_date = dt
                bld_obj.review_status = result

        if result == 'skip':
            errmsg = dict(error=False, msg=None)
            return errmsg

        pkg_files_64 = glob.glob('%s/%s-***' % (STAGING_64, pkg_obj.pkgname))
        pkg_files_32 = glob.glob('%s/%s-***' % (STAGING_32, pkg_obj.pkgname))
        pkg_files = pkg_files_64 + pkg_files_32

        if pkg_files or True:
            for f in pkg_files_64:
                logger.debug('f in pkg_files_64 fired!')
                if result == 'passed':
                    copy(f, MAIN_64)
                    copy(f, '/tmp')
                if result != 'skip':
                    os.remove(f)
            for f in pkg_files_32:
                if result == 'passed':
                    copy(f, MAIN_32)
                    copy(f, '/tmp')
                if result != 'skip':
                    os.remove(f)
            if result and result != 'skip':
                repo_queue.enqueue_call(builder.update_main_repo, (result, None, True, bld_obj.pkgname), timeout=9600)
                errmsg = dict(error=False, msg=None)

        else:
            logger.error('While moving to main, no packages were found to move.')
            err = 'While moving to main, no packages were found to move.'
            errmsg = dict(error=True, msg=err)

    except (OSError, Exception) as err:
        logger.error('@@-antbs.py-@@ | Error while moving to main: %s', err)
        err = str(err)
        errmsg = dict(error=True, msg=err)

    return errmsg


@cache.memoize(timeout=900, unless=cache_buster)
def get_timeline(tlpage=None):
    """

    :param tlpage:
    :return:
    """
    event_ids = status.all_tl_events
    timeline = []
    if not tlpage:
        tlpage = 1
    for event_id in event_ids:
        event = tl_event(event_id=event_id)
        timeline.append(event)
    this_page, all_pages = get_paginated(timeline, 6, tlpage, True)

    return this_page, all_pages


@app.errorhandler(404)
def page_not_found(e):
    """

    :param e:
    :return:
    """
    return render_template('404.html'), 404


@app.errorhandler(500)
def internal_error(e):
    """

    :param e:
    :return:
    """
    if e is not None:
        logger.error(e)
    return render_template('500.html'), 500


@app.errorhandler(400)
def flask_error(e):
    """

    :param e:
    :return:
    """
    if e is not None:
        logger.error(e)
    return render_template('500.html'), 400


# @app.errorhandler(Exception)
# def unhandled_exception(e):
#    if e is not None:
#        logger.debug(e)
#    return render_template('500.html'), 500


@app.route("/timeline/<int:tlpage>")
@app.route("/")
@cache.memoize(timeout=900, unless=cache_buster)
def homepage(tlpage=None):
    """

    :param tlpage:
    :return:
    """
    if tlpage is None:
        tlpage = 1
    check_stats = ['queue', 'completed', 'failed']
    building = status.current_status
    tl_events, all_pages = get_timeline(tlpage)

    stats = {}
    for stat in check_stats:
        builds = getattr(status, stat)
        res = len(builds)
        if stat != "queue":
            builds = [x for x in builds if x is not None]
            within = []
            nodup = []
            for bnum in builds:
                try:
                    bld_obj = build_obj.get_build_object(bnum=bnum)
                except (ValueError, AttributeError):
                    continue
                ver = '%s:%s' % (bld_obj.pkgname, bld_obj.version_str)
                end = datetime.strptime(bld_obj.end_str, '%m/%d/%Y %I:%M%p') if bld_obj.end_str != '' else ''
                if end != '' and (datetime.now() - end) < timedelta(hours=48) and ver not in nodup and bld_obj.pkgname:
                    within.append(bld_obj.bnum)
                    nodup.append(ver)

            stats[stat] = len(within)
        else:
            stats[stat] = res

    main_repo = glob.glob('/srv/antergos.info/repo/antergos/x86_64/*.pkg.tar.xz')
    staging_repo = glob.glob('/srv/antergos.info/repo/antergos-staging/x86_64/*.pkg.tar.xz')

    for repo in [main_repo, staging_repo]:
        if repo:
            filtered = []
            for file_path in repo:
                new_fp = os.path.basename(file_path)
                if 'dummy-package' not in new_fp:
                    filtered.append(new_fp)
            if '-staging' not in repo[0]:
                repo_name = 'repo_main'
            else:
                repo_name = 'repo_staging'
            stats[repo_name] = len(set(filtered))
        else:
            stats['repo_staging'] = 0

    return render_template("overview.html", stats=stats, user=user, building=building,
                           tl_events=tl_events, all_pages=all_pages, page=tlpage, rev_pending=[])


@app.route("/building")
def build():
    """


    :return:
    """
    now_building = status.now_building
    ver = ''
    bnum = ''
    start = ''
    cont = status.container
    if cont:
        container = cont[:20]
    else:
        container = None
    if not status.idle:
        bnum = status.building_num
        start = status.building_start
        if bnum and bnum != '':
            bld_obj = build_obj.get_build_object(bnum=bnum)
            ver = bld_obj.version_str

    return render_template("building.html", building=now_building, container=container, bnum=bnum,
                           start=start, ver=ver)


@app.route('/get_log')
def get_log():
    """


    :return:
    """
    if status.idle:
        abort(404)

    return Response(get_live_build_output(), direct_passthrough=True, mimetype='text/event-stream')


@app.route('/hook', methods=['POST', 'GET'])
def hooked():
    """


    :return:
    """
    hook = webhook.Webhook(request)
    if hook.result is int:
        abort(hook.result)
    else:
        return json.dumps(hook.result)


@app.before_request
def maybe_check_for_remote_commits():
    """


    """
    check = repo_mon.maybe_check_for_new_items()
    if not check:
        repo_queue.enqueue_call(repo_mon.check_for_new_items)


@app.route('/scheduled')
def scheduled():
    """


    :return:
    """
    try:
        queued = status.queue
    except Exception:
        queued = None
    building = status.now_building
    the_queue = []
    if queued and len(queued) > 0:
        for pak in queued:
            try:
                pkg_obj = package.Package(name=pak)
                the_queue.append(pkg_obj)
            except ValueError as err:
                logger.error(err)

    return render_template("scheduled.html", building=building, queue=the_queue, user=user)


@app.route('/completed/<int:page>')
@app.route('/completed/search/<name>')
@app.route('/completed/search/<name>/<int:page>')
@app.route('/completed')
def completed(page=None, name=None):
    """

    :param page:
    :param name:
    :return:
    """
    build_status = 'completed'
    is_logged_in = user.is_authenticated()
    if (page is None and name is None) or (name is not None and page is None):
        page = 1

    building = status.now_building
    completed, all_pages, rev_pending = get_build_info(page, build_status, is_logged_in, name)
    pagination = utils.pagination.Pagination(page, 10, all_pages)

    return render_template("completed.html", building=building, completed=completed, all_pages=all_pages,
                           rev_pending=rev_pending, user=user, pagination=pagination)


@app.route('/failed/<int:page>')
@app.route('/failed')
def failed(page=None):
    """

    :param page:
    :return:
    """
    build_status = 'failed'
    if page is None:
        page = 1
    building = status.now_building
    is_logged_in = user.is_authenticated()

    failed, all_pages, rev_pending = get_build_info(page, build_status, is_logged_in)
    pagination = utils.pagination.Pagination(page, 10, all_pages)

    return render_template("failed.html", building=building, failed=failed, all_pages=all_pages,
                           page=page, rev_pending=rev_pending, user=user, pagination=pagination)


@app.route('/build/<int:num>')
def build_info(num):
    """

    :param num:
    :return:
    """
    if not num:
        abort(404)
    try:
        bld_obj = build_obj.get_build_object(bnum=num)
    except Exception:
        abort(404)

    cont = status.container
    log = bld_obj.log_str
    if not log:
        log = 'Unavailable'
    log = log.decode("utf8")
    if cont:
        container = cont[:20]
    else:
        container = None
    res = 'completed' if bld_obj.completed else 'failed'

    return render_template("build_info.html", pkg=bld_obj.pkgname, ver=bld_obj.version_str, res=res,
                           start=bld_obj.start_str, end=bld_obj.end_str, bnum=bld_obj.bnum, container=container,
                           log=log, user=user)


@app.route('/browse/<goto>')
@app.route('/browse')
def repo_browser(goto=None):
    """

    :param goto:
    :return:
    """
    building = status.now_building
    release = False
    testing = False
    main = False
    template = "repo_browser.html"
    if goto == 'release':
        release = True
    elif goto == 'testing':
        testing = True
    elif goto == 'main':
        main = True
        template = "repo_browser_main.html"

    return render_template(template, building=building, release=release, testing=testing,
                           main=main, user=user)


@app.route('/pkg_review/<int:page>')
@app.route('/pkg_review', methods=['POST', 'GET'])
@groups_required(['admin'])
def dev_pkg_check(page=None):
    """

    :param page:
    :return:
    """
    build_status = 'completed'
    set_rev_error = False
    set_rev_error_msg = None
    review = True
    is_logged_in = user.is_authenticated()
    if page is None:
        page = 1
    if request.method == 'POST':
        payload = json.loads(request.data)
        bnum = payload['bnum']
        dev = payload['dev']
        result = payload['result']
        if all(i is not None for i in (bnum, dev, result)):
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
    return render_template("pkg_review.html", completed=completed, all_pages=all_pages,
                           set_rev_error=set_rev_error, set_rev_error_msg=set_rev_error_msg, user=user,
                           rev_pending=rev_pending, pagination=pagination)


@app.route('/build_pkg_now', methods=['POST', 'GET'])
@groups_required(['admin'])
def build_pkg_now():
    """


    :return:
    """
    if request.method == 'POST':
        pkgname = request.form['pkgname']
        dev = request.form['dev']
        if not pkgname or pkgname is None or pkgname == '':
            abort(500)
        pexists = status.all_packages
        pexists = pexists.ismember(pkgname)
        if not pexists:
            try:
                package.Package(name=pkgname)
                if os.path.exists('/var/tmp/antergos-packages/' + pkgname) or 'antergos-iso' in pkgname:
                    pexists = True
            except Exception:
                pass

        if pexists:
            is_logged_in = user.is_authenticated()
            p, a, rev_pending = get_build_info(1, 'completed', is_logged_in)
            # logger.info(rev_pending)
            pending = False
            for bnum in rev_pending:
                bld_obj = build_obj.get_build_object(bnum=bnum)
                if pkgname == bld_obj.pkgname:
                    pending = True
                    break

            if pending:
                flash('Unable to build %s because it is in "pending review" status.' % pkgname, category='error')
            else:
                if '-x86_64' in pkgname or '-i686' in pkgname:
                    if not status.iso_building:
                        status.iso_flag = True
                        if 'minimal' in pkgname:
                            status.iso_minimal = True
                        else:
                            status.iso_minimal = False
                    else:
                        logger.info('RATE LIMIT ON ANTERGOS ISO IN EFFECT')
                        return redirect(redirect_url())

                q = status.hook_queue
                q.rpush(pkgname)
                hook_queue.enqueue_call(builder.handle_hook, timeout=84600)
                tl_event(
                    msg='<strong>%s</strong> added <strong>%s</strong> to the build queue.' % (dev, pkgname),
                    tl_type='0')
        else:
            flash('Package not found. Has the PKGBUILD been pushed to github?', category='error')

    return redirect(redirect_url())


@app.route('/get_status', methods=['GET'])
@app.route('/api/ajax', methods=['GET'])
def get_status():
    """


    :return:
    """
    building = status.current_status
    iso_release = bool(request.args.get('do_iso_release', False)) and user.is_authenticated()
    reset_queue = bool(request.args.get('reset_build_queue', False)) and user.is_authenticated()
    rerun_transaction = int(request.args.get('rerun_transaction', 0))

    if not all([iso_release, reset_queue]) and rerun_transaction == 0:
        return Response(get_live_status_updates(), direct_passthrough=True, mimetype='text/event-stream')

    message = dict(msg='Ok')

    if iso_release:
        queue.enqueue_call(iso.iso_release_job)
        return json.dumps(message)

    elif reset_queue:
        if queue.count > 0:
            queue.empty()
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
        event = tl_event(event_id=rerun_transaction)
        pkgs = event.packages
        if pkgs:
            for pkg in pkgs:
                if pkg not in status.hook_queue:
                    status.hook_queue.rpush(pkg)
            hook_queue.enqueue_call(builder.handle_hook, timeout=84600)
        return json.dumps(message)


@app.route('/issues', methods=['GET'])
def show_issues():
    """


    :return:
    """
    return render_template('issues.html')


@app.route('/pkg/<pkgname>', methods=['GET'])
def get_and_show_pkg_profile(pkgname=None):
    """

    :param pkgname:
    :return:
    """
    if pkgname is None:
        abort(404)
    check = status.all_packages
    check = check.ismember(pkgname)
    if not check:
        abort(404)

    pkgobj = package.Package(name=pkgname)
    if '' == pkgobj.description:
        desc = pkgobj.get_from_pkgbuild('pkgdesc')
        pkgobj.description = desc
        pkgobj.pkgdesc = desc

    return render_template('package.html', pkg=pkgobj)


@app.route('/repo_packages/<repo>')
def repo_packages(repo=None):
    """

    :param repo:
    :return:
    """
    if repo is None or repo not in ['antergos', 'antergos-staging']:
        abort(404)
    is_logged_in = user.is_authenticated()
    building = status.now_building
    packages, rev_pending = get_repo_info(repo, is_logged_in)
    return render_template("repo_pkgs.html", building=building, repo_packages=packages,
                           rev_pending=rev_pending, user=user, name=repo)


@app.route('/slack/overflow', methods=['post'])
@app.route('/slack/todo', methods=['post'])
@app.route('/slack/tableflip', methods=['post'])
def overflow():
    """


    :return:
    """
    token = request.values.get('token')
    if not token or '' == token:
        abort(404)
    if 'tableflip' in request.url:
        channel = request.values.get('channel_name')
        from_user = request.values.get('user_name')
        payload = {"text": "(╯°□°)╯︵ ┻━┻", "username": from_user, "icon_emoji": ":bam:", "channel": '#' + channel}
        slack = 'https://hooks.slack.com/services/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'
        requests.post(slack, data=json.dumps(payload))

        return Response(status=200)

    text = request.values.get('text')
    command = request.values.get('command')

    res = slack_bot.overflow(command, text)

    return Response(res['msg'], content_type=res['content_type'])


if __name__ == "__main__":
    app.run(host='127.0.0.1', port=8020, debug=False, use_reloader=False)
