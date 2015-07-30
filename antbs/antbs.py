#!/usr/bin/python
# -*- coding: utf-8 -*-
#
# build_pkg.py
#
# Copyright © 2013-2015 Antergos
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston,
# MA 02110-1301, USA.

"""Main AntBS (Antergos Build Server) Module"""

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
from utils.redis_connection import db, status
import utils.logging_config as logconf
import package
import webhook
import repo_monitor as repo_mon
import utils.slack_bot as slack_bot

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
tl_event = logconf.Timeline


def copy(src, dst):
    if os.path.islink(src):
        linkto = os.readlink(src)
        os.symlink(linkto, dst)
    else:
        try:
            shutil.copy(src, dst)
        except shutil.SameFileError:
            pass
        except shutil.Error:
            pass


def remove(src):
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
    w = Worker([queue], exc_handler=handle_worker_exception)
    repo_queue = Queue('repo_queue')
    repo_w = Worker([repo_queue], exc_handler=handle_worker_exception)


# def stream_template(template_name, **context):
# app.update_template_context(context)
# t = app.jinja_env.get_template(template_name)
# rv = t.stream(context)
#     # rv.enable_buffering(5)
#     rv.disable_buffering()
#     return rv

def url_for_other_page(page):
    args = request.view_args.copy()
    args['page'] = page
    return url_for(request.endpoint, **args)


app.jinja_env.globals['url_for_other_page'] = url_for_other_page


def get_live_build_ouput():
    psub = db.pubsub()
    psub.subscribe('build-output')
    first_run = True
    while True:
        message = psub.get_message()
        if message:
            if first_run and (message['data'] == '1' or message['data'] == 1):
                message['data'] = db.get('build_log_last_line')
                first_run = False
            elif message['data'] == '1' or message['data'] == 1:
                message['data'] = '...'

            yield 'data: %s\n\n' % message['data']

        gevent.sleep(.05)

    psub.close()


def get_paginated(item_list, per_page, page, timeline):
    page -= 1
    if not timeline:
        item_list.reverse()
    paginated = [item_list[i:i + per_page] for i in range(0, len(item_list), per_page)]
    this_page = paginated[page]
    all_pages = len(paginated)

    return this_page, all_pages


def match_pkg_name_build_log(bnum=None, match=None):
    if not bnum or not match:
        return False
    pname = db.get('build_log:%s:pkg' % bnum)
    logger.info(bnum)
    if pname:
        return match in pname
    else:
        return False


def cache_buster():
    if db.exists('antbs:misc:cache_buster:flag'):
        db.delete('antbs:misc:cache_buster:flag')
        return True

    return False


@cache.cached(timeout=900, key_prefix='build_info', unless=cache_buster)
def get_build_info(page=None, build_status=None, logged_in=False, search=None):
    if page is None or build_status is None:
        abort(500)

    if 'antergos' in build_status:
        build_status = 'completed'

    pkg_list = {}
    rev_pending = {}
    all_builds = None
    all_pages = None

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
                    build_obj = build.Build(bnum=bnum)
                except Exception as err:
                    logger.error('Unable to ge build object - %s' % err)
                    continue

                all_info = dict(bnum=build_obj.bnum, name=build_obj.pkgname, version=build_obj.version_str,
                                start=build_obj.start_str, end=build_obj.end_str, review_stat=build_obj.review_status,
                                review_dev=build_obj.review_dev, review_date=build_obj.review_date)
                pkg_list[bnum] = all_info

                if logged_in and build_obj.review_stat == "pending":
                    rev_pending[bnum] = all_info

    return pkg_list, int(all_pages), rev_pending


def get_repo_info(repo=None, logged_in=False):
    if repo is None:
        abort(500)
    rinfo_key = 'cache:repo_info:%s' % repo
    repo_info_cache = db.exists(rinfo_key)
    pkg_list = {}
    p, a, rev_pending = get_build_info(1, repo, logged_in)

    # logger.info('@@-antbs.py-@@ 293 | GET_REPO_INFO - FIRED')
    if not repo_info_cache:
        logger.info('@@-antbs.py-@@ 295 | GET_REPO_INFO - CACHE CHECK FAILED. WE ARE NOT USING CACHED INFO')
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
                pkg = package.Package(item)
                builds = pkg.builds
                try:
                    bnum = builds[0]
                except Exception:
                    bnum = ''
                review_stat = db.get('build_log:%s:review_stat' % bnum) or 'n/a'
                review_stat = db.get('review_stat:%s:string' % review_stat) or 'n/a'
                review_dev = db.get('build_log:%s:review_dev' % bnum) or 'n/a'
                review_date = db.get('build_log:%s:review_date' % bnum) or 'n/a'
                all_info = dict(bnum=bnum, name=pkg.name, version=pkg.version, review_dev=review_dev,
                                review_stat=review_stat, review_date=review_date, pkgid=pkg.pkgid)

                db.hmset('%s:%s' % (rinfo_key, pkg.pkgid), all_info)
                db.expire('%s:%s' % (rinfo_key, pkg.pkgid), 901)
                db.rpush(rinfo_key, pkg.pkgid)
                db.expire(rinfo_key, 900)
                pkg_list[pkg.pkgid] = all_info

    else:
        logger.info('@@-antbs.py-@@ 318 | GET_REPO_INFO - CACHE CHECK PASSED. WE ARE USING CACHED INFO')
        rindex = db.lrange(rinfo_key, 0, -1)
        for i in rindex:
            h = db.hgetall('%s:%s' % (rinfo_key, i))
            pkg_list[i] = h
            # logger.info('@@-antbs.py-@@ 320 | GET_REPO_INFO - pkg_list hash is %s' % str(pkg_list))

    return pkg_list, rev_pending


