#!/usr/bin/python
# -*- coding: utf-8 -*-
#
# base_objects.py
#
# Copyright © 2013-2016 Antergos
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


import errno
import json
import os
import time

from ._redis_types import db, RedisDataHashField, RedisDataRedisObject


class RedisObject:
    """ A base object backed by redis. This class should not be used directly. """

    db = db

    def __init__(self, full_key=None, *args, **kwargs):
        """ Create or load a RedisObject. """
        self.attrib_lists = dict(string=[], bool=[], int=[], list=[], set=[], path=[])

        if full_key:
            self.full_key = full_key
        else:
            raise ValueError('A key is required to initialize a redis object.')

    def __bool__(self):
        """ Tests if this object currently exists in redis. """
        if isinstance(self, (RedisList, RedisZSet)) and len(self) < 1:
            return False

        return self.db.exists(self.full_key)

    def __eq__(self, other):
        """ Tests if two redis objects are equal (they have the same full_key). """
        res = False

        if isinstance(other, RedisObject):
            res = self.full_key == other.full_key

        return res

    def __getitem__(self, index):
        """ Load an item by index where index is either an int or a slice. """

        if not isinstance(self, (RedisList, RedisZSet)):
            raise NotImplementedError('Cannot __getitem__ of RedisHash object')

        if isinstance(index, slice):
            if index.step and index.step > 1:
                raise NotImplementedError('Cannot specify a step to a {0} object slice'.format(
                                          self.__class__.__name__))

            if isinstance(self, RedisList):
                return [
                    RedisObject.decode_value(self.item_type, el)
                    for el in self.db.lrange(self.full_key, index.start, index.stop)
                    ]
            if isinstance(self, RedisZSet):
                return [
                    RedisObject.decode_value(self.item_type, el)
                    for el in self.db.zrange(self.full_key, index.start, index.stop)
                    ]

        else:
            return RedisObject.decode_value(self.item_type, self.db.lindex(self.full_key, index))

    def __iter__(self):
        raise NotImplementedError

    def __jsonable__(self):
        """
        Returns this object as a python built-in type so it can be serialized by the json module.

        """

        res = None

        if isinstance(self, (RedisList, RedisZSet)):
            res = list(self.__iter__())

        elif 'ServerStatus' == self.__class__.__name__:
            raise RuntimeError(
                'ServerStatus object cant be converted to json (it contains private data!!)'
            )

        elif isinstance(self, RedisHash):
            as_dict = dict()

            for key in self.all_attribs:
                if key in ['log_str', 'log', 'pkgbuild']:
                    continue

                val = getattr(self, key)

                if not isinstance(val, (str, dict, bool, int)) and hasattr(val, '__jsonable__'):
                    as_dict[key] = val.__jsonable__()
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
        return json.dumps(self.__jsonable__())


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
        """ Add member to sorted set if it doesn't exist. """
        vals = []
        for val in values:
            vals.append(1)
            vals.append(val)
        self.db.zadd(self.full_key, *vals)

    def append(self, val):
        self.add(val)

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


class RedisHashMeta(type):

    def __new__(mcs, cls, bases, cls_dict):
        instance = super().__new__(mcs, cls, bases, cls_dict)
        _strings = instance.attrib_lists['string'] + instance.attrib_lists['path']
        instance.all_attribs = [
            item for sublist in instance.attrib_lists.values()
            for item in sublist
        ]

        for attrib_name in instance.all_attribs:
            if attrib_name in _strings:
                value = RedisDataHashField(attrib_name, '', str)

            elif attrib_name in instance.attrib_lists['bool']:
                value = RedisDataHashField(attrib_name, False, bool)

            elif attrib_name in instance.attrib_lists['int']:
                value = RedisDataHashField(attrib_name, 0, int)

            elif attrib_name in instance.attrib_lists['list']:
                value = RedisDataRedisObject(attrib_name, RedisList)

            elif attrib_name in instance.attrib_lists['set']:
                value = RedisDataRedisObject(attrib_name, RedisZSet)

            else:
                raise ValueError()

            setattr(instance, attrib_name, value)

        return instance


