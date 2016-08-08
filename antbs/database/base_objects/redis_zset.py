#!/usr/bin/env python
#  -*- coding: utf-8 -*-
#
#  redis_zset.py
#
#  Copyright © 2014-2016 Antergos
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

from . import RedisObject


class RedisZSet(RedisObject, set):
    """
    A sorted set where all items are stored in Redis.

    Args:
        full_key (str): use this as the redis key.
        item_type (object): The constructor to use when reading items from redis.
        values (list): Default values to store during construction.

    """

    def __init__(self, full_key=None, item_type=str, items=None):

        super().__init__(full_key=full_key)
        self.item_type = item_type

        if items:
            for item in items:
                self.add(item)

    def __contains__(self, item):
        """ Check if item is in the set. """
        return item in self.db.zrange(self.full_key, 0, -1)

    def __iter__(self):
        """ Iterate over all items in this set. """
        for el in self.db.zrange(self.full_key, 0, -1):
            yield super().decode_value(self.item_type, el)

    def __len__(self):
        """ Return the size of the set. """
        return self.db.zcard(self.full_key)

    def __str__(self):
        """ Return this object as a string """
        return str([x for x in self.__iter__()])

    def add(self, *values):
        """ Add member(s) to sorted set. """
        vals = []

        for val in values:
            vals.extend([1, val])

        self.db.zadd(self.full_key, *vals)

    def append(self, val):
        self.add(val)

    def extend(self, vals):
        self.add(*vals)

    def ismember(self, val):
        """ Check if value is a member of set. """
        return self.db.zrank(self.full_key, super().encode_value(val))

    def remove(self, val):
        """ Remove a member from the set. """
        self.db.zrem(self.full_key, super().encode_value(val))

    def remove_range(self, start, stop):
        """ Remove all members at indexes from start to stop """
        return self.db.zremrangebyrank(self.full_key, start, stop)

    def sort(self, alpha=True):
        """ Get list of members sorted alphabetically. """
        return self.db.sort(self.full_key, alpha=alpha)