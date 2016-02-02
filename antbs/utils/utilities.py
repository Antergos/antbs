#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
#  utilities.py
#
#  Copyright © 2016 Antergos
#
#  This file is part of Antergos Build Server, (AntBS).
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

""" Various utility classes and metaclasses (kind of like mixins) """


class Singleton(type):
    _instance = None

    def __call__(cls, *args, **kwargs):
        if not cls._instance:
            cls._instance = super(Singleton, cls).__call__(*args, **kwargs)
        return cls._instance


class DateTimeStrings:

    @staticmethod
    def dt_date_to_string(dt):
        return dt.strftime("%m/%d/%Y")

    @staticmethod
    def dt_time_to_string(dt):
        return dt.strftime("%I:%M%p")

    @staticmethod
    def dt_to_string(dt):
        return dt.strftime("%m/%d/%Y %I:%M%p")

