#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Convert NavigaTor measurements to JSON format.
"""

# Author: Robert Annessi <robert.annessi@nt.tuwien.ac.at>
# License: GPLv2 (2015-2016)

from cPickle import load
from json import dumps
from argparse import ArgumentParser
from os.path import exists

from r9 import Probestat


def _main():
    parser = ArgumentParser(description="")
    parser.add_argument("--input", type=str, required=True, help="Input file.")
    parser.add_argument("--output", type=str, required=True,
                        help="Output file.")
    args = parser.parse_args()
    assert exists(args.input), 'Non-existing input file.'
    assert not exists(args.output), 'Existing output file.'

    with open(args.input, 'r') as f:
        with open(args.output, 'w') as g:
            try:
                while True:
                    probe = load(f)
                    date = str(probe.date)
                    bw = None
                    if len(probe.bws) > 0 and isinstance(probe.bws[0], int):
                        bw = 5242880/(probe.bws[0]/1000.0)*8/1024/1024
                    g.write(dumps((date, probe.entry, probe.middle, probe.exit,
                                   probe.cbt, probe.cbtp, probe.cbtb,
                                   probe.rtts, probe.rttp, probe.rttb,
                                   probe.ttfbs, bw, probe.cong, probe.congp,
                                   probe.congb))+'\n')
            except EOFError:
                pass


if __name__ == '__main__':
    _main()
