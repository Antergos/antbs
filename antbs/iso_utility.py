#!/usr/bin/python
# -*- coding: utf-8 -*-
#
# iso.py
#
# Copyright © 2013-2017 Antergos
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
import re
import shutil
import subprocess

import requests
from requests_toolbelt.adapters.source import SourceAddressAdapter

from utils import batch_sign

from database import (
    get_pkg_object,
    db,
    status
)

logger = status.logger
TESTING_DIR = os.path.join(status.ISO_DIR, 'testing')
RELEASE_DIR = os.path.join(status.ISO_DIR, 'release')
SCRIPTS_DIR = os.path.join(status.APP_DIR, 'scripts')
PASSWORD = status.gpg_password
GPG_KEY = status.gpg_key
API_KEY = db.get(status.ANTERGOS_API_DB_KEY_NAME)


class ISOUtility:
    def __init__(self, pkg_obj):
        self.version = pkg_obj.pkgver
        self.pkgname = pkg_obj.pkgname
        self.file_name = self.get_file_name(pkg_obj)
        self.file_path = os.path.join(TESTING_DIR, self.file_name)
        self.md5sums_path = os.path.join(TESTING_DIR, 'MD5SUMS-{}'.format(self.version))
        self.mirror_url = 'http://mirrors.antergos.com/iso/release/{0}'.format(self.file_name)
        self.files = [
            self.file_path,
            '{0}.sig'.format(self.file_path),
            '{0}.torrent'.format(self.file_path)
        ]
        self.md5 = None
        self.webseeds = self.get_webseeds()

    @staticmethod
    def get_webseeds():
        webseeds = []

        with open(os.path.join(SCRIPTS_DIR, 'webseeds.list'), 'r') as seeds_list:
            for line in seeds_list:
                webseeds.append(line.strip())

        return webseeds

    @staticmethod
    def get_file_name(pkgobj):
        pkgver = pkgobj.pkgver.split('.')[1]
        pkgver = pkgver if '0' not in pkgver else pkgver[-1]
        pkgver = '17.{}'.format(pkgver)
        if 'minimal' in pkgobj.pkgname:
            file_name = 'antergos-minimal-{0}-x86_64.iso'.format(pkgver)
        else:
            file_name = 'antergos-{0}-x86_64.iso'.format(pkgver)

        return file_name

    def prep_release(self):
        tpl = 'ISO Release: Step 1/3 - Generating checksum for {}'
        status.current_status = tpl.format(self.file_name)
        logger.debug(status.current_status)
        self.generate_checksums()
        tpl = 'ISO Release: Step 2/3 - Creating torrent file for {}'
        status.current_status = tpl.format(self.file_name)
        logger.debug(status.current_status)
        self.create_torrent_file()

    def create_torrent_file(self):
        try:
            trackers = [
                'udp://tracker.openbittorrent.com:80',
                'udp://tracker.coppersurfer.tk:6969',
                'udp://tracker.leechers-paradise.org:6969',
                'udp://open.demonii.com:1337'
            ]
            cmd = ['mktorrent',
                   '-a', ','.join(trackers),
                   '-n', self.file_name,
                   '-o', self.file_name + '.torrent',
                   '-w', ','.join(self.webseeds),
                   self.file_name]

            subprocess.check_output(cmd, cwd=TESTING_DIR)

        except subprocess.CalledProcessError as err:
            logger.error(err.output)

    def do_release(self):
        tpl = 'ISO Release: Step 4/4 - Moving {0} to release directory.'
        status.current_status = tpl.format(self.file_name)
        logger.debug(status.current_status)
        for f in self.files:
            shutil.move(f, RELEASE_DIR)

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
        md5_sum = self.checksum_md5(self.file_path)
        self.md5 = md5_sum
        line = '{} {}\n'.format(md5_sum, self.file_name)

        with open(self.md5sums_path, 'a') as check_sum:
            check_sum.write(line)

    def sign_with_gnupg(self):
        """ Create a detached signature using GNUPG. """
        batch_sign({self.file_path}, db, passphrase=PASSWORD, is_iso=True)


