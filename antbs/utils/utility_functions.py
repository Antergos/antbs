#!/usr/bin/env python
#  -*- coding: utf-8 -*-
#
#  utility_functions.py
#
#  Copyright © 2016 Antergos
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

import logging
import os
import shutil
import subprocess


def truncate_middle(s, n):
    if len(s) <= n:
        # string is already short-enough
        return s
    # half of the size, minus the 3 .'s
    n_2 = int(n) / 3 - 3
    # whatever's left
    n_1 = n - n_2 - 3
    return '{0}...{1}'.format(s[:n_1], s[-n_2:])


def remove(src):
    if not isinstance(src, str):
        raise ValueError('src must be of type(str), type({0}) given.'.format(type(src)))

    if os.path.islink(src):
        method_to_call = os.unlink

    elif os.path.isdir(src):
        method_to_call = shutil.rmtree

    else:
        method_to_call = os.remove

    try:
        method_to_call(src)
    except Exception as err:
        logging.exception(err)


def set_uid_and_gid():
    uid = os.geteuid()
    gid = os.getegid()

    os.setresgid(33, 33, gid)
    os.setresuid(33, 33, uid)


def copy_or_symlink(src, dst, logger=None):
    """
    Copies the file at `src` to `dst`. If `src` is a symlink the link will be
    followed to get the file that will be copied. If `dst` is a symlink then it will
    be removed.

    Args:
        src (str): The path to the file that will be copied.
        dst (str): The path to where the src file should be copied to.

    """

    uid = os.geteuid()
    gid = os.getegid()

    os.setegid(33)
    os.seteuid(33)

    if logger:
        logger.debug([src, dst])
    if os.path.islink(src):
        linkto = os.readlink(src)
        os.symlink(linkto, dst)
    else:
        try:
            shutil.copy(src, dst)
        except shutil.SameFileError:
            if os.path.islink(dst):
                os.unlink(dst)
                shutil.copy(src, dst)
        except Exception as err:
            logger.error(err)

    os.setegid(gid)
    os.seteuid(uid)


def symlink(src, dst, relative_to=None):
    """
    Creates a symbolic link at `dst` to the file at `src`. If `src` is a symlink the
    link will be followed to get the actual file that will be linked at `dst`. If `dst`
    is a symlink then it will be removed.

    Args:
        src (str): The path to the file that will be linked.
        dst (str): The path at which the link to the file at `src` should be created.

    """

    uid = os.geteuid()
    gid = os.getegid()

    os.setegid(33)
    os.seteuid(33)

    if relative_to:
        os.chdir(relative_to)

    if os.path.islink(src):
        src = os.readlink(src) if not relative_to else os.path.relpath(os.readlink(src))

    if os.path.islink(dst):
        os.unlink(dst)

    os.symlink(src, dst)

    os.setegid(gid)
    os.seteuid(uid)


def quiet_down_noisy_loggers():
    noisy_loggers = ["github3",
                     "requests",
                     "stormpath.http"]

    for logger_name in noisy_loggers:
        noisy_logger = logging.getLogger(logger_name)
        noisy_logger.setLevel(logging.ERROR)


def try_run_command(cmd, cwd, logger=None):
    """
    Tries to run command and then returns the result (success/fail)
    and any output that is captured.

    Args:
        cmd (list): Command to run as a list. See `subprocess` docs for details.
        cwd (str): Set the current working directory to use when running command.

    Returns:
        success (bool): Whether or not the command returned successfully (exit 0)
        res (str): The output that was captured.

    """

    res = None
    success = False

    try:
        res = subprocess.check_output(
            cmd,
            stderr=subprocess.STDOUT,
            universal_newlines=True,
            cwd=cwd,
            preexec_fn=set_uid_and_gid
        )
        success = True
    except subprocess.CalledProcessError as err:
        if logger is not None:
            logger.exception((err.output, err.stderr))
        else:
            logging.exception((err.output, err.stderr))
        res = err.output

    return success, res


def get_build_queue(status_obj, get_transaction):
    if not status_obj.transactions_running and not status_obj.transaction_queue:
        return []

    queued = []
    running = [t for t in status_obj.transactions_running if t]
    waiting = [t for t in status_obj.transaction_queue if t]
    all_transactions = running + waiting

    for tnum in all_transactions:
        trans_obj = get_transaction(tnum=tnum)

        if trans_obj.queue:
            queued_pkgs = [(trans_obj.tnum, p) for p in trans_obj.queue]
            queued.extend(queued_pkgs)

    return queued


def all_file_paths_exist(paths):
    return not any(True for p in paths if not os.path.exists(p))


def recursive_chown(path, uid, gid):
    for root, dirs, files in os.walk(path, followlinks=True):
        for item in dirs:
            os.chown(os.path.join(root, item), uid, gid)
        for item in files:
            os.chown(os.path.join(root, item), uid, gid)
