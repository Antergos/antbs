#!/usr/bin/python
# -*- coding: utf-8 -*-
#
# iso.py
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


""" Utility class for publishing iso releases. """

import hashlib
import os
import re
import shutil
import subprocess

import requests
from requests_toolbelt.adapters.source import SourceAddressAdapter

import utils.sign_pkgs as sign
from database import package
from database.base_objects import db
from database.server_status import status
from utils.logging_config import logger

REPO_DIR = '/srv/antergos.info/repo/iso'
TESTING_DIR = os.path.join(REPO_DIR, 'testing')
RELEASE_DIR = os.path.join(REPO_DIR, 'release')
PASSWORD = status.gpg_password
GPG_KEY = status.gpg_key
API_KEY = db.get('antbs:misc:antergos.com_api_key')


class ISOUtility:

    def __init__(self, pkg_obj=None):
        if not pkg_obj:
            raise AttributeError
        self.version = pkg_obj.pkgver
        self.pkgname = pkg_obj.pkgname
        self.file_name = (pkg_obj.pkgname.rsplit('-', 1)[-2] + '-' + self.version + '-' +
                          pkg_obj.pkgname.rsplit('-', 1)[-1] + '.iso')
        self.file_path = os.path.join(TESTING_DIR, self.file_name)
        self.mirror_url = 'http://mirrors.antergos.com/iso/release/' + self.file_name
        self.files = [self.file_path, self.file_path + '.sig',
                      self.file_path + '.md5', self.file_path + '.torrent']
        self.md5 = None

    @staticmethod
    def get_version():
        iso = [x for x in os.listdir(TESTING_DIR) if x.endswith('.iso')]
        match = re.match('\d{4}(\.\d{1,2}){2}', iso[0])
        if match:
            logger.info(match)
            return match.group(0)
        raise ValueError

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
        md5_path = self.file_path + '.md5'
        md5_sum = self.checksum_md5(self.file_path)
        self.md5 = md5_sum

        with open(md5_path, 'w') as check_sum:
            check_sum.write(md5_sum)

        check_sum.close()

    def sign_with_gnupg(self):
        """ Create a detached signature using GNUPG. """
        sign.batch_sign([self.file_path], is_iso=True)


class WordPressBridge:

    def __init__(self, auth):
        self.post_id_map = {
            'antergos-x86_64': '2563',
            'antergos-i686': '2564',
            'antergos-minimal-x86_64': '2565',
            'antergos-minimal-i686': '2566'
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
                post_url = 'https://' + self.dist + '.com/?' + query + nonce + '&api_key=' + API_KEY
                req = session.post(post_url, data=dict(pid=pid, url=iso_obj.iso_url,
                                                       md5=iso_obj.iso_md5, version=iso_obj.pkgver))
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

    if len(all_files) <= 16:
        return
    for f in all_files:
        files = [os.path.join(RELEASE_DIR, f) for f in os.listdir(RELEASE_DIR)]
        if version not in f and len(files) > 16:
            shutil.move(f, '/opt/old-iso-images')
            moved.append(os.path.basename(f))

    old_imgs = '/opt/old-iso-images'
    all_old_files = [os.path.join(old_imgs, f) for f in os.listdir(old_imgs)]
    if len(moved) > 0:
        for f in all_old_files:
            if os.path.basename(f) not in moved:
                os.remove(f)


def iso_release_job():
    saved_status = False
    if not status.idle and 'Idle' not in status.current_status:
        saved_status = status.current_status
    else:
        status.idle = False

    status.current_status = 'Starting ISO Release Job...'
    iso_names = ['antergos-x86_64', 'antergos-i686',
                 'antergos-minimal-x86_64', 'antergos-minimal-i686']
    version = None

    for name in iso_names:
        try:
            pkg_obj = package.get_pkg_object(name=name)
            iso = ISOUtility(pkg_obj=pkg_obj)

            iso.prep_release()
            iso.do_release()

            pkg_obj.iso_url = iso.mirror_url
            pkg_obj.iso_md5 = iso.md5

            if version is None:
                version = iso.version

            status.iso_pkgs.add(pkg_obj.name)

        except Exception as err:
            logger.error(err)

    if version and db:
        # We will use the repo monitor class to check propagation of the new files
        # before deleting the old files.
        db.set('antbs:misc:iso-release:do_check', version)

    if saved_status and not status.idle:
        status.current_status = saved_status
    else:
        status.idle = True
        status.current_status = 'Idle.'
