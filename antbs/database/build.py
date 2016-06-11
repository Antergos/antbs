#!/usr/bin/python
# -*- coding: utf-8 -*-
#
# build_obj.py
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

import os
from datetime import datetime
from multiprocessing import Process

import gevent
from rq import Connection, get_current_job
from pygments import highlight
from pygments.formatters import HtmlFormatter
from pygments.lexers import BashLexer

from database.base_objects import RedisHash
from database.server_status import status, get_timeline_object
from utils.logging_config import logger
from utils.docker_util import DockerUtils
from utils.utilities import CustomSet, remove
from utils.sign_pkgs import sign_packages


doc_util = DockerUtils()
doc = doc_util.doc
PKG_EXT = '.pkg.tar.xz'
SIG_EXT = '.sig'


class Build(RedisHash):
    """
    This class represents a "build" throughout the build server app. It is used
    to get and set build data to the database.

    Args:
        pkg_obj (Package): Create a new build for this package.
        bnum (int): Get an existing build identified by its `bnum`.

    Attributes:
        (str)
            pkgname, pkgver, epoch, pkgrel: self explanatory (see `man PKGBUILD`)
            version_str: The package's version including pkgrel for displaying on the frontend.
            path: Absolute path to the package's directory (subdir of antergos-packages directory)
            build_path: Absolute path to the the package's build directory.
            start_str: The build's start timestamp.
            end_str: The build's end timestamp.
            container: The build's Docker container ID.
            review_status: The build's developer review status.
            review_dev: The developer who reviewed the build result.
            review_date: The review's timestamp.
            log_str: The build log, fully processed into HTML for display on the front-end.


        (bool)
            failed: The build failed (Only one of `failed` and `completed` can be `True`)
            completed: The build completed (Only one of `failed` and `completed` can be `True`)

        (int)
            bnum: ID assigned to the build.
            pkg_id: ID of the package that this build is for.
            tnum: ID of the transaction that this build is a part of.

        (list)
            log: The build log, unprocessed, stored as lines in a list.

    Raises:
        ValueError: If both `pkg_obj` and `bnum` are Falsey.

    """

    def __init__(self, pkg_obj=None, bnum=None, tnum=None, prefix='build'):
        if not pkg_obj and not bnum:
            raise ValueError

        the_bnum = bnum
        if not bnum:
            the_bnum = self.db.incr('antbs:misc:bnum:next')

        super().__init__(prefix=prefix, key=the_bnum)

        self.attrib_lists.update(dict(
            string=['pkgname', 'pkgver', 'epoch', 'pkgrel', 'path', 'build_path',
                    'start_str', 'end_str', 'version_str', 'container', 'review_status',
                    'review_dev', 'review_date', 'log_str', 'pkg_id', 'bnum', 'tnum',
                    'repo_container', 'live_output_key', 'last_line_key'],
            bool=['failed', 'completed', 'is_iso'],
            int=[],
            list=['log', 'generated_files'],
            set=['generated_pkgs'],
            path=['build_dir', 'result_dir', '_32build', '_32bit', 'cache', 'cache_i686']
        ))

        self.__namespaceinit__()

        self._pkg_obj = None

        if pkg_obj and (not self or not self.bnum):
            self._pkg_obj = pkg_obj

            self.__keysinit__()

            for key in pkg_obj.all_attribs:
                if key in self.all_attribs:
                    val = getattr(pkg_obj, key)
                    value = False if 'is_iso' == key and '' == val else val
                    setattr(self, key, value)

            self.bnum = the_bnum
            self.tnum = tnum
            self.failed = False
            self.completed = False
            self.live_output_key = 'live:build_output:{0}'.format(self.bnum)
            self.last_line_key = 'tmp:build_log_last_line:{0}'.format(self.bnum)

    @staticmethod
    def datetime_to_string(dt):
        """
        Converts a datetime to a string.

        Args:
            dt (datetime.datetime): `datetime` to be converted.

        Returns:
            str: The datetime string.

        """
        return dt.strftime("%m/%d/%Y %I:%M%p")

    def publish_build_output(self):
        if not self.container:
            logger.error('Unable to publish build output. (Container is None)')
            return

        output = doc.logs(container=self.container, stream=True, follow=True)
        nodup = CustomSet()
        content = []

        for line in output:
            line = line.decode('UTF-8').rstrip()
            if not line or 'makepkg]# PS1="' in line:
                continue
            end = line[25:]
            if nodup.add(end):
                line = line.replace("'", '')
                line = line.replace('"', '')
                line = '[{0}]: {1}'.format(datetime.now().strftime("%m/%d/%Y %I:%M%p"), line)

                content.append(line)
                self.db.publish(self.live_output_key, line)
                self.db.setex(self.last_line_key, 1800, line)

        result_ready = self.completed != self.failed

        if not result_ready:
            waiting = 0

            while not result_ready:
                waiting += 5
                result_ready = self.completed != self.failed

                gevent.sleep(5)

                if waiting > 300:
                    logger.error('timed out will waiting for this build\'s final status')
                    break

        if self.failed:
            self.db.publish(self.live_output_key, 'ENDOFLOG')

        for line in content:
            self.log.rpush(line)

        log_content = '\n '.join(self.log)
        self.log_str = highlight(log_content, BashLexer(),
                                 HtmlFormatter(style='monokai', linenos='inline',
                                               prestyles="background:#272822;color:#fff;"))

    def start(self, pkg_obj=None):
        if not self._pkg_obj and not pkg_obj:
            raise RuntimeError('Cannot start build without `pkg_obj`')

        if not pkg_obj:
            pkg_obj = self._pkg_obj
        elif not self._pkg_obj:
            self._pkg_obj = pkg_obj

        self.process_and_save_build_metadata(self._pkg_obj.pkgver)
        
        if self.is_iso:
            result = self._build_iso()
        else:
            result = self._build_package()

        return result

    def process_and_save_build_metadata(self, version_str=None):
        """
        Initializes the build metadata.

        Args:
            pkg_obj (Package): Package object for the package being built.

        Returns:
            Build: A build object.

        """

        self.start_str = self.datetime_to_string(datetime.now())

        if version_str:
            self.version_str = version_str
        else:
            self.version_str = self._pkg_obj.version_str

        pkg_link = '<a href="{0}">{0}</a>'.format(self._pkg_obj.pkgname)

        tpl = 'Build <a href="/build/{0}">{0}</a> for {1} <strong>{2}</strong> started.'

        tlmsg = tpl.format(self.bnum, pkg_link, self.version_str)

        get_timeline_object(msg=tlmsg, tl_type=3, ret=False)

        self._pkg_obj.builds.append(self.bnum)
        status.now_building.append(self.bnum)

        with Connection(self.db):
            current_job = get_current_job()
            current_job.meta['building_num'] = self.bnum
            current_job.save()

    def save_build_results(self, result):
        if result is True:
            tpl = 'Build <a href="/build/{0}">{0}</a> for <strong>{1}-{2}</strong> was successful.'
            tlmsg = tpl.format(str(self.bnum), self._pkg_obj.pkgname, self.version_str)
            _ = get_timeline_object(msg=tlmsg, tl_type=4)

            self.review_status = 'pending'
            self.failed = False
            self.completed = True

            status.completed.rpush(self.bnum)

        else:
            tpl = 'Build <a href="/build/{0}">{0}</a> for <strong>{1}-{2}</strong> failed.'
            tlmsg = tpl.format(str(self.bnum), self._pkg_obj.pkgname, self.version_str)
            _ = get_timeline_object(msg=tlmsg, tl_type=5)

            self.failed = True
            self.completed = False

            status.failed.rpush(self.bnum)

        self.end_str = self.datetime_to_string(datetime.now())

    def get_save_pkgbuild_generates(self):
        try:
            generated_pkgs = self._pkg_obj._pkgbuild.get_generates(self.result_dir)
        except Exception:
            self._pkg_obj.setup_pkgbuild_parser()
            generated_pkgs = self._pkg_obj._pkgbuild.get_generates(self.result_dir)

        for gen_pkg in generated_pkgs:
            if gen_pkg:
                self.generated_pkgs.add(gen_pkg)

    def get_save_generated_files_paths(self):
        generated_files = [
            os.path.join(self.result_dir, f)
            for f in os.listdir(self.result_dir)
            if f.endswith(PKG_EXT)
        ]
        logger.debug(generated_files)

        self.generated_files.extend(generated_files)

        return generated_files

    def get_save_generated_signatures_paths(self):
        generated_files = [
            os.path.join(self.result_dir, f)
            for f in os.listdir(self.result_dir)
            if f.endswith(PKG_EXT + SIG_EXT)
        ]

        self.generated_files.extend(generated_files)

        return generated_files

    def _build_package(self):
        self.building = self._pkg_obj.pkgname
        own_status = (
            'Building {0}-{1} with makepkg.'.format(self.building, self._pkg_obj.version_str)
        )
        status.current_status = own_status
        status.idle = False

        doc_util.do_docker_clean(self._pkg_obj.pkgname)

        build_env = ['_AUTOSUMS=True'] if self._pkg_obj.auto_sum else ['_AUTOSUMS=False']

        # if '/cinnamon/' in self._pkg_obj.gh_path:
        #    build_env.append('_ALEXPKG=True')
        # else:
        build_env.append('_ALEXPKG=False')

        hconfig = doc_util.get_host_config('packages', self.build_dir, self.result_dir, self.cache,
                                           self.cache_i686, self._32build, self._32bit)
        container = {}
        try:
            container = doc.create_container(
                'antergos/makepkg',
                command='/makepkg/build.sh',
                volumes=['/var/cache/pacman', '/makepkg', '/antergos',
                         '/pkg', '/root/.gnupg', '/staging', '/32bit',
                         '/32build', '/result',
                         '/var/cache/pacman_i686'],
                environment=build_env,
                cpuset='0-3',
                name=self._pkg_obj.pkgname,
                host_config=hconfig
            )
            if container.get('Warnings', False):
                logger.error(container.get('Warnings'))

        except Exception as err:
            logger.error('Create container failed. Error Msg: %s', err)
            self.save_build_results(False)
            return False

        container_id = container.get('Id', '')
        self.container = container_id
        stream_process = Process(target=self.publish_build_output)

        try:
            doc.start(container_id)
            stream_process.start()

            result = doc.wait(container_id)

            if int(result) != 0:
                self.failed = True
                tpl = 'Container %s exited with a non-zero return code. Return code was %s'
                logger.error(tpl, self._pkg_obj.pkgname, result)
            else:
                self.completed = True
                logger.info('Container %s exited. Return code was %s', self._pkg_obj.pkgname, result)

        except Exception as err:
            logger.error('Start container failed. Error Msg: %s', err)
            self.save_build_results(False)
            return False

        stream_process.join()

        if not self.failed:
            # self.get_save_pkgbuild_generates()
            generated_files = self.get_save_generated_files_paths()

            _signed_packages = sign_packages(generated_files, self.bnum)

            if not _signed_packages:
                logger.error('Failed to sign packages!')
                self.save_build_results(False)
                return False

            if self._pkg_obj.builds and len(self._pkg_obj.builds) > 1:
                last_build = self._pkg_obj.builds[-2]

                if last_build:
                    last_bld_obj = get_build_object(bnum=last_build)

                    if 'pending' == last_bld_obj.review_status and last_bld_obj.bnum != self.bnum:
                        last_bld_obj.review_status = 'skip'

            self.save_build_results(True)
            self.get_save_generated_signatures_paths()
            return True

        self.save_build_results(False)
        return False

    def _build_iso(self):
        # TODO: Rework this, possibly abstract away parts in common with self.build_package()
        own_status = 'Building {0}-{1} with mkarchiso.'.format(self._pkg_obj.pkgname,
                                                               self._pkg_obj.pkgver)
        status.current_status = own_status
        status.iso_building = True

        bld_obj = self.process_and_save_build_metadata(self._pkg_obj.pkgver)

        i686_flag = os.path.join(status.REPO_BASE_DIR, 'iso/testing/.ISO32')
        minimal = os.path.join(status.REPO_BASE_DIR, 'iso/testing/.MINIMAL')

        if 'i686' in self._pkg_obj.pkgname:
            if not os.path.exists(i686_flag):
                open(i686_flag, 'a').close()
        else:
            if os.path.exists(i686_flag):
                os.remove(i686_flag)

        if 'minimal' in self._pkg_obj.pkgname:
            if not os.path.exists(minimal):
                open(minimal, 'a').close()
        else:
            if os.path.exists(minimal):
                os.remove(minimal)

        doc_util.do_docker_clean(self._pkg_obj.pkgname)

        in_dir_last = len(
            [name for name in os.listdir(os.path.join(status.REPO_BASE_DIR, 'iso/testing'))]
        )

        # Create docker host config dict
        hconfig = doc.create_host_config(
            privileged=True,
            cap_add=['ALL'],
            binds={
                status.MKARCHISO_DIR:
                    {
                        'bind': '/start',
                        'ro': False
                    },
                '/run/dbus':
                    {
                        'bind': '/var/run/dbus',
                        'ro': False
                    },
                os.path.join(status.REPO_BASE_DIR, 'iso/testing'):
                    {
                        'bind': '/out',
                        'ro': False
                    }
            },
            restart_policy={
                "MaximumRetryCount": 2,
                "Name": "on-failure"
            },
            mem_limit='2G',
            memswap_limit='-1'
        )

        iso_container = {}

        try:
            iso_container = doc.create_container("antergos/mkarchiso", command='/start/run.sh',
                                                 name=self._pkg_obj.pkgname, host_config=hconfig,
                                                 cpuset='0-3')
            if iso_container.get('Warnings', False):
                logger.error(iso_container.get('Warnings'))
        except Exception as err:
            logger.error('Create container failed. Error Msg: %s' % err)
            self.save_build_results(False)
            return False

        self.container = iso_container.get('Id')
        status.container = self.container

        open(os.path.join(status.MKARCHISO_DIR, 'first-run'), 'a').close()

        try:
            doc.start(bld_obj.container)
            cont = self.container
            stream_process = Process(target=self.publish_build_output)
            stream_process.start()
            result = doc.wait(cont)
            inspect = doc.inspect_container(cont)
            restarting = (
                inspect['State'].get('Restarting', '') or inspect.get('RestartCount', 0) != 2
            )

            if result != 0:
                if restarting:
                    while restarting:
                        gevent.sleep(5)
                        inspect = doc.inspect_container(cont)
                        restarting = (
                            inspect['State'].get('Restarting', '') or
                            inspect.get('RestartCount', 0) != 2
                        )

            if inspect['State'].get('ExitCode', 1) == 1:
                logger.error(
                    '[CONTAINER EXIT CODE] Container %s exited. Return code was %s',
                    self._pkg_obj.pkgname,
                    result
                )
                self.save_build_results(False)
                return False

            else:
                logger.info(
                    '[CONTAINER EXIT CODE] Container %s exited. Return code was %s',
                    self._pkg_obj.pkgname,
                    result
                )

        except Exception as err:
            logger.error('Start container failed. Error Msg: %s', err)
            self.save_build_results(False)
            return False

        stream_process.join()

        if not bld_obj.failed:
            remove('/opt/archlinux-mkarchiso/antergos-iso')
            doc_util.do_docker_clean(self._pkg_obj.pkgname)

        in_dir = len(
            [name for name in os.listdir(os.path.join(status.REPO_BASE_DIR, 'iso/testing'))]
        )

        if in_dir > in_dir_last:
            self.save_build_results(True)
            return True
        else:
            self.save_build_results(False)
            return False


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
