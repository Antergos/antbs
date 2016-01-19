#!/usr/bin/python
# -*- coding: utf-8 -*-
#
# redis_connection.py
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
# You should have received a copy of the GNU General Public License
# along with AntBS; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston,
# MA 02110-1301, USA.

""" Database module """

import redis
import json

db = redis.StrictRedis(unix_socket_path='/var/run/redis/redis.sock')


class RedisField(object):
    """ A base object backed by redis. This is not meant to be used directly. """

    db = db

    def __init__(self, id_key=None):
        """ Create or load a RedisField. """

        if id_key:
            self.id_key = id_key
        else:
            raise AttributeError('A key is required to initialize a redis object.')

    def __bool__(self):
        """ Tests if this object currently exists in redis. """

        return self.db.exists(self.id_key)

    def __nonzero__(self):
        return self.__bool__()

    def __eq__(self, other):
        """ Tests if two redis objects are equal (they have the same id_key). """

        return self.id_key == other.id_key

    def __str__(self):
        """ Return this object's id_key as a string. This can easily be extended by sybclasses. """

        return self.id_key

    def __iter__(self):
        raise NotImplementedError

    def delete(self):
        """ Delete this object from redis. """

        self.db.delete(self.id_key)

    def __jsonable__(self):
        """
        Returns this object as a python data type so it can be serialized by json module.

        """
        res = None
        if isinstance(self, (RedisList, RedisZSet)):
            res = list(self.__iter__())
        elif isinstance(self, RedisObject):
            as_dict = dict()

            for key in self.all_keys:
                val = getattr(self, key)

                if key in ['log_str', 'log', 'pkgbuild']:
                    continue
                elif not isinstance(val, (str, dict, bool, int)) and hasattr(val, '__jsonable__'):
                    as_dict[key] = val.__jsonable__()
                else:
                    as_dict[key] = val

            res = as_dict

        return res

    def json(self):
        """ Return this object as a json serialized string. """

        return json.dumps(self.__jsonable__())

    @classmethod
    def as_child(cls, parent, tag, item_type):
        """
        Alternative callable constructor that instead defines this as a child object.
        This allows you to store classes derived from `RedisField` inside other classes
        that are also derived from `RedisField`.

        Args:
            parent (RedisObject): The parent object.
            tag (str):            Short name for this object. It will be combined with parent
                                  object's `id_key` to create this object's `id_key`.
            item_type (str()):    The built-in type object for the type of data stored in this
                                  object.
        """

        def helper(_=None):
            return cls(parent.full_key + ':' + tag, item_type)

        return helper()

    @staticmethod
    def decode_value(obj_type, value):
        """ Decode a value if it is non-None, otherwise, decode with no arguments.
        :param obj_type:
        :param value:
        """

        if value is None:
            return obj_type()
        else:
            return obj_type(value)

    @staticmethod
    def encode_value(value):
        """ Encode a value using json.dumps, with default = str.
        :param value:
        """

        return str(value)


