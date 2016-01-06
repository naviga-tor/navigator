#!/usr/bin/env python
# -*- coding: utf-8 -*-

""" Verify NavigaTor's output. """

# Author: Robert Annessi <robert.annessi@nt.tuwien.ac.at>
# License: GPLv2 (2013-2015)

from sys import stdin, stdout
from cPickle import loads
import tarfile

from lzo import decompress
from stem.response.events import StreamEvent, CircuitEvent
from stem.descriptor.router_status_entry import RouterStatusEntryV3
from stem.descriptor.server_descriptor import RelayDescriptor

from NavigaTor import Probe, Node


def stream_from_good_probe(streams):
    """ Check if list of streams represents a successful RTT-probe. """
    # Input validation
    assert isinstance(streams, list), 'Input must be list.'
    for i in range(0, len(streams)):
        assert isinstance(streams[i], StreamEvent), \
            'All list elements must be of type StreamEvent.'
    if len(streams) != 4:
        return False
    # Check if probe was successful.
    if streams[0].status == 'NEW' and streams[0].purpose == 'USER' and \
       streams[1].status == 'SENTCONNECT' and \
       streams[2].status == 'FAILED' and \
       (streams[2].reason == 'TORPROTOCOL' or
        (streams[2].reason == 'END' and
         streams[2].remote_reason == 'CONNECTREFUSED')) and \
       streams[3].status == 'CLOSED' and \
       (streams[3].reason == 'TORPROTOCOL' or
        (streams[3].reason == 'END' and
         streams[3].remote_reason == 'CONNECTREFUSED')):
        return True
    else:
        return False


def stream_from_timeout_probe(streams):
    """
    Check if list of streams represents a RTT-probe that timed out.
    """
    # Input validation
    assert (isinstance(streams, list)), 'Input must be list.'
    for i in range(0, len(streams)):
        assert isinstance(streams[i], StreamEvent), \
            'All list elements must be of type StreamEvent.'
    if len(streams) != 5:
        return False
    # Check if circuit failed during probing.
    if streams[0].status == 'NEW' and streams[0].purpose == 'USER' and \
       streams[1].status == 'SENTCONNECT' and \
       streams[2].status == 'DETACHED' and \
       (streams[2].reason == 'TIMEOUT' or
        (streams[2].reason == 'END' and
         (streams[2].remote_reason == 'HIBERNATING' or
          streams[2].remote_reason == 'MISC' or
          streams[2].remote_reason == 'RESOURCELIMIT'))) and \
       streams[3].status == 'FAILED' and streams[3].reason == 'TIMEOUT' and \
       streams[4].status == 'CLOSED' and streams[4].reason == 'TIMEOUT':
        return True
    return False


def stream_from_bad_probe(streams):
    """ Check if list of streams represents an unsuccessful RTT-probe. """
    # Input validation
    assert (isinstance(streams, list)), 'Input must be list.'
    for i in range(0, len(streams)):
        assert isinstance(streams[i], StreamEvent), \
            'All list elements must be of type StreamEvent.'
    if len(streams) == 3:
        # Check if circuit has failed before probing.
        if streams[0].status == 'NEW' and streams[0].purpose == 'USER' and \
           streams[1].status == 'FAILED' and streams[1].reason == 'MISC' and \
           streams[2].status == 'CLOSED' and streams[2].reason == 'MISC':
            return True
    elif len(streams) == 4:
        # Check if circuit was unexpectedly closed.
        if streams[0].status == 'NEW' and streams[0].purpose == 'USER' and \
           streams[1].status == 'SENTCONNECT' and \
           streams[2].status == 'FAILED' and \
           streams[2].reason == 'DESTROY' and \
           streams[3].status == 'CLOSED' and streams[3].reason == 'DESTROY':
            return True
    return False


def _built_circuit(circs):
    """
    Check if a list of circuit events represents a successfully built circuit.
    """
    # Input validation
    assert isinstance(circs, list), \
        'Circuit list has wrong type: %s.' % type(circs)
    for i in range(0, len(circs)):
        assert isinstance(circs[i], CircuitEvent), \
            'All list elements must be of type CircuitEvent.'
    # Check if circuit was successfully built.
    if ['LAUNCHED', 'EXTENDED', 'EXTENDED', 'EXTENDED', 'BUILT', 'CLOSED'] == \
       [circ.status for circ in circs]:
        return True
    return False


