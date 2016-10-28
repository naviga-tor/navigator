#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Calculate congestion delay for circuit.
"""

# Author: Robert Annessi <robert.annessi@nt.tuwien.ac.at>
# License: GPLv2 (2015-2016)


from cPickle import load
from argparse import ArgumentParser
from numpy import average
from os.path import exists
from cPickle import dump, HIGHEST_PROTOCOL
from collections import namedtuple


Probedata = namedtuple('Probedata', 'date entry middle exit cbt rtts perfs bws cong')


def _main():
    # found empirically by CAT authors
    gamma = 20

    # maximum number of congestion measurements for each relay
    # (from CAT authors)
    L = 20

    # initialization value (medmed congestion delay of nodes ~= 5)
    cong_init = 5

    relay_congestion = dict()

    parser = ArgumentParser(description="")
    parser.add_argument("--input", type=str, required=True,
                        help="Input file.")
    parser.add_argument("--output", type=str, required=True,
                        help="Output file.")
    args = parser.parse_args()
    assert exists(args.input), 'Invalid input file.'
    assert not exists(args.output), 'Invalid output file.'

    probes = []
    with open(args.input, 'r') as f:
        while True:
            try:
                probes.append(load(f))
            except EOFError:
                break

    with open(args.output, 'w') as f:
        for probe in probes:
            # Check type and number of RTT measurements
            rtts = [rtt for rtt in probe.rtts if isinstance(rtt, int)]
            if len(rtts) != 5:
                probedata = Probedata(date=probe.date, entry=probe.entry,
                                      middle=probe.middle, exit=probe.exit,
                                      cbt=probe.cbt, rtts=probe.rtts,
                                      perfs=probe.perfs, bws=probe.bws,
                                      cong=None)
                dump(probedata, f, HIGHEST_PROTOCOL)
                continue

            # Create congestion entry for relay if it does not exist yet.
            for i in probe.entry, probe.middle, probe.exit:
                if i not in relay_congestion:
                    relay_congestion[i] = [cong_init]

            # Get reference value and remove it from the list for comparison
            t_min = min(rtts)
            rtts.remove(t_min)

            for rtt in rtts:
                # calculate congestion delay for each node
                T_c = rtt - t_min + gamma
                t_c_1 = T_c * 2 * average(relay_congestion[probe.entry]) / (2 * average(relay_congestion[probe.entry]) + 2 * average(relay_congestion[probe.middle]) + average(relay_congestion[probe.exit]))
                t_c_2 = T_c * 2 * average(relay_congestion[probe.middle]) / (2 * average(relay_congestion[probe.entry]) + 2 * average(relay_congestion[probe.middle]) + average(relay_congestion[probe.exit]))
                t_c_3 = T_c * average(relay_congestion[probe.exit]) / (2 * average(relay_congestion[probe.entry]) + 2 * average(relay_congestion[probe.middle]) + average(relay_congestion[probe.exit]))

                # delete initialization value if still present
                for i in probe.entry, probe.middle, probe.exit:
                    if len(relay_congestion[i]) == 1 and relay_congestion[i][0] == cong_init:
                        del relay_congestion[i][0]

                # add new congestion delays to nodes and delete oldest value
                # if max number of measurements is reached
                relay_congestion[probe.entry].append(t_c_1)
                if len(relay_congestion[probe.entry]) > L:
                    del relay_congestion[probe.entry][0]
                relay_congestion[probe.middle].append(t_c_2)
                if len(relay_congestion[probe.middle]) > L:
                    del relay_congestion[probe.middle][0]
                relay_congestion[probe.exit].append(t_c_3)
                if len(relay_congestion[probe.exit]) > L:
                    del relay_congestion[probe.exit][0]

            # calculate congestion delay for circuit
            congestion = average(relay_congestion[probe.entry]) + average(relay_congestion[probe.middle]) + average(relay_congestion[probe.exit])
            probedata = Probedata(date=probe.date, entry=probe.entry,
                                  middle=probe.middle, exit=probe.exit,
                                  cbt=probe.cbt, rtts=probe.rtts,
                                  perfs=probe.perfs, bws=probe.bws,
                                  cong=int(round(congestion)))
            dump(probedata, f, HIGHEST_PROTOCOL)


if __name__ == '__main__':
    _main()
