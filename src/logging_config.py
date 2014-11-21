#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# redis_connection.py
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
# MA 02110-1301, USA.

""" Logging module """

import logging
import logging.config
import src.redis_connection


# class RedisHandler(logging.Handler):
#     def __init__(self, channel, conn, *args, **kwargs):
#         logging.Handler.__init__(self, *args, **kwargs)
#         self._formatter = logging.Formatter()
#         self.channel = channel
#         self.redis_conn = conn
#
#     def setFormatter(self, formatter):
#         self._formatter = formatter
#
#     def emit(self, record):
#         msg = self._formatter.format(record)
#         try:
#             self.redis_conn.pipeline() \
#                 .publish(self.channel, msg) \
#                 .rpush(self.channel, msg) \
#                 .ltrim(self.channel, -1000, -1) \
#                 .execute()
#
#         except Exception:
#             pass
#db = src.redis_connection.db
logger = logging.getLogger()

logging.config.dictConfig({
    'version': 1,
    'disable_existing_loggers': False,

    'formatters': {
        'file': {
            'format': ' %(asctime)s [%(levelname)s]: %(message)s -[in %(pathname)s: %(lineno)d]'
        },
        'email': {
            'format': '%(levelname)s\n%(pathname)s: %(lineno)d\n%(module)s\n%(funcName)s\n%(asctime)s\nMsg: %(message)s'
        }
    },
    'handlers': {
        'default': {
            'level': 'INFO',
            'class': 'logging.StreamHandler',
            'formatter': 'file'
        },
        'file': {
            'level': 'INFO',
            'class': 'logging.handlers.RotatingFileHandler',
            'filename': 'antbs.log',
            'maxBytes': 200000,
            'backupCount': 5
        },
        'redis': {
            'level': 'INFO',
            'class': 'rlog.RedisHandler',
            'channel': 'log_stream',
            'redis_client': src.redis_connection.db,
            'formatter': 'file'
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
        }
    },
    'loggers': {
        '': {
            'handlers': ['default', 'file', 'redis', 'email'],
            'level': 'INFO',
            'propagate': True
        }
    }
})



