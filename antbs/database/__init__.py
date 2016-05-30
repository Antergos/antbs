#!/usr/bin/python
# -*- coding: utf-8 -*-
#
# __init__.py
#
# Copyright © 2016 Antergos
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

from database.build import Build
from database.monitor import Monitor
from database.package import Package
from database.repo import AntergosRepo, AntergosStagingRepo
from database.server_status import status
from database.transaction import Transaction


def get_pkg_object(name, fetch_pkgbuild=False):
    """
    Gets an existing package or creates a new one.

    Args:
        name (str): The package name.
        fetch_pkgbuild (bool): Whether or not pkgbuild should be fetched from github.

    Returns:
        Package: A fully initiallized `Package` object.

    """

    pkg_obj = Package(name=name, fetch_pkgbuild=fetch_pkgbuild)

    return pkg_obj


def get_build_object(pkg_obj=None, bnum=None, tnum=None):
    """
    Gets an existing build or creates a new one.

    Args:
        pkg_obj (Package): Create a new build for this package.
        bnum (int): Get an existing build identified by `bnum`.

    Returns:
        Build: A fully initiallized `Build`.

    Raises:
        ValueError: If both `pkg_obj` and `bnum` are Falsey or Truthy.

    """

    if not any([pkg_obj, bnum]):
        raise ValueError('At least one of [pkg_obj, bnum] required.')
    elif all([pkg_obj, bnum]):
        raise ValueError('Only one of [pkg_obj, bnum] can be given, not both.')

    bld_obj = Build(pkg_obj=pkg_obj, bnum=bnum, tnum=tnum)

    return bld_obj


def get_monitor_object(name):
    """
    Gets an existing repo monitor or creates a new one.

    Args:
        name (str): Name of 3rd-party provider/service (eg. Github).

    Returns:
        Monitor: A fully initiallized `Monitor` object.

    """

    monitor_obj = Monitor(name=name)

    return monitor_obj


def get_repo_object(name, path=None):
    """
    Gets an existing repo object or creates a new one.

    Args:
        name (str): Repo name.
        path (str): Absolute path to repo directory.

    Returns:
        PacmanRepo: A fully initiallized `PacmanRepo` object.

    Raises:
        ValueError: If name is not one of allowed names.

    """

    if not path:
        path = status.REPO_BASE_DIR
    if 'antergos' == name:
        repo_obj = AntergosRepo(name=name, path=path)
    elif 'antergos-staging' == name:
        repo_obj = AntergosStagingRepo(name=name, path=path)
    else:
        raise ValueError('name must be one of [antergos, antergos-staging]')

    return repo_obj


def get_trans_object(packages=None, tnum=None, repo_queue=None):
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

    trans_obj = Transaction(packages=packages, tnum=tnum, repo_queue=repo_queue)

    return trans_obj