def _finished_circuit(circs):
    """
    Check if a list of circuit events represents a successfully
    finished circuit.
    """
    return _built_circuit(circs) and circs[5].reason == 'REQUESTED'


def _valid_circuit(circs):
    """ Check basic validity of given circuit. """
    # input validation
    assert isinstance(circs, list), \
        'Circuit list has wrong type: %s.' % type(circs)
    for i in range(0, len(circs)):
        assert isinstance(circs[i], CircuitEvent), \
            'All list elements must be of type CircuitEvent.'
    # check purpose
    for circ in circs:
        assert circ.purpose == 'GENERAL', \
            'Circuit has wrong purpose: %s.' % circ.purpose
    return True


def _checkpath(path_list):
    """ Check validity of a path. """
    assert isinstance(path_list, list), \
        'Path has wrong type: %s.' % type(path_list)
    for node in path_list:
        assert isinstance(node, Node), 'Node has wrong type: %s.' % type(node)
        assert node.ns, 'Node has no networkstatus.'
        assert node.desc, 'Node has no descriptor.'
        assert isinstance(node.ns, RouterStatusEntryV3), \
            'Wrong ns type: %s.' % type(node.ns)
        assert isinstance(node.desc, RelayDescriptor), \
            'Wrong desc type: %s.' % type(node.desc)
        assert 'Running' in node.ns.flags, 'Node is not running.'
        assert 'Valid' in node.ns.flags, 'Node is not valid.'
    assert len(path_list) in range(0, 4), \
        'Path length is out of range: %d.' % len(path_list)
    if len(path_list) > 0:
        networkstatus = path_list[0].ns
        assert 'Guard' in networkstatus.flags, 'Entry node is not guard.'
    if len(path_list) == 3:
        assert path_list[2].desc.exit_policy.is_exiting_allowed(), \
            'Last node has no exit policy.'
    return True


def _good_path(path):
    """ Check if path is valid and path length = 3. """
    if not _checkpath(path):
        return False
    return len(path) == 3


def cprobes():
    """ Iterate through compressed probes generated by NavigaTor. """
    with tarfile.open(fileobj=stdin, mode="r|") as tar:
        while True:
            cprobe = tar.next()
            if not cprobe:
                raise StopIteration()
            tarx = tar.extractfile(cprobe)
            if not tarx:
                continue
            yield tarx.read()


def probes():
    """ Iterate through uncompressed probes generated by NavigaTor. """
    for cprobe in cprobes():
        try:
            probe = loads(decompress(cprobe))
        # Backward compatibility when bandwidth probes were not implemented.
        except TypeError:
            from NavigaTor import Probe_old as Probe
            probe = loads(decompress(cprobe))
        yield probe


def _checkcircs(path, circs):
    """ Check validity of circuits. """
    assert len(set([circ.id for circ in circs])) == 1, \
        'Probe must have exactly one circuit ID.'
    # Nodes in circs must be the same as nodes in path
    for circ in circs:
        cnt = 0
        for node in circ.path:
            fingerprint = path[cnt].ns.fingerprint
            assert node[0] == fingerprint, \
                'Wrong node! Expected %s. Got %s.' % (node[0], fingerprint)
            cnt += 1
    assert len(circs) <= 7, 'Too many circ events: %d.' % len(circs)
    stati = [x.status for x in circs]
    assert stati[0] == 'LAUNCHED', 'First status: %s.' % stati[0]
    if len(circs) < 6:
        assert stati[len(stati) - 1] == 'FAILED', \
            'Last status: %s.' % stati[len(stati) - 1]


