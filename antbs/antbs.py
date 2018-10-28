#!/usr/bin/python
# -*- coding: utf-8 -*-
#
# antbs.py
#
# Copyright © 2013-2017 Antergos
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

from datetime import timedelta
from importlib import import_module

from flask import Flask

import re

from flask import (
    current_app,
    render_template,
    request,
    url_for,
    abort,
    session,
)

from database import (
    status,
    get_build_object,
)
from utils import get_current_user

from logging_config import handle_exceptions

from database import (
    status,
    get_monitor_object,
    check_repos_for_changes
)

from config import AntBSConfig
from views import all_views
from extensions import (
    debug_toolbar,
    rq_dashboard
)
import webhook

logger = status.logger


def create_app():
    """
    Creates global flask app object and initializes settings.

    """

    _app = Flask('antbs')

    # Bugsnag Mixin
    handle_exceptions(_app)

    # Apply Configuration
    antbs_config = AntBSConfig(status, logger)
    _app = antbs_config.apply_all(_app)

    # Debug Toolbar
    # _app.debug = True
    debug_toolbar.init_app(_app)

    # RQ Dashboard
    _app.register_blueprint(rq_dashboard.blueprint, url_prefix='/rq')

    # Register Views
    for view_class in all_views:
        view = view_class()
        view.register(_app)

    # Hookup Middlewares
    with _app.app_context():
        @current_app.errorhandler(400)
        @current_app.errorhandler(403)
        @current_app.errorhandler(404)
        @current_app.errorhandler(500)
        def error_handler(err):
            """ Setup default error templates. """
            code = getattr(err, 'code', 500)  # If 500, err == the exception.
            error_tpl = 'error/error.html'

            if code in [403, 404, 500]:
                error_tpl = 'error/{}.html'.format(str(code))

            return render_template(error_tpl, code=code), code

        @current_app.context_processor
        def inject_global_template_variables():
            return dict(
                idle=status.idle,
                current_status=status.current_status,
                now_building=status.now_building,
                rev_pending=status.pending_review,
                user=get_current_user(),
                current_user=get_current_user(),
                _all_packages=status.all_packages,
                pkg_groups=status.package_groups,
            )

        @current_app.before_request
        def rq_dashboard_requires_auth():
            if '/rq' in request.path and not get_current_user().is_authenticated:
                abort(403)

        @current_app.template_filter()
        def tpl_name(s):
            """ Extracts and returns the template name from a url path string. """
            res = re.findall('\'([^\']*)\'', str(s))

            return None if not res else res[0]

        @current_app.template_filter()
        def build_failed(bnum):
            build = get_build_object(bnum=int(bnum))
            return build.failed

    return _app


app = create_app()


if __name__ == "__main__":
    app = create_app()
    app.run(host='127.0.0.1', port=8020, debug=True, use_reloader=True)
