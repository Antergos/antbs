#!/usr/bin/env python
#  -*- coding: utf-8 -*-
#
#  redis_list.py
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

from ._redis_object import RedisObject


class RedisList(RedisObject, list):
    """
    A list where all items are stored in Redis.

    Args:
        full_key (str):     Use this as the redis key.
        item_type (object): The constructor to use when reading items from redis.
        items (list):       Default values to store during construction.

    """

    def __init__(self, full_key=None, item_type=str, items=None):

        super().__init__(full_key=full_key)
        self.item_type = item_type

        if items:
            for item in items:
                self.append(item)

    def __add__(self, other_list):
        """ Combine elements from this list (self) and other_list into a new list. """
        return [x for x in self.__iter__()] + [x for x in other_list.__iter__()]

    def __contains__(self, item):
        """ Check if item is in this list. """
        return item in self.db.lrange(self.full_key, 0, -1)

    def __delitem__(self, index):
        """ Delete an item from this list by index. """
        self.db.lset(self.full_key, index, '__DELETED__')
        self.db.lrem(self.full_key, 1, '__DELETED__')

    def __iter__(self):
        """ Iterate over all items in this list. """
        for el in self.db.lrange(self.full_key, 0, -1):
            yield super().decode_value(self.item_type, el)

    def __len__(self):
        """ Return the size of the list. """
        return self.db.llen(self.full_key)

    def __setitem__(self, index, val):
        """ Update an item by index. """
        self.db.lset(self.full_key, index, super().encode_value(val))

    def __str__(self):
        """ Return this object as a string """
        return str([x for x in self.__iter__()])

    def append(self, val):
        """ Append value to the end of this list """
        self.rpush(val)

    def extend(self, iterable):
        """ Append values in iterable to the end of this list """
        for item in iterable:
            self.append(item)

    def lpop(self):
        """ Remove and return a value from the left (low) end of the list. """
        return super().decode_value(self.item_type, self.db.lpop(self.full_key))

    def lpush(self, val):
        """ Add an item to the left (low) end of the list. """
        self.db.lpush(self.full_key, super().encode_value(val))

    def remove(self, val):
        self.db.lrem(self.full_key, 0, val)

    def remove_range(self, start, stop):
        self.db.ltrim(self.full_key, start, stop)

    def reverse(self):
        cp = list(self.db.lrange(self.full_key, 0, -1))
        return cp.reverse()

    def rpop(self):
        """ Remove a value from the right (high) end of the list. """
        return super().decode_value(self.item_type, self.db.rpop(self.full_key))

    def rpush(self, val):
        """ Add an item to the right (high) end of the list. """
        self.db.rpush(self.full_key, super().encode_value(val))