def _main():
    """ Actually run tests and calculate RTTs. """
    nr_measurements = None
    global_addresses = set()

    for probe in probes():
        assert isinstance(probe, Probe), \
            'Probe has wrong type: %s' % type(probe)
        _checkpath(probe.path)

        # calculate number of measurements
        streams = dict()
        address = set()
        for stream in probe.streams:
            if stream.id not in streams:
                streams[stream.id] = []
            streams[stream.id].append(stream)
            address.add(stream.target_address)
        if not nr_measurements:
            nr_measurements = len(streams)
        assert len(streams) == 0 or len(streams) == nr_measurements, \
            'Wrong number of measurements.'

        # Check occurences of target addresses
        assert len(address) in range(0, 2), \
            'Target address occured %d.' % len(address)
        if len(address) == 1:
            address = address.pop()
            assert address not in global_addresses, \
                'Target address has been used before.'
            global_addresses.add(address)

        # classify measurements
        good_streams = []
        bad_streams = []
        timeout_streams = []
        for sid in streams.iterkeys():
            stream = streams[sid]
            if stream_from_good_probe(stream):
                good_streams.append(stream)
            elif stream_from_bad_probe(stream):
                bad_streams.append(stream)
            elif stream_from_timeout_probe(stream):
                timeout_streams.append(stream)
            else:
                # debug stream classification
                for i in stream:
                    stdout.write(str(i) + '\n')
        total_len = len(good_streams) + len(bad_streams) + len(timeout_streams)
        assert len(streams) == total_len, 'Stream classification is broken.'

        # Calculate RTTS for probe
        rtts = [srm[2].arrived_at - srm[0].arrived_at for srm in good_streams]
        _checkcircs(probe.path, probe.circs)

        # Circuit classification
        built = _built_circuit(probe.circs)
        finished = _finished_circuit(probe.circs)
        assert len(rtts) == 0 or built, \
            'Circuit did not build but has measurements.'
        assert _valid_circuit(probe.circs), 'Circuit detection is broken.'
        assert built or not finished, 'Circuit detection is really broken.'
        assert (len(rtts) + len(timeout_streams)) == nr_measurements or \
            not finished, 'Circuit finished but not enough measurements.'

        # Check measurements and path length
        assert _good_path(probe.path) or len(rtts) == 0, \
            'RTTs measured but path is not good!'
        for rtt in rtts:
            assert isinstance(rtt, float), 'Wrong RTT type: %s' % type(rtt)

        # Check CBT
        assert isinstance(probe.cbt, set), \
            'CBT has wrong type: %s.' % type(probe.cbt)
        assert len(probe.cbt) in range(0, 2), \
            'CBT has wrong size: %d.' % len(probe.cbt)
        assert len(probe.cbt) == 1 or len(rtts) == 0, \
            'Probe has no CBT but RTTs.'

        # Check performance measurements
        assert isinstance(probe.perf, list), \
            'Performance measurement has wrong type: %s' % type(probe.perf)
        assert len(probe.cbt) == 1 or len(probe.perf) == 0, \
            'Probe has no CBT but performance measurements.'
        for perf in probe.perf:
            assert len(perf) == 1 or len(perf) == 3, \
                'Performance measurement has wrong length: %d' % len(perf)
            if len(perf) == 1:
                assert isinstance(perf[0], str), \
                    'Wrong perf type: %s' % type(perf[0])
            elif len(perf) == 3:
                for i in perf:
                    assert isinstance(i, float), \
                        'Wrong measurement type: %s' % type(i)
        assert len(probe.cbt) == 1 or len(probe.bw) == 0, \
            'Probe has no CBT but bandwidth measurements.'
        for bwp in probe.bw:
            assert len(bwp) == 1 or len(bwp) == 3, \
                'Bandwidth measurement has wrong length: %d' % len(bwp)
            if len(bwp) == 1:
                assert isinstance(bwp[0], str), \
                    'Wrong bw type: %s' % type(bwp[0])
            elif len(bwp) == 3:
                for i in bwp:
                    assert isinstance(i, float), \
                        'Wrong measurement type: %s' % type(i)

        text = 'Broken'
        if built:
            text = 'Built'
        if finished:
            text = 'Finished'
        stdout.write('%s circuit has %d good and %d timedout measurements.\n'
                     % (text, len(rtts), len(timeout_streams)))


if __name__ == '__main__':
    try:
        _main()
    except KeyboardInterrupt:
        pass
