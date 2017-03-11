#!/usr/bin/env python
#  -*- coding: utf-8 -*-
#
#  pkgbuild.py
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

import os
from datetime import datetime


class Pkgbuild:
    """
    Utility class for parsing PKGBUILDs.

    Args:
        (str) contents: The content to be parsed.

    Attributes:
        (str) contents: See Args.
    """

    def __init__(self, contents):
        if not contents:
            raise ValueError('contents cannot be Falsey.')

        self.contents = contents
        self.values = {}

        self.key_lists = dict(
            string=['pkgver', 'pkgrel', 'epoch', 'pkgdesc', 'url', 'install', 'changelog',
                    '_pkgver', '_buildver', '_is_monitored', '_monitored_service',
                    '_monitored_type', '_monitored_repo', '_monitored_project',
                    '_monitored_match_pattern', '_monitored_version_pattern',
                    '_monitored_file_url', '_monitored_version_url', '_auto_sum', '_autosums'],

            list=['pkgname', 'license', 'source', 'groups', 'arch', 'backup', 'depends',
                  'makedepends', 'checkdepends', 'optdepends', 'conflicts', 'provides',
                  'replaces', '_allowed_in', 'sha1sums', 'md5sums']
        )

        self.all_keys = [item for sublist in self.key_lists.values() for item in sublist]
        self.in_array = False
        self.array_values = []
        self.current_key = ''
        self.current_value = ''

    def get_value(self, key):
        if key in self.values and self.values[key] and 'None' not in self.values[key]:
            return self.values[key]

        self.current_key = key
        lines = self.get_line_with_current_key()
        line = next(lines)
        self.current_key, self.current_value = line.split('=', 1)

        if ' ' == self.current_key:
            return ''

        if self.current_key in self.key_lists['list']:
            self.process_list_value()

            if self.in_array:
                while self.in_array:
                    lines.send(True)
                    line = next(lines)
                    self.current_value = line
                    self.process_list_value()

        elif self.current_key in self.contents:
            self.process_string_value()

        self.reset_parser_attributes()

        return self.values[key]

    def parse_contents(self):
        _all_keys = [k for k in self.all_keys if k in self.contents]

        for line in self.contents.splitlines():
            if not _all_keys:
                break
            elif not line or line.startswith('#'):
                continue

            line = line.strip()

            if self.in_array:
                # We're in a multi-line array
                self.current_value = line
                self.process_list_value()
                continue

            if '=' not in line:
                continue

            self.current_key, self.current_value = line.split('=', 1)

            if self.current_key not in _all_keys:
                continue

            if self.current_key in self.key_lists['string']:
                self.process_string_value()
                _all_keys.remove(self.current_key)
                continue

            if self.current_key in self.key_lists['list']:
                self.process_list_value()

                if not self.in_array:
                    _all_keys.remove(self.current_key)

        self.maybe_fix_pkgver()
        self.reset_parser_attributes()

    def process_string_value(self):
        val = self.current_value.strip("'\"")
        self.values[self.current_key] = val if val and 'None' not in val else ''

    def process_list_value(self):
        self.maybe_toggle_in_array_status()

        vals = [i.strip("'\"") for i in self.current_value.strip('()').split(' ')]

        if vals:
            self.array_values.extend(vals)

        if not self.in_array:
            # We just processed either a single-line array or the last line in a multi-line array.
            self.values[self.current_key] = self.array_values or []
            self.array_values = []

    def maybe_toggle_in_array_status(self):
        if not self.in_array and self.current_value.startswith('('):
            # Current line has the start of an array (it might also have the end of the array)
            self.in_array = True
        if self.in_array and self.current_value.endswith(')'):
            # Current line has the end of an array.
            self.in_array = False

    def maybe_fix_pkgver(self):
        if 'pkgver' not in self.values:
            self.get_value('pkgver')
        if 'pkgver' not in self.values:
            self.get_value('_pkgver')
            self.values['pkgver'] = self.values['_pkgver']
        if '$' not in self.values['pkgver']:
            return

        if '_buildver' in self.values['pkgver']:
            pkgver = '{0}.{1}'.format(self.values['_pkgver'], self.values['_buildver'])
        elif '_pkgver' in self.values['pkgver']:
            pkgver = self.values['_pkgver']
        elif 'date' in self.values['pkgver']:
            pkgver = datetime.now().strftime('%y.%-m')
        else:
            pkgver = self.values['pkgver'] if 'None' not in self.values['pkgver'] else ''

        self.values['pkgver'] = pkgver

    def reset_parser_attributes(self):
        self.in_array = False
        self.array_values = []
        self.current_key = ''
        self.current_value = ''

    def get_line_with_current_key(self, get_next_line=False):
        for line in self.contents.splitlines():
            if get_next_line or (line.startswith(self.current_key) and '=' in line):
                get_next_line = yield line.strip()
        else:
            yield ' = '

    @staticmethod
    def get_generates(result_dir):
        generates = os.path.join(result_dir, 'generates')

        with open(generates, 'r') as output:
            contents = output.readlines()
            pkgs = [p.strip() for p in contents if p]

        return pkgs
