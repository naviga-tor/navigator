#!/usr/bin/env python
# -*- coding: utf-8 -*-

""" Read NavigaTor measurement data. """

# Author: Robert Annessi <robert.annessi@nt.tuwien.ac.at>
# License: GPLv2 (2015)


from json import loads
from argparse import ArgumentParser
from os.path import exists
import sys


# Argument handling
parser = ArgumentParser(description="Parse JSON data from NavigaTor.")
parser.add_argument("--input", type=str, required=True, help="Input CSV file.")
args = parser.parse_args()
if not exists(args.input):
    sys.stderr.write("ERROR: Input file '%s' does not exist!\n" % args.input)
    sys.exit(1)

with open(args.input, 'r') as f:
    try:
        while True:
            # here is the magic. interpret every line as JSON string
            sys.stdout.write("%s\n" % loads(f.readline()))
    except ValueError:
        pass