def redirect_url(default='homepage'):
    return request.args.get('next') or request.referrer or url_for(default)


def set_pkg_review_result(bnum=None, dev=None, result=None):
    # TODO: This is garbage. Needs rewrite.
    if any(i is None for i in (bnum, dev, result)):
        abort(500)
    errmsg = dict(error=True, msg=None)
    dt = datetime.now().strftime("%m/%d/%Y %I:%M%p")
    try:
        build_obj = build.Build(bnum=bnum)
        status.idle = False
        pkg_obj = package.Package(name=build_obj.pkgname)
        if pkg_obj:
            if 'main' not in pkg_obj.allowed_in and result == 'passed':
                msg = '%s is not allowed in main repo.' % pkg_obj.pkgname
                errmsg.update(error=True, msg=msg)
                return errmsg
            else:
                build_obj.review_dev = dev
                build_obj.review_date = dt
                build_obj.review_status = result

        pkg_files_64 = glob.glob('%s/%s-***' % (STAGING_64, pkg_obj.pkgname))
        pkg_files_32 = glob.glob('%s/%s-***' % (STAGING_32, pkg_obj.pkgname))
        pkg_files = pkg_files_64 + pkg_files_32

        if result == 'skip':
            return errmsg
        if pkg_files:
            logger.info('Moving %s from staging to main repo.', pkg_obj.pkgname)
            for f in pkg_files_64:
                if result == 'passed':
                    copy(f, MAIN_64)
                    copy(f, '/tmp')
                elif result == 'failed':
                    os.remove(f)
            for f in pkg_files_32:
                if result == 'passed':
                    copy(f, MAIN_32)
                    copy(f, '/tmp')
                elif result == 'failed':
                    os.remove(f)
            if result and result != 'skip':
                repo_queue.enqueue_call(builder.update_main_repo, args=(pkg_obj.pkgname, result), timeout=9600)

        else:
            logger.error('While moving to main, no packages were found to move.')
            err = 'While moving to main, no packages were found to move.'
            errmsg = dict(error=True, msg=err)

    except (OSError, Exception) as err:
        logger.error('@@-antbs.py-@@ | Error while moving to main: %s', err)
        err = str(err)
        errmsg = dict(error=True, msg=err)

    return errmsg


def get_timeline(tlpage=None):
    event_ids = status.all_tl_events
    timeline = []
    for event_id in event_ids:
        ev_obj = tl_event(event_id=event_id)
        allinfo = dict(event_id=ev_obj.event_id, date=ev_obj.date_str, msg=ev_obj.msg, time=ev_obj.time_str,
                       tltype=ev_obj.tl_type)
        event = {event_id: allinfo}
        timeline.append(event)
    this_page, all_pages = get_paginated(timeline, 6, tlpage, True)

    return this_page, all_pages


@app.errorhandler(404)
def page_not_found(e):
    return render_template('404.html'), 404


@app.errorhandler(500)
def internal_error(e):
    if e is not None:
        logger.error(e)
    return render_template('500.html'), 500


@app.errorhandler(400)
def flask_error(e):
    if e is not None:
        logger.error(e)
    return render_template('500.html'), 400


# @app.errorhandler(Exception)
# def unhandled_exception(e):
#     if e is not None:
#         logger.debug(e)
#     return render_template('500.html'), 500


