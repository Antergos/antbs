#!/usr/bin/env python
#  -*- coding: utf-8 -*-
#
#  redis_hash.py
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

import errno
import os
import time

from . import (
    db,
    RedisObject,
    RedisDataHashField,
    RedisDataRedisObject,
    RedisList,
    RedisZSet,
    Singleton
)


class RedisHashMCS(type):
    def __new__(mcs, cls, bases, cls_dict):
        instance = super().__new__(mcs, cls, bases, cls_dict)

        _strings = instance.attrib_lists['string'] + instance.attrib_lists['path']
        instance.all_attribs = [
            item for sublist in instance.attrib_lists.values()
            for item in sublist
        ]

        for attrib_name in instance.all_attribs:
            can_expire = attrib_name in instance.can_expire

            if attrib_name in _strings:
                value = RedisDataHashField(attrib_name, '', str, can_expire)

            elif attrib_name in instance.attrib_lists['bool']:
                value = RedisDataHashField(attrib_name, False, bool, can_expire)

            elif attrib_name in instance.attrib_lists['int']:
                value = RedisDataHashField(attrib_name, 0, int, can_expire)

            elif attrib_name in instance.attrib_lists['list']:
                value = RedisDataRedisObject(attrib_name, RedisList)

            elif attrib_name in instance.attrib_lists['set']:
                value = RedisDataRedisObject(attrib_name, RedisZSet)

            else:
                raise ValueError()

            setattr(instance, attrib_name, value)

        return instance


class RedisSingleton(Singleton, RedisHashMCS):
    pass


class RedisHash(RedisObject, metaclass=RedisHashMCS):
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

    all_attribs = []
    attrib_lists = dict(string=[], bool=[], int=[], list=[], set=[], path=[])
    can_expire = []

    def __init__(self, namespace='antbs', prefix='', key='', *args, **kwargs):
        if 'status' != prefix and not key and not prefix:
            raise ValueError('Both "prefix" and "key" are required')

        id_key = '{0}:{1}:{2}'.format(namespace, prefix, key)

        super().__init__(full_key=id_key)

        self.namespace = namespace
        self.prefix = prefix
        self.key = key
        self.full_key = id_key

        self.all_attribs = getattr(type(self), 'all_attribs')
        self.attrib_lists = getattr(type(self), 'attrib_lists')

    def __getitem__(self, item):
        """ Get and return the value of a field (item) from this objects redis hash."""
        return getattr(self, item)

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
