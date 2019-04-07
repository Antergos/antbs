#!/usr/bin/python
# -*- coding: utf-8 -*-
#
# _redis_object.py
#
# Copyright © 2013-2017 Antergos
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

import json

from . import db


class RedisObject:
    """ A base object backed by redis. This class should not be used directly. """

    db = db
    _subclass_names = ['RedisList', 'RedisZset']
    attrib_lists = dict(string=[], bool=[], int=[], list=[], set=[], path=[])
    all_attribs = []

    def __init__(self, full_key=None, *args, **kwargs):
        """ Create or load a RedisObject. """
        self.item_type = None

        if not full_key:
            raise ValueError('A key is required to initialize a redis object.')

        self.full_key = full_key

    def __bool__(self):
        """ Tests if this object currently exists in redis. """
        return self.db.exists(self.full_key)

    def __eq__(self, other):
        """ Tests if two redis objects are equal (they have the same full_key). """
        res = False

        if isinstance(other, RedisObject):
            res = self.full_key == other.full_key

        return res

    def __getitem__(self, index):
        """ Load an item by index where index is either an int or a slice. """

        if self.__class__.__name__ not in self._subclass_names:
            raise NotImplementedError('Cannot __getitem__ of RedisHash object')

        if isinstance(index, slice):
            if index.step and index.step > 1:
                raise NotImplementedError(
                    'Cannot specify a step to a {0} object slice'.format(self.__class__.__name__)
                )

            if self.__class__.__name__ == 'RedisList':
                return [
                    RedisObject.decode_value(self.item_type, el)
                    for el in self.db.lrange(self.full_key, index.start, index.stop)
                    ]
            elif self.__class__.__name__ == 'RedisZSet':
                return [
                    RedisObject.decode_value(self.item_type, el)
                    for el in self.db.zrange(self.full_key, index.start, index.stop)
                    ]

        else:
            return RedisObject.decode_value(self.item_type, self.db.lindex(self.full_key, index))

    def __iter__(self):
        raise NotImplementedError

    def __json__(self):
        """
        Returns this object as a python built-in type so it can be serialized by the json module.

        """

        res = None

        if self.__class__.__name__ in self._subclass_names:
            res = list(self.__iter__())

        elif 'ServerStatus' == self.__class__.__name__:
            raise RuntimeError(
                'ServerStatus object cant be converted to json (it contains private data!!)'
            )

        elif 'RedisHash' == self.__class__.__name__:
            as_dict = dict()

            for key in self.all_attribs:
                if key in ['log_str', 'log', 'pkgbuild']:
                    continue

                val = getattr(self, key)

                if not isinstance(val, (str, dict, bool, int)) and hasattr(val, '__json__'):
                    as_dict[key] = val.__json__()
                else:
                    as_dict[key] = val

            res = as_dict

        return res

    def __len__(self):
        raise NotImplementedError('Subclasses must implement this method!')

    def __nonzero__(self):
        return self.__bool__()

    def __str__(self):
        """ Return this object's hash_key as a string. This can be extended by subclasses. """
        return self.full_key

    @classmethod
    def as_child(cls, key, item_type):
        """
        Alternative callable constructor that instead defines this as a child object.
        This allows you to store classes derived from `RedisObject` inside other classes
        that are also derived from `RedisObject`.

        Args:
            key (str):             The redis key for this object.
            item_type (type(str)): The built-in type object for the type of data stored in
                                   this object.
        """

        def helper(_=None):
            return cls(key, item_type)

        return helper()

    @staticmethod
    def decode_value(obj_type, value):
        """ Decode a value if it is non-None, otherwise, decode with no arguments. """
        if value is None:
            return obj_type()
        else:
            return obj_type(value)

    def delete(self):
        """ Delete this object from redis. """
        self.db.delete(self.full_key)

    @staticmethod
    def encode_value(value):
        """ Encode a value using json.dumps, with default = str. """
        return str(value)

    def json(self):
        """ Return this object as a json serialized string. """
        return json.dumps(self.__json__(), sort_keys=True, indent=4)
