#!/usr/bin/env python
#  -*- coding: utf-8 -*-
#
#  api.py
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

EMPTY_RESPONSE = json.dumps({})


class APIView(FlaskView):
    route_base = '/api'

    def _get_live_build_output(self, bnum):
        psub = db.pubsub()
        psub.subscribe('live:build_output:{0}'.format(bnum))
        last_line_key = 'tmp:build_log_last_line:{0}'.format(bnum)
        first_run = True
        keep_alive = 0

        while True:
            message = psub.get_message()

            if message:
                if first_run:
                    message['data'] = db.get(last_line_key)
                    first_run = False

                if message['data'] not in ['1', 1]:
                    tpl = 'event: build_output\ndata: {0}\n\n'
                    yield tpl.format(message['data']).encode('UTF-8')

            elif keep_alive > 560:
                keep_alive = 0
                yield ':'.encode('UTF-8')

            keep_alive += 1
            gevent.sleep(.05)

        psub.close()

    def _get_live_status_updates(self):
        last_event = None
        keep_alive = 0

        while True:
            if status.idle and 'Idle' != last_event:
                last_event = 'Idle'
                yield 'event: status\ndata: {0}\n\n'.format('Idle').encode('UTF-8')
            elif not status.idle and status.current_status != last_event:
                last_event = status.current_status
                yield 'event: status\ndata: {0}\n\n'.format(status.current_status).encode('UTF-8')
            elif keep_alive > 15:
                keep_alive = 0
                yield ':'.encode('UTF-8')

            keep_alive += 1
            gevent.sleep(1)

    def _set_pkg_review_result(self, bnum=False, dev=False, result=False):
        # TODO: Simplify this by splitting into multiple methods.
        if not all([bnum, dev, result]):
            err = 'all args required!'
            logger.error(err)
            return dict(error=True, msg=err)

        errmsg = dict(error=True, msg=None)
        dt = datetime.now().strftime("%m/%d/%Y %I:%M%p")

        bld_obj = get_build_object(bnum=bnum)
        pkg_obj = get_pkg_object(name=bld_obj.pkgname)

        if not pkg_obj or not bld_obj:
            err = 'Cant move packages to main repo without pkg_obj and bld_obj!.'
            logger.error(err)
            return dict(error=True, msg=err)

        if 'passed' == result and 'main' not in pkg_obj.allowed_in:
            # msg = '{0} is not allowed in main repo.'.format(pkg_obj.pkgname)
            # return dict(error=True, msg=msg)
            pkg_obj.allowed_in.append('main')

        bld_obj.review_dev = dev
        bld_obj.review_date = dt
        bld_obj.review_status = result

        if result == 'skip':
            return dict(error=False, msg=None)

        file_count = len(bld_obj.staging_files) or len(bld_obj.generated_files)
        fnames = []

        files_exist = bld_obj.staging_files and all_file_paths_exist(bld_obj.staging_files)

        if not result or not files_exist or not (file_count % 2 == 0):
            err = 'While moving to main, invalid number of files found.'
            logger.error(err)
            return dict(error=True, msg=err)

        for pkg_file in bld_obj.staging_files:
            if 'i686' in pkg_file:
                continue

            if 'passed' == result:
                fname = os.path.basename(pkg_file)

                copy_or_symlink(pkg_file, status.MAIN_64, logger)

                if '-any.pkg' in pkg_file:
                    src = os.path.basename(pkg_file)
                    dst = '../i686/{}'.format(fname)

                    success, res = try_run_command(
                        ['/bin/ln', '-srf', src, dst],
                        cwd=status.MAIN_64,
                        logger=logger
                    )
                    if not success:
                        logger.error(res)

            remove(pkg_file)

        for pkg_file in bld_obj.staging_files:
            if 'x86_64' in pkg_file or '-any.pkg' in pkg_file:
                continue

            if 'passed' == result:
                copy_or_symlink(pkg_file, status.MAIN_32, logger)

            remove(pkg_file)

        repo_queue.enqueue_call(update_repo_databases, timeout=9600)
        errmsg = dict(error=False, msg=None)

        return errmsg

    @route('/build_pkg_now', methods=['POST', 'GET'])
    @auth_required
    def build_pkg_now(self):
        if request.method == 'POST':
            pkgnames = request.form['pkgname']
            dev = request.form['dev']
            names = []

            if not pkgnames:
                return EMPTY_RESPONSE, 500
            elif ',' in pkgnames:
                names = pkgnames.split(',')
            else:
                names = [pkgnames]

            pkgnames = []

            for name in names:
                if name not in status.all_packages and name in status.package_groups:
                    pkgnames.extend(get_group_packages(names))
                else:
                    pkgnames.extend([name])

            if pkgnames:
                if '-x86_64' in pkgnames[0] or '-i686' in pkgnames[0]:
                    status.iso_flag = True
                    status.iso_minimal = 'minimal' in pkgnames[0]

                if 'cnchi-dev' == pkgnames[0]:
                    db.set('CNCHI-DEV-OVERRIDE', True)

                trans = get_trans_object(packages=list(set(pkgnames)), repo_queue=repo_queue)
                status.transaction_queue.rpush(trans.tnum)
                transaction_queue.enqueue_call(handle_hook, timeout=84600)
                get_timeline_object(
                    msg='<strong>%s</strong> added <strong>%s</strong> to the build queue.' % (
                        dev, ' '.join(pkgnames)), tl_type=0)
            else:
                flash(
                    'Package not found. Has the PKGBUILD been pushed to github?',
                    category='error'
                )

        return redirect(redirect_url())

    @route('/ajax', methods=['GET', 'POST'])
    @auth_required
    def ajax(self):
        if not current_user.is_authenticated:
            return EMPTY_RESPONSE, 403

        iso_release = bool(request.args.get('do_iso_release', False))
        reset_queue = bool(request.args.get('reset_build_queue', False))
        rerun_transaction = int(request.args.get('rerun_transaction', 0))
        update_repos = bool(request.args.get('update_repos', False))
        message = dict(msg='Ok')

        if iso_release:
            transaction_queue.enqueue_call(iso_release_job)

        elif reset_queue:
            if transaction_queue.count > 0:
                transaction_queue.empty()
            if repo_queue.count > 0:
                repo_queue.empty()
            items = len(status.transaction_queue)
            if items > 0:
                for item in range(items):
                    popped = status.transaction_queue.rpop()
                    logger.debug(popped)
            status.idle = True
            status.current_status = 'Idle.'

        elif rerun_transaction:
            event = get_timeline_object(event_id=rerun_transaction)
            pkgs = event.packages
            old_tobj = None

            if event.tnum:
                old_tobj = get_trans_object(tnum=event.tnum)

            if pkgs:
                _ = {}
                for pkg in pkgs:
                    _[pkg] = get_pkg_object(pkg, fetch_pkgbuild=True)
                tobj = get_trans_object(pkgs, repo_queue=repo_queue)

                if old_tobj:
                    tobj.gh_sha_before, tobj.gh_sha_after = old_tobj.gh_sha_before, old_tobj.gh_sha_after

                status.transaction_queue.rpush(tobj.tnum)
                transaction_queue.enqueue_call(handle_hook, timeout=84600)

        elif update_repos:
            repo_queue.enqueue_call(update_repo_databases, timeout=9600)

        return json.dumps(message)

    @route('/get_log')
    @route("/get_log/<int:bnum>")
    def get_log(self, bnum=None):
        if status.idle or not status.now_building:
            return EMPTY_RESPONSE, 204

        if not bnum:
            bnum = status.now_building[0]

        headers = {
            'Content-Type': 'text/event-stream',
            'Cache-Control': 'no-cache',
        }

        return Response(
            self._get_live_build_output(bnum),
            direct_passthrough=True,
            mimetype='text/event-stream',
            headers=headers
        )

    @route('/get_status')
    def get_status(self):
        headers = {
            'Content-Type': 'text/event-stream',
            'Cache-Control': 'no-cache',
        }
        return Response(
            self._get_live_status_updates(),
            direct_passthrough=True,
            mimetype='text/event-stream',
            headers=headers
        )

    @route('/hook', methods=['POST', 'GET'])
    def hook(self):
        hook = Webhook(request)

        if isinstance(hook.result, int):
            abort(hook.result)

        return json.dumps(hook.result)

    @route('/ajax/pkg_review', methods=['POST'])
    @auth_required
    def pkg_review(self):
        payload = json.loads(request.data.decode('utf-8'))
        bnum = payload['bnum']
        dev = payload['dev']
        result = payload['result']

        if len([x for x in (bnum, dev, result) if x]) == 3:
            set_review = self._set_pkg_review_result(bnum, dev, result)

            if set_review.get('error', False):
                set_rev_error = set_review.get('msg')
                message = dict(msg=set_rev_error)
                return json.dumps(message)
            else:
                message = dict(msg='ok')
                return json.dumps(message)

    @route('/package/<pkgname>', methods=['POST'])
    @auth_required
    def update_package_meta(self, pkgname):
        if not pkgname or not pkgname.isalpha() or pkgname not in status.all_packages:
            abort(400)

        payload = request.form
        pkg_obj = get_pkg_object(pkgname)
        to_update = {
            attr: (getattr(pkg_obj, attr), value)
            for attr, value in payload.items()
            if hasattr(pkg_obj, attr)
        }

        if not to_update:
            abort(400)

        if pkg_obj.update_pkgbuild_and_push_github(to_update):
            for attr, value in payload.items():
                setattr(pkg_obj, attr, value)

            return json.dumps(dict(msg='ok'))

        else:
            abort(500)
