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
import gevent
import gevent.monkey
import src.pagination
import src.build_pkg as builder
from src.redis_connection import db
from src.logging_config import Logger
import src.package as package
import src.webhook as webhook
# import newrelic

gevent.monkey.patch_all()

SRC_DIR = os.path.dirname(__file__) or '.'
STAGING_REPO = '/srv/antergos.info/repo/iso/testing/uefi/antergos-staging'
MAIN_REPO = '/srv/antergos.info/repo/antergos'
STAGING_64 = os.path.join(STAGING_REPO, 'x86_64')
STAGING_32 = os.path.join(STAGING_REPO, 'i686')
MAIN_64 = os.path.join(MAIN_REPO, 'x86_64')
MAIN_32 = os.path.join(MAIN_REPO, 'i686')


# Create the variable `app` which is an instance of the Flask class that
# we just imported.
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

logger = Logger()


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
    queue = db.lrange('queue', 0, -1)
    now_building = db.hgetall('now_building')
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

            db.set('idle', "True")
            db.set('building', 'Idle')
            db.hset('now_building', 'pkg', '')
            db.set('container', '')
            db.set('building_num', '')
            db.set('building_start', '')

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


def get_build_info(page=None, status=None, logged_in=False, search=None):
    if page is None or status is None:
        abort(500)
    if search is not None:
        sinfo_key = 'cache:search_info:%s:%s:%s' % (status, search, page)
        search_info_cache = db.exists(sinfo_key)
        srevinfo_key = 'cache:srev_info:%s:%s:%s' % (status, search, page)
        check_cache = search_info_cache
    else:
        pinfo_key = 'cache:pkg_info:%s:%s' % (status, page)
        revinfo_key = 'cache:rev_info:%s:%s' % (status, page)
        pkg_info_cache = db.exists(pinfo_key)
        rev_info_cache = db.exists(revinfo_key)
        check_cache = all(i for i in (pkg_info_cache, rev_info_cache))
        if 'antergos' in status:
            status = 'completed'

    pkg_list = {}
    rev_pending = {}

    # logger.info('@@-antbs.py-@@ 221 | GET_BUILD_INFO - FIRED')
    if not check_cache:
        logger.info('@@-antbs.py-@@ 223 | GET_BUILD_INFO - "ALL" CONDITION FAILED. WE ARE NOT USING CACHED INFO')
        try:
            all_builds = db.lrange(status, 0, -1)
        except Exception:
            logger.error('@@-antbs.py-@@ 227 | GET_BUILD_INFO - DATABASE ERROR')
            abort(500)

        if all_builds is not None:
            if search is not None:
                search_all_builds = [x for x in all_builds if x is not None and match_pkg_name_build_log(x, search)]
                logger.info('@@-antbs.py-@@ [completed route search] | search_all_builds is %s', search_all_builds)
                all_builds = search_all_builds
                pinfo_key = sinfo_key
            if all_builds is not None:

                builds, all_pages = get_paginated(all_builds, 10, page, False)
                db.set('%s:all_pages' % pinfo_key, all_pages)
                logger.info('@@-antbs.py-@@ [completed route search] | builds is %s',  builds)
                logger.info('@@-antbs.py-@@ [completed route search] | all_pages is %s', all_pages)
                for build in builds:
                    # logger.info(build)
                    try:
                        pkg = db.get('build_log:%s:pkg' % build)
                        # logger.info(pkg)
                    except Exception:
                        logger.error('exception')
                        continue
                    bnum = build
                    version = db.get('build_log:%s:version' % bnum)
                    if not version or version is None:
                        version = db.get('pkg:%s:version' % pkg)
                    start = db.get('build_log:%s:start' % bnum)
                    end = db.get('build_log:%s:end' % bnum)
                    review_stat = db.get('build_log:%s:review_stat' % bnum)
                    review_stat = db.get('review_stat:%s:string' % review_stat)
                    review_dev = db.get('build_log:%s:review_dev' % bnum)
                    review_date = db.get('build_log:%s:review_date' % bnum)
                    all_info = dict(bnum=bnum, name=pkg, version=version, start=start, end=end,
                                    review_stat=review_stat, review_dev=review_dev, review_date=review_date)
                    pkg_list[bnum] = all_info
                    if search:
                        db.hmset('%s:%s' % (sinfo_key, bnum), all_info)
                        db.expire('%s:%s' % (sinfo_key, bnum), 902)
                        db.rpush(sinfo_key, bnum)
                        db.expire(sinfo_key, 901)
                    else:
                        db.hmset('%s:%s' % (pinfo_key, bnum), all_info)
                        db.expire('%s:%s' % (pinfo_key, bnum), 902)
                        db.rpush(pinfo_key, bnum)
                        db.expire(pinfo_key, 901)
                    # db.rpush('pkg_info_cache:%s:list:%s' % (status, page), pkg_info)
                    if logged_in and review_stat == "pending" and search is not None:
                        rev_pending[bnum] = all_info
                        db.hmset('%s:%s' % (srevinfo_key, bnum), all_info)
                        db.expire('%s:%s' % (srevinfo_key, bnum), 901)
                        db.rpush(srevinfo_key, bnum)
                        db.expire(srevinfo_key, 900)
                    elif logged_in and review_stat == "pending":
                        rev_pending[bnum] = all_info
                        db.hmset('%s:%s' % (revinfo_key, bnum), all_info)
                        db.expire('%s:%s' % (revinfo_key, bnum), 901)
                        db.rpush(revinfo_key, bnum)
                        db.expire(revinfo_key, 900)
                        # db.rpush('pending_rev_cache:list:%s' % page, pkg_info)

    else:
        logger.info('@@-antbs.py-@@ 276 | GET_BUILD_INFO - "ALL" CONDITION MET. WE ARE USING CACHED INFO')
        if search:
            srevindex = db.lrange(srevinfo_key, 0, -1)
            sindex = db.lrange(sinfo_key, 0, -1)
            for p in sindex:
                h = db.hgetall('%s:%s' % (sinfo_key, p))
                pkg_list[p] = h
            for rev in srevindex:
                h = db.hgetall('%s:%s' % (srevinfo_key, rev))
                rev_pending[rev] = h
            # logger.info('@@-antbs.py-@@ 280 | GET_BUILD_INFO - pkg_list hash is %s' % str(pkg_list))
            all_pages = db.get('%s:all_pages' % sinfo_key)
        else:
            revindex = db.lrange(revinfo_key, 0, -1)
            pindex = db.lrange(pinfo_key, 0, -1)
            for p in pindex:
                h = db.hgetall('%s:%s' % (pinfo_key, p))
                pkg_list[p] = h
            for rev in revindex:
                h = db.hgetall('%s:%s' % (revinfo_key, rev))
                rev_pending[rev] = h
            # logger.info('@@-antbs.py-@@ 280 | GET_BUILD_INFO - pkg_list hash is %s' % str(pkg_list))
            all_pages = db.get('%s:all_pages' % pinfo_key)

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
                pkg = package.Package(item, db)
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
    errmsg = dict(error=False, msg=None)
    dt = datetime.now().strftime("%m/%d/%Y %I:%M%p")
    try:
        if result == "4":
            return errmsg
        db.set('build_log:%s:review_dev' % bnum, dev)
        db.set('build_log:%s:review_date' % bnum, dt)
        db.set('idle', 'False')
        result = int(result)
        pkg = db.get('build_log:%s:pkg' % bnum)
        if pkg:
            pobj = package.Package(pkg, db)
            if 'main' not in pobj.allowed_in and result == 2:
                msg = '%s is not allowed in main repo.' % pkg
                errmsg.update(error=True, msg=msg)
                return errmsg
            else:
                db.set('build_log:%s:review_stat' % bnum, result)

        # logger.info('Updating pkg review status for %s.' % pkg)
        # logger.info('[UPDATE REPO]: pkg is %s' % pkg)
        # logger.info('[UPDATE REPO]: STAGING_64 is %s' % STAGING_64)
        pkg_files_64 = glob.glob('%s/%s-***' % (STAGING_64, pkg))
        pkg_files_32 = glob.glob('%s/%s-***' % (STAGING_32, pkg))
        pkg_files = pkg_files_64 + pkg_files_32
        # logger.info('[UPDATE REPO]: pkg_files is %s' % pkg_files)
        # logger.info('[PKG_FILES]:')
        if pkg_files and pkg_files is not None:
            # logger.info(pkg_files)
            logger.info('Moving %s from staging to main repo.', pkg)

            for f in pkg_files_64:
                if result is 2 or result == '2':
                    copy(f, MAIN_64)
                    copy(f, '/tmp')
                elif result is 3 or result == '3':
                    os.remove(f)
            for f in pkg_files_32:
                if result is 2 or result == '2':
                    copy(f, MAIN_32)
                    copy(f, '/tmp')
                elif result is 3 or result == '3':
                    os.remove(f)
            if result and result is not 4 and result != '4':
                repo_queue.enqueue_call(builder.update_main_repo, args=(pkg, str(result)), timeout=9600)

        else:
            logger.error('@@-antbs.py-@@ | While moving to main, no packages were found to move.')
            err = 'While moving to main, no packages were found to move.'
            errmsg = dict(error=True, msg=err)

    except (OSError, Exception) as err:
        logger.error('@@-antbs.py-@@ | Error while moving to main: %s', err)
        err = str(err)
        errmsg = dict(error=True, msg=err)

    return errmsg


