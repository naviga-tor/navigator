#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
NavigaTor measures Round-Trip-Times (RTT), Time-To-First-Byte (TTFB), and
throughput of Tor circuits. Measurements are written as serialized,
lzo-compressed objects to files for further processing.
"""

# Author: Robert Annessi <robert.annessi@nt.tuwien.ac.at>
# License: GPLv2 (2013-2015)


import sys
from cPickle import dumps, HIGHEST_PROTOCOL
from threading import Thread, Lock, Event
from re import match, findall
import tarfile
from StringIO import StringIO
from time import mktime, sleep
from stat import S_IMODE
from collections import namedtuple
from argparse import ArgumentParser
from os.path import join, dirname

from pkg_resources import get_distribution
from lzo import compress
from stem import OperationFailed, InvalidRequest, InvalidArguments
from stem.control import Controller, EventType
from stem.connection import connect_port
from stem.version import Version
import pycurl

sys.path.append(join(dirname(__file__), 'libs'))
from SocksiPy.socks import socksocket, Socks5Error, PROXY_TYPE_SOCKS5


Probe = namedtuple('Probe', 'path circs cbt streams perf bw')
Probe_old = namedtuple('Probe', 'path circs cbt streams perf')
Node = namedtuple('Node', 'desc ns')


def NavigaTor(controller, num_circuits=1, num_rttprobes=1, num_ttfbprobes=1,
              num_bwprobes=1, probesleep=0, num_threads=1, output='probe_',
              network_protection=True):
    """
    Configure Tor client and start threads for probing the RTT and/or TTFB
    of Tor circuits.
        "controller": authenticated Tor Controller from stem.control.
        "num_circuits": number of circuits to be probed.
        "num_rttprobes": number of RTT probes to be taken for each circuit.
        "num_ttfbprobes": number of TTFB probes to be taken for each circuit.
        "num_bwprobes": number of bw probes to be taken for each circuit.
        "probesleep": number of seconds to wait between probes.
        "num_threads": number of threads to start that actually do the
                       probing.
        "output": prefix for output file(s).
        "network_protection": Anti-Hammering protection for the Tor network.
    """

    # RouterStatusEntryV3 support in Stem
    assert get_distribution('stem').version > '1.4.0', \
        'Stem module version must be greater then 1.4.0.'

    # socks5 + hostname support has been added in 7.21.7
    assert pycurl.version_info()[1] >= '7.21.7', \
        'pycurl version (%s) must be >= 7.21.7' % pycurl.version_info()[1]

    # Validate input parameters.
    assert isinstance(controller, Controller), \
        'Controller has wrong type: %s.' % type(controller)
    for i in num_circuits, num_rttprobes, num_ttfbprobes, num_bwprobes,\
            num_threads:
        assert isinstance(i, int), '%s has wrong type: %s.' % (i, type(i))
    # Maximum number of circuits that can be probed is limited by
    # the unique destination IP calculation. Currently there is no need to
    # raise this limit.
    max_circuits = 255 + 255 * 256 + 255 * pow(256, 2) - 1
    assert num_circuits in range(1, max_circuits), \
        'num_circuits is out of range: %d.' % (num_circuits)

    assert controller.get_version() > Version('0.2.3'), \
        ('Your tor version (%s) is too old. ' % controller.get_version() +
         'Tor version 0.2.3.x is required.')
    assert controller.get_version() < Version('0.2.4'), \
        ('Your tor version (%s) is too new. ' % controller.get_version() +
         'Tor version 0.2.3.x is required.')

    try:
        # Configure tor client
        controller.set_conf("__DisablePredictedCircuits", "1")
        controller.set_conf("__LeaveStreamsUnattached", "1")
        controller.set_conf("MaxClientCircuitsPending", "1024")
        # Workaround ticket 9543. 10s average for each RTT probe and
        # 10s for each TTFB probe should be enough.
        max_dirtiness = (num_rttprobes + num_ttfbprobes) * 10
        if int(controller.get_conf("MaxCircuitDirtiness")) < max_dirtiness:
            controller.set_conf("MaxCircuitDirtiness", str(max_dirtiness))

        # Close all non-internal circuits.
        for circ in controller.get_circuits():
            if not circ.build_flags or 'IS_INTERNAL' not in circ.build_flags:
                controller.close_circuit(circ.id)

        manager = _Manager(controller, num_circuits, num_rttprobes,
                           num_ttfbprobes, num_bwprobes, probesleep,
                           num_threads, output, network_protection)
        while True:
            manager.join(1)
            if not manager.is_alive():
                break

    except KeyboardInterrupt:
        pass

    finally:
        controller.reset_conf("__DisablePredictedCircuits")
        controller.reset_conf("__LeaveStreamsUnattached")
        controller.reset_conf("MaxCircuitDirtiness")
        controller.reset_conf("MaxClientCircuitsPending")
        controller.close()


class _Manager(Thread):
    """
    Start worker threads and provide methods to them for accessing shared
    resources exclusively.
    """
    def __init__(self, controller, num_circuits, num_rttprobes,
                 num_ttfbprobes, num_bwprobes, probesleep, num_threads,
                 output, network_protection):
        self._controller = controller
        self._num_circuits = num_circuits
        self._lock = Lock()
        self._output = output
        self._fileno = 0
        self._tar = self._create_tar_file()
        self._bytes_written = 0
        self._descriptors_known = Event()
        self._circ_closed = Event()
        self._nodes_processing = set()
        self._paths_waiting = []
        self.circ_launched = Lock()
        self._threads = set()
        self._threads_finished = []
        self._num_threads = num_threads
        self._num_rttprobes = num_rttprobes
        self._num_ttfbprobes = num_ttfbprobes
        self._num_bwprobes = num_bwprobes
        self._probesleep = probesleep
        self._network_protection = network_protection
        self.perf_lock = Lock()
        self.bw_lock = Lock()
        self._worker_finished = Event()
        self._worker_finished.set()
        Thread.__init__(self)
        self.start()

    def _create_tar_file(self):
        """ Create new output file name and open it as tar file. """
        self._fileno += 1
        name = "%s%03d" % (self._output, self._fileno)
        return tarfile.open(name, mode="w|")

    def start_worker(self, path, dest):
        """ Start worker thread. """
        thread = _Worker(self._controller, self, path, dest,
                         self._num_rttprobes, self._num_ttfbprobes,
                         self._num_bwprobes, self._probesleep)
        self._threads.add(thread)

    def run(self):
        while True:
            with self._lock:
                for _ in range(self._num_threads - len(self._threads)):
                    # Prefer any usable waiting path.
                    data = self._get_waiting_path()
                    if data:
                        self.start_worker(data.path, data.dest)
                    # Respect queue size.
                    elif len(self._paths_waiting) >= 2 * self._num_threads:
                        break
                    # Find a new path and check if it can be used.
                    elif self._num_circuits > 0:
                        self._num_circuits -= 1
                        path = self._get_new_path()
                        if self._is_unused(path):
                            if self._network_protection:
                                for node in path:
                                    fp = node.ns.fingerprint
                                    assert node.ns.fingerprint not in \
                                        self._nodes_processing, \
                                        '%s being processed.' % fp
                                    self._nodes_processing.add(fp)
                            self.start_worker(path, self._get_dest())
                        else:
                            probedata = namedtuple('probedata', 'path dest')
                            data = probedata(path=path, dest=self._get_dest())
                            self._paths_waiting.append(data)
                self._worker_finished.clear()

            sys.stderr.write('Threads: %d, ' % len(self._threads) +
                             'Circuits: %d, ' % self._num_circuits +
                             'Queue: %d\n' % len(self._paths_waiting))
            # Stop Manager, if no new workers have been spawned and queue is
            # empty.
            if len(self._threads) == 0:
                break

            # Wait for a worker to signal that it finished
            self._worker_finished.wait()
            # Wait for that worker to really have finished and
            # prevent possible race condition.
            self._threads_finished[0].join()

            # Clean up thread queue.
            tmp = self._threads.copy()
            for _ in range(len(tmp)):
                thread = tmp.pop()
                if not thread.isAlive():
                    # Remove nodes from processing list.
                    if self._network_protection:
                        for node in thread.path:
                            fp = node.ns.fingerprint
                            assert fp in self._nodes_processing, \
                                'Node %s not in list.' % fp
                            self._nodes_processing.remove(fp)
                    self._threads.remove(thread)
                    self._threads_finished.remove(thread)
                    del thread
            del tmp
        # close open tar file
        self._tar.close()

    def _descriptor_check(self, event):
        """
        Event listener for checking that tor knows about all server
        descriptors.
        """
        mat = '^We now have enough directory information to build circuits\. $'
        if match(mat, (event.message)):
            self._descriptors_known.set()

    def _circuit_check(self, event):
        """
        Event listener to check when the circuit close event has
        arrived.
        """
        if event.status == 'FAILED' and event.reason == 'NONE':
            if 'NEED_CAPACITY' in event.build_flags:
                self._circ_closed.set()

    def _get_dest(self):
        """
        Calculate a unique destination IP address for stream probing.
        """
        quot = self._num_circuits
        div = 256 * 256
        dest = '127'
        while div > 0:
            rem, quot = divmod(quot, div)
            dest += '.' + str(rem)
            div /= 256
        return dest

    def _get_waiting_path(self):
        """
        Choose a path with detailed information on the nodes that is
        currently in the waiting queue.
        """
        for data in self._paths_waiting:
            if self._is_unused(data.path):
                for node in data.path:
                    assert node.ns.fingerprint not in self._nodes_processing, \
                        '%s being processed.' % node.ns.fingerprint
                    self._nodes_processing.add(node.ns.fingerprint)
                self._paths_waiting.remove(data)
                return data

    def _get_new_path(self):
        """
        Choose a new path with detailed information on the nodes.
        """
        # Wait until tor knows all descriptors.
        if not self._controller.get_info('status/enough-dir-info') == '1':
            sys.stderr.write("Waiting for server descriptors..\n")
            self._controller.add_event_listener(self._descriptor_check,
                                                EventType.NOTICE)
            self._descriptors_known.wait()
            sys.stderr.write('All server descriptors received. Proceeding..\n')
            self._descriptors_known.clear()
            self._controller.remove_event_listener(self._descriptor_check)

        # Change guard nodes for every path.
        msg = self._controller.msg('DUMPGUARDS')
        assert msg.is_ok(), 'DUMPGUARDS command failed with error "%s". '\
                            'Is your tor client patched?\n' % str(msg)

        # Get a path from Tor.
        self._circ_closed.clear()
        self._controller.add_event_listener(self._circuit_check,
                                            EventType.CIRC)

        # It is very unlikely (~2*10^-7) but possible still that
        # information on a specific node becomes unavailable
        # between the FINDPATH command and querying a node's network status
        # and server descriptor.
        # Therefore, we try at most 10 times to find a path.
        for _ in range(10):
            msg = self._controller.msg('FINDPATH')
            assert msg.is_ok(), 'FINDPATH command failed with error "%s".'\
                                'Is your tor client patched?\n' % str(msg)

            # Get node information and add it to path. This should be done
            # here and not in the worker thread because the path may be
            # queued before actually being probed and the information
            # about the nodes should be close to the time when they were
            # chosen for a path.
            path = []
            for fprint in findall('[A-Z0-9]{40}', str(msg)):
                try:
                    node_ns = self._controller.get_network_status(fprint)
                except InvalidArguments:
                    sys.stderr.write("Could not find network status " +
                                     "for '%s'.\n" % fprint)
                    continue
                try:
                    node_desc = self._controller.get_server_descriptor(fprint)
                except InvalidArguments:
                    sys.stderr.write("Could not find server descriptor " +
                                     "for '%s'.\n" % fprint)
                    continue
                node = Node(ns=node_ns, desc=node_desc)
                path.append(node)
        assert len(path) == 3, 'pycurl version (%s) must be >= 7.21.7'

        self._circ_closed.wait()
        self._controller.remove_event_listener(self._circuit_check)
        return path

    def _is_unused(self, path):
        """
        A path is only usable at that time if all nodes within that
        path are currently not being probed.
        """
        for node in path:
            if node.ns.fingerprint in self._nodes_processing:
                return False
        return True

    def write(self, worker, probe, dest):
        """
        Serialize probe data, compress it and write it exclusively
        to output file.
        """
        data = StringIO()
        data.write(compress(dumps(probe, HIGHEST_PROTOCOL)))
        data.seek(0)
        info = tarfile.TarInfo()
        info.name = 'Probe_%s.lzo' % dest
        info.uid = 0
        info.gid = 0
        info.size = len(data.buf)
        info.mode = S_IMODE(0o0444)
        info.mtime = mktime(probe.circs[0].created.timetuple())
        with self._lock:
            # Maximum file size is about 1 GB
            if self._bytes_written >= 1 * 1000 * 1000 * 1000:
                self._tar.close()
                self._tar = self._create_tar_file()
                self._bytes_written = 0
            self._tar.addfile(tarinfo=info, fileobj=data)
            self._bytes_written += info.size
            self._threads_finished.append(worker)
            self._worker_finished.set()


class _Worker(Thread):
    """
    Thread that actually does the RTT- and/or TTFB-probing.
        "controller": an authenticated Tor controller.
        "manager": for accessing shared resources.
        "path": path to probe.
        "ip": target IP address to use.
        "num_rttprobes": number of RTT probes for each circuit.
        "num_ttfbprobes": number of TTFB probes for each circuit.
        "probesleep": number of seconds to wait between probes.
    """
    def __init__(self, controller, manager, path, dest, num_rttprobes,
                 num_ttfbprobes, num_bwprobes, probesleep):
        self._controller = controller
        self._manager = manager
        self.path = path
        self._dest = dest
        self._num_rttprobes = num_rttprobes
        self._num_ttfbprobes = num_ttfbprobes
        self._num_bwprobes = num_bwprobes
        self._probesleep = probesleep
        self._cid = None
        self._circuit_finished = Event()
        self._cbt_received = Event()
        self._stream_finished = Event()
        self._circuit_built = Event()
        Thread.__init__(self)
        self.start()

    def _attach_stream(self, event):
        """ Attach stream to circuit. """
        try:
            self._controller.attach_stream(event.id, self._cid)
        except (OperationFailed, InvalidRequest), error:
            error = str(error)
            # If circuit is already closed, close stream too.
            if error in (('Unknown circuit "%s"' % self._cid),
                         "Can't attach stream to non-open origin circuit"):
                self._controller.close_stream(event.id)
            # Ignore the rare cases (~5*10^-7) where a stream has already been
            # closed almost directly after its NEW-event has been received.
            elif error == 'Unknown stream "%s"' % event.id:
                sys.stderr.write('Stream %s has already been ' +
                                 'closed.\n' % event.id)
            else:
                raise

    def run(self):
        def _circuit_handler(event):
            """ Event handler for handling circuit states. """
            if not event.build_flags or 'IS_INTERNAL' not in event.build_flags:
                if event.id == self._cid:
                    probe.circs.append(event)
                    if self._circuit_built.is_set():
                        if event.status in ('FAILED', 'CLOSED'):
                            self._circuit_finished.set()
                    if not self._circuit_built.is_set():
                        if event.status in ('FAILED', 'BUILT'):
                            self._circuit_built.set()
                elif event.status == 'LAUNCHED' and not self._cid:
                    self._cid = event.id
                    probe.circs.append(event)
                    self._manager.circ_launched.release()

        def _stream_probing(event):
            """
            Event handler for detecting start and end of RTT probing streams.
            """
            if event.target_address == self._dest:
                probe.streams.append(event)
                if event.status == 'CLOSED':
                    self._stream_finished.set()
                elif event.status == 'NEW' and event.purpose == 'USER':
                    self._attach_stream(event)

        def _stream_performance(event):
            """ Event handler for detecting start of performance stream. """
            # Make sure we don't handle a probing stream.
            if not event.target_address.startswith('127.'):
                if event.status == 'NEW' and event.purpose == 'USER':
                    self._controller.remove_event_listener(_stream_performance)
                    self._manager.perf_lock.release()
                    self._attach_stream(event)

        def _stream_bw(event):
            """ Event handler for detecting start of bandwidth stream. """
            # Make sure we don't handle a probing stream.
            if not event.target_address.startswith('127.'):
                if event.status == 'NEW' and event.purpose == 'USER':
                    self._controller.remove_event_listener(_stream_bw)
                    self._attach_stream(event)
                    self._manager.bw_lock.release()

        def _cbt_check(event):
            """ Check for CBT message from tor. """
            cbt_m = match('^circuit_send_next_onion_skin\(\): circuit '
                          '([0-9]+) built in ([0-9]+)msec $', (event.message))
            if cbt_m and cbt_m.group(1) == self._cid:
                assert len(probe.cbt) == 0, \
                    'CBT for %s is already set: %d.' % (self._cid, probe.cbt)
                probe.cbt.add(int(cbt_m.group(2)))
                self._cbt_received.set()

        def devnull(body):
            """ Drop Curl output. """
            return

        socks_ip = self._controller.get_socks_listeners()[0][0]
        socks_port = self._controller.get_socks_listeners()[0][1]

        probe = Probe(path=self.path, circs=[], cbt=set(), streams=[],
                      perf=[], bw=[])

        # Build new circuit.
        circ_path = [node.desc.fingerprint for node in self.path]
        # Launching a circuit must be exclusive since we get the circuit
        # identifier from the LAUNCH event.
        self._manager.circ_launched.acquire()
        self._controller.add_event_listener(_circuit_handler, EventType.CIRC)
        self._controller.add_event_listener(_cbt_check, EventType.INFO)
        self._controller.extend_circuit(path=circ_path)
        self._circuit_built.wait()
        build_status = probe.circs[len(probe.circs) - 1].status
        assert build_status == 'BUILT' or build_status == 'FAILED', \
            'Wrong circuit status: %s.' % build_status
        if build_status == 'FAILED':
            self._controller.remove_event_listener(_circuit_handler)
            self._controller.remove_event_listener(_cbt_check)
            self._manager.write(self, probe, self._dest)
            return

        # Make sure CBT has been set
        self._cbt_received.wait()
        self._controller.remove_event_listener(_cbt_check)

        # RTT probe circuit.
        for _ in range(0, self._num_rttprobes):
            self._stream_finished.clear()
            socket = socksocket()
            socket.setproxy(PROXY_TYPE_SOCKS5, socks_ip, socks_port)
            self._controller.add_event_listener(_stream_probing,
                                                EventType.STREAM)
            try:
                socket.connect((self._dest, 80))
            except Socks5Error, error:
                # tor's socks implementation sends a general error response
                # when the Tor protocol is violated.
                # See stream_end_reason_to_socks5_response()
                err = ("(1, 'general SOCKS server failure')",
                       "(5, 'Connection refused')",
                       "(6, 'TTL expired')")
                if str(error) not in err:
                    raise Socks5Error(str(error))
            # Make sure stream has been closed.
            self._stream_finished.wait()
            self._controller.remove_event_listener(_stream_probing)
            socket.close()

        # TTFB probe circuit
        for _ in range(0, self._num_ttfbprobes):
            sleep(self._probesleep)
            curl = pycurl.Curl()
            curl.setopt(curl.PROXY, 'socks5h://%s:%s' % (socks_ip, socks_port))
            curl.setopt(curl.CONNECTTIMEOUT, 120)
            curl.setopt(curl.TIMEOUT, 120)
            curl.setopt(curl.HEADER, 1)
            curl.setopt(pycurl.USERAGENT, "")
            # HTTP header only
            curl.setopt(curl.NOBODY, 1)
            curl.setopt(curl.WRITEFUNCTION, devnull)
            curl.setopt(curl.HEADERFUNCTION, devnull)
            curl.setopt(curl.URL, 'http://www.google.com/')
            self._manager.perf_lock.acquire()
            self._controller.add_event_listener(_stream_performance,
                                                EventType.STREAM)
            try:
                # self._manager.perf_lock is released in _stream_performance
                # from here
                curl.perform()
            except pycurl.error, errorstr:
                probe.perf.append([str(errorstr)])
                curl.close()
                break
            if curl.getinfo(pycurl.SIZE_DOWNLOAD) != 0.0:
                probe.perf.append(['Wrong response length: %0.2f'
                                   % curl.getinfo(pycurl.SIZE_DOWNLOAD)])
            elif curl.getinfo(pycurl.REDIRECT_COUNT) != 0:
                probe.perf.append(['HTTP redirects: %d'
                                   % curl.getinfo(pycurl.REDIRECT_COUNT)])
            else:
                # http://curl.haxx.se/libcurl/c/curl_easy_getinfo.html#TIMES
                # CONNECT_TIME: Time, in seconds, it took from the start until
                #               the connect to the remote host (or proxy) was
                #               completed.
                # STARTTRANSFER_TIME: Time, in seconds, it took from the start
                #                     until the first byte is received.
                # TOTAL_TIME: Total time in seconds for the transfer,
                #             including name resolving, TCP connect etc.
                probe.perf.append([curl.getinfo(pycurl.CONNECT_TIME),
                                   curl.getinfo(pycurl.STARTTRANSFER_TIME),
                                   curl.getinfo(pycurl.TOTAL_TIME)])
            curl.close()

        # Bandwidth probe circuit
        for _ in range(0, self._num_bwprobes):
            self._manager.bw_lock.acquire()
            curl = pycurl.Curl()
            curl.setopt(curl.PROXY, 'socks5h://%s:%s' % (socks_ip, socks_port))
            curl.setopt(curl.CONNECTTIMEOUT, 120)
            curl.setopt(curl.TIMEOUT, 3600)
            curl.setopt(curl.URL, 'http://www.torrtt.info/')
            curl.setopt(curl.WRITEFUNCTION, devnull)
            curl.setopt(pycurl.USERAGENT, "")
            # No compression of HTTP response
            curl.setopt(pycurl.ENCODING, "identity")
            self._controller.add_event_listener(_stream_bw, EventType.STREAM)
            try:
                curl.perform()
            except pycurl.error, errorstr:
                probe.bw.append([str(errorstr)])
                curl.close()
                continue
            if curl.getinfo(pycurl.SIZE_DOWNLOAD) != 5242880.0:
                probe.bw.append(['Wrong response length: %0.2f'
                                 % pycurl.SIZE_DOWNLOAD])
            elif curl.getinfo(pycurl.REDIRECT_COUNT) != 0:
                probe.bw.append(['HTTP redirects: %d'
                                 % pycurl.REDIRECT_COUNT])
            else:
                # http://curl.haxx.se/libcurl/c/curl_easy_getinfo.html#TIMES
                # CONNECT_TIME: Time, in seconds, it took from the start until
                #               the connect to the remote host (or proxy) was
                #               completed.
                # STARTTRANSFER_TIME: Time, in seconds, it took from the start
                #                     until the first byte is received.
                # TOTAL_TIME: Total time in seconds for the transfer,
                #             including name resolving, TCP connect etc.
                probe.bw.append([curl.getinfo(pycurl.CONNECT_TIME),
                                 curl.getinfo(pycurl.STARTTRANSFER_TIME),
                                 curl.getinfo(pycurl.TOTAL_TIME)])
            curl.close()

        # close circuit, but ignore if it does not exist anymore
        try:
            self._controller.close_circuit(self._cid)
        except InvalidArguments:
            pass

        # Make sure circuit has finished.
        self._circuit_finished.wait()
        self._controller.remove_event_listener(_circuit_handler)

        # Output probe data
        self._manager.write(self, probe, self._dest)


def _main():
    """
    Parse command line arguments, connect to tor and call NavigaTor().
    """
    # connect_port support in Stem
    assert get_distribution('stem').version > '1.4.0', \
        'Stem module version must be greater then 1.4.0.'
    parser = ArgumentParser(description="Probe RTTs of Tor circuits.")
    parser.add_argument("--circuits", type=int, default=1,
                        help="Number of circuits to build and measure.")
    parser.add_argument("--rttprobes", type=int, default=1,
                        help="Number of RTT measurements on each circuit.")
    parser.add_argument("--ttfbprobes", type=int, default=1,
                        help="Number of TTFB measurements on each circuit.")
    parser.add_argument("--bwprobes", type=int, default=1,
                        help="Number of throughput measurements on each " +
                             "circuit.")
    parser.add_argument("--probesleep", type=float, default=0,
                        help="Waiting interval between probes in seconds.")
    parser.add_argument("--threads", type=int, default=1,
                        help="Number of parallel measurement threads.")
    parser.add_argument("--output", type=str, default='probe_',
                        help="Prefix for output files.")
    parser.add_argument('--network-protection', dest='network_protection',
                        action='store_true', help="Prevent hammering the " +
                                                  "Tor network.")
    parser.add_argument('--no-network-protection', dest='network_protection',
                        action='store_false', help="Do not prevent hammering" +
                                                   "the Tor network.")
    parser.add_argument("--port", type=int, default=9051,
                        help="tor control port.")
    parser.set_defaults(network_protection=True)
    args = parser.parse_args()

    controller = connect_port(port=args.port)
    if not controller:
        sys.stderr.write("ERROR: Couldn't connect to tor.\n")
        sys.exit(1)
    if not controller.is_authenticated():
        controller.authenticate()
    NavigaTor(controller, args.circuits, args.rttprobes, args.ttfbprobes,
              args.bwprobes, args.probesleep, args.threads, args.output,
              args.network_protection)
    controller.close()


if __name__ == "__main__":
    _main()
