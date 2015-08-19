#!/usr/bin/python
# -*- coding: utf-8 -*-
#
# iso.py
#
# Copyright 2015 Antergos
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


""" Utility class for publishing iso releases. """
import hashlib
import os
import shutil
import subprocess
from utils.logging_config import logger
from utils.server_status import status
#import gnupg
import package
import utils.sign_pkgs as sign

REPO_DIR = '/srv/antergos.info/repo/iso'
TESTING_DIR = os.path.join(REPO_DIR, 'testing')
RELEASE_DIR = os.path.join(REPO_DIR, 'release')
PASSWORD = status.gpg_password
GPG_KEY = status.gpg_key


class ISOUtility(object):
    """

    :param pkg_obj:
    :param bnum:
    :raise AttributeError:
    """

    def __init__(self, pkg_obj=None):
        if not pkg_obj:
            raise AttributeError
        self.version = pkg_obj.pkgver
        self.pkgname = pkg_obj.pkgname
        self.file_name = (pkg_obj.pkgname.rsplit('-', 1)[-2] + '-' + self.version + '-' +
                          pkg_obj.pkgname.rsplit('-', 1)[-1] + '.iso')
        self.file_path = os.path.join(TESTING_DIR, self.file_name)
        self.mirror_url = 'http://mirrors.antergos.com/iso/release/' + self.file_name

    def prep_release(self):
        status.current_status = 'ISO Release: Step 1/4 - Generating checksum for %s' % self.file_name
        logger.debug(status.current_status)
        self.generate_checksums()
        status.current_status = 'ISO Release: Step 2/4 - Creating detached gpg signature for %s' % self.file_name
        logger.debug(status.current_status)
        self.sign_with_gnupg()
        status.current_status = 'ISO Release: Step 3/4 - Creating torrent file for %s' % self.file_name
        logger.debug(status.current_status)
        self.create_torrent_file()

    def create_torrent_file(self):
        try:
            trackers = {
                'obt': 'udp://tracker.openbittorrent.com:80,',
                'cps': 'udp://tracker.coppersurfer.tk:6969,',
                'lpd': 'udp://tracker.leechers-paradise.org:6969,',
                'dem': 'udp://open.demonii.com:1337'
            }
            cmd = ['mktorrent',
                   '-a',
                   trackers['obt'] + trackers['cps'] + trackers['lpd'] + trackers['dem'],
                   '-n',
                   self.file_name,
                   '-o',
                   self.file_name + '.torrent',
                   '-w',
                   self.mirror_url,
                   self.file_name]

            subprocess.check_output(cmd, cwd=TESTING_DIR)

        except subprocess.CalledProcessError as err:
            logger.error(err.output)

    def do_release(self):
        status.current_status = 'ISO Release: Step 4/4 - Moving %s to release directory.' % self.file_name
        logger.debug(status.current_status)
        for f in [self.file_path, self.file_path + '.sig', self.file_path + '.md5', self.file_path + '.torrent']:
            shutil.copy2(f, RELEASE_DIR)

    @staticmethod
    def checksum_md5(filename):
        """ Generate and return md5 checksum of a file. """

        md5 = hashlib.md5()
        with open(filename, 'rb') as f:
            for chunk in iter(lambda: f.read(8192), b''):
                md5.update(chunk)
        return md5.hexdigest()

    def generate_checksums(self):
        """ Write checksum to a file """

        md5_path = self.file_path + '.md5'
        md5_sum = self.checksum_md5(self.file_path)

        with open(md5_path, 'w') as check_sum:
            check_sum.write(md5_sum)

        check_sum.close()

    def sign_with_gnupg(self):
        """ Create a detached signature using GNUPG. """

        sign.batch_sign([self.file_path], is_iso=True)


def iso_release_job():
    try:
        status.idle = False
        iso_names = ['antergos-x86_64', 'antergos-i686', 'antergos-minimal-x86_64', 'antergos-minimal-i686']
        version = None
        for name in iso_names:
            pkg_obj = package.Package(name=name)
            iso = ISOUtility(pkg_obj=pkg_obj)
            version = pkg_obj.pkgver
            iso.prep_release()
            iso.do_release()

        if version and isinstance(version, str):
            files = os.listdir(RELEASE_DIR)
            for f in files:
                if version not in f:
                    shutil.move(f, '/opt/old-iso-images')
            files = os.listdir(TESTING_DIR)
            for f in files:
                if not os.path.isdir(f) and version in f:
                    shutil.move(f, '/tmp')

    except Exception as err:
        logger.error(err)

    status.idle = True



