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
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#  GNU General Public License for more details.
#
#  You should have received a copy of the GNU General Public License
#  along with this program; if not, write to the Free Software
#  Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston,
#  MA 02110-1301, USA.

""" Database module """

import redis

db = redis.StrictRedis(unix_socket_path='/var/run/redis/redis.sock')


class RedisField(object):
    """ A base object backed by redis. This is not meant to be used directly. """

    def __init__(self, id_key=None):
        """ Create or load a RedisObject. """

        if id_key:
            self.id_key = id_key
        else:
            raise AttributeError('A key is required to initialize a redis object.')

    def __bool__(self):
        """ Test if an object currently exists. """

        return db.exists(self.id_key)

    def __nonzero__(self):
        """ Test if an object currently exists in database. """

        return db.exists(self.id_key)

    def __eq__(self, other):
        """ Tests if two redis objects are equal (they have the same id/key). """

        return self.id_key == other.id_key

    def __str__(self):
        """ Return this object's id/key as a string for testing purposes. """

        return self.id_key

    def delete(self):
        """ Delete this object from redis. """

        db.delete(self.id_key)

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


class RedisList(RedisField):
    """ An equivalent to list where all items are stored in Redis. """

    def __init__(self, id_key=None, item_type=str, items=None):
        """ Create a new RedisList
        id_key: use this as the redis key.
        item_type: The constructor to use when reading items from redis.
        values: Default values to store during construction. """

        RedisField.__init__(self, id_key)

        self.item_type = item_type

        if items:
            for item in items:
                self.append(item)

    @classmethod
    def as_child(cls, parent, tag, item_type):
        """ Alternative callable constructor that instead defines this as a child object
        :param parent:
        :param tag:
        :param item_type:
        """

        def helper(_=None):
            """

            :param _:
            :return:
            """
            return cls(parent.namespace + tag, item_type)

        return helper

    def __str__(self):
        """ Return this object as a string """

        return str([x for x in self.__iter__()])

    def __repr__(self):
        """ Return this object as a string """

        return str([x for x in self.__iter__()])

    def __getitem__(self, index):
        """ Load an item by index where index is either an int or a slice. """

        if isinstance(index, slice):
            if slice.step != 1:
                raise NotImplementedError('Cannot specify a step to a RedisObject slice')

            return [
                RedisField.decode_value(self.item_type, el)
                for el in db.lrange(self.id_key, slice.start, slice.end)
                ]
        else:
            return RedisField.decode_value(self.item_type, db.lindex(self.id_key, index))

    def __setitem__(self, index, val):
        """ Update an item by index. """

        db.lset(self.id_key, index, RedisField.encode_value(val))

    def __len__(self):
        """ Return the size of the list. """

        return db.llen(self.id_key)

    def __delitem__(self, index):
        """ Delete an item from a RedisList by index. """

        db.lset(self.id_key, index, '__DELETED__')
        db.lrem(self.id_key, 1, '__DELETED__')

    def __iter__(self):
        """ Iterate over all items in this list. """

        for el in db.lrange(self.id_key, 0, -1):
            yield RedisField.decode_value(self.item_type, el)

    def lpop(self):
        """ Remove and return a value from the left (low) end of the list. """

        return RedisField.decode_value(self.item_type, db.lpop(self.id_key))

    def rpop(self):
        """ Remove a value from the right (high) end of the list. """

        return RedisField.decode_value(self.item_type, db.rpop(self.id_key))

    def lpush(self, val):
        """ Add an item to the left (low) end of the list.
        :param val:
        """

        db.lpush(self.id_key, RedisField.encode_value(val))

    def rpush(self, val):
        """ Add an item to the right (high) end of the list.
        :param val:
        """

        db.rpush(self.id_key, RedisField.encode_value(val))

    def append(self, val):
        """

        :param val:
        """
        self.rpush(val)

    def delete(self):
        """


        """
        db.delete(self.id_key)

    def reverse(self):
        """


        :return:
        """
        cp = list(db.lrange(self.id_key, 0, -1))
        return cp.reverse()

    def remove(self, val):
        """

        :param val:
        """
        db.lrem(self.id_key, 0, val)