@app.route("/timeline/<int:tlpage>")
@app.route("/")
@cache.cached(timeout=900, unless=cache_buster)
def homepage(tlpage=None):
    if tlpage is None:
        tlpage = 1
    is_idle = status.idle
    check_stats = ['queue', 'completed', 'failed']
    building = status.current_status
    this_page, all_pages = get_timeline(tlpage)

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
                    bld_obj = build.Build(bnum=bnum)
                except ValueError:
                    continue
                ver = '%s:%s' % (bld_obj.pkgname, bld_obj.version_str)
                end = datetime.strptime(bld_obj.end_str, '%m/%d/%Y %I:%M%p')
                if (datetime.now() - end) < timedelta(hours=48) and ver not in nodup and bld_obj.pkgname:
                    within.append(bld_obj.bnum)
                    nodup.append(ver)

            stats[stat] = len(within)
        else:
            stats[stat] = res

    main_repo = glob.glob('/srv/antergos.info/repo/antergos/x86_64/*.pkg.tar.xz')
    staging_repo = glob.glob('/srv/antergos.info/repo/iso/testing/uefi/antergos-staging/x86_64/*.pkg.tar.xz')

    for repo in [main_repo, staging_repo]:
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

    return render_template("overview.html", idle=is_idle, stats=stats, user=user, building=building,
                           this_page=this_page, all_pages=all_pages, page=tlpage, rev_pending=[])


@app.route("/building")
def build():
    is_idle = status.idle
    now_building = status.now_building
    cont = status.container
    if cont:
        container = cont[:20]
    else:
        container = None
    bnum = status.building_num
    start = status.building_start
    try:
        bld_obj = build.Build(bnum=bnum)
        ver = bld_obj.version_str
    except ValueError as err:
        logger.error(err)
        ver = ''

    return render_template("building.html", idle=is_idle, building=now_building, container=container, bnum=bnum,
                           start=start, ver=ver)


@app.route('/get_log')
def get_log():
    is_idle = status.idle
    if is_idle:
        abort(404)

    return Response(get_live_build_ouput(), direct_passthrough=True, mimetype='text/event-stream')


@app.route('/hook', methods=['POST', 'GET'])
def hooked():
    hook = webhook.Webhook(request, db)
    if hook.result is int:
        abort(hook.result)
    else:
        return json.dumps(hook.result)


@app.before_request
def maybe_check_for_remote_commits():
    check = repo_mon.maybe_check_for_new_items()
    if not check:
        repo_mon.check_for_new_items()


@app.route('/scheduled')
def scheduled():
    is_idle = status.idle
    try:
        queued = status.queue
    except Exception:
        queued = None
    building = status.now_building
    the_queue = []
    if queued:
        for pak in queued:
            try:
                pkg_obj = package.Package(name=pak)
                name = pkg_obj.pkgname
                version = pkg_obj.version_str
                all_info = (name, version)
                the_queue.append(all_info)
            except ValueError as err:
                logger.error(err)

    return render_template("scheduled.html", idle=is_idle, building=building, queue=the_queue, user=user)


@app.route('/completed/<int:page>')
@app.route('/completed/search/<name>')
@app.route('/completed/search/<name>/<int:page>')
@app.route('/completed')
def completed(page=None, name=None):
    is_idle = status.idle
    build_status = 'completed'
    is_logged_in = user.is_authenticated()
    if page is None and name is None:
        page = 1
    if name is not None and page is None:
        page = 1
    building = status.now_building
    completed, all_pages, rev_pending = get_build_info(page, build_status, is_logged_in, name)
    pagination = utils.pagination.Pagination(page, 10, all_pages)

    return render_template("completed.html", idle=is_idle, building=building, completed=completed, all_pages=all_pages,
                           rev_pending=rev_pending, user=user, pagination=pagination)


@app.route('/failed/<int:page>')
@app.route('/failed')
def failed(page=None):
    is_idle = status.idle
    build_status = 'failed'
    if page is None:
        page = 1
    building = status.now_building
    is_logged_in = user.is_authenticated()

    failed, all_pages, rev_pending = get_build_info(page, build_status, is_logged_in)
    pagination = utils.pagination.Pagination(page, 10, all_pages)

    return render_template("failed.html", idle=is_idle, building=building, failed=failed, all_pages=all_pages,
                           page=page, rev_pending=rev_pending, user=user, pagination=pagination)


@app.route('/build/<int:num>')
def build_info(num):
    if not num:
        abort(404)
    try:
        bld_obj = build.Build(bnum=num)
    except Exception:
        abort(404)

    cont = status.container
    log = bld_obj.log
    if not log:
        log = 'Unavailable'
    log = log.decode("utf8")
    if cont:
        container = cont[:20]
    else:
        container = None

    return render_template("build_info.html", pkg=bld_obj.pkgname, ver=bld_obj.version_str, res=bld_obj.result,
                           start=bld_obj.start_str, end=bld_obj.end_str, bnum=bld_obj.bnum, container=container,
                           log=log, user=user)


@app.route('/browse/<goto>')
@app.route('/browse')
def repo_browser(goto=None):
    is_idle = status.idle
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

    return render_template(template, idle=is_idle, building=building, release=release, testing=testing,
                           main=main, user=user)


