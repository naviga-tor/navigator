#!/usr/bin/env python
# -*- coding: utf-8 -*-

""" Truncate data from NavigaTor measurements. """

# Author: Robert Annessi <robert.annessi@nt.tuwien.ac.at>
# License: GPLv2 (2013-2015)

import sys
from cPickle import loads, dump, HIGHEST_PROTOCOL
from Queue import Queue
from multiprocessing import Manager
from multiprocessing import cpu_count
from multiprocessing.pool import Pool
from os.path import dirname, abspath

from lzo import decompress
from collections import namedtuple

sys.path.append(dirname(abspath(__file__)))
from testdata import stream_from_good_probe, stream_from_timeout_probe
from testdata import stream_from_bad_probe, cprobes
from NavigaTor import Node, Probe


# Probedata = namedtuple('Probedata', 'date entry exit cbt rtts perfs bws')
Probedata = namedtuple('Probedata', 'date entry middle exit cbt rtts perfs bws')


def _truncate(cprobe, wlock):
    """
    Truncate data from measurement and write serialized output to stdout.
    """
    def to_ms(val):
        """ Convert value to ms. """
        return int(round((val * 1000)))

    probe = loads(decompress(cprobe))

    cbt = None
    if len(probe.cbt) > 0:
        cbt = probe.cbt.pop()

    streams = dict()
    for stream in probe.streams:
        if stream.id not in streams:
            streams[stream.id] = []
        streams[stream.id].append(stream)

    rtts = []
    for sid in streams.iterkeys():
        stream = streams[sid]
        if stream_from_good_probe(stream):
            # Calculate RTT and convert to ms.
            rtts.append(to_ms(stream[2].arrived_at - stream[1].arrived_at))
        elif stream_from_timeout_probe(stream):
            rtts.append('TIMEOUT')
        elif stream_from_bad_probe(stream):
            rtts.append('BAD')
    perfs = []
    for perf in probe.perf:
        assert len(perf) in [1, 3], 'perf has wrong length!'
        if len(perf) == 1:
            perfs.append(perf[0])
        if len(perf) == 3:
            # Convert to ms.
            perf = [to_ms(val) for val in perf]
            # STARTTRANSFER_TIME - CONNECT_TIME
            perfs.append(perf[1] - perf[0])
    bws = []
    try:
        for bwp in probe.bw:
            if len(bwp) == 3:
                # Convert to ms.
                bwp = [to_ms(val) for val in bwp]
                # TOTAL_TIME - STARTTRANSFER_TIME
                bws.append(bwp[2] - bwp[1])
            else:
                bws.append(None)
    # Backward compatibility when bandwidth probes were not implemented.
    except AttributeError:
        bws.append(None)
    probedata = Probedata(date=probe.circs[0].created,
                          entry=probe.path[0].desc.fingerprint,
                          middle=probe.path[1].desc.fingerprint,
                          exit=probe.path[2].desc.fingerprint,
                          cbt=cbt, rtts=rtts, perfs=perfs, bws=bws)
    with wlock:
        dump(probedata, sys.stdout, HIGHEST_PROTOCOL)
        sys.stdout.flush()


class PoolLimit(Pool):
    """ Limit maximum number of tasks in the waiting queue to 2*cpu_count. """
    def __init__(self):
        try:
            cpus = cpu_count()
        except NotImplementedError:
            cpus = 1
        self._taskqueue = Queue(maxsize=(2 * cpus))
        Pool.__init__(self)


def _main():
    """ Start multiple processes to truncate data out of measurements. """
    wlock = Manager().Lock()
    pool = PoolLimit()
    probes = cprobes()
    try:
        while True:
            try:
                cprobe = probes.next()
            except StopIteration:
                break
            pool.apply_async(_truncate, (cprobe, wlock))
        pool.close()
        pool.join()
    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    _main()
