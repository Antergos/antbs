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


from database.base_objects import RedisHash


class Transaction(RedisHash):
    """
    This class represents a single "build transaction" throughout the app. It is used
    to get/set transaction data from/to the database. A transaction is comprised
    of one or more builds. When a new transaction is initialized it creates its own build
    directory which it will delete once all builds are completed. This allows for
    build concurrency through multiple transactions and can be easily scaled as needed.

        Args:
            packages (list): Names of packages to build. This creates a new `Transaction`.
            tnum (int): Get an existing `Transaction` identified by its `tnum`.

        Attributes:
            tnum (int): This transaction's number or id if you prefer calling it that.
            base_path (str): Absolute path to the top-level build directory (for all transactions).
            path (str): Absolute path to this transaction's build directory.
            builds (list): This transaction's builds (list of bnums)
            is_running (bool): Whether or not the transaction is currently running.
            is_completed (bool): Whether or not the transaction is done (regardless of build results)
            building (str): The name of the package currently building.
            start_str (str): The datetime string for when this transaction started.
            end_str (str): The datetime string for when this transaction ended.

        Raises:
            ValueError: If both `packages` and `tnum` are Falsey.
    """

    def __init__(self, packages=None, tnum=None, base_path=None, prefix='trans'):
        if not any([packages, tnum]):
            raise ValueError('At least one of [packages, tnum] required.')
        elif all([packages, tnum]):
            raise ValueError('Only one of [packages, tnum] can be given, not both.')

        the_tnum = tnum
        if not tnum:
            the_tnum = self.db.incr('antbs:misc:tnum:next')

        super().__init__(prefix=prefix, key=the_tnum)

        self.key_lists.update(dict(
            string=['building', 'start_str', 'end_str'],
            bool=['is_running', 'completed'],
            int=['tnum'],
            list=[],
            zset=['packages', 'builds'],
            path=['base_path', 'path']
        ))


def get_trans_object(packages=None, tnum=None):
    """
    Gets an existing transaction or creates a new one.

    Args:
        packages (list): Create a new transaction with these packages.
        tnum (int): Get an existing transaction identified by `tnum`.

    Returns:
        Transaction: A fully initiallized `Transaction` object.

    Raises:
        ValueError: If both `packages` and `tnum` are Falsey or Truthy.

    """
    if not any([packages, tnum]):
        raise ValueError('At least one of [packages, tnum] required.')
    elif all([packages, tnum]):
        raise ValueError('Only one of [packages, tnum] can be given, not both.')

    trans_obj = Transaction(packages=packages, tnum=tnum)

    return trans_obj

