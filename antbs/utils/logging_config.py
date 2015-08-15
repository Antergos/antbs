#!/usr/bin/python
# -*- coding: utf-8 -*-
#
# logging_config.py
#
# Copyright 2013-2015 Antergos
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

from redis_connection import db

stmpath = logging.getLogger('stormpath.http')
stmpath.setLevel(logging.ERROR)

logger = logging.getLogger()

logging.config.dictConfig({
    'version': 1,
    'disable_existing_loggers': True,

    'formatters': {
        'file': {
            'format': '%(asctime)s [ %(levelname)s ] - %(filename)s : %(lineno)d : %(funcName)s | %(message)s'
        },
        'email': {
            'format': 'LEVEL: %(levelname)s\n PATH: %(pathname)s: %(lineno)d\nMODULE: %(module)s\nFUNCTION: '
                      '%(funcName)s\nDATE: %(asctime)s\nMSG: %(message)s'
        },
        'redis': {
            'format': '%(asctime)s [ %(levelname)s ] - %(filename)s : %(lineno)d : %(funcName)s | %(message)s'
        }
    },
    'handlers': {
        'default': {
            'level': 'DEBUG',
            'class': 'logging.StreamHandler',
            'formatter': 'file'
        },
        'file': {
            'level': 'INFO',
            'class': 'logging.handlers.RotatingFileHandler',
            'filename': 'antbs.log',
            'maxBytes': 5000000,
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
        }
    },
    'loggers': {
        '': {
            'handlers': ['default', 'file', 'redis', 'email'],
            'level': 'DEBUG',
            'propagate': True
        }
    }
})