@app.route('/pkg_review/<int:page>')
@app.route('/pkg_review', methods=['POST', 'GET'])
@groups_required(['admin'])
def dev_pkg_check(page=None):
    is_idle = status.idle
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
    return render_template("pkg_review.html", idle=is_idle, completed=completed, all_pages=all_pages,
                           set_rev_error=set_rev_error, set_rev_error_msg=set_rev_error_msg, user=user,
                           rev_pending=rev_pending, pagination=pagination)


@app.route('/build_pkg_now', methods=['POST', 'GET'])
@groups_required(['admin'])
def build_pkg_now():
    if request.method == 'POST':
        pkgname = request.form['pkgname']
        dev = request.form['dev']
        if not pkgname or pkgname is None or pkgname == '':
            abort(500)
        pexists = db.exists('pkg:%s' % pkgname) or True
        is_logged_in = user.is_authenticated()
        p, a, rev_pending = get_build_info(1, 'completed', is_logged_in)
        # logger.info(rev_pending)
        pending = False
        for bnum in rev_pending.keys():
            # logger.info(bnum)
            if pkgname == rev_pending[bnum]['name']:
                pending = True
                break
        if not pexists:
            flash('Package not found. Has the PKGBUILD been pushed to github?', category='error')
        elif pending:
            flash('Unable to build %s because it is in "pending review" status.' % pkgname, category='error')
        else:
            args = (True, True)
            if 'antergos-iso' in pkgname:
                if db.get('isoBuilding') == 'False':
                    db.set('isoFlag', 'True')
                    args = (True, True)
                    if 'openbox' in pkgname:
                        db.set('isoMinimal', 'True')
                else:
                    logger.info('RATE LIMIT ON ANTERGOS ISO IN EFFECT')
                    return redirect(redirect_url())

            db.rpush('queue', pkgname)
            db.set('build:pkg:now', "True")
            queue.enqueue_call(builder.handle_hook, args=args, timeout=84600)
            logconf.new_timeline_event(
                '<strong>%s</strong> added <strong>%s</strong> to the build queue.' % (dev, pkgname))

    return redirect(redirect_url())


@app.route('/get_status', methods=['GET'])
def get_status():
    idle = status.idle
    building = status.now_building
    if idle:
        message = dict(msg='Idle')
    else:
        message = dict(msg=building)

    return json.dumps(message)


@app.route('/issues', methods=['GET'])
def show_issues():
    return render_template('issues.html')


@app.route('/pkg/<pkgname>', methods=['GET'])
def get_and_show_pkg_profile(pkgname=None):
    if pkgname is None:
        abort(404)
    check = db.exists('pkg:%s:name' % pkgname)
    if not check:
        abort(404)

    # all_pkgs = db.scan_iter('pkg:*:name', 100)
    #
    # for pkg in all_pkgs:
    #     try:
    #         pkgobj = package.Package(db.get(pkg))
    #         #key = 'pkg:' + db.get(pkg) + ':'
    #         completed = db.lrange('completed', 0, -1)
    #         failed = db.lrange('failed', 0, -1)
    #         success = len([x for x in pkgobj.builds if x in completed])
    #         failure = len([x for x in pkgobj.builds if x in failed])
    #         total = len(pkgobj.builds)
    #         success = 100 * success/total
    #         failure = 100 * failure/total
    #         pkgobj.save_to_db('success_rate', success)
    #         pkgobj.save_to_db('failure_rate', failure)
    #     except Exception as err:
    #         logger.error(err)
    pkgobj = package.Package(pkgname)
    if '' == pkgobj.description:
        desc = pkgobj.get_from_pkgbuild('pkgdesc')
        pkgobj.save_to_db('description', desc)

    return render_template('package.html', pkg=pkgobj)


@app.route('/repo_packages/<repo>')
def repo_packages(repo=None):
    if repo is None or repo not in ['antergos', 'antergos-staging']:
        abort(404)
    # is_idle = db.get('idle')
    is_idle = status.idle
    is_logged_in = user.is_authenticated()
    # building = db.get('building')
    building = status.now_building
    packages, rev_pending = get_repo_info(repo, is_logged_in)
    return render_template("repo_pkgs.html", idle=is_idle, building=building, repo_packages=packages,
                           rev_pending=rev_pending, user=user, name=repo)


@app.route('/slack/overflow', methods=['post'])
@app.route('/slack/todo', methods=['post'])
def overflow():
    token = request.values.get('token')
    if not token or '' == token:
        abort(404)
    text = request.values.get('text')
    command = request.values.get('command')

    res = slack_bot.overflow(command, text)

    return Response(res['msg'], content_type=res['content_type'])


if __name__ == "__main__":
    app.run(host='127.0.0.1', port=8020, debug=True, use_reloader=False)
