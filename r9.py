#!/usr/bin/env python
# -*- coding: utf-8 -*-

""" Add statistical information to circuit probes. """

# Author: Robert Annessi <robert.annessi@nt.tuwien.ac.at>
# License: GPLv2 (2014-2016)

import sys
from multiprocessing import Manager, Pool
from collections import namedtuple
from cPickle import load, dump, HIGHEST_PROTOCOL
from gc import collect
from argparse import ArgumentParser
from os.path import exists
from Queue import Empty
import traceback

from rpy2.robjects import r
from rpy2.robjects.packages import importr
from rpy2.robjects.vectors import DataFrame, IntVector

from cat import Probedata

Probestat = namedtuple('Probestat',
                       'date entry middle exit cbt cbtp cbtb rtts rttp rttb ttfbs bws cong congp congb')


def _convert_to_dataframe(x):
    """ Convert Python list of integers to R data frame. """
    tmp = dict()
    tmp['y'] = IntVector(x)
    return DataFrame(tmp)


def _get_data_from_backup(vgam, queue, wlock):
    try:
        with wlock:
            assert queue.qsize() in range(0, 2), 'Wrong size of queue!'
            try:
                data = queue.get(block=False)
            except Empty:
                return False
            queue.put(data, block=False)
        fdata = _convert_to_dataframe(data)
        fit = vgam.vglm("y ~ 1", vgam.egev, fdata)
        return fit
    except:
        sys.stderr.write(str(traceback.format_exc()))


def _calc_quantile(val, data, queue, wlock):
    """
    Calculate quantile for a specific CBT/RTT value from list of CBTs/RTTs.
    """
    try:
        vgam = importr('VGAM')
        r_base = importr('base')
        fdata = _convert_to_dataframe(data)
        backup = False
    except:
        sys.stderr.write(str(traceback.format_exc()))
    try:
        # Estimate GEV parameters.
        fit = vgam.vglm("y ~ 1", vgam.egev, fdata)
    except:
        sys.stderr.write('Could not estimate parameters. Using backup..\n')
        backup = True
    try:
        if not backup:
            with wlock:
                while True:
                    try:
                        queue.get(block=False)
                    except Empty:
                        break
                queue.put(data, block=False)
    except:
        sys.stderr.write(str(traceback.format_exc()))

    try:
        for _ in range(0, 2):
            if backup:
                fit = _get_data_from_backup(vgam, queue, wlock)
                if not fit:
                    return (None, backup)

            location, scale, shape = vgam.Coef(fit)

            if not isinstance(shape, float) or not shape > 0 or not shape < float('inf'):
                if backup:
                    return (None, backup)
                else:
                    backup = True
                    continue

            # Calculate probability that the CBT/RTT with the given probability
            # distribution will be found to be less than or equal to the
            # probe's CBT/RTT value.
            try:
                prob = vgam.pgev(q=val, location=location, scale=scale, shape=shape)[0]
            except ValueError:
                if backup:
                    return (None, backup)
                else:
                    backup = True
                    continue
            except:
                sys.stderr.write(str(traceback.format_exc()))

            # Remove VGAM from R's memory space.
            r_base.detach("package:VGAM")
            del vgam
            return (prob, backup)
    except:
        sys.stderr.write(str(traceback.format_exc()))

    assert False, 'We should never get here!'


def _update_probe(probe, cbts, rtts, congs, wlock, cqueue, rqueue, oqueue, ofile):
    """ Generate probe including quantile values. """
    try:
        cbtp = None
        cbtbak = False
        rttp = None
        rttbak = False
        congp = None
        congbak = False
        if probe.cbt and len(cbts) >= 1000:
            cbtp, cbtbak = _calc_quantile(probe.cbt, cbts, cqueue, wlock)
        if len(probe.rtts) > 0 and isinstance(probe.rtts[0], int) and len(rtts) >= 1000:
            rttp, rttbak = _calc_quantile(probe.rtts[0], rtts, rqueue, wlock)
        if probe.cong and len(congs) >= 1000:
            congp, congbak = _calc_quantile(probe.cong, congs, oqueue, wlock)

        # We have to explicitely clean up R's memory space.
        r.rm(list=r.ls(all_names=True))
        r.gc()

        data = Probestat(date=probe.date, entry=probe.entry,
                         middle=probe.middle, exit=probe.exit, cbt=probe.cbt,
                         cbtp=cbtp, cbtb=cbtbak, rtts=probe.rtts, rttp=rttp,
                         rttb=rttbak, ttfbs=probe.perfs, bws=probe.bws,
                         cong=probe.cong, congp=congp, congb=congbak)

        with wlock:
            with open(ofile, 'a') as f:
                dump(data, f, HIGHEST_PROTOCOL)
    except:
        sys.stderr.write(str(traceback.format_exc()))


def _main():
    """ Add statistical information to all probes' CBTs and RTTs. """
    rtts = []
    cbts = []
    congs = []
    manager = Manager()
    wlock = manager.Lock()
    cqueue = manager.Queue()
    rqueue = manager.Queue()
    oqueue = manager.Queue()
    pool = Pool()
    parser = ArgumentParser(description="Add statistical information" +
                                        "to probes")
    parser.add_argument("--input", type=str, required=True, help="Input file.")
    parser.add_argument("--output", type=str, required=True,
                        help="Output file.")
    args = parser.parse_args()
    assert args.input and exists(args.input), 'Invalid input file.'
    assert args.output and not exists(args.output), 'Invalid output file.'

    with open(args.input, 'r') as f:
        try:
            while True:
                try:
                    probe = load(f)
                except EOFError:
                    break
                if probe.cbt:
                    cbts.append(probe.cbt)
                if len(probe.rtts) > 0 and isinstance(probe.rtts[0], int):
                    rtts.append(probe.rtts[0])
                if probe.cong:
                    congs.append(probe.cong)
                pool.apply_async(_update_probe, (probe, cbts, rtts,
                                                 congs, wlock, cqueue,
                                                 rqueue, oqueue, args.output))
                if len(cbts) > 1000:
                    del cbts[0]
                if len(rtts) > 1000:
                    del rtts[0]
                if len(congs) > 1000:
                    del congs[0]
                # Run garbage collector after cleaning R's memory space.
                collect()
        except KeyboardInterrupt:
            pass
        except:
            sys.stderr.write(str(traceback.format_exc()))
    pool.close()
    pool.join()
    assert cqueue.qsize() in range(0, 2), 'Wrong size of queue!'
    assert rqueue.qsize() in range(0, 2), 'Wrong size of queue!'


if __name__ == '__main__':
    _main()
