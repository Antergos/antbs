#!/usr/bin/env python
#  -*- coding: utf-8 -*-
#
#  live.py
#
#  Copyright © 2016-2017 Antergos
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

from . import *


class LiveView(FlaskView):
    route_base = '/live'

    def index(self):
        return self.get()

    def get(self, bnum=None):
        bld_objs = {}
        selected = dict(bnum=0, pkgname='', version='', start='', container='')

        if bnum and bnum not in status.now_building:
            abort(400)

        if status.now_building and not status.idle:
            try:
                bld_objs = {b: get_build_object(bnum=b) for b in status.now_building if b}
            except Exception as err:
                logger.error(err)
                abort(500)

            if not bnum or bnum not in bld_objs:
                bnum = sorted(bld_objs.keys())[0]

            selected = dict(bnum=bnum, pkgname=bld_objs[bnum].pkgname,
                            version=bld_objs[bnum].version_str, start=bld_objs[bnum].start_str,
                            container=bld_objs[bnum].container)

        return try_render_template('building.html', bld_objs=bld_objs, selected=selected)
