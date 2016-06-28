#!/usr/bin/python
# -*- coding: utf-8 -*-
#
# sign_pkgs.py
#
# Copyright © 2014-2016 Antergos
#
# The code in this module was originally written by Xyne (Arch Linux TU)
# and was modified to suit the needs of this application.
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

""" Sign packages with gpg """

import os
import subprocess
import logging

from . import remove

logger = logging.getLogger('antbs')
GPG_BIN = '/usr/bin/gpg'
SIG_EXT = '.sig'
PKG_EXT = '.pkg.tar.xz'


def batch_sign(paths, db, bnum='', uid='', passphrase='', is_iso=False):
    if not isinstance(paths, list):
        logger.error('paths must be a list')
        return False

    for path in paths:
        logger.info('[SIGN PKG] Creating detached signature for %s' % path)
        db.publish(
            'live:build_output:{0}'.format(bnum),
            'Creating detached signature for %s' % path
        )

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

        if not passphrase:
            return False

        cmd = [GPG_BIN, '-sbu', 'Antergos', '--batch', '--passphrase-fd', '0', path]
        p = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )

        out, err = p.communicate(passphrase.encode('UTF-8'))

        if len(out) > 0:
            logger.info('GPG OUTPUT is: {0}'.format(out.decode('UTF-8')))
            db.publish(
                'live:build_output:{0}'.format(bnum),
                'GPG OUTPUT is: {0}'.format(out.decode('UTF-8'))
            )

        if len(err) > 0:
            db.publish(
                'live:build_output:{0}'.format(bnum),
                'Signing FAILED for {0}. Error output: {1}'.format(path, err.decode('UTF-8'))
            )
            logger.error(
                '[SIGN PKG] Signing FAILED for {0}. Error output: {1}'.format(
                    path,
                    err.decode('UTF-8')
                )
            )

            paths = [p for p in paths if not os.path.isdir(p) and not is_iso]

            for p in paths:
                remove(p)
                remove(p + '.sig')

            return False

    return True


def sign_packages(generated_pkgs, db, bnum='', uid='', gpg_pass=''):

    db.publish('live:build_output:{0}'.format(bnum), 'Signing packages..')

    logger.info('[PKGS TO SIGN] %s' % generated_pkgs)

    if generated_pkgs:
        for pkg in generated_pkgs:
            existing_sig = '{0}.sig'.format(pkg)

            if os.path.exists(existing_sig):
                remove(existing_sig)

        return batch_sign(generated_pkgs, db, bnum, uid, gpg_pass)

    return False
