#!/usr/bin/python
# -*- coding: utf-8 -*-
#
# transaction.py
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
# along with AntBS; If not, see <http://www.gnu.org/licenses/>.

""" Transaction Class """

from utils.logging_config import logger
from utils.redis_connection import RedisList, RedisHash, RedisZSet, db
from utils.server_status import status


class Transaction(RedisHash):
    """
    This class represents a single "build transaction" throughout the app. It is used
    to get/set transaction data from/to the database. A transaction is comprised
    of one or more builds. When a new transaction is initialized it creates its own build
    directory which it will once all builds are completed. This allows for build concurrency
    across multiple transactions and can be easily scaled as needed.

        Args:
            pkg_objs (list): Names of packages to build. This creates a new `Transaction`.
            tnum (int): Get an existing `Transaction` identified by its `tnum`.

        Attributes:
            tnum (int): This transaction number or id if you prefer calling it that.
            base_path (str): Absolute path to the top-level build directory (for all transactions).
            path (str): Absolute path to this transaction's build directory.
            builds (list): This transaction's builds (list of bnums)
            is_running (bool): Whether or not the transaction is currently running.
            is_completed (bool): Whether or not the transaction is done (regardless of build results)
            building (str): The name of the package currently building.

        Raises:
            ValueError: If both `pkg_objs` and `tnum` are Falsey.
    """

    def __init__(self, pkg_objs=None, tnum=None):
        super().__init__()
