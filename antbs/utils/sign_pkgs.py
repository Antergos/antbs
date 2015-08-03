#!/usr/bin/python
# -*- coding: utf-8 -*-
#
# sign_pkgs.py
#
# Copyright 2014 Antergos
# The code in this module was originally written by Xyne (Arch Linux TU)
# and was modified to suit the needs of this application.
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston,
# MA 02110-1301, USA.

""" Sign packages with gpg """

import glob
import os
import subprocess
import shutil

from redis_connection import db
from logging_config import logger
from server_status import status

GPG_BIN = '/usr/bin/gpg'
SIG_EXT = '.sig'
password = status.gpg_password
gpg_key = status.gpg_key


def remove(src):
    if src != str(src):
        return True
    if os.path.isdir(src):
        try:
            shutil.rmtree(src)
        except Exception as err:
            logger.error(err)
            return True
    elif os.path.isfile(src):
        try:
            os.remove(src)
        except Exception as err:
            logger.error(err)
            return True
    else:
        return True


def batch_sign(paths, uid=gpg_key, passphrase=password):
    """
    Batch sign several files with the key matching the given UID.

    If no passphrase is given then the user is prompted for one.

    The passphrase is returned to avoid further prompts.
    """
    for path in paths:
        db.publish('build-output', 'Creating detached signature for %s' % path)
        logger.info('[SIGN PKG] Creating detached signature for %s' % path)
        # Verify existing signatures. This fails if the sig is invalid or
        # non-existent. Either way a new one will be needed.
        cmd = [GPG_BIN, '--verify', path + SIG_EXT]
        with open(os.devnull, 'w') as f:
            p = subprocess.Popen(cmd, stdout=f, stderr=f)
            e = p.wait()
            if e == 0:
                continue

        sigpath = path + '.sig'
        try:
            os.remove(sigpath)
        except OSError:
            pass

        db.publish('build-output', 'Signing %s' % path)
        logger.info('[SIGN PKG] Signing %s' % path)
        if not passphrase:
            return False
            # passphrase = getpass.getpass("Enter passphrase for %s: " % uid).encode('utf-8')
        cmd = [GPG_BIN, '-sbu', 'Antergos', '--batch', '--passphrase-fd', '0', path]
        p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        out, err = p.communicate(passphrase)
        if len(out) > 0:
            db.publish('build-output', 'GPG OUTPUT is: %s' % out)
            logger.info('GPG OUTPUT is: %s' % out)
        if len(err) > 0:
            db.publish('build-output', 'Signing FAILED for %s. Error output: %s' % (path, err))
            logger.error('[SIGN PKG] Signing FAILED for %s. Error output: %s' % (path, err))
            for p in paths:
                remove(p)
                remove(p + '.sig')
            return False

    return True


def sign_packages(pkgname=None):
    if pkgname:
        db.publish('build-output', 'Signing package..')
        pkgs2sign = glob.glob(
            '/srv/antergos.info/repo/iso/testing/uefi/antergos-staging/x86_64/%s-***.xz' % pkgname)
        pkgs2sign32 = glob.glob(
            '/srv/antergos.info/repo/iso/testing/uefi/antergos-staging/i686/%s-***.xz' % pkgname)
        pkgs2sign = pkgs2sign + pkgs2sign32
        logger.info('[PKGS TO SIGN] %s' % pkgs2sign)

        if pkgs2sign is not None and pkgs2sign != []:
            return batch_sign(pkgs2sign)

    return False
