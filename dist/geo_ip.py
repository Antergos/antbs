#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
#  geo_ip.py
#
#  Copyright © 2016 Antergos
#
#  This file is part of Antergos Build Server, (AntBS).
#
#  Poodle is free software; you can redistribute it and/or modify
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

# !/bin/python

import geoip2.database
import redis

db = redis.StrictRedis(unix_socket_path='/var/run/redis/redis.sock', decode_responses=True)

reader = geoip2.database.Reader('GeoLite2-Country.mmdb')

countries = dict()
counted = []

for key in db.scan_iter('antbs:cnchi:user:*'):
    ip = db.hget(key, 'ip')
    if ip and ip not in counted:
        counted.append(ip)
        response = reader.country(ip)
        country = response.country.name
        if country not in countries.keys():
            countries[country] = 0

        countries[country] += 1

print(countries)
