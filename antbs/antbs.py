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
        import_module('middleware')

    return _app

app = create_app()


if __name__ == "__main__":
    app = create_app()
    app.run(host='127.0.0.1', port=8020, debug=True, use_reloader=True)