class RedisHash(RedisObject, metaclass=RedisHashMeta):
    """
        This is the base class for all of the redis-backed classes in this application.
        The class provides access to predefined keys as class attributes which are stored in redis.

        Args:
            namespace (str): This is used as the first part of the redis key. It should
                             usually be the application name (top-most-level identifier)
            prefix (str):    This is used as the second part of the redis key. It should
                             describe all objects of the subclass type.
            key (str):       This is used as the last part of the redis key. It should
                             describe a single object of the subclass type (like an instance).

        Attributes:
            namespace (str):     See Args.
            prefix (str):        See Args.
            key (str):           See Args.
            full_key (str):      This objects redis key, eg. `namespace:prefix:key`.
            attrib_lists (dict): Contains lists of class attributes that are stored in redis
                                 organized by their value type.
            all_keys (list):  List of all class attributes that are stored in redis.

    """

    attrib_lists = dict(string=[], bool=[], int=[], list=[], set=[], path=[])

    def __init__(self, namespace='antbs', prefix='', key='', *args, **kwargs):
        if 'status' != prefix and not key and not prefix:
            raise ValueError('Both "prefix" and "key" are required')

        id_key = '{0}:{1}:{2}'.format(namespace, prefix, key)

        super().__init__(full_key=id_key)

        self.namespace = namespace
        self.prefix = prefix
        self.key = key
        self.full_key = id_key
        self.all_attribs = []

    def __getitem__(self, item):
        """ Get and return the value of a field (item) from this objects redis hash."""
        return getattr(self, item)

    @staticmethod
    def __hget__(key, field, default_value):
        val = db.hget(key, field)
        return val if val is not None else default_value

    def __is_expired__(self, attrib):
        exp_key = attrib + '__exp'
        expire_time = self.__hget__(self.full_key, exp_key, 0)
        now = int(time.time())

        return now > int(expire_time)

    def __iter__(self):
        """ Return an iterator with all the keys in redis hash. """
        return [key for key in self.all_attribs]

    def __len__(self):
        """ Return the len of this object (total number of fields in its redis hash). """
        return self.db.hlen(self.full_key)

    def __namespaceinit__(self):
        """ Ensures that the object's `full_key` attribute is set properly. """
        if self.full_key[-1] == ':':
            self.full_key = self.full_key[:-1]

    def __setitem__(self, field_name, value):
        """ Set the value of a field (item) from this objects redis hash."""
        return setattr(self, field_name, value)

    def __str__(self):
        """ Return this object as a friendly (human readable) string. """
        return '<{0} {1}>'.format(self.__class__.__name__, self.key)

    @staticmethod
    def datetime_to_string(dt):
        """
        Converts a datetime to a string.

        Args:
            dt (datetime.datetime): `datetime` to be converted.

        Returns:
            str: The datetime string.

        """
        return dt.strftime("%m/%d/%Y %I:%M%p")

    def expire_in(self, attrib, seconds):
        expires = int(time.time()) + seconds
        field_name = attrib + '__exp'

        self.db.hset(self.full_key, field_name, expires)

    @staticmethod
    def is_pathname_valid(pathname):
        """
        Determines whether or not a string is a valid pathname (linux only).

        Args:
            pathname (str): String to check.

        Returns:
            `True` if the passed pathname is a valid pathname. `False` otherwise.

        Notes:
            Modified version of this SO answer: http://stackoverflow.com/a/34102855/2639936
        """

        # If pathname is either not a string or empty, this pathname is invalid.
        if not isinstance(pathname, str) or not pathname:
            return False

        try:
            # Directory guaranteed to exist (the root directory).
            root_dirname = os.path.sep

            # Test whether each path component split from pathname is valid,
            # ignoring non-existent and non-readable path components.
            for pathname_part in pathname.split(os.path.sep):
                try:
                    os.lstat(root_dirname + pathname_part)
                except OSError as err:
                    if err.errno in {errno.ENAMETOOLONG, errno.ERANGE}:
                        return False

        except TypeError:
            # pathname is invalid.
            return False
        else:
            # All path components and hence pathname itself are valid.
            return True

    def iterkeys(self):
        return self.__iter__()