class RedisList(RedisField, list):
    """ An equivalent to `list` where all items are stored in Redis. """

    def __init__(self, id_key=None, item_type=str, items=None):
        """
        Create a new RedisList

        id_key: use this as the redis key.
        item_type: The constructor to use when reading items from redis.
        values: Default values to store during construction.

        """

        super().__init__(id_key=id_key)

        self.item_type = item_type

        if items:
            for item in items:
                self.append(item)

    def __str__(self):
        """ Return this object as a string """

        return str([x for x in self.__iter__()])

    def __getitem__(self, index):
        """ Load an item by index where index is either an int or a slice. """

        if isinstance(index, slice):
            if slice.step != 1:
                raise NotImplementedError('Cannot specify a step to a RedisObject slice')

            return [
                super().decode_value(self.item_type, el)
                for el in self.db.lrange(self.id_key, slice.start, slice.end)
                ]
        else:
            return super().decode_value(self.item_type, self.db.lindex(self.id_key, index))

    def __setitem__(self, index, val):
        """ Update an item by index. """

        self.db.lset(self.id_key, index, super().encode_value(val))

    def __len__(self):
        """ Return the size of the list. """

        return self.db.llen(self.id_key)

    def __delitem__(self, index):
        """ Delete an item from a RedisList by index. """

        self.db.lset(self.id_key, index, '__DELETED__')
        self.db.lrem(self.id_key, 1, '__DELETED__')

    def __iter__(self):
        """ Iterate over all items in this list. """

        for el in self.db.lrange(self.id_key, 0, -1):
            yield super().decode_value(self.item_type, el)

    def __contains__(self, item):
        """
        Check if item is in this list.

        :param (str) item: Item to check.
        :return: (bool) True if item is in list else False

        """
        items = self.db.lrange(self.id_key, 0, -1)
        return item in items

    def __add__(self, other_list):
        """
        Combine elements from this list (self) and other_list into a new list.

        :param (list) other_list:
        :return (list): new_list

        """
        return [x for x in self.__iter__()] + [x for x in other_list.__iter__()]

    def lpop(self):
        """ Remove and return a value from the left (low) end of the list. """

        return super().decode_value(self.item_type, self.db.lpop(self.id_key))

    def rpop(self):
        """ Remove a value from the right (high) end of the list. """

        return super().decode_value(self.item_type, self.db.rpop(self.id_key))

    def lpush(self, val):
        """ Add an item to the left (low) end of the list. """

        self.db.lpush(self.id_key, super().encode_value(val))

    def rpush(self, val):
        """ Add an item to the right (high) end of the list. """

        self.db.rpush(self.id_key, super().encode_value(val))

    def append(self, val):
        self.rpush(val)

    def reverse(self):
        cp = list(self.db.lrange(self.id_key, 0, -1))
        return cp.reverse()

    def remove(self, val):
        self.db.lrem(self.id_key, 0, val)


class RedisZSet(RedisField, set):
    """
    A sorted set where all items are stored in Redis.


        Args:
            id_key (str): use this as the redis key.
            item_type (object): The constructor to use when reading items from redis.
            values (list): Default values to store during construction.

    """

    def __init__(self, id_key=None, item_type=str, items=None):

        super().__init__(id_key=id_key)

        self.item_type = item_type

        if items:
            for item in items:
                self.add(item)

    def __len__(self):
        """ Return the size of the set. """
        return self.db.zcard(self.id_key) if self.db.exists(self.id_key) else 0

    def __iter__(self):
        """ Iterate over all items in this set. """
        for el in self.db.zrange(self.id_key, 0, -1):
            yield super().decode_value(self.item_type, el)

    def __str__(self):
        """ Return this object as a string """
        return str([x for x in self.__iter__()])

    def __contains__(self, item):
        """ Check if item is in the set. """
        return item in self.db.zrange(self.id_key, 0, -1)

    def add(self, val):
        """ Add member to set if it doesn't exist. """
        self.db.zadd(self.id_key, 1, super().encode_value(val))

    def remove(self, val):
        """ Remove a member from the set. """
        self.db.zrem(self.id_key, super().encode_value(val))

    def ismember(self, val):
        """ Check if value is a member of set. """
        rank = self.db.zrank(self.id_key, super().encode_value(val))
        return True if rank else False


