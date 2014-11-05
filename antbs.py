#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# build_pkg.py
#
# Copyright 2013 Antergos
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
#  MA 02110-1301, USA.

"""Import the main class from the "Flask" library that we need to create our
web application, as well as the `render_template` function for returning
HTML files as responses and getting `request` objects that contain
information about requests that we receive, like the URL and cookies and
stuff like that."""

import ipaddress
import json
import subprocess
import time
import os
from rq import Queue, Connection, Worker
from flask import Flask, request, Response, abort, render_template
from werkzeug.contrib.fixers import ProxyFix
import requests
import docker
import src.build_pkg as builder
from src.redis_connection import db
import logging
import logging.config
import logging.handlers
import src.logging_config as logconf
import glob
import re
import shutil
from flask.ext.stormpath import StormpathManager, groups_required, user
from datetime import datetime, timedelta
import newrelic
import gevent
import gevent.monkey

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
stormpath_manager = StormpathManager(app)
app.jinja_options = Flask.jinja_options.copy()
app.jinja_options['lstrip_blocks'] = True
app.jinja_options['trim_blocks'] = True

settings = newrelic.agent.global_settings()
settings.app_name = 'AntBS'

# Use gunicorn to proxy with nginx
app.wsgi_app = ProxyFix(app.wsgi_app)

# Setup logging
logger = logging.getLogger('')
logging.config.dictConfig(logconf.log_config)


def handle_worker_exception(job, *exc_info):
    doc = docker.Client(base_url='unix://var/run/docker.sock', version='1.12', timeout=10)
    container = db.get('container')
    doc.kill(container)
    doc.remove_container(container)
    repo = os.path.join("/tmp", "staging")
    cache = os.path.join("/tmp", "pkg_cache")
    try:
        shutil.rmtree(repo)
        shutil.rmtree(cache)
        shutil.rmtree('/opt/antergos-packages')
    except Exception:
        pass
    db.set('idle', "True")
    db.set('building', 'Idle')
    db.set('container', '')
    db.set('building_num', '')
    db.set('building_start', '')
    db.ltrim('queue', 0, 0)


with Connection(db):
    queue = Queue('build_queue')
    w = Worker([queue], exc_handler=handle_worker_exception)


def stream_template(template_name, **context):
    app.update_template_context(context)
    t = app.jinja_env.get_template(template_name)
    rv = t.stream(context)
    #rv.enable_buffering(5)
    rv.disable_buffering()
    return rv


def get_log_stream(bnum=None):
    #doc = docker.Client(base_url='unix://var/run/docker.sock', version='1.12', timeout=10)
    is_idle = db.get('idle')
    now_building = db.get('building')
    container = db.get('container')
    if bnum is not None:
        log = db.lrange('build_log:%s:content' % bnum, 0, -1)
        log_end = db.lrange('build_log:%s:content' % bnum, -1, -1)
        nodata = ['data: Unable to retrieve build log.\n\n']
        if not log:
            for line in nodata:
                yield line
            yield 'data: ENDOFLOG\n\n'
        else:
            for line in log:
                yield 'data: %s\n\n' % line
            yield 'data: ENDOFLOG\n\n'
    else:
        nodata = ['data: There are no active builds.\n\n']
        if is_idle == "True" or now_building == "Idle" or now_building == 'Initializing...':
            app.logger.debug("No active container detected")
            for line in nodata:
                yield line
            yield 'data: ENDOFLOG\n\n'
        else:
            #doclog = doc.logs(container, stdout=True, stderr=True, stream=True, timestamps=True)
            proc = subprocess.Popen(['docker', 'logs', '--follow', '-t', container], stdout=subprocess.PIPE)
            stream = iter(proc.stdout.readline, '')
            nodup = set()
            part2 = None
            random = os.urandom(12)
            db.set('stream:%s' % random, 'True')
            db.expire('stream:%s' % random, 22)
            session = db.exists('stream:%s' % random)
            for line in stream:
                if not session:
                    yield 'data: PINGPING\n\n'
                    db.set('stream:%s' % random, 'True')
                    db.expire('stream:%s' % random, 22)
                if not line or line == '':
                    continue
                line = line.rstrip()
                end = line[20:]
                if end not in nodup:
                    gevent.sleep(.05)
                    nodup.add(end)
                    line = line.replace("can't", "can not")
                    #if len(line) > 210:
                    #    part1 = line[:210]
                    #    part2 = line[211:]
                    #    yield 'data: %s\n\n' % part1
                    #   continue
                    #elif part2:
                    #   yield 'data: %s\n\n' % part2
                    #   part2 = None
                    #   continue
                    #else:
                    yield 'data: %s\n\n' % line
            yield 'data: ENDOFLOG\n\n'


