#!/usr/bin/python
# -*- coding: utf-8 -*-
#
# transaction_handler.py
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


""" Bot utilities for Slack """

from database.base_objects import db
from github3 import login
from stackexchange import DESC, Site, Sort, StackOverflow

from .logging_config import logger

se_key = db.get('SLACK:API-KEY')
gh_user = db.get('ANTBS_GITHUB_TOKEN')

so = Site(StackOverflow, se_key)
gh = login(token=gh_user)

MAX_QUESTIONS = 5


def get_response_string(q):
    """

    :param q:
    :return:
    """
    q_data = q.json

    check = ' :white_check_mark:' if q.json['is_answered'] else ''
    return "|%d|%s <%s|%s> (%d answers)" % (q_data['score'], check, q.url,
                                            q.title, q_data['answer_count'])


def overflow(command=None, text=None):
    """

    :param command:
    :param text:
    :return:
    """
    if command is None or text is None:
        return False

    if '/overflow' == command:

        try:
            qs = so.search(intitle=text, sort=Sort.Votes, order=DESC)
        except UnicodeEncodeError:
            return dict(msg='Only English language is supported. %s is not valid input.' % text,
                        content_type='text/plain; charset=utf-8')

        resp_qs = ['Stack Overflow Top Questions for "%s"\n' % text]
        resp_qs.extend(map(get_response_string, qs[:MAX_QUESTIONS]))

        if len(resp_qs) is 1:
            resp_qs.append(('No questions found. Please try a broader search or '
                            'search directly on '
                            '<https://stackoverflow.com|StackOverflow>.'))

        res = dict(msg='\n'.join(resp_qs), content_type='text/plain; charset=utf-8')

    elif '/todo' == command:

        repo = gh.repository('lots0logs', 'compi')
        res = repo.issues(labels='feature', state='open')
        issues = []
        for i in res:
            issue_str = ':slack: <%s|%s>' % (i.html_url, i.title)
            issues.append(issue_str)

        logger.info(issues)

        resp_qs = []
        resp_qs.extend(issues)
        resp_qs.reverse()
        resp_qs.insert(0, '*Feature Roadmap For Compi*\n')

        res = dict(msg='\n'.join(resp_qs), content_type='text/plain; charset=utf-8')

    return res
