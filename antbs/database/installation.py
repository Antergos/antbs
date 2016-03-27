#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
#  installation.py
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


import datetime

from database.base_objects import RedisHash, db
from utils.utilities import DateTimeStrings

next_install_id_key = 'cnchi:misc:next_install_id'

if not db.exists(next_install_id_key):
    db.set(next_install_id_key, 0)


class AntergosInstallation(RedisHash, DateTimeStrings):

    def __init__(self, namespace='cnchi', prefix='install', install_id='',
                 ip=None, *args, **kwargs):
        if not install_id and not ip:
            raise ValueError('ip is required to initialize this class')

        if not install_id:
            install_id = self.db.incr(next_install_id_key)

        super().__init__(namespace=namespace, prefix=prefix, key=install_id, *args, **kwargs)

        self.key_lists.update(dict(string=['install_id', 'ip_address', 'start_date', 'start_time',
                                           'start_str', 'end_date', 'end_time', 'end_str'],
                                   bool=['completed']))

        self.__namespaceinit__()

        if not self or not self.install_id:
            if not ip:
                raise ValueError('ip required to create a new installation object')
            self.__keysinit__()
            self.install_id = install_id
            self.ip_address = ip
            dt = datetime.datetime.now()
            self.start_date = self.dt_date_to_string(dt)
            self.start_time = self.dt_time_to_string(dt)
            self.start_str = self.dt_to_string(dt)

    def set_installation_ended(self):
        dt = datetime.datetime.now()
        self.end_date = self.dt_date_to_string(dt)
        self.end_time = self.dt_time_to_string(dt)
        self.end_str = self.dt_to_string(dt)


class AntergosInstallationUser(RedisHash):

    def __init__(self, ip=None, install_id=None, namespace='cnchi',
                 prefix='user', *args, **kwargs):

        super().__init__(namespace=namespace, prefix=prefix, key=ip, *args, **kwargs)

        self.key_lists.update(dict(string=['ip_address', 'country'],
                                   set=['installs', 'installs_completed', 'installs_failed']))

        self.__namespaceinit__()

        if not self or not self.ip_address:
            if not ip:
                raise ValueError('ip required to create a new InstallationUser object')

            self.__keysinit__()
            self.ip_address = ip

        if install_id:
            self.installs.add(install_id)