def get_live_build_ouput():
    pubsub = db.pubsub()
    pubsub.subscribe('build-output')
    for message in pubsub.listen():
        gevent.sleep(.05)
        if message['data'] == '1' or message['data'] == 1:
            message['data'] = '...'
        yield 'data: %s\n\n' % message['data']


def get_paginated(pkg_list, per_page, page):
    pkg_list.reverse()
    page -= 1
    paginated = [pkg_list[i:i + per_page] for i in range(0, len(pkg_list), per_page)]
    logger.info(paginated)
    this_page = paginated[page]
    all_pages = len(paginated)

    return this_page, all_pages


def get_build_info(page=None, status=None, logged_in=False, review=False):
    if page is None or status is None:
        abort(500)
    pkg_info_cache = db.exists('pkg_info_cache:%s' % status)
    rev_info_cache = not db.exists('rev_info_cache') and review and logged_in
    pending_rev_cache = not db.exists('pending_rev_cache') and logged_in
    logger.info('[GET_BUILD_INFO] - CALLED')
    all_pages = 1
    if any(not i for i in(pkg_info_cache, rev_info_cache, pending_rev_cache)):
        logger.info('[GET_BUILD_INFO] - "NOT ANY" CONDITION SATISFIED')
        try:
            all_builds = db.lrange(status, 0, -1)
        except Exception:
            all_builds = None

        pkg_list = []
        rev_pending = []

        if all_builds is not None:
            #builds, all_pages = get_paginated(all_builds, 10, page)
            for build in all_builds:
                try:
                    pkg = db.get('build_log:%s:pkg' % build)
                except Exception:
                    continue
                name = db.get('pkg:%s:name' % pkg)
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
                if logged_in and review and review_stat != "pending":
                    continue
                all_info = dict(bnum=bnum, name=name, version=version, start=start, end=end, review_stat=review_stat,
                                review_dev=review_dev, review_date=review_date)
                pkg_info = {bnum: all_info}
                pkg_list.append(pkg_info)
                if logged_in and review_stat == "pending" and len(rev_pending) < 5:
                    rev_pending.append(pkg_info)
                else:
                    continue

            if review:
                db.setex('rev_info_cache', 10800, pkg_list)
            else:
                db.setex('pkg_info_cache:%s' % status,  10800, pkg_list)
            db.setex('pending_rev_cache', 10800, rev_pending)
    else:
        logger.info('[GET_BUILD_INFO] - "NOT ANY" CONDITION NOT SATISFIED')
        if review:
            pkg_list = list(db.get('rev_info_cache'))
        else:
            pkg_list = list(db.get('pkg_info_cache:%s' % status))
        rev_pending = list(db.get('pending_rev_cache'))

    return pkg_list, all_pages, rev_pending


def copy(src, dst):
    if os.path.islink(src):
        linkto = os.readlink(src)
        os.symlink(linkto, dst)
    else:
        shutil.copy(src, dst)

def set_pkg_review_result(bnum=None, dev=None, result=None):
    if not all(i is not None for i in (bnum, dev, result)):
        abort(500)
    errmsg = dict(error=False, msg=None)
    dt = datetime.now().strftime("%m/%d/%Y %I:%M%p")
    try:
        db.set('build_log:%s:review_stat' % bnum, result)
        db.set('build_log:%s:review_dev' % bnum, dev)
        db.set('build_log:%s:review_date' % bnum, dt)
        pkg = db.get('build_log:%s:pkg' % bnum)
        pkg_files = glob.glob('%s/*/%s*.*' % (STAGING_REPO, pkg))
        logger.info('[PKG_FILES]:')
        if pkg_files and pkg_files is not None:
            logger.info(pkg_files)
            for file in pkg_files:
                copy(file, MAIN_REPO)
    except Exception as err:
        errmsg = dict(error=True, msg=err)

    queue.enqueue_call(builder.update_main_repo, timeout=9600)

    return errmsg


@app.errorhandler(404)
def page_not_found(e):
    return render_template('404.html'), 404


@app.errorhandler(500)
def internal_error(e):
    return render_template('500.html'), 500

