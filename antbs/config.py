#!/usr/bin/env python
#  -*- coding: utf-8 -*-
#
#  config.py
#
#  Copyright © 2016-2017 Antergos
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

from datetime import timedelta
import os

from flask import Flask

from extensions import (
    rq_dashboard,
    url_for_other_page,
    ProxyFix
)


class AntBSConfig:
    status = None

    def __init__(self, status, logger=None):
        self.app = None
        self.logger = logger
        self.configs = [c for c in self.__class__.__dict__ if not self.__excluded(c)]

        if self.status is None:
            self.status = status

    def __apply_config(self, config_for):
        apply_config = getattr(self, config_for)
        apply_config()

    @staticmethod
    def __excluded(item):
        return '__' in item or not item.startswith('_')

    def _flask(self):
        if not self.status.sp_session_key:
            self.status.sp_session_key = os.environ.get('SP_SESSION_KEY')

        config = {'DEBUG_TB_PROFILER_ENABLED': False,
                  'SECRET_KEY': self.status.sp_session_key,
                  'TEMPLATES_AUTO_RELOAD': True}

        self.app.config.update(config)

    def _jinja(self):
        self.app.jinja_options = Flask.jinja_options.copy()
        self.app.jinja_options['lstrip_blocks'] = True
        self.app.jinja_options['trim_blocks'] = True
        self.app.jinja_env.globals['url_for_other_page'] = url_for_other_page
        self.app.jinja_env.add_extension('jinja2.ext.do')

    def _proxy_fix(self):
        self.app.wsgi_app = ProxyFix(self.app.wsgi_app)

    def _rq_dashboard(self):
        self.app.config.from_object(rq_dashboard.default_settings)

    def _stormpath(self):
        if not self.status.sp_api_id:
            for item in ('SP_API_ID', 'SP_API_KEY', 'SP_APP'):
                setattr(self.status, item.lower(), os.environ.get(item))

        config = {'STORMPATH_API_KEY_ID': self.status.sp_api_id,
                  'STORMPATH_API_KEY_SECRET': self.status.sp_api_key,
                  'STORMPATH_APPLICATION': self.status.sp_app,
                  'STORMPATH_COOKIE_DURATION': timedelta(days=14),
                  'STORMPATH_ENABLE_FORGOT_PASSWORD': True,
                  'STORMPATH_ENABLE_REGISTRATION': False,
                  'STORMPATH_ENABLE_USERNAME': True,
                  'STORMPATH_LOGIN_TEMPLATE': 'admin/login.html',
                  'STORMPATH_REDIRECT_URL': '/builds/completed',
                  'STORMPATH_REQUIRE_USERNAME': True}

        self.app.config.update(config)

    def apply_all(self, app):
        self.app = app

        for config in self.configs:
            self.__apply_config(config)

        return self.app
