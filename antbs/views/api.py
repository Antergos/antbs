#!/usr/bin/env python
#  -*- coding: utf-8 -*-
#
#  api.py
#
#  Copyright © 2016  Antergos
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

from views import *

api_view = Blueprint('api', __name__)


###
##
#   Utility Functions For This View
##
###

def get_live_build_output(bnum):
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
                yield 'event: build_output\ndata: {0}\n\n'.format(message['data']).encode('UTF-8')

        elif keep_alive > 560:
            keep_alive = 0
            yield ':'.encode('UTF-8')

        keep_alive += 1
        gevent.sleep(.05)

    psub.close()


def get_live_status_updates():
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


def set_pkg_review_result(bnum=False, dev=False, result=False):
    if not any([bnum, dev, result]):
        abort(500)

    errmsg = dict(error=True, msg=None)
    dt = datetime.now().strftime("%m/%d/%Y %I:%M%p")

    try:
        bld_obj = get_build_object(bnum=bnum)
        pkg_obj = get_pkg_object(name=bld_obj.pkgname)

        if not pkg_obj and not bld_obj:
            err = 'Cant move packages to main repo without pkg_obj and bld_obj!.'
            logger.error(err)
            return dict(error=True, msg=err)

        if 'main' not in pkg_obj.allowed_in and result == 'passed':
            msg = '{0} is not allowed in main repo.'.format(pkg_obj.pkgname)
            errmsg.update(error=True, msg=msg)
            return errmsg
        else:
            bld_obj.review_dev = dev
            bld_obj.review_date = dt
            bld_obj.review_status = result

        if result == 'skip' or 'mate' in pkg_obj.groups:
            errmsg = dict(error=False, msg=None)
            return errmsg

        glob_string_64 = '{0}/**/{1}**'.format(status.STAGING_64, pkg_obj.filename_str)
        glob_string_32 = '{0}/**/{1}**'.format(status.STAGING_32, pkg_obj.filename_str)
        pkg_files_64 = glob(glob_string_64, recursive=True)
        pkg_files_32 = glob(glob_string_32, recursive=True)
        pkg_files = pkg_files_64 + pkg_files_32

        if pkg_obj.is_split_package and pkg_obj.split_packages:
            for split_pkg in pkg_obj.split_packages:
                fname = pkg_obj.filename_str.replace(pkg_obj.pkgname, split_pkg)
                glob_string_64 = '{0}/**/{1}**'.format(status.STAGING_64, fname)
                glob_string_32 = '{0}/**/{1}**'.format(status.STAGING_32, fname)
                pkg_files.extend(glob(glob_string_64, recursive=True))
                pkg_files.extend(glob(glob_string_32, recursive=True))

        if not pkg_files or not result:
            err = 'While moving to main, no packages were found to move.'
            logger.error(err)
            return dict(error=True, msg=err)

        for f in pkg_files_64:
            if result == 'passed':
                copy_or_symlink(f, status.MAIN_64)
                copy_or_symlink(f, '/tmp')

                if '-any.pkg' in f:
                    fname = os.path.basename(f)
                    linkto = os.path.join(status.MAIN_64, fname)
                    link_from = os.path.join(status.MAIN_32, fname)

                    symlink(linkto, link_from)

            if result != 'skip':
                remove(f)

        for f in pkg_files_32:
            if result == 'passed' and '-any.pkg' not in f:
                copy_or_symlink(f, status.MAIN_32)
                copy_or_symlink(f, '/tmp')

            if result != 'skip':
                remove(f)

        if result != 'skip':
            repo_queue.enqueue_call(process_dev_review, args=(bld_obj.bnum,), timeout=9600)
            errmsg = dict(error=False, msg=None)

    except (OSError, Exception) as err:
        logger.error('Error while moving to main: %s', err)
        err = str(err)
        errmsg = dict(error=True, msg=err)

    return errmsg


###
##
#   Views Start Here
##
###

@api_view.route('/hook', methods=['POST', 'GET'])
def receive_webhook():
    hook = Webhook(request)
    if hook.result is int:
        abort(hook.result)
    else:
        return json.dumps(hook.result)


@api_view.route('/get_log')
@api_view.route("/get_log/<int:bnum>")
def get_build_log_stream(bnum=None):
    if status.idle or not status.now_building:
        abort(404)

    if not bnum:
        bnum = status.now_building[0]

    if not bnum:
        abort(404)

    headers = {
        'Content-Type': 'text/event-stream',
        'Cache-Control': 'no-cache',
    }
    return Response(get_live_build_output(bnum), direct_passthrough=True,
                    mimetype='text/event-stream', headers=headers)