@app.route("/")
def homepage():
    is_idle = db.get('idle')
    check_stats = ['queue', 'completed', 'failed']
    stats = {}
    for stat in check_stats:
        res = db.llen(stat)
        stats[stat] = res
        if stat is not "queue":
            builds = db.lrange(stat, 0, -1)
            within = []
            for build in builds:
                end = db.get('build_log:%s:start' % build)
                end_fmt = datetime.strptime(end, '%m/%d/%Y %I:%M%p')
                if (datetime.now() - end_fmt) < timedelta(hours=72):
                    within.append(build)
                stats[stat] = len(within)

    repos = ['main', 'staging']
    x86_64 = None
    i686 = None
    cached = None
    for repo in repos:
        if repo == 'main':
            x86_64 = glob.glob('/srv/antergos.info/repo/antergos/x86_64/*.*.pkg.tar.xz')
            i686 = glob.glob('/srv/antergos.info/repo/antergos/i686/*.*.pkg.tar.xz')
            cached = db.exists('repo-count-main')
            if cached and cached is not None:
                stats['repo_main'] = db.get('repo-count-main')
        elif repo == 'staging':
            x86_64 = glob.glob('/srv/antergos.info/repo/iso/testing/uefi/antergos-staging/x86_64/*.*.pkg.tar.xz')
            i686 = glob.glob('/srv/antergos.info/repo/iso/testing/uefi/antergos-staging/i686/*.*.pkg.tar.xz')
            cached = db.exists('repo-count-staging')
            if cached and cached is not None:
                stats['repo_staging'] = db.get('repo-count-staging')

        all_p = x86_64 + i686
        filtered = []

        if not cached or cached is None:
            for fp in all_p:
                new_fp = os.path.basename(fp)
                new_fp = new_fp.split('.')
                new_fp = new_fp[0]
                filtered.append(new_fp)
            stats['repo_' + repo] = len(set(filtered))
            db.setex('repo-count-%s' % repo, 1800, stats['repo_' + repo])

    return render_template("overview.html", idle=is_idle, stats=stats)


@app.route("/building")
def build():
    is_idle = db.get('idle')
    now_building = db.get('building')
    cont = db.get('container')
    if cont:
        container = cont[:20]
    else:
        container = None
    bnum = db.get('building_num')
    start = db.get('building_start')

    return render_template("building.html", idle=is_idle, building=now_building, container=container, bnum=bnum, start=start)
    #return Response(stream_template('building.html', idle=is_idle, bnum=bnum, start=start, building=now_building,
    #                                container=container))


@app.route('/get_log')
def get_log():
    is_idle = db.get('idle')
    if is_idle == "True":
        abort(404)

    return Response(get_live_build_ouput(), direct_passthrough=True, mimetype='text/event-stream')