def get_timeline(tlpage=None):
    event_ids = db.lrange('timeline:all', 0, -1)
    timeline = []
    for event_id in event_ids:
        date = db.get('timeline:%s:date' % event_id)
        time = db.get('timeline:%s:time' % event_id)
        msg = db.get('timeline:%s:msg' % event_id)
        tltype = db.get('timeline:%s:type' % event_id)
        allinfo = dict(event_id=event_id, date=date, msg=msg, time=time, tltype=tltype)
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


@app.errorhandler(Exception)
def unhandled_exception(e):
    if e is not None:
        logger.debug(e)
    return render_template('500.html'), 500


@app.route("/timeline/<int:tlpage>")
@app.route("/")
def homepage(tlpage=None):
    if tlpage is None:
        tlpage = 1
    is_idle = db.get('idle')
    check_stats = ['queue', 'completed', 'failed']
    building = db.get('building')
    this_page, all_pages = get_timeline(tlpage)
    is_logged_in = user.is_authenticated()
    c, a, rev_pending = get_build_info(1, 'completed', is_logged_in)
    # logger.info('@@-antbs.py-@@ | this_page is %s' % all_pages)
    stats = {}
    for stat in check_stats:
        res = db.llen(stat)
        if stat is not "queue":
            builds = db.lrange(stat, 0, -1)
            builds = [x for x in builds if x is not None]
            within = []
            nodup = []
            for build in builds:
                end = db.get('build_log:%s:start' % build) or '12/15/2014 06:12PM'
                end_fmt = datetime.strptime(end, '%m/%d/%Y %I:%M%p')
                ver = db.get('build_log:%s:version' % build) or '0.00'
                name = db.get('build_log:%s:pkg' % build) or 'None'
                ver = '%s:%s' % (name, ver)
                if (datetime.now() - end_fmt) < timedelta(hours=48) and ver not in nodup and name != 'None':
                    within.append(build)
                    nodup.append(ver)

            stats[stat] = len(within)
        else:
            stats[stat] = res

    repos = ['main', 'staging']
    for repo in repos:
        x86_64 = None
        cached = None
        if repo == 'main':
            x86_64 = glob.glob('/srv/antergos.info/repo/antergos/x86_64/*.pkg.tar.xz')
            cached = db.exists('repo-count-main')
            if cached and cached is not None:
                stats['repo_main'] = db.get('repo-count-main')
        elif repo == 'staging':
            x86_64 = glob.glob('/srv/antergos.info/repo/iso/testing/uefi/antergos-staging/x86_64/*.pkg.tar.xz')
            cached = db.exists('repo-count-staging')
            if cached and cached is not None:
                stats['repo_staging'] = db.get('repo-count-staging')

        all_p = x86_64
        filtered = []

        if not cached or cached is None:
            for fp in all_p:
                new_fp = os.path.basename(fp)
                if 'dummy-package' not in new_fp:
                    filtered.append(new_fp)
            stats['repo_' + repo] = len(set(filtered))
            db.setex('repo-count-%s' % repo, 1800, stats['repo_' + repo])

    return render_template("overview.html", idle=is_idle, stats=stats, user=user, building=building,
                           this_page=this_page, all_pages=all_pages, page=tlpage, rev_pending=rev_pending)


