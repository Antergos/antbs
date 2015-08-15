#!/usr/bin/python
# -*- coding: utf-8 -*-
#
# pagination.py
#
# Copyright 2014 Antergos
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


class Pagination(object):
    """

    :param page:
    :param per_page:
    :param total_count:
    """

    def __init__(self, page, per_page, total_count):
        self.page = page
        self.per_page = per_page
        self.total_count = total_count

    @property
    def pages(self):
        """


        :return:
        """
        return int(self.total_count)

    @property
    def has_prev(self):
        """


        :return:
        """
        return self.page > 1

    @property
    def has_next(self):
        """


        :return:
        """
        return self.page < self.pages

    def iter_pages(self, left_edge=2, left_current=2,
                   right_current=5, right_edge=2):
        """

        :param left_edge:
        :param left_current:
        :param right_current:
        :param right_edge:
        """
        last = 0
        for num in xrange(1, self.pages + 1):
            if num <= left_edge or (self.page - left_current - 1 < num < self.page + right_current) or \
                    num > self.pages - right_edge:
                if last + 1 != num:
                    yield None
                yield num
                last = num