@app.route('/hook', methods=['POST', 'GET'])
def hooked():
    is_phab = None
    if request.method == 'GET':
        return ' Nothing to see here, move along ...'

    elif request.method == 'POST':
        # Check if the POST request if from github.com
        phab = int(request.args.get('phab', '0'))
        is_phab = False
        if phab and phab > 0:
            is_phab = True
        else:
            # Store the IP address blocks that github uses for hook requests.
            hook_blocks = requests.get('https://api.github.com/meta').json()['hooks']
            for block in hook_blocks:
                ip = ipaddress.ip_address(u'%s' % request.remote_addr)
                if ipaddress.ip_address(ip) in ipaddress.ip_network(block):
                    break  # the remote_addr is within the network range of github
            else:
                abort(403)
            if request.headers.get('X-GitHub-Event') == "ping":
                return json.dumps({'msg': 'Hi!'})
            if request.headers.get('X-GitHub-Event') != "push":
                return json.dumps({'msg': "wrong event type"})
    changes = []
    if is_phab:
        repo = 'antergos-packages'
        db.set('pullFrom', 'lots0logs')
        the_queue = list(db.lrange('queue', 0, -1))
        building = db.get('building')
        match = None
        nx_pkg = None
        if request.args['repo'] == "NX":
            nx_pkg = 'numix-icon-theme'
        elif request.args['repo'] == "NXSQ":
            nx_pkg = 'numix-icon-theme-square'
        if the_queue and nx_pkg:
            for p in the_queue:
                if p == nx_pkg or p == building:
                    match = True
                    break
                else:
                    continue
            if match is None:
                changes.append(list(nx_pkg))
            else:
                return json.dumps({'msg': 'OK!'})
    else:
        payload = json.loads(request.data)
        full_name = payload['repository']['full_name']
        if 'lots0logs' in full_name:
            db.set('pullFrom', 'lots0logs')
        else:
            db.set('pullFrom', 'antergos')
        repo = payload['repository']['name']
        commits = payload['commits']
        for commit in commits:
            changes.append(commit['modified'])
            changes.append(commit['added'])

    if repo == "antergos-packages":
        db.set('idle', 'False')
        db.set('building', "Initializing...")
        logger.info(changes)
        has_pkgs = False
        no_dups = []

        for changed in changes:
            for item in changed:
                if is_phab:
                    pak = item
                else:
                    pak = os.path.dirname(item)
                if pak is not None and pak != '':
                    logger.info('Adding %s to the build queue' % pak)
                    no_dups.append(pak)
                    has_pkgs = True

        if has_pkgs:
            the_pkgs = list(set(no_dups))
            first = True
            last = False
            last_pkg = the_pkgs[-1]
            for p in the_pkgs:
                db.rpush('queue', p)
                if p == last_pkg:
                    last = True
                queue.enqueue_call(builder.handle_hook, args=(first, last), timeout=9600)
                first = False

    elif repo == "antergos-iso":
        last = db.get('pkg:antergos-iso:last_commit')
        if (last and (datetime.now() - last) > timedelta(hours=1)) or (last and (last is None or last == '0')):
            db.set('idle', 'False')
            db.set('building', "Initializing...")
            db.set('isoFlag', 'True')
            db.set('pkg:antergos-iso:last_commit', datetime.now())
            queue.enqueue_call(builder.handle_hook, timeout=10000)

    return json.dumps({'msg': 'OK!'})


@app.route('/scheduled')
def scheduled():
    is_idle = db.get('idle')
    try:
        pkgs = db.lrange('queue', 0, -1)
    except Exception:
        pkgs = None
    building = db.get('building')
    the_queue = {}
    if pkgs is not None:
        for pak in pkgs:
            name = db.get('pkg:%s:name' % pak)
            version = db.get('pkg:%s:version' % pak)
            all_info = dict(name=name, version=version)
            the_queue[pak] = all_info

    return render_template("scheduled.html", idle=is_idle, building=building, queue=the_queue, user=user)


@app.route('/completed/<int:page>')
@app.route('/completed')
def completed(page=None):
    is_idle = db.get('idle')
    status = 'completed'
    is_logged_in = user.is_authenticated()
    if page is None:
        page = 1
    building = db.get('building')
    completed, all_pages, rev_pending = get_build_info(page, status, is_logged_in)

    return render_template("completed.html", idle=is_idle, building=building, completed=completed, all_pages=all_pages,
                           page=page, rev_pending=rev_pending, user=user)


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

    return render_template("failed.html", idle=is_idle, building=building, failed=failed, all_pages=all_pages,
                           page=page, rev_pending=rev_pending, user=user)


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
    log = db.get('build_log:%s:content' % bnum)
    log = log.decode("utf8")
    if cont:
        container = cont[:20]
    else:
        container = None

    return render_template("build_info.html", pkg=pkg, ver=ver, res=res, start=start, end=end,
                           bnum=bnum, container=container, log=log)


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

    return render_template(template, idle=is_idle, building=building, release=release, testing=testing, main=main)


@app.route('/pkg_review', methods=['POST', 'GET'])
@groups_required(['admin'])
def dev_pkg_check():
    is_idle = db.get('idle')
    status = 'completed'
    set_rev_error = False
    set_rev_error_msg = None
    review = True
    uname = user.username
    #if page is None:
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
                message = dict(error=set_rev_error)
                return json.dumps(message)
            else:
                message = dict(msg='ok')
                db.delete('rev_info_cache')
                db.delete('pkg_info_cache:completed')
                db.delete('pending_rev_cache')
                return json.dumps(message)

    completed, all_pages, rev_pending = get_build_info(page, status, True, True)

    return render_template("pkg_review.html", idle=is_idle, completed=completed, all_pages=all_pages, page=page,
                           set_rev_error=set_rev_error, set_rev_error_msg=set_rev_error_msg, uname=uname, rev_pending=rev_pending, user=user)


# Some boilerplate code that just says "if you're running this from the command
# line, start here." It's not critical to know what this means yet.
if __name__ == "__main__":
    app.debug = True
    app.run(port=8020)