class RedisObject(RedisField):
    """
    This is the base class for all of the redis-backed classes in this application.

    Args:
        namespace (str): This is used as the first part of the redis key. It should
                         usually be the application name (top-most-level identifier)
        prefix (str):    This is used as the second part of the redis key. It should
                         describe all objects of the subclass type.
        key (str):       This is used as the last part of the redis key. It should
                         describe a single object of the subclass type (like an instance).

    Attributes:
        namespace (str):  See Args.
        prefix (str):     See Args.
        key (str):        See Args.
        full_key (str):   This objects redis key, eg. `namespace:prefix:key`.
        key_lists (dict): Contains lists of object attributes that are stored in redis
                          organized by their value type.
        all_keys (list):  List of all object attributes that are stored in redis.

    """

    def __init__(self, namespace='antbs', prefix='', key=''):
        if not all([prefix, key]):
            not_empty = [x for x in [prefix, key] if x]
            raise ValueError('(4) args required, but only (%s) given', len(not_empty))

        id_key = '{0}:{1}:{2}'.format(namespace, prefix, key)

        super().__init__(id_key=id_key)

        self.namespace = namespace
        self.prefix = prefix
        self.key = key
        self.full_key = id_key
        self.key_lists = dict(string=[], bool=[], int=[], list=[], set=[])
        self.all_keys = []

    def _namespaceinit_(self):
        if self.full_key[-1] == ':':
            self.full_key = self.full_key[:-1]

    def _keysinit_(self):
        for key in self.all_keys:
            val = getattr(self, key, '')
            initialized = '' != val and '_' != val

            if initialized:
                continue
            elif key in self.key_lists['string']:
                setattr(self, key, '')
            elif key in self.key_lists['bool']:
                setattr(self, key, False)
            elif key in self.key_lists['int']:
                setattr(self, key, 0)
            elif key in self.key_lists['list']:
                setattr(self, key, RedisList.as_child(self, key, str))
            elif key in self.key_lists['set']:
                setattr(self, key, RedisZSet.as_child(self, key, str))

    def __str__(self):
        """ Return this object as a friendly (human readable) string. """

        as_string = dict()
        for key in self.all_keys:
            value = getattr(self, key) if hasattr(self, key) else ''
            as_string[key] = value if isinstance(value, str) else value.__str__()

        return str(as_string)

    def __len__(self):
        """ Return the len of this object (total number of fields in its redis hash). """
        return int(self.db.hlen(self.full_key))

    def __getitem__(self, item):
        """ Get and return the value of a field (item) from this objects redis hash."""
        return self.__getattribute__(item)

    def __setitem__(self, key, value):
        """ Set the value of a field (item) from this objects redis hash."""
        return self.__setattribute__(key, value)

    def __iter__(self):
        """ Return an iterator with all the keys in redis hash. """
        return [key for key in self.all_keys]

    def iterkeys(self):
        return self.__iter__()

    def delete(self):
        """ Delete this object from redis. """
        self.db.delete(self.full_key)

    def __getattribute__(self, attrib):
        """ Get attribute value if stored in redis otherwise pass call to parent class """

        pass_list = ['key_lists', 'all_keys', 'namespace', 'database', '_build',
                     'key', 'full_key', 'prefix', 'db']

        if attrib in pass_list or attrib not in self.all_keys:
            return super().__getattribute__(attrib)

        key = self.full_key

        if attrib in self.key_lists['string']:
            return self.db.hget(key, attrib) if self.db.hexists(key, attrib) else '_'

        elif attrib in self.key_lists['bool']:
            val = self.db.hget(key, attrib) if self.db.hexists(key, attrib) else ''
            return self.bool_string_helper(val) if '' != val else ''

        elif attrib in self.key_lists['int']:
            return int(self.db.hget(key, attrib)) if self.db.hexists(key, attrib) else ''

        elif attrib in self.key_lists['list']:
            return RedisList.as_child(self, attrib, str)

        elif attrib in self.key_lists['set']:
            return RedisZSet.as_child(self, attrib, str)

    def __setattr__(self, attrib, value, score=None):
        """ Set attribute value if stored in redis otherwise pass call to parent class """

        pass_list = ['key_lists', 'all_keys', 'namespace', 'database', '_build',
                     'key', 'full_key', 'prefix', 'db']

        is_child = attrib in self.key_lists['list'] or attrib in self.key_lists['set']
        pass_it = is_child or attrib in pass_list or attrib not in self.all_keys

        if pass_it:
            return super().__setattr__(attrib, value)

        key = self.full_key

        if attrib in self.key_lists['string'] or attrib in self.key_lists['int']:
            self.db.hset(key, attrib, value)

        elif attrib in self.key_lists['bool']:
            if value in [True, False]:
                self.db.hset(key, attrib, self.bool_string_helper(value))
            else:
                raise AttributeError('%s.%s must be of type(bool), %s given.',
                                     self.__class__.__name__, attrib, value)
        else:
            raise AttributeError('Unable to set attribute %s of class %s. Unknown Error.',
                                 attrib, self.__class__.__name__)

    @staticmethod
    def bool_string_helper(value):
        """
        If value is a `str` return value as `bool`.
        If value is a `bool` return value as `str`.

        """
        if isinstance(value, str):
            return True if 'True' == value else False
        elif isinstance(value, bool):
            return 'True' if value else 'False'