@app.route("/building")
def build():
    is_idle = db.get('idle')
    now_building = db.hget('now_building', 'pkg')
    cont = db.get('container')
    if cont:
        container = cont[:20]
    else:
        container = None
    bnum = db.get('building_num')
    start = db.get('building_start')
    ver = db.get('build_log:%s:version' % bnum)

    return render_template("building.html", idle=is_idle, building=now_building, container=container, bnum=bnum,
                           start=start, ver=ver)
    # return Response(stream_template('building.html', idle=is_idle, bnum=bnum, start=start, building=now_building,
    #                                container=container))


@app.route('/get_log')
def get_log():
    is_idle = db.get('idle')
    if is_idle == "True":
        abort(404)

    return Response(get_live_build_ouput(), direct_passthrough=True, mimetype='text/event-stream')


@app.route('/hook', methods=['POST', 'GET'])
def hooked():
    hook = webhook.Webhook(request, db, queue)
    if hook.result is int:
        abort(hook.result)
    else:
        return hook.result


@app.route('/scheduled')
def scheduled():
    is_idle = db.get('idle')
    try:
        pkgs = db.lrange('queue', 0, -1)
    except Exception:
        pkgs = None
    building = db.get('building')
    the_queue = []
    if pkgs is not None:
        for pak in pkgs:
            name = db.get('pkg:%s:name' % pak)
            version = db.get('pkg:%s:version' % pak)
            all_info = (name, version)
            the_queue.append(all_info)

    return render_template("scheduled.html", idle=is_idle, building=building, queue=the_queue, user=user)


