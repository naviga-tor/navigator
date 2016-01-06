#!/usr/bin/env python
# -*- coding: utf-8 -*-

""" Merge and sort truncated measurement data files. """

# Author: Robert Annessi <robert.annessi@nt.tuwien.ac.at>
# License: GPLv2 (2014-2015)


import sys
from cPickle import load, dump, HIGHEST_PROTOCOL

from truncatedata import Probedata


if not len(sys.argv) > 1:
    print "Usage: %s truncprobefile1 [truncprobefile2 .. truncprobefileN]" % \
          sys.argv[0]
    sys.exit(1)


probes = []
for i in sys.argv:
    if i == sys.argv[0]:
        continue
    with open(i, 'r') as f:
        try:
            while True:
                probes.append(load(f))
        except EOFError:
            pass

# sort by date
probes.sort()

for probe in probes:
    dump(probe, sys.stdout, HIGHEST_PROTOCOL)
