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

import gevent
from pygments import highlight
from pygments.formatters import HtmlFormatter
from pygments.lexers import BashLexer

from database.base_objects import RedisHash
from utils.logging_config import logger
from utils.docker_util import DockerUtils
from utils.utilities import CustomSet
from datetime import datetime


doc = DockerUtils().doc


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

        self.attrib_lists.update(
                dict(string=['pkgname', 'pkgver', 'epoch', 'pkgrel', 'path', 'build_path',
                             'start_str', 'end_str', 'version_str', 'container', 'review_status',
                             'review_dev', 'review_date', 'log_str', 'pkg_id', 'bnum', 'tnum',
                             'repo_container'],
                     bool=['failed', 'completed', 'is_iso'],
                     int=[],
                     list=['log'],
                     set=[],
                     path=[]))

        self.__namespaceinit__()

        if pkg_obj and (not self or not self.bnum):
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

    def publish_build_output(self, upd_repo=False):
        if not self.container or (upd_repo and not self.repo_container):
            logger.error('Unable to publish build output. (Container is None)')
            return

        container = self.container if not upd_repo else self.repo_container

        output = doc.logs(container=container, stream=True, follow=True)
        nodup = CustomSet()
        content = []
        live_output_key = 'live:build_output:{0}'.format(self.bnum)
        last_line_key = 'tmp:build_log_last_line:{0}'.format(self.bnum)
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
                self.db.publish(live_output_key, line)
                self.db.setex(last_line_key, 3600, line)

        result_ready = self.completed != self.failed
        if not result_ready:
            while not result_ready:
                result_ready = self.completed != self.failed
                gevent.sleep(2)

        if upd_repo or self.failed:
            self.db.publish(live_output_key, 'ENDOFLOG')

        existing = True
        if len(self.log) < 1 and not self.failed and not self.is_iso:
            existing = False

        for line in content:
            self.log.rpush(line)

        if existing:
            log_content = '\n '.join(self.log)
            self.log_str = highlight(log_content,
                                     BashLexer(),
                                     HtmlFormatter(style='monokai',
                                                   linenos='inline',
                                                   prestyles="background:#272822;color:#fff;"))

    def start(self, pkg_obj):
        self.building = pkg_obj.name
        own_status = 'Building {0}-{1} with makepkg.'.format(pkg_obj.name,
                                                             self._pkgvers[pkg_obj.name])
        status.current_status = own_status
        status.idle = False

        in_dir_last = len([name for name in os.listdir(self.result_dir)])
        db.setex('antbs:misc:pkg_count:{0}'.format(self.tnum), 86400, in_dir_last)

        bld_obj = self.process_and_save_build_metadata(pkg_obj, self._pkgvers[pkg_obj.name])

        doc_util.do_docker_clean(pkg_obj.name)
        self.setup_package_build_directory(pkg_obj.name)

        build_env = ['_AUTOSUMS=True'] if pkg_obj.auto_sum else ['_AUTOSUMS=False']

        # if '/cinnamon/' in pkg_obj.gh_path:
        #    build_env.append('_ALEXPKG=True')
        # else:
        build_env.append('_ALEXPKG=False')

        build_dir = self._build_dirpaths[pkg_obj.name]['build_dir']
        _32bit = self._build_dirpaths[pkg_obj.name]['32bit']
        _32build = self._build_dirpaths[pkg_obj.name]['32build']
        result_dir = self._build_dirpaths[pkg_obj.name]['result']
        hconfig = doc_util.get_host_config('packages', build_dir, result_dir, self.cache,
                                           self.cache_i686, _32build, _32bit)
        container = {}
        try:
            container = doc.create_container("antergos/makepkg",
                                             command='/makepkg/build.sh',
                                             volumes=['/var/cache/pacman', '/makepkg', '/antergos',
                                                      '/pkg', '/root/.gnupg', '/staging', '/32bit',
                                                      '/32build', '/result',
                                                      '/var/cache/pacman_i686'],
                                             environment=build_env, cpuset='0-3',
                                             name=pkg_obj.name, host_config=hconfig)
            if container.get('Warnings', False):
                logger.error(container.get('Warnings'))
        except Exception as err:
            logger.error('Create container failed. Error Msg: %s', err)
            bld_obj.failed = True

        container_id = container.get('Id', '')
        bld_obj.container = container_id
        status.container = container_id
        stream_process = Process(target=bld_obj.publish_build_output, kwargs=dict(upd_repo=False))

        try:
            doc.start(container_id)
            stream_process.start()
            result = doc.wait(container_id)
            if int(result) != 0:
                bld_obj.failed = True
                tpl = 'Container %s exited with a non-zero return code. Return code was %s'
                logger.error(tpl, pkg_obj.name, result)
            else:
                bld_obj.completed = True
                logger.info('Container %s exited. Return code was %s', pkg_obj.name, result)
        except Exception as err:
            bld_obj.failed = True
            logger.error('Start container failed. Error Msg: %s', err)

        stream_process.join()

        _signed_packages = False

        if bld_obj.completed:
            logger.debug('bld_obj.completed!')
            self.get_and_save_generated_packages(pkg_obj, result_dir)

            _signed_packages = sign_packages(pkg_obj, self.generated_pkgs, bld_obj.bnum)

        if _signed_packages:
            tpl = 'Build <a href="/build/{0}">{0}</a> for <strong>{1}-{2}</strong> was successful.'
            tlmsg = tpl.format(str(bld_obj.bnum), pkg_obj.name, bld_obj.version_str)
            _ = get_timeline_object(msg=tlmsg, tl_type=4)
            status.completed.rpush(bld_obj.bnum)
            self.completed.add(pkg_obj.pkgname)
            bld_obj.review_status = 'pending'
        else:
            tpl = 'Build <a href="/build/{0}">{0}</a> for <strong>{1}-{2}</strong> failed.'
            tlmsg = tpl.format(str(bld_obj.bnum), pkg_obj.name, bld_obj.version_str)
            _ = get_timeline_object(msg=tlmsg, tl_type=5)
            bld_obj.failed = True
            bld_obj.completed = False
            self.failed.add(pkg_obj.pkgname)

        bld_obj.end_str = datetime.datetime.now().strftime("%m/%d/%Y %I:%M%p")

        # TODO: Need to toss out the current repo update method and come up with something better.
        if self.completed and pkg_obj.pkgname in self.completed:
            fnames = [p for p in self.generated_pkgs if p.rsplit('-', 3)[0] == pkg_obj.pkgname]
            fname = ''.join(fnames)
            pkg_obj.filename_str = fname or ''

            pkgs2add = [p for p in self.generated_pkgs if p.rsplit('-', 3)[0] in self.completed]
            self._staging_repo.update_repo(bld_obj=bld_obj, pkgs2_add_rm=pkgs2add)

        self.building = ''
        status.now_building.remove(bld_obj.bnum)
        if own_status == status.current_status:
            if not status.now_building and not self.queue and not status.transaction_queue:
                status.idle = True

        if not bld_obj.failed:
            last_build = pkg_obj.builds[-2] if pkg_obj.builds else None
            if last_build:
                last_bld_obj = get_build_object(bnum=last_build)
                if 'pending' == last_bld_obj.review_status and last_bld_obj.bnum != bld_obj.bnum:
                    last_bld_obj.review_status = 'skip'

            return True

        status.failed.rpush(bld_obj.bnum)
        return False

    def build_iso(self, pkg_obj):
        # TODO: Rework this, possibly abstract away parts in common with self.build_package()
        own_status = 'Building {0}-{1} with mkarchiso.'.format(pkg_obj.name,
                                                               self._pkgvers[pkg_obj.name])
        status.current_status = own_status
        status.iso_building = True

        bld_obj = self.process_and_save_build_metadata(pkg_obj=pkg_obj)
        build_id = bld_obj.bnum

        self.fetch_and_compile_translations(translations_for=["cnchi_updater", "antergos-gfxboot"])

        i686_flag = '/srv/antergos.info/repo/iso/testing/.ISO32'
        minimal = '/srv/antergos.info/repo/iso/testing/.MINIMAL'

        if 'i686' in pkg_obj.name:
            if not os.path.exists(i686_flag):
                open(i686_flag, 'a').close()
        else:
            if os.path.exists(i686_flag):
                os.remove(i686_flag)

        if 'minimal' in pkg_obj.name:
            if not os.path.exists(minimal):
                open(minimal, 'a').close()
        else:
            if os.path.exists(minimal):
                os.remove(minimal)

        in_dir_last = len([name for name in os.listdir('/srv/antergos.info/repo/iso/testing')])
        db.set('pkg_count_iso', in_dir_last)

        doc_util.do_docker_clean(pkg_obj.name)

        # Create docker host config dict
        hconfig = doc.create_host_config(privileged=True, cap_add=['ALL'],
                                         binds={
                                             '/opt/archlinux-mkarchiso':
                                                 {
                                                     'bind': '/start',
                                                     'ro': False
                                                 },
                                             '/run/dbus':
                                                 {
                                                     'bind': '/var/run/dbus',
                                                     'ro': False
                                                 },
                                             '/srv/antergos.info/repo/iso/testing':
                                                 {
                                                     'bind': '/out',
                                                     'ro': False
                                                 }},
                                         restart_policy={
                                             "MaximumRetryCount": 2,
                                             "Name": "on-failure"},
                                         mem_limit='2G',
                                         memswap_limit='-1')
        iso_container = {}
        try:
            iso_container = doc.create_container("antergos/mkarchiso", command='/start/run.sh',
                                                 name=pkg_obj.name, host_config=hconfig,
                                                 cpuset='0-3')
            if iso_container.get('Warnings', False):
                logger.error(iso_container.get('Warnings'))
        except Exception as err:
            logger.error('Create container failed. Error Msg: %s' % err)
            bld_obj.failed = True
            status.failed.append(bld_obj.bnum)
            return False

        bld_obj.container = iso_container.get('Id')
        status.container = bld_obj.container
        open('/opt/archlinux-mkarchiso/first-run', 'a').close()

        try:
            doc.start(bld_obj.container)
            cont = bld_obj.container
            stream_process = Process(target=bld_obj.publish_build_output)
            stream_process.start()
            result = doc.wait(cont)
            inspect = doc.inspect_container(cont)
            if result != 0:
                if inspect['State'].get('Restarting', '') or inspect.get('RestartCount', 0) != 2:
                    while inspect['State'].get('Restarting', '') or inspect.get('RestartCount',
                                                                                0) != 2:
                        time.sleep(5)
                        inspect = doc.inspect_container(cont)

            if inspect['State'].get('ExitCode', 1) == 1:
                bld_obj.failed = True
                logger.error('[CONTAINER EXIT CODE] Container %s exited. Return code was %s',
                             pkg_obj.name, result)
            else:
                bld_obj.completed = True
                logger.info('[CONTAINER EXIT CODE] Container %s exited. Return code was %s',
                            pkg_obj.name, result)
        except Exception as err:
            logger.error('Start container failed. Error Msg: %s', err)
            bld_obj.failed = True
            status.failed.append(bld_obj.bnum)
            return False

        stream_process.join()
        in_dir = len([name for name in os.listdir('/srv/antergos.info/repo/iso/testing')])
        last_count = int(db.get('pkg_count_iso'))

        if in_dir > last_count:
            bld_obj.completed = True
            tpl = 'Build <a href="/build/{0}">{0}</a> for <strong>{1}</strong> was successful.'
            tlmsg = tpl.format(build_id, pkg_obj.name)
            _ = get_timeline_object(msg=tlmsg, tl_type=4)
            status.completed.rpush(bld_obj.bnum)
        else:
            bld_obj.failed = True
            bld_obj.completed = False
            tpl = 'Build <a href="/build/{0}">{0}</a> for <strong>{1}</strong> failed.'
            tlmsg = tpl.format(build_id, pkg_obj.name)
            _ = get_timeline_object(msg=tlmsg, tl_type=5)
            status.failed.rpush(build_id)

        bld_obj.end_str = datetime.datetime.now().strftime("%m/%d/%Y %I:%M%p")

        if not bld_obj.failed:
            remove('/opt/archlinux-mkarchiso/antergos-iso')
            doc_util.do_docker_clean(pkg_obj.name)
            return True

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
