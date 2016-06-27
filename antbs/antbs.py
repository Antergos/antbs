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

# Start ignoring PyImportSortBear as monkey patching needs to be done before other imports
import gevent
import gevent.monkey

gevent.monkey.patch_all()
# Stop ignoring

import re
from datetime import timedelta

from flask import (
    Flask, abort, render_template, request, url_for
)

from flask_stormpath import StormpathManager, current_user
from werkzeug.contrib.fixers import ProxyFix

import rq_dashboard

from utils import logger, handle_exceptions, AntBSDebugToolbar
from database.server_status import status
from database.monitor import get_monitor_object, check_repos_for_changes

import views


def url_for_other_page(page):
    args = request.view_args.copy()
    args['page'] = page
    return url_for(request.endpoint, **args)


def initialize_app():
    """
    Creates global flask app object and initializes settings.

    """

    app = Flask('antbs')

    handle_exceptions(app)

    # Configuration
    app.config.update({'SECRET_KEY': status.sp_session_key,
                       'STORMPATH_API_KEY_ID': status.sp_api_id,
                       'STORMPATH_API_KEY_SECRET': status.sp_api_key,
                       'STORMPATH_APPLICATION': status.sp_app,
                       'STORMPATH_ENABLE_USERNAME': True,
                       'STORMPATH_REQUIRE_USERNAME': True,
                       'STORMPATH_ENABLE_REGISTRATION': False,
                       'STORMPATH_REDIRECT_URL': '/builds/completed',
                       'STORMPATH_LOGIN_TEMPLATE': 'admin/login.html',
                       'STORMPATH_COOKIE_DURATION': timedelta(days=14),
                       'STORMPATH_ENABLE_FORGOT_PASSWORD': True,
                       'DEBUG_TB_PROFILER_ENABLED': True})

    # Debug Toolbar - only enabled in debug mode and only for logged-in users:
    # app.debug = True
    # AntBSDebugToolbar(app)

    # Create Stormpath Manager object.
    StormpathManager(app)

    # Jinja2 configuration
    app.jinja_options = Flask.jinja_options.copy()
    app.jinja_options['lstrip_blocks'] = True
    app.jinja_options['trim_blocks'] = True
    app.jinja_env.globals['url_for_other_page'] = url_for_other_page

    # Use gunicorn with nginx proxy
    app.wsgi_app = ProxyFix(app.wsgi_app)

    # Setup rq_dashboard (accessible at '/rq' endpoint)
    app.config.from_object(rq_dashboard.default_settings)
    app.register_blueprint(rq_dashboard.blueprint, url_prefix='/rq')

    # Register our views
    app.register_blueprint(views.api_view, url_prefix='/api')
    app.register_blueprint(views.build_view, url_prefix='/builds')
    app.register_blueprint(views.build_view, url_prefix='/build')
    app.register_blueprint(views.home_view)
    app.register_blueprint(views.live_view, url_prefix='/building')
    app.register_blueprint(views.package_view, url_prefix='/package')
    app.register_blueprint(views.repo_view, url_prefix='/repo')

    return app


# Make `app` available to gunicorn
app = initialize_app()


@app.before_request
def rq_dashboard_requires_auth():
    if '/rq' in request.path and not current_user.is_authenticated:
        abort(403)


@app.before_request
def maybe_check_monitored_repos():
    monitor_obj = get_monitor_object('github')

    if not monitor_obj.checked_recently:
        views.repo_queue.enqueue_call(check_repos_for_changes('github'))


@app.context_processor
def inject_global_template_variables():
    return dict(
        idle=status.idle,
        current_status=status.current_status,
        now_building=status.now_building,
        rev_pending=status.pending_review,
        user=current_user,
        current_user=current_user,
        _all_packages=status.all_packages
    )


@app.template_filter('tpl_name')
def tpl_name(s):
    res = re.findall('\'([^\']*)\'', str(s))

    return None if not res else res[0]


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


@app.route('/issues', methods=['GET'])
def show_issues():
    return render_template('issues.html')


if __name__ == "__main__":
    app.run(host='127.0.0.1', port=8020, debug=True, use_reloader=False)
