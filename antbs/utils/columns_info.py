#!/usr/bin/python
# -*- coding: utf-8 -*-
#
# columns_info.py
#
# Copyright © 2016 Antergos
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


class ColumnsInfo:
    """
    Utility class which holds columns info data for HTML tables.

    """

    def __init__(self, current_user):
        self.current_user = current_user

    @property
    def columns_info(self):
        columns_info = [
            {
                'heading_text': 'ID',
                'obj_attr': 'pkg_id',
                'content_type': 'link',
                'base_url': '/package/',
                'dd_info': ''
            },
            {
                'heading_text': 'Name',
                'obj_attr': 'pkgname',
                'content_type': 'link',
                'base_url': '/package/',
                'dd_info': ''
            },
            {
                'heading_text': 'Version',
                'obj_attr': 'version_str',
                'content_type': 'text',
                'base_url': '',
                'dd_info': ''
            },
            {
                'heading_text': 'Last Build',
                'obj_attr': '_build.bnum',
                'content_type': 'link',
                'base_url': '/build/',
                'dd_info': ''
            },
            {
                'heading_text': 'Review Status',
                'obj_attr': '_build.review_status',
                'content_type': 'text_with_icon',
                'icon_info': {
                    'class': {
                        'pending': 'clock-o',
                        'passed': 'check',
                        'failed': 'exclamation-circle',
                        'skip': 'eye-slash'
                    },
                    'color': {
                        'pending': '#F0DE10',
                        'passed': '#2CC36B',
                        'failed': '#EA6153',
                        'skip': '#999999'
                    }
                },
                'base_url': '',
                'dd_info': ''
            },
            {
                'heading_text': 'Reviewed By',
                'obj_attr': '_build.review_dev',
                'content_type': 'text_with_icon',
                'icon_info': {
                    'class': 'user',
                    'color': ''
                },
                'base_url': '',
                'dd_info': ''
            },
            {
                'heading_text': 'Reviewed On',
                'obj_attr': '_build.review_date',
                'content_type': 'text_with_icon',
                'icon_info': {
                    'class': 'calendar',
                    'color': ''
                },
                'base_url': '',
                'dd_info': ''
            }
        ]

        if self.current_user.is_authenticated:
            columns_info.append({
                'heading_text': 'Manage',
                'obj_attr': '',
                'content_type': 'dropdown',
                'base_url': '',
                'dd_info': {
                    'dd_type': 'manage',
                    'menu_items': [
                        {
                            'text': 'Build',
                            'icon_class': 'hammer',
                            'link_class': 'dd_manage',
                            'icon_color': '#3D566D'
                        },
                        {
                            'text': 'Remove From Repo',
                            'icon_class': 'cross',
                            'link_class': 'dd_remove',
                            'icon_color': '#EA6153'
                        }
                    ],
                    'dd_class': 'dd_manage'
                }
            })

        return columns_info
