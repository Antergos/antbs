#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# auth0.py
#
# Copyright © 2017 Antergos
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

import requests

from views import *


class Auth0View(FlaskView):
    route_base = '/auth'

    @route('/auth0/callback')
    def callback(self):
        token_url = 'https://{0}/oauth/token'.format(status.auth0_domain)

        token_payload = {
            'client_id': status.auth0_id,
            'client_secret': status.auth0_secret,
            'redirect_uri': url_for('Auth0View:callback', _external=True),
            'code': request.args.get('code'),
            'grant_type': 'authorization_code',
        }

        token_info = requests.post(token_url, json=token_payload).json()

        user_url = 'https://{0}/userinfo?access_token={1}'.format(
            status.auth0_domain,
            token_info['access_token']
        )

        user_info = requests.get(user_url).json()

        if user_info:
            is_authenticated = user_info['app_metadata'].get('antbs', False) is True
            user = dict(username=user_info['nickname'], is_authenticated=is_authenticated)
            if 'user' in session:
                status.logger.debug(session['user'])
            session['user'] = user
            session.permanent = is_authenticated

            return redirect('/builds/completed')

        return redirect('/')

    @route('/login')
    def login(self):
        return try_render_template(
            'admin/login.html',
            auth0_id=status.auth0_id,
            auth0_domain=status.auth0_domain
        )