class WordPressBridge:

    def __init__(self, auth):
        self.post_id_map = {
            'antergos-x86_64': '26386',
            'antergos-minimal-x86_64': '26387'
        }
        self.auth = auth
        logger.info('WordPressBridge Object Initialized')
        self.success = False
        self.dist = 'antergos'

    def add_new_iso_version(self, iso_pkg_obj=None):
        if iso_pkg_obj is None:
            logger.error('iso cant be None')
            return False
        else:
            iso_obj = iso_pkg_obj
            logger.info('adding_new_iso_version: %s', iso_obj)

        pid = self.post_id_map[iso_obj.pkgname]
        query = 'json=get_nonce&controller=' + self.dist + '&method=handle_request'
        post_url = 'https://' + self.dist + '.com/?' + query
        session = requests.Session()
        session.mount('http://', SourceAddressAdapter((status.request_from, 0)))
        session.mount('https://', SourceAddressAdapter((status.request_from, 0)))
        session.auth = self.auth
        try:
            req = session.get(post_url)
            req.raise_for_status()
            logger.info(req.text)
            req = req.json()
            logger.info(req)

            if req.get('nonce', False):
                nonce = req.get('nonce')
                query = 'json=' + self.dist + '.handle_request&nonce='
                url = 'https://' + self.dist + '.com/?' + query + nonce + '&api_key=' + API_KEY
                post_url = url
                req = session.post(
                    post_url,
                    data=dict(pid=pid,
                              url=iso_obj.iso_url,
                              md5=iso_obj.iso_md5,
                              version=iso_obj.pkgver)
                )
                req.raise_for_status()
                logger.info(req.text)
                self.success = True
        except Exception as err:
            self.success = False
            logger.error(err)
            return False

        return True


def clean_up_after_release(version):
    status.current_status = 'ISO Release: Cleaning up old files.'
    logger.debug(status.current_status)
    all_files = [os.path.join(RELEASE_DIR, f) for f in os.listdir(RELEASE_DIR)]
    moved = []

    if len(all_files) <= 5:
        return

    for f in all_files:
        files = [os.path.join(RELEASE_DIR, f) for f in os.listdir(RELEASE_DIR)]
        if version not in f and len(files) > 5:
            shutil.move(f, status.OLD_ISO_IMAGES_DIR)
            moved.append(os.path.basename(f))

    old_imgs = status.OLD_ISO_IMAGES_DIR
    all_old_files = [os.path.join(old_imgs, f) for f in os.listdir(old_imgs)]
    if len(moved) > 0:
        for f in all_old_files:
            if os.path.basename(f) not in moved:
                os.remove(f)


def iso_release_job():
    saved_status = False
    if not status.idle and 'Idle' not in status.current_status:
        saved_status = status.current_status

    status.idle = False
    status.current_status = 'Starting ISO Release Job...'
    iso_names = ['antergos-x86_64', 'antergos-minimal-x86_64']
    version = iso_obj = None

    for name in iso_names:
        try:
            pkg_obj = get_pkg_object(name=name)
            iso_obj = ISOUtility(pkg_obj=pkg_obj)

            iso_obj.prep_release()
            iso_obj.sign_with_gnupg()
            iso_obj.do_release()

            pkg_obj.iso_url = iso_obj.mirror_url
            pkg_obj.iso_md5 = iso_obj.md5

            if version is None:
                version = iso_obj.version

            status.iso_pkgs.add(pkg_obj.name)

        except Exception as err:
            logger.error(err)

    if version and db and iso_obj:
        shutil.move(iso_obj.md5sums_path, RELEASE_DIR)
        # We will use the repo monitor class to check propagation of the new files
        # before deleting the old files.
        db.set(status.iso_release_check_key, version)

    if saved_status and not status.idle:
        status.current_status = saved_status
    else:
        status.idle = True
        status.current_status = 'Idle.'
