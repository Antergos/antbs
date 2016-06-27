#!/usr/bin/python
# -*- coding: utf-8 -*-
#
# logging_config.py
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


""" Logging module """

import logging
import logging.config
import bugsnag
from bugsnag.handlers import BugsnagHandler
from bugsnag.flask import handle_exceptions

from database.base_objects import db
from database.server_status import status

from utils import Singleton


class LoggingConfig(metaclass=Singleton):
    logger = None

    def __init__(self):
        self.noisy_loggers = ["github3",
                              "requests",
                              "stormpath.http",
                              "docker"]

        if self.logger is None:
            self._initialize()

    def _initialize(self):
        bugsnag.configure(api_key=status.bugsnag_key, project_root=status.APP_DIR)
        logging.config.dictConfig(self.get_logging_config())

        self.logger = logging.getLogger()

        for logger_name in self.noisy_loggers:
            _logger = logging.getLogger(logger_name)
            _logger.setLevel(logging.ERROR)

        bs_handler_found = [h for h in self.logger.handlers if isinstance(h, BugsnagHandler)]

        if not bs_handler_found:
            bugsnag_handler = BugsnagHandler()
            bugsnag_handler.setLevel(logging.WARNING)
            self.logger.addHandler(bugsnag_handler)

    def get_logging_config(self):
        return {
            'version': 1,
            'disable_existing_loggers': True,

            'formatters': {
                'file': {
                    'format': '%(asctime)s [ %(levelname)s ] %(module)s - %(filename)s:%('
                              'lineno)d : %(funcName)s | %(message)s'
                },
                'email': {
                    'format': 'LEVEL: %(levelname)s\n PATH: %(pathname)s: %(lineno)d\nMODULE: %('
                              'module)s\nFUNCTION: %(funcName)s\nDATE: %(asctime)s\nMSG: %('
                              'message)s'
                },
                'redis': {
                    'format': '%(asctime)s [ %(levelname)s ] - %(filename)s : %(lineno)d : %('
                              'funcName)s | %(message)s'
                }
            },
            'handlers': {
                'default': {
                    'level': 'DEBUG',
                    'class': 'logging.StreamHandler',
                    'formatter': 'file'
                },
                'file': {
                    'level': 'DEBUG',
                    'class': 'logging.handlers.RotatingFileHandler',
                    'filename': 'antbs.log',
                    'maxBytes': 3000000,
                    'backupCount': 3
                },
                'redis': {
                    'level': 'DEBUG',
                    'class': 'rlog.RedisHandler',
                    'channel': 'log_stream',
                    'redis_client': db,
                    'formatter': 'redis'
                },
                'email': {
                    'level': 'ERROR',
                    'class': 'logging.handlers.SMTPHandler',
                    'mailhost': 'localhost',
                    'fromaddr': 'error@build.antergos.org',
                    'toaddrs': 'admin@antergos.org',
                    'subject': 'AntBS Error Report',
                    'credentials': '["error@build.antergos.org", "U7tGQGoi4spS"]',
                    'formatter': 'email'
                },
                'bugsnag': {
                    'level': 'WARNING',
                    'class': 'bugsnag.handlers.BugsnagHandler',
                    'api_key': status.bugsnag_key
                }
            },
            'loggers': {
                'antbs': {
                    'handlers': ['default', 'file', 'redis', 'email', 'bugsnag'],
                    'level': 'DEBUG',
                    'propagate': True
                }
            }
        }


logging_config = LoggingConfig()
logger = logging_config.logger
