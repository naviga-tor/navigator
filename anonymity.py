#!/usr/bin/env python
# -*- coding: utf-8 -*-

""" Anonymity metrics. """

# Author: Robert Annessi <robert.annessi@nt.tuwien.ac.at>
# License: GPLv2 (2015)

from math import log

from numpy import mean
from rpy2.robjects.packages import importr
from rpy2.robjects.vectors import IntVector


def calc_gini(probes):
    # Count node occurences.
    nodes = dict()
    for probe in probes:
        if not probe[1] in nodes:
            nodes[probe[1]] = 1
        else:
            nodes[probe[1]] += 1
        if not probe[3] in nodes:
            nodes[probe[3]] = 1
        else:
            nodes[probe[3]] += 1

    # Calculate Gini coefficient.
    r_stats = importr('stats')
    total = 0
    node_selection = [nodes[node] for node in nodes.iterkeys()]
    if len(node_selection) == 0:
        return 1.0
    fdata = IntVector(node_selection)
    Fn = r_stats.ecdf(fdata)
    for nr in set(node_selection):
        cdf_x = Fn(nr)[0]
        total += cdf_x * (1 - cdf_x)
    return total / mean(node_selection)


def shannon_entropy(probes, total):
    """ Calculate normalized Shannon entropy for probes. """
    ee = {}
    for probe in probes:
        if probe[1] + probe[3] in ee:
            ee[probe[1] + probe[3]] += 1
        elif probe[3] + probe[1] in ee:
            ee[probe[3] + probe[1]] += 1
        else:
            ee[probe[1] + probe[3]] = 1

    # Calculate entropy.
    total = float(total)
    entropy = 0
    for key in ee.iterkeys():
        probability = ee[key] / total
        ld = log(probability, 2)
        entropy -= probability * ld
    return entropy / log(total, 2)
