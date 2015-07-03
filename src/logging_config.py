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
import redis_connection
import datetime

db = redis_connection.db

logger = logging.getLogger()

logging.config.dictConfig({
    'version': 1,
    'disable_existing_loggers': False,

    'formatters': {
        'file': {
            'format': '%(asctime)s [%(levelname)s]: %(message)s -[in %(pathname)s: %(lineno)d]'
        },
        'email': {
            'format': 'LEVEL: %(levelname)s\n PATH: %(pathname)s: %(lineno)d\nMODULE: %(module)s\nFUNCTION: '
                      '%(funcName)s\nDATE: %(asctime)s\nMSG: %(message)s'
        },
        'redis': {
            'format': 'LEVEL: %(levelname)s\n PATH: %(pathname)s: %(lineno)d\nMODULE: %(module)s\nFUNCTION: '
                      '%(funcName)s\nDATE: %(asctime)s\nMSG: %(message)s'
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
            'maxBytes': 200000,
            'backupCount': 5
        },
        'redis': {
            'level': 'DEBUG',
            'class': 'rlog.RedisHandler',
            'channel': 'log_stream',
            'redis_client': redis_connection.db,
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


class Logger(object):

    _logger = logging.getLogger()

    def __init__(self):
        self.name = 'Logging Object'

    def error(self, msg, *args):
        self.maybe_output_log_msg(msg, 'error', *args)

    def info(self, msg, *args):
        self.maybe_output_log_msg(msg, 'info', *args)

    def debug(self, msg, *args):
        self.maybe_output_log_msg(msg, 'debug', *args)

    def maybe_output_log_msg(self, msg, msg_type, *args):
        if db.exists('LOGGING:ENABLED:GLOBAL') or 'error' == msg_type:
            log = getattr(self._logger, msg_type)
            log(msg, *args)

logger_tl = logger

def new_timeline_event(msg=None, tl_type=None):
    if msg is not None:
        if not db.exists('next-timeline-id'):
            db.set('next-timeline-id', '0')
        event_id = db.incr('next-timeline-id')
        dt_date = datetime.datetime.now().strftime("%b %d")
        dt_time = datetime.datetime.now().strftime("%I:%M%p")
        tl = 'timeline:%s' % event_id
        success = False
        try:
            db.set(tl, 'True')
            db.set('%s:date' % tl, dt_date)
            db.set('%s:time' % tl, dt_time)
            db.set('%s:msg' % tl, msg)
            db.set('%s:type' % tl, tl_type)
            db.lpush('timeline:all', event_id)
            popid = db.rpop('timeline:all')
            success = True
        except Exception as err:
            logger_tl.error('@@-logging_config.py-@@ | Unable to save timeline event, error msg: %s' % err)

        if success:
            try:
                pop_event = db.scan_iter('timeline:%s:**' % popid, 20)
                for pev in pop_event:
                    db.delete(pev)
            except Exception as err:
                logger_tl.error('@@-logging_config.py-@@ | Unable to delete oldest timeline event, error msg: %s' % err)

        return event_id
