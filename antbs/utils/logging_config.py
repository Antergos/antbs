#!/usr/bin/python
# -*- coding: utf-8 -*-
#
# redis_connection.py
#
# Copyright 2013-2015 Antergos
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