@app.route('/completed/<int:page>')
@app.route('/completed/search/<name>')
@app.route('/completed/search/<name>/<int:page>')
@app.route('/completed')
def completed(page=None, name=None):
    is_idle = db.get('idle')
    status = 'completed'
    is_logged_in = user.is_authenticated()
    if page is None and name is None:
        page = 1
    if name is not None and page is None:
        page = 1
    building = db.get('building')
    completed, all_pages, rev_pending = get_build_info(page, status, is_logged_in, name)
    # logger.info('@@-antbs.py-@@ [completed route] | %s' % all_pages)
    pagination = src.pagination.Pagination(page, 10, all_pages)
    # logger.info('@@-antbs.py-@@ [completed route] | %s, %s, %s' % (
    #    pagination.page, pagination.per_page, pagination.total_count))

    return render_template("completed.html", idle=is_idle, building=building, completed=completed, all_pages=all_pages,
                           rev_pending=rev_pending, user=user, pagination=pagination)


@app.route('/failed/<int:page>')
@app.route('/failed')
def failed(page=None):
    is_idle = db.get('idle')
    status = 'failed'
    if page is None:
        page = 1
    building = db.get('building')
    is_logged_in = user.is_authenticated()

    failed, all_pages, rev_pending = get_build_info(page, status, is_logged_in)
    pagination = src.pagination.Pagination(page, 10, all_pages)
    return render_template("failed.html", idle=is_idle, building=building, failed=failed, all_pages=all_pages,
                           page=page, rev_pending=rev_pending, user=user, pagination=pagination)


@app.route('/build/<int:num>')
def build_info(num):
    if not num:
        abort(404)
    pkg = db.get('build_log:%s:pkg' % num)
    if not pkg:
        abort(404)
    ver = db.get('pkg:%s:version' % pkg)
    res = db.get('build_log:%s:result' % num)
    start = db.get('build_log:%s:start' % num)
    end = db.get('build_log:%s:end' % num)
    bnum = num
    cont = db.get('container')
    check_log = db.hexists('build_log:%s:content' % bnum, 'content')
    if not check_log:
        log = db.get('build_log:%s:content' % bnum)
    else:
        log = db.hget('build_log:%s:content' % bnum, 'content')
    if log is None or log == '':
        log = 'Unavailable'
    log = log.decode("utf8")
    if cont:
        container = cont[:20]
    else:
        container = None

    return render_template("build_info.html", pkg=pkg, ver=ver, res=res, start=start, end=end,
                           bnum=bnum, container=container, log=log, user=user)


@app.route('/browse/<goto>')
@app.route('/browse')
def repo_browser(goto=None):
    is_idle = db.get('idle')
    building = db.get('building')
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
    is_idle = db.get('idle')
    status = 'completed'
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
                db.delete('rev_info_cache')
                db.delete('pkg_info_cache:completed')
                db.delete('pending_rev_cache')
                return json.dumps(message)

    completed, all_pages, rev_pending = get_build_info(page, status, is_logged_in)
    pagination = src.pagination.Pagination(page, 10, len(rev_pending))
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
        pexists = db.exists('pkg:%s' % pkgname)
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
    idle = db.get('idle')
    building = db.get('building')
    if idle == 'True':
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
    is_idle = db.get('idle')
    is_logged_in = user.is_authenticated()
    building = db.get('building')
    packages, rev_pending = get_repo_info(repo, is_logged_in)
    return render_template("repo_pkgs.html", idle=is_idle, building=building, repo_packages=packages,
                           rev_pending=rev_pending, user=user, name=repo)

# Some boilerplate code that just says "if you're running this from the command
# line, start here." It's not critical to know what this means yet.
if __name__ == "__main__":
    app.debug = False
    app.run(port=8020)
