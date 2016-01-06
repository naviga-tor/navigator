#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Generate Tor paths.
"""

# Author: Robert Annessi <robert.annessi@nt.tuwien.ac.at>
# License: GPLv2 (2015)


import sys
from argparse import ArgumentParser
from re import findall

from pkg_resources import get_distribution
from stem.control import Controller, EventType
from stem.connection import connect_port
from stem.version import Version


def findpath(controller):
    """
    Generate Tor paths.
        "controller": authenticated Tor Controller from stem.control.
    """
    # Validate input parameters.
    assert isinstance(controller, Controller), \
        'Controller has wrong type: %s.' % type(controller)

    assert controller.get_version() > Version('0.2.3'), \
        ('Your tor version (%s) is too old. ' % controller.get_version() +
         'Tor version 0.2.3.x is required.')
    assert controller.get_version() < Version('0.2.4'), \
        ('Your tor version (%s) is too new. ' % controller.get_version() +
         'Tor version 0.2.3.x is required.')

    # Change guard nodes for every path.
    msg = controller.msg('DUMPGUARDS')
    assert msg.is_ok(), ("DUMPGUARDS command failed with error " +
                         "'%s'. Is your tor client patched?\n" % str(msg))

    # Get a path from tor.
    msg = controller.msg('FINDPATH')
    assert msg.is_ok(), ("FINDPATH command failed with error " +
                         "'%s'. Is your tor client patched?\n" % str(msg))
    sys.stdout.write("%s\n" % findall('[A-Z0-9]{40}', str(msg)))


def _main():
    """
    Parse command line arguments, connect to tor, and call findpath().
    """
    # connect_port support in Stem
    assert get_distribution('stem').version > '1.4.0', \
        'Stem module version must be greater than 1.4.0.'
    parser = ArgumentParser(description="Generate Tor paths.")
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
    findpath(controller)
    controller.close()


if __name__ == "__main__":
    _main()
