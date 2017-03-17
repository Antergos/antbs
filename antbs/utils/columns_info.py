#!/usr/bin/python
# -*- coding: utf-8 -*-
#
# columns_info.py
#
# Copyright © 2016-2017 Antergos
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

    def __init__(self, current_user, request, _filter=None, filter_by=None):
        self.current_user = current_user
        self.request = request
        self.filter = _filter
        self.filter_by = filter_by

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
                'content_type': 'link_with_icon',
                'icon_info': {
                    'class': {
                        'completed': 'check',
                        'failed': 'times',
                    },
                    'color':  {
                        'completed': '#2CC36B',
                        'failed': '#EA6153',
                    },
                },
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

        if self.current_user.is_authenticated and '/packages' in self.request.path:
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

        if 'group' == self.filter:
            columns_info.insert(3, {
                'heading_text': 'Group',
                'obj_attr': '',
                'content_type': 'label_tag_link',
                'base_url': self.request.path,
                'dd_info': '',
                'color_class': 'info',
                'group': self.filter_by
            })

        elif '/monitored' in self.request.path:
            columns_info.insert(3, {
                'heading_text': 'Service',
                'obj_attr': 'mon_service',
                'content_type': 'label_tag_link',
                'base_url': '',
                'dd_info': '',
                'color_class': 'default'
            })
            columns_info.insert(4, {
                'heading_text': 'Type',
                'obj_attr': 'mon_type',
                'content_type': 'text',
                'dd_info': ''
            })
            columns_info.insert(5, {
                'heading_text': 'Repo',
                'obj_attr': ['mon_project', 'mon_repo'],
                'content_type': 'link',
                'base_url': 'https://github.com/',
                'dd_info': '',
                'color_class': 'default'
            })
            columns_info.insert(6, {
                'heading_text': 'Last Checked',
                'obj_attr': 'mon_last_checked',
                'content_type': 'text_with_icon',
                'icon_info': {
                    'class': 'calendar',
                    'color': ''
                },
                'dd_info': ''
            })
            columns_info.insert(7, {
                'heading_text': 'Last Result',
                'obj_attr': 'mon_last_result',
                'content_type': 'text',
                'dd_info': ''
            })

            columns_info = columns_info[:-3]

        return columns_info

    @staticmethod
    def get_new_icon_info_dict():
        return {'class': {}, 'color': {}}

    def get_repo_monitor_services_icons_info(self):
        icon_info = self.get_new_icon_info_dict()
        icon_info['class']['GitHub'] = 'github'
        icon_info['color']['GitHub'] = '#000000'
        icon_info['class']['Gitlab'] = 'gitlab'
        icon_info['color']['Gitlab'] = '#ffffff'

        return icon_info

