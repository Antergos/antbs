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

    def __init__(self, id=None):
        """ Create or load a RedisObject. """

        if id:
            self.id = id
        else:
            raise AttributeError('A key is required to initialize a redis object.')

    def __bool__(self):
        """ Test if an object currently exists. """

        return db.exists(self.id)

    def __eq__(self, other):
        """ Tests if two redis objects are equal (they have the same id/key). """

        return self.id == other.id

    def __str__(self):
        """ Return this object's id/key as a string for testing purposes. """

        return self.id

    def delete(self):
        """ Delete this object from redis. """

        db.delete(self.id)

    @staticmethod
    def decode_value(type, value):
        """ Decode a value if it is non-None, otherwise, decode with no arguments. """

        if value is None:
            return type()
        else:
            return type(value)

    @staticmethod
    def encode_value(value):
        """ Encode a value using json.dumps, with default = str. """

        return str(value)


class RedisList(RedisField):
    """ An equivalent to list where all items are stored in Redis. """

    def __init__(self, id=None, item_type=str, items=None):
        """ Create a new RedisList
        id: use this as the redis key.
        item_type: The constructor to use when reading items from redis.
        values: Default values to store during construction. """

        RedisField.__init__(self, id)

        self.item_type = item_type

        if items:
            for item in items:
                self.append(item)

    @classmethod
    def as_child(cls, parent, tag, item_type):
        """ Alternative callable constructor that instead defines this as a child object """

        def helper(_=None):
            return cls(parent.namespace[:-1] + ':' + tag, item_type)

        return helper

    def __getitem__(self, index):
        """ Load an item by index where index is either an int or a slice. """

        if isinstance(index, slice):
            if slice.step != 1:
                raise NotImplemented('Cannot specify a step to a RedisObject slice')

            return [
                RedisField.decode_value(self.item_type, el)
                for el in db.lrange(self.id, slice.start, slice.end)
                ]
        else:
            return RedisField.decode_value(self.item_type, db.lindex(self.id, index))

    def __setitem__(self, index, val):
        """ Update an item by index. """

        db.lset(self.id, index, RedisField.encode_value(val))

    def __len__(self):
        """ Return the size of the list. """

        return db.llen(self.id)

    def __delitem__(self, index):
        """ Delete an item from a RedisList by index. """

        db.lset(self.id, index, '__DELETED__')
        db.lrem(self.id, 1, '__DELETED__')

    def __iter__(self):
        """ Iterate over all items in this list. """

        for el in db.lrange(self.id, 0, -1):
            yield RedisField.decode_value(self.item_type, el)

    def lpop(self):
        """ Remove and return a value from the left (low) end of the list. """

        return RedisField.decode_value(self.item_type, db.lpop(self.id))

    def rpop(self):
        """ Remove a value from the right (high) end of the list. """

        return RedisField.decode_value(self.item_type, db.rpop(self.id))

    def lpush(self, val):
        """ Add an item to the left (low) end of the list. """

        db.lpush(self.id, RedisField.encode_value(val))

    def rpush(self, val):
        """ Add an item to the right (high) end of the list."""

        db.rpush(self.id, RedisField.encode_value(val))

    def append(self, val):
        self.rpush(val)

    def delete(self):
        db.delete(self.id)


class RedisZSet(RedisField):
    """ A sorted set where all items are stored in Redis. """

    def __init__(self, id=None, item_type=str, items=None):
        """ Create a new RedisList
        id: use this as the redis key.
        item_type: The constructor to use when reading items from redis.
        values: Default values to store during construction. """

        RedisField.__init__(self, id)

        self.item_type = item_type

        if items:
            for item in items:
                self.add(item)

    @classmethod
    def as_child(cls, parent, tag, item_type):
        """ Alternative callable constructor that instead defines this as a child object """

        def helper(_=None):
            return cls(parent.namespace[:-1] + ':' + tag, item_type)

        return helper

    def __len__(self):
        """ Return the size of the set. """

        return db.zcard(self.id) if db.exists(self.id) else 0

    def __iter__(self):
        """ Iterate over all items in this set. """

        for el in db.zrange(self.id, 0, -1):
            yield RedisField.decode_value(self.item_type, el)

    def add(self, val):
        """ Add member to set if it doesn't exist. """

        db.zadd(self.id, 1, RedisField.encode_value(val))

    def remove(self, val):
        """ Remove a member from the set. """

        db.zrem(self.id, RedisField.encode_value(val))

    def ismember(self, val):
        """ Check if value is a member of set """

        return db.zrank(self.id, RedisField.encode_value(val))


class RedisObject(object):
    all_keys = dict(
        redis_string=[],
        redis_string_bool=[],
        redis_string_int=[],
        redis_list=[],
        redis_zset=[])

    database = db
    namespace = 'antbs:'

    def __getattribute__(self, attrib):
        if attrib in ['all_keys', 'namespace', 'database'] or '__' in attrib:
            return super(RedisObject, self).__getattribute__(attrib)

        all_keys = super(RedisObject, self).__getattribute__('all_keys')
        akeys = [item for sublist in all_keys.values() for item in sublist]
        if attrib not in akeys:
            return super(RedisObject, self).__getattribute__(attrib)

        key = self.namespace[:-1]

        if attrib in all_keys['redis_string']:
            return db.hget(key, attrib) if db.hexists(key, attrib) else ''

        elif attrib in all_keys['redis_string_bool']:
            return bool(db.hget(key, attrib)) if db.hexists(key, attrib) else ''

        elif attrib in all_keys['redis_string_int']:
            return int(db.hget(key, attrib)) if db.hexists(key, attrib) else ''

        elif attrib in all_keys['redis_list']:
            key = self.namespace + attrib
            return RedisList.as_child(self, attrib, str)
            #return db.lrange(key, 0, -1) if db.exists(key) else []

        elif attrib in all_keys['redis_zset']:
            return RedisZSet.as_child(self, attrib, str)

    def __setattr__(self, attrib, value, score=None):
        if attrib in ['all_keys', 'namespace', 'database'] or '__' in attrib:
            super(RedisObject, self).__setattr__(attrib, value)
            return

        all_keys = super(RedisObject, self).__getattribute__('all_keys')
        akeys = [item for sublist in all_keys.values() for item in sublist]
        if attrib not in akeys and '__' not in attrib:
            super(RedisObject, self).__setattr__(attrib, value)
            return

        key = self.namespace[:-1]

        if attrib in all_keys['redis_string']:
            db.hset(key, attrib, value)

        elif attrib in all_keys['redis_string_bool']:
            db.hset(key, attrib, str(value))

        elif attrib in all_keys['redis_string_int']:
            db.hset(key, attrib, str(value))

        elif attrib in all_keys['redis_list'] or attrib in all_keys['redis_zset']:
            if not callable(value):
                raise ValueError(type(value))
            super(RedisObject, self).__setattr__(attrib, value)