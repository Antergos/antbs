#!/bin/python

import pexpect
import sys

child = pexpect.spawnu('/usr/bin/makepkg',
                       ['-smfL', '--noconfirm', '--noprogressbar', '--asroot', '--sign', '--needed'],
                       cwd='/pkg')
child.logfile = sys.stdout
child.waitnoecho(timeout=1800)
# child.expect('Passphrase:', timeout=1800)
child.sendline('RanDom!')
child.expect(pexpect.EOF)
