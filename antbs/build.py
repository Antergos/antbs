#!/usr/bin/python
# -*- coding: utf-8 -*-
#
# build.py
#
# Copyright 2014-2015 Antergos
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

""" Build Class - Represents a single build """

import datetime

from utils.redis_connection import db, DB


class Build(DB.Model):
    database = db
    namespace = 'antbs:build'
    index_separator = ':'
    bnum = DB.AutoIncrementField(primary_key=True)

    pkgname = DB.TextField(fts=True)
    pkgver = DB.FloatField(index=True)
    pkgrel = DB.IntegerField()
    epoch = DB.IntegerField()
    version_str = DB.TextField()

    start = DB.DateTimeField(default=datetime.datetime.now)
    end = DB.DateTimeField()

    start_str = DB.TextField(index=True)
    end_str = DB.TextField(index=True)

    container = DB.TextField(index=True)

    log = DB.ListField(fts=True)

    @staticmethod
    def datetime_to_string(dt):
        return dt.strftime("%m/%d/%Y %I:%M%p")