@api_view.route('/build_pkg_now', methods=['POST', 'GET'])
@groups_required(['admin'])
def build_pkg_now():
    if request.method == 'POST':
        pkg_obj = None
        pkgname = request.form['pkgname']
        dev = request.form['dev']

        if not pkgname:
            abort(500)

        try:
            pkg_obj = get_pkg_object(pkgname, fetch_pkgbuild=True)
        except Exception as err:
            logger.error(err)

        if pkg_obj:
            rev_pending = []
            pending = False

            for bnum in rev_pending:
                bld_obj = get_build_object(bnum=bnum)
                if bld_obj and pkg_obj.pkgname == bld_obj.pkgname:
                    pending = True
                    break

            if pending:
                flash('Unable to build %s because it is in "pending review" status.' % pkgname,
                      category='error')
            else:
                if '-x86_64' in pkg_obj.pkgname or '-i686' in pkg_obj.pkgname:
                    status.iso_flag = True
                    if 'minimal' in pkgname:
                        status.iso_minimal = True
                    else:
                        status.iso_minimal = False

                if 'cnchi-dev' == pkgname:
                    db.set('CNCHI-DEV-OVERRIDE', True)

                trans = get_trans_object(packages=[pkgname], repo_queue=repo_queue)
                status.transaction_queue.rpush(trans.tnum)
                transaction_queue.enqueue_call(handle_hook, timeout=84600)
                get_timeline_object(
                    msg='<strong>%s</strong> added <strong>%s</strong> to the build queue.' % (
                        dev, pkgname), tl_type='0')
        else:
            flash('Package not found. Has the PKGBUILD been pushed to github?', category='error')

    return redirect(redirect_url())


@api_view.route('/get_status', methods=['GET'])
@api_view.route('/ajax', methods=['GET', 'POST'])
def live_status_updates():
    if 'get_status' in request.path:
        headers = {
            'Content-Type': 'text/event-stream',
            'Cache-Control': 'no-cache',
        }
        return Response(get_live_status_updates(), direct_passthrough=True,
                        mimetype='text/event-stream', headers=headers)

    if not current_user.is_authenticated:
        abort(403)

    iso_release = bool(request.args.get('do_iso_release', False))
    reset_queue = bool(request.args.get('reset_build_queue', False))
    rerun_transaction = int(request.args.get('rerun_transaction', 0))
    message = dict(msg='Ok')

    # if request.method == 'POST':
    #     payload = json.loads(request.data.decode('UTF-8'))
    #     pkg = payload.get('pkg', None)
    #     dev = payload.get('dev', None)
    #     action = payload.get('result', None)
    #
    #     if all(i is not None for i in (pkg, dev, action)):
    #         if action in ['remove']:
    #             repo_queue.enqueue_call(
    #                 transaction_handler.update_main_repo(is_action=True, action=action,
    #                                                      action_pkg=pkg))
    #         elif 'rebuild' == action:
    #             trans_obj = get_trans_object([pkg], repo_queue=repo_queue)
    #             status.transaction_queue.rpush(trans_obj.tnum)
    #             transaction_queue.enqueue_call(transaction_handler.handle_hook, timeout=84600)
    #             get_timeline_object(
    #                 msg='<strong>%s</strong> added <strong>%s</strong> to the build queue.' % (
    #                     dev, pkg), tl_type='0')
    #         return json.dumps(message)

    if iso_release and current_user.is_authenticated:
        transaction_queue.enqueue_call(iso.iso_release_job)
        return json.dumps(message)

    elif reset_queue and current_user.is_authenticated:
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
        return json.dumps(message)

    elif rerun_transaction and current_user.is_authenticated:
        event = get_timeline_object(event_id=rerun_transaction)
        pkgs = event.packages
        if pkgs:
            _ = {}
            for pkg in pkgs:
                _[pkg] = get_pkg_object(pkg, fetch_pkgbuild=True)
            trans_obj = get_trans_object(pkgs, repo_queue=repo_queue)
            status.transaction_queue.rpush(trans_obj.tnum)
            transaction_queue.enqueue_call(handle_hook, timeout=84600)
        return json.dumps(message)


@api_view.route('/ajax/pkg_review', methods=['POST'])
@groups_required(['admin'])
def dev_package_review():
    payload = json.loads(request.data.decode('utf-8'))
    bnum = payload['bnum']
    dev = payload['dev']
    result = payload['result']
    if len([x for x in (bnum, dev, result) if x]) == 3:
        logger.debug('fired!')
        set_review = set_pkg_review_result(bnum, dev, result)
        if set_review.get('error'):
            set_rev_error = set_review.get('msg')
            message = dict(msg=set_rev_error)
            return json.dumps(message)
        else:
            message = dict(msg='ok')
            return json.dumps(message)