class RedisZSet(RedisField):
    """ A sorted set where all items are stored in Redis. """

    def __init__(self, id_key=None, item_type=str, items=None):
        """ Create a new RedisList
        id_key: use this as the redis key.
        item_type: The constructor to use when reading items from redis.
        values: Default values to store during construction. """

        RedisField.__init__(self, id_key)

        self.item_type = item_type

        if items:
            for item in items:
                self.add(item)

    @classmethod
    def as_child(cls, parent, tag, item_type):
        """ Alternative callable constructor that instead defines this as a child object
        :param parent:
        :param tag:
        :param item_type:
        """

        def helper(_=None):
            """

            :param _:
            :return:
            """
            return cls(parent.namespace + tag, item_type)

        return helper

    def __len__(self):
        """ Return the size of the set. """

        return db.zcard(self.id_key) if db.exists(self.id_key) else 0

    def __iter__(self):
        """ Iterate over all items in this set. """

        for el in db.zrange(self.id_key, 0, -1):
            yield RedisField.decode_value(self.item_type, el)

    def add(self, val):
        """ Add member to set if it doesn't exist.
        :param val:
        """

        db.zadd(self.id_key, 1, RedisField.encode_value(val))

    def remove(self, val):
        """ Remove a member from the set.
        :param val:
        """

        db.zrem(self.id_key, RedisField.encode_value(val))

    def ismember(self, val):
        """ Check if value is a member of set
        :param val:
        """

        return db.zrank(self.id_key, RedisField.encode_value(val))


class RedisObject(object):
    """

    """
    database = db

    def __init__(self):
        self.namespace = 'antbs:'
        self.key_lists = dict(
            redis_string=[],
            redis_string_bool=[],
            redis_string_int=[],
            redis_list=[],
            redis_zset=[])
        self.all_keys = []

    def __nonzero__(self):
        """ Test if an object currently exists in database. """

        return db.exists(self.namespace[:-1])

    __bool__ = __nonzero__

    def __getattribute__(self, attrib):
        if attrib in ['key_lists', 'all_keys', 'namespace', 'database'] or attrib not in self.all_keys:
            return super(RedisObject, self).__getattribute__(attrib)

        key = self.namespace[:-1]

        if attrib in self.key_lists['redis_string']:
            return db.hget(key, attrib) if db.hexists(key, attrib) else ''

        elif attrib in self.key_lists['redis_string_bool']:
            val = db.hget(key, attrib) if db.hexists(key, attrib) else False
            return self.bool_string_helper(val)

        elif attrib in self.key_lists['redis_string_int']:
            return int(db.hget(key, attrib)) if db.hexists(key, attrib) else 0

        elif attrib in self.key_lists['redis_list']:
            return RedisList.as_child(self, attrib, str)

        elif attrib in self.key_lists['redis_zset']:
            return RedisZSet.as_child(self, attrib, str)

    def __setattr__(self, attrib, value, score=None):
        if attrib in ['key_lists', 'all_keys', 'namespace', 'database'] or attrib not in self.all_keys:
            super(RedisObject, self).__setattr__(attrib, value)
            return

        key = self.namespace[:-1]

        if attrib in self.key_lists['redis_string']:
            db.hset(key, attrib, value)

        elif attrib in self.key_lists['redis_string_bool']:
            if isinstance(value, bool):
                value = self.bool_string_helper(value)
            if isinstance(value, str) and value in ['True', 'False']:
                db.hset(key, attrib, value)
            else:
                raise ValueError

        elif attrib in self.key_lists['redis_string_int']:
            db.hset(key, attrib, str(value))

        elif attrib in self.key_lists['redis_list'] or attrib in self.key_lists['redis_zset']:
            if not callable(value):
                raise ValueError(type(value))
            super(RedisObject, self).__setattr__(attrib, value)

    @staticmethod
    def bool_string_helper(value):
        """

        :param value:
        :return:
        """
        if isinstance(value, str):
            return True if 'True' == value else False
        elif isinstance(value, bool):
            return 'True' if value else 'False'
