#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Flag Measurement Set based on scalarly averaged data
"""

import argparse
import sys

import numpy as np
from astropy import units

from sunblocker.sunblocker import Sunblocker


def parse_args() -> argparse.Namespace:
    """Command line interface for SunBlocker"""
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )

    parser.add_argument(
        "inset",
        help="Input data set(s)",
        nargs="+",
        type=str,
    )
    parser.add_argument(
        "outset",
        help="Name of output data set or None, in which case outset = inset, in case of a list, must have the same length as inset",
        nargs="+",
        type=str,
    )
    parser.add_argument(
        "-c",
        "--col",
        help="Column name to base flagging on (e.g. 'DATA' or 'CORRECTED')",
        default="DATA",
        type=str,
    )
    parser.add_argument(
        "-ch",
        "--channels",
        help="File with bool array with True for channels to base the analysis on 'False' channels will be ignored",
        default=None,
        type=str,
    )
    parser.add_argument(
        "-b",
        "--baselines",
        help="File with nx2 array with antenna pairs for baselines to base the analysis on",
        default=None,
        type=str,
    )
    parser.add_argument(
        "-f",
        "--fields",
        help="Fields to select or None if all fields should be used",
        type=int,
        default=None,
    )
    parser.add_argument(
        "-i",
        "--imsize",
        help="Size of image in pixels",
        default=256,
        type=int,
    )
    parser.add_argument(
        "-e",
        "--cell",
        help="Size of cell in arcsec",
        default=1.0,
        type=float,
    )
    parser.add_argument(
        "-m",
        "--mode",
        help="Flagging based on 'all' data, repeated per 'antenna', or repeated per 'baseline'",
        default="all",
        choices=["all", "antenna", "baseline"],
        type=str,
    )
    parser.add_argument(
        "-p",
        "--pol",
        help="Polarization selection, Stokes 'i', or Stokes 'q'",
        default="i",
        choices=["i", "q"],
        type=str,
    )
    parser.add_argument(
        "-t",
        "--threshmode",
        help="Method to determine sigma, 'fit': fit Gaussian at the max to determine sigma, standard deviation otherwise",
        default="fit",
        choices=["fit", "std", "fixed", "mad"],
        type=str,
    )
    parser.add_argument(
        "-r",
        "--threshold",
        help="Distance from average beyond which data are flagged in units of sigma",
        default=5.0,
        type=float,
    )
    parser.add_argument(
        "-R",
        "--radrange",
        help="Each selected point is expanded in a wedge with this radial range",
        default=0.0,
        type=float,
    )
    parser.add_argument(
        "-a",
        "--angle",
        help="Each selected point is expanded in a wedge with this angular range",
        default=0.0,
        type=float,
    )
    parser.add_argument(
        "-v",
        "--vampirisms",
        help="Evaluate only daytime data",
        action="store_true",
    )
    parser.add_argument(
        "-A",
        "--avantsoleil",
        help="Time to be evaluated before sunrise in astropy units (defaults to 30 minutes)",
        default=30,
        type=float,
    )
    parser.add_argument(
        "-N",
        "--apresnuit",
        help="Time to be evaluated after sunrise in astropy units (defaults to 60 minutes)",
        default=60,
        type=float,
    )
    parser.add_argument(
        "-n",
        "--avantnuit",
        help="Time to be evaluated before sunset in astropy units (defaults to 60 minutes)",
        default=60,
        type=float,
    )
    parser.add_argument(
        "-O",
        "--apresoleil",
        help="Time to be evaluated after sunset in astropy units (defaults to 30 minutes)",
        default=30,
        type=float,
    )
    parser.add_argument(
        "-H",
        "--horizon",
        help="Height above horizon of the sun to define sunset in astropy units (defaults to -34 arcmin)",
        default=-34,
        type=float,
    )
    parser.add_argument(
        "--nononsoleil",
        help="Apply only on time windows around sunrise and sunset or on all day time data (if True, which is the default)",
        action="store_true",
    )
    parser.add_argument(
        "-u",
        "--uvmin",
        help="Restrict analysis to visibilities with a baseline b with uvmax > b > uvmin",
        default=0.0,
        type=float,
    )
    parser.add_argument(
        "-U",
        "--uvmax",
        help="Restrict analysis to visibilities with a baseline b with uvmax > b > uvmin",
        default=0.0,
        type=float,
    )
    parser.add_argument(
        "-d",
        "--flagonlyday",
        help="Flag only data taken at 'day' time, as defined by avantsoleil, apresnuit, avantnuit, apresoleil, nononsoleil",
        action="store_true",
    )
    parser.add_argument(
        "-s",
        "--show",
        help="Plot name for showing histogram and cutoff line in a viewgraph",
        default=None,
    )
    parser.add_argument(
        "-D",
        "--showdir",
        help="Directory to put viewgraphs in",
        default=".",
    )
    parser.add_argument(
        "-y",
        "--dryrun",
        help="Do not apply flags, but (e.g. produce viewgraphs only)",
        action="store_true",
    )
    parser.add_argument(
        "-V",
        "--verbose",
        help="Increase verbosity",
        action="store_true",
    )
    parser.add_argument(
        "--debug",
        help="Increase verbosity to debug level",
        action="store_true",
    )
    args = parser.parse_args()
    return args


def cli() -> None:
    args = parse_args()

    blocker = Sunblocker(
        verb=args.verbose,
        debug=args.debug,
    )
    if args.channels:
        args.channels = np.loadtxt(args.channels, dtype=bool)

    if args.baselines:
        args.baselines = np.loadtxt(args.baselines, dtype=int)

    blocker.phazer(
        inset=args.inset,
        outset=args.outset,
        col=args.col,
        channels=args.channels,
        baselines=args.baselines,
        fields=args.fields,
        imsize=args.imsize,
        cell=args.cell,
        mode=args.mode,
        pol=args.pol,
        threshmode=args.threshmode,
        threshold=args.threshold,
        radrange=args.radrange,
        angle=args.angle,
        flagonlyday=args.flagonlyday,
        vampirisms=args.vampirisms,
        avantsoleil=args.avantsoleil * units.s,
        apresnuit=args.apresnuit * units.s,
        avantnuit=args.avantnuit * units.s,
        apresoleil=args.apresoleil * units.s,
        horizon=-args.horizon * units.arcmin,
        nononsoleil=args.nononsoleil,
        uvmin=args.uvmin,
        uvmax=args.uvmax,
        show=args.show,
        showdir=args.showdir,
        dryrun=args.dryrun,
    )


if __name__ == "__main__":
    sys.exit(cli())
