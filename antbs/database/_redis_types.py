#!/usr/bin/python
# -*- coding: utf-8 -*-
#
# _redis_types.py
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

""" Descriptor objects for data stored in redis. """

import redis

db = redis.StrictRedis(unix_socket_path='/var/run/redis/redis.sock', decode_responses=True)


class RedisData:
    """
       Base class for descriptors that faciliate attribute access to data stored in redis.

       Attributes:
           key (str): The key under which the data is stored in redis.
           default_value (mixed): The default value for the bound attribute.
           value_type (mixed): The python type for the value of the bound attribute.

    """

    _not_implemented = 'Subclasses must implement this method!'

    def __init__(self, default_value, value_type):
        self.default_value = default_value
        self.value_type = value_type

    def __get__(self, obj, obj_type):
        raise NotImplementedError(self._not_implemented)

    def __set__(self, obj, value):
        raise NotImplementedError(self._not_implemented)

    @staticmethod
    def bool_string_helper(value):
        """ Given a `str`, returns value as `bool`. Given a `bool`, returns value as `str`. """

        if isinstance(value, str):
            return True if 'True' == value else False
        elif isinstance(value, bool):
            return 'True' if value else 'False'
        else:
            raise ValueError(
                'value must be of type(bool) or type(str), {0} given.'.format(type(value))
            )

    @staticmethod
    def _decode_value(value, default_value, value_type):
        val = value if value is not None else default_value
        return val if isinstance(val, value_type) else value_type(val)

    @staticmethod
    def _encode_value(value, default_value):
        val = value if value is not None else default_value
        return val if isinstance(val, str) else str(val)

    @staticmethod
    def _type_check(value, value_type, class_name):
        if not isinstance(value, (value_type, None)):
            errmsg = '{0} values must be of type: {1}, type: {2} given'.format(
                class_name,
                type(value_type),
                type(value)
            )

            raise ValueError(errmsg)


class RedisDataHashField(RedisData):
    """
       Descriptor that faciliates attribute access to data stored in redis hashes.

       Attributes:
           key (str): The key for the redis hash.
           field_name (str): The name of the redis hash field for the bound attribute.
           default_value (mixed): The default value for the bound attribute.
           value_type (mixed): The python type for the value of the bound attribute.

    """

    def __init__(self, field_name, default_value, value_type):
        super().__init__(default_value, value_type)

        self.field_name = field_name

    def __get__(self, obj, obj_type):
        val = db.hget(obj.full_key, self.field_name)
        value = val if self.value_type is not bool else self.bool_string_helper(val)

        self._type_check(value, self.value_type, self.__class__.__name__)

        return self._decode_value(value, self.default_value, self.value_type)

    def __set__(self, obj, value):
        self._type_check(value, self.value_type, self.__class__.__name__)

        val = self._encode_value(value, self.default_value)

        db.hset(obj.full_key, self.field_name, val)


class RedisDataRedisObject(RedisData):
    """
       Descriptor that faciliates attribute access to other redis objects from a redis object.

       Attributes:
           parent_key (str): The key for the redis hash.
           name (str): The name for the bound attribute (redis key = parent_key:name)
           default_value (mixed): The default value for the bound attribute.

    """

    _instances = None

    def __init__(self, key, default_value):
        super().__init__(default_value, default_value)
        self.key = key

        if self._instances is None:
            self._instances = {}

    def __get__(self, obj, obj_type):
        name, full_key = self._get_key_info_from_object(obj)

        if name not in self._instances:
            self._instances[name] = self.default_value.as_child(full_key, str)

        return self._instances[name]

    def __set__(self, obj, value):
        name, full_key = self._get_key_info_from_object(obj)

        self._type_check(value, self.value_type, self.__class__.__name__)

        self._instances[name] = value

    def _get_key_info_from_object(self, obj):
        full_key = '{0}:{1}'.format(obj.full_key, self.key)
        name = obj.full_key.split(':')[-1]

        return name, full_key

