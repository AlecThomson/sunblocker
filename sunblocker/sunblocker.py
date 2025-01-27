#!/usr/bin/env python3
"""
Class Sunblocker

Taylored towards method phazer, aimed at removing solar interference from interferometric Measurement Set (MS) data. See description therein. All other methods are helpers.

Methods:
    opensilent          - Opening inset with pyrap as a table suppressing any feedback from pyrap
    gaussian            - Gaussian function
    wedge_around_centre - Return a boolean array selecting points in a 2-D wedge
    histoclip           - Measure sigma and return a mask indicating data at a distance larger than threshold times sigma from the average
    readdata            - Open a data set inset and return a few tables
    phazer              - Flag Measurement Set based on scalarly averaged data

Copyright (c) 2017 Gyula Istvan Geza Jozsa, Paolo Serra, Kshitij Thorat, Sphesihle Makhatini, NRF (Square Kilometre Array South Africa) - All Rights Reserved

    
"""
import logging
import os
import sys

import astropy.coordinates as coordinates
import astropy.time as time
import astropy.units as units
import ephem
import matplotlib
import matplotlib.pyplot as plt
import numpy as np
import pyrap.tables as tables
import scipy.constants as scconstants
import scipy.optimize as opt
from astropy.stats import mad_std, sigma_clip
from matplotlib.patches import PathPatch
from matplotlib.path import Path
from scipy import stats
from tqdm.auto import tqdm

from sunblocker.loggers import logger

matplotlib.use("Agg")


class Sunblocker:
    def __init__(self, verb=False, debug=False):
        if verb:
            logger.setLevel(logging.INFO)
        if debug:
            logger.setLevel(logging.DEBUG)

    def opensilent(self, inset=None, readonly=True):
        """
        Opening inset with pyrap as a table suppressing any feedback from pyrap

        Input:
        inset (string): Input data set (string, pyrap table handle, or None)

        Output (pyrap table object): opensilent

        If inset is a string it's interpreted as file name, which is then opened, otherwise just returns the input.
        """

        # This is where we check what is opened
        if not isinstance(inset, str):
            return inset

        old_stdout = sys.stdout
        sys.stdout = open(os.devnull, "w")
        t = tables.table(inset, readonly=readonly)
        sys.stdout.close()
        sys.stdout = old_stdout
        return t

    def gaussian(self, x, cent, amp, sigma):
        """
        Gaussian function

        Input:
        cent (float): centre
        amp (float) : amplitude
        sigma (float) : sigma

        Return:
        gaussian() Gaussian
        """
        return amp * np.exp(-0.5 * np.power((x - cent) / sigma, 2))

    def wedge_around_centre(self, coord, radrange, angle):
        """
        Return a boolean array selecting points in a 2-D wedge

        Input:
        coord (array-like)    : coordinates of centre
        radrange (float)      : radial range of wedge
        angle (float)         : width of the wedge in degrees

        The radial range of the wedge is the radius of the centre plus and
        minus half of radrange, the angular range is given by the
        direction of the centre plus and minus angle/2. A boolean array is
        returned, True for all data points with uvcoords inside the wedge,
        False elsewhere.

        """

        # Number of points in an arc
        npoints = 100  # This should be enough

        alpha_min = np.arctan2(coord[0], coord[1]) - np.pi * angle / 180.0 / 2.0
        alpha_max = alpha_min + np.pi * angle / 180.0
        rmin = np.sqrt(np.power(coord[0], 2) + np.power(coord[1], 2)) - radrange / 2.0
        rmax = rmin + radrange

        # Generate arcs and the polygon
        a = np.linspace(alpha_min, alpha_max, npoints)
        s = np.sin(a)
        c = np.cos(a)
        arc1 = zip(rmax * s, rmax * c)
        arc2 = zip(rmin * np.flipud(s), rmin * np.flipud(c))
        polygon = arc1 + arc2 + [arc1[0]]
        path = Path(polygon)
        return path

    def selwith_path(self, path, uvcoords):
        """
        Return a boolean array indicating coordinates (pairs) inside a polygon path

        path                  : a path as returned by matplotlib.path.Path describing a polygon
        uvcoords (array-like) : nx2 array-like of coordinates of points

        Output:
        array-like(dtype = bool) selwith_wedge_around_centre:

        Returns a boolean array flagging points inside the polygon as True
        """

        # Create array of tuples
        points = np.array(zip(uvcoords[:, 0], uvcoords[:, 1]))
        boolarray = path.contains_points(points)
        return boolarray

    def histoclip(
        self,
        data,
        mask,
        gruvcoord,
        unflags=None,
        threshmode="fit",
        threshold=5.0,
        ax=None,
        title="",
    ):
        """Measure sigma and return a mask indicating data at a distance larger than threshold times sigma from the average

        Input:
        data (ndarray, type = float)   : Input data, one dimension
        mask (ndarray, type = bool)    : Mask indicating data points to ignore for evaluation, same shape as data
        unflags (ndarray, type = bool) : Mask indicating data points not to flag
        gruvcoord (nx2 ndarray, type = float): Array of the same size of data x 2 denoting grid positions of the data
        threshmode (string)            : Method to determine sigma, 'fit': fit Gaussian at the max to determine sigma, 'fixed': threshold is in absolute units (sigma is 1.), 'mad': use MAD statistics to derive standard deviation, otherwise standard deviation
        threshold (float)              : Distance from average beyond which data are flagged in units of sigma
        show (bool)                    : Show histogram to monitor what is happening
        title (string)                 : title of histogram

        Output:
        histoclip (ndarray, type = bool): Mask indicating points with a distance of larger than threshold*sigma from average (or peak position)

        Calculates amplitude of all points in data and average along the
        frequency axis. For these data calculate average and standard
        deviation (rms) of all points in data whose indices are not
        flagged as True in mask. If mode == 'fit' create a histogram and
        attempt to fit a Gaussian, replacing the rms with the fitted
        dispersion and the average with the centre of the Gaussian. If mode == 'mad', use MAD statistics to use the median instead of the mean, and a sigma using mad statistics. Then
        create an output mask histoclip, flagging all indices of data
        points in data, which have a positive offset by more than
        threshold times the sigma (rms or fitted dispersion) from the mean
        (average or fitted centre of the Gaussian) as True. Sigma means an
        absolute number if threshmode == 'fixed', the fitted sigma if
        threshmode == 'fitted', the standard deviation otherwise. Mean in
        this context means 0 if threshmode == 'fixed', the fitted centre
        if threshmode == 'fitted', the average otherwise. Unflags is a boolean list indicating indices for which the flags are subsequently removed (hereby excluding data points from a mask).

        """

        # Copy data
        av = np.copy(data)

        # This does not help
        # av[mask==True] = np.nan

        # Make a grid
        ugrid = np.unique(gruvcoord[:, 0])
        vgrid = np.unique(gruvcoord[:, 1])

        # Do this again. We really want a histogram only with values related to the unflagged visibilities
        uvgridded = np.zeros((ugrid.size * vgrid.size), dtype=float) + np.nan
        i = 0
        for uu in ugrid:
            for vv in vgrid:
                active_visibs = av[
                    (gruvcoord[:, 0] == uu) * (gruvcoord[:, 1] == vv) * (mask != True)
                ]
                if active_visibs.size == 0:
                    uvgridded[i] = np.nan
                else:
                    uvgridded[i] = active_visibs[0]
                i = i + 1

        # Average data, then look for shape
        # av = np.nanmean(ampar,axis=1)
        npoints = uvgridded[np.isfinite(uvgridded)].size
        logger.info("grid has {:d} nonzero points.".format(npoints))
        if npoints < 3:
            logger.info(
                "This is not sufficient for any statistics, returning no flags."
            )
            return np.zeros(av.shape, dtype=bool)

        # Do some sigma clipping
        uvgridded_clipped = sigma_clip(
            uvgridded,
            sigma=threshold,
            maxiters=None,
            stdfunc=mad_std,
            cenfunc=np.nanmedian,
            masked=False,
        )
        npoints_clipped = uvgridded_clipped[np.isfinite(uvgridded_clipped)].size

        # Find average and standard deviation
        average = np.nanmean(uvgridded_clipped)
        stdev = np.nanstd(uvgridded_clipped)

        logger.info("average: {}, stdev: {}".format(average, stdev))

        if average == np.nan:
            logger.info("cannot calculate average, returing no flags")
            return np.zeros(av.shape, dtype=bool)

        if stdev == np.nan:
            logger.info("cannot calculate standard deviation, returing no flags")
            return np.zeros(av.shape, dtype=bool)

        med = np.nanmedian(uvgridded_clipped)
        mad = mad_std(uvgridded_clipped)

        if threshmode == "fit" or ax != None:
            # Build a histogram
            hist, bin_edges = np.histogram(
                uvgridded_clipped[np.isfinite(uvgridded_clipped)],
                bins=int(np.sqrt(npoints_clipped)) + 1,
            )
            bin_centers = bin_edges[:-1] + 0.5 * (bin_edges[1:] - bin_edges[:-1])
            widthes = bin_edges[1:] - bin_edges[:-1]

            # Find maximum in histogram
            maxhi = np.amax(hist)
            maxhiposval = bin_centers[np.argmax(hist)]

            # Fit a Gaussian
            try:
                popt, pcov = opt.curve_fit(
                    self.gaussian,
                    bin_centers,
                    hist,
                    p0=[maxhiposval, maxhi, stdev / 2.0],
                )
            except:
                popt = np.array(
                    [
                        average,
                        widthes[0] * npoints / (np.sqrt(2 * np.pi) * stdev),
                        stdev,
                    ]
                )

        if threshmode == "abs":
            std = 1.0
            ave = 0.0
        if threshmode == "std":
            std = stdev
            ave = average
        if threshmode == "mad":
            std = mad
            ave = med
        if threshmode == "fit":
            std = popt[2]
            ave = popt[0]

        # Build a new mask based on the statistics and return it
        #    select = av <= average-threshold*stdev
        select = av >= ave + threshold * std
        if unflags is not None:
            select = select * np.logical_not(unflags)

        # Plot histogram and Gaussians
        if ax != None:
            # Calculating overplotted visibilities
            showgouse = np.linspace(
                1.5 * bin_centers[0] - 0.5 * bin_centers[1],
                1.5 * bin_centers[-1] - 0.5 * bin_centers[-2],
                200,
            )
            calculated = self.gaussian(
                showgouse,
                average,
                widthes[0] * npoints / (np.sqrt(2 * np.pi) * stdev),
                stdev,
            )

            # mad
            madded = self.gaussian(
                showgouse, med, widthes[0] * npoints / (np.sqrt(2 * np.pi) * mad), mad
            )

            # In case of using only stats, this is right on top
            fitted = self.gaussian(showgouse, popt[0], popt[1], popt[2])

            hists, bins, _ = ax.hist(
                uvgridded_clipped,
                bins=int(np.sqrt(npoints_clipped)) + 1,
                label="Clipped",
                alpha=0.7,
                density=True,
            )
            ax.hist(
                uvgridded,
                bins=int(np.sqrt(npoints_clipped)) + 1,
                label="Unclipped",
                alpha=0.5,
                density=True,
                zorder=10,
            )

            ax.plot(
                showgouse,
                calculated / calculated.max() * hists.max(),
                label="calculated",
            )
            ax.plot(showgouse, fitted / fitted.max() * hists.max(), label="fitted")
            ax.plot(showgouse, madded / madded.max() * hists.max(), label="mad")
            ax.axvline(
                x=average - threshold * stdev,
                linewidth=1,
                color="k",
                ls="--",
                label=f"Lower threshold = {average - threshold * stdev:0.1f}",
            )
            ax.axvline(
                x=average + threshold * stdev,
                linewidth=1,
                color="tab:red",
                ls="--",
                label=f"Upper threshold = {average + threshold * stdev:0.1f}",
            )
            ax.set_xlim(0, np.nanmax(uvgridded))
            ax.set_title(title)
            ax.set_xlabel("Amplitude")
            ax.set_ylabel("PDF")
            ax.legend()

        return select

    def readdata(
        self,
        inset=None,
        col="DATA",
        fields=None,
        channels=None,
        baselines=None,
        pol="i",
    ):
        """Open a data set inset and return a few tables

        Input:
        inset (str)            : Input data set, either string (which gets opened and closed) or a pyrap table handle, which will not be closed.
        col (str)              : Column name to base flagging on (e.g. 'DATA' or 'CORRECTED')
        fields (int)           : Fields to select or None if all fields should be used
        channels (bool array)  : dtype = bool array with True for channels to base the analysis on "False" channels will be ignored
        baselines (array)      : nx2 array with antenna pairs for baselines to base the analysis on
        pol (str)              : Polarization selection, Stokes 'i', or Stokes 'q'

        Output:
        readdate: data (complex array, array of single visibilities, Stokes I or Q per frequency), flags (bool array), uv (float array, uv coordinates), antenna1 (int array), antenna2 (int array), antennanames (string array)

        Will read in data set inset. If inset is a string it
        interprets as file name. If it is None, program will stop and
        assume a pyrap file handle otherwise. Then read data column
        col (can be 'DATA', 'CORRECTED', etc.). Apply flags, then
        calculate Stokes I or Q. Change flags to True for any
        visibility not in the specified fields (None means use
        everything). Change flags to True for anything not True in
        specified channels. Change flags to True for anything not
        contained in the baselines array. Then set all data read to
        numpy.nan if flags == True. Return data, flags,
        uv-coordinates, antenna1, antenna2, antennanames.

        """

        # We really don't want to hear about this
        if isinstance(inset, str):
            t = self.opensilent(inset)
        else:
            t = inset

        # Read column (think, axes are by default ordered as time, frequency, polarization) and flags, which should have same dimension
        logger.info("reading visibilities.")
        data = t.getcol(col)
        logger.info("reading original flags.")
        flags = t.getcol("FLAG")

        ### The following two lines belong to test 2
        # print '1: shape'
        # print data.shape
        ###

        # Divide uv coordinates by wavelength, for this use average frequencies in Hz
        # If bandwidth becomes large, we have to come up with something better
        logger.info("acquiring spectral information.")
        avspecchan = np.average(t.SPECTRAL_WINDOW.getcol("CHAN_FREQ"))
        logger.info(
            "average wavelength is {:.3f} m.".format(scconstants.c / avspecchan)
        )  # This is for testing: should be ~0.21 if local HI

        logger.info("reading and calculating approximate uv coordinates.")
        uv = t.getcol("UVW")[:, :2] * avspecchan / scconstants.c

        # Convert into desired stokes parameters and adjust mask
        i = data.shape[2] - 1
        stflags = np.logical_not(flags[:, :, 0]).astype(float) + np.logical_not(
            flags[:, :, i]
        ).astype(float)

        # if polarisation is i, then take either average or single value, flag the rest
        if pol == "i":
            logger.info("calculating Stokes I.")

            # Calculate stokes i, reduce the number of polarizations to one, flag if not at least one pol is available
            with np.errstate(divide="ignore", invalid="ignore"):
                data = (
                    data[:, :, 0] * np.logical_not(flags)[:, :, 0]
                    + data[:, :, i] * np.logical_not(flags)[:, :, i]
                ) / stflags
            flags = stflags < 1.0
        elif pol == "q":
            logger.info("calculating Stokes Q.")

            # Calculate stokes q, reduce the number of polarizations to one, flag everything if not both pols are available
            with np.errstate(divide="ignore", invalid="ignore"):
                data = (
                    data[:, :, 0] * np.logical_not(flags)[:, :, 0]
                    - data[:, :, i] * np.logical_not(flags)[:, :, i]
                ) / stflags
            flags = stflags < 2.0
        else:
            raise ("Polarisation must be i or q.")

        ### The following two lines belong to test 2
        # print '2: shape'
        # print data.shape
        ###

        #        flags[:,:,0] = np.logical_not((stflags.astype(bool)))

        # Also mask anything not listed in fields
        if fields is not None:
            logger.info("Selecting specified fields.")
            field = t.getcol("FIELD")
            select = np.zeros(field.shape, dtype=bool)
            if isinstance(fields, list):
                for i in fields:
                    select |= field == i
            else:
                select |= field == fields

            flags[np.logical_not(select), :] = True

        # Flag autocorrelations
        # print t.ANTENNA.getcol('NAME')[0]
        logger.info("reading antenna information.")
        antenna1 = t.getcol("ANTENNA1")
        antenna2 = t.getcol("ANTENNA2")

        logger.info("de-selecting autocorrelations (if any).")
        flags[antenna1 == antenna2] = True

        # Select channels and flag everything outside provided channels
        if channels is not None:
            logger.info("selecting specified channels.")
            flags[:, np.logical_not(channels)] = True

        # Select baselines and select everything outside provided baselines
        if baselines is not None:
            logger.info("selecting specified baselines.")
            flags[
                np.logical_not(
                    [
                        i in zip(np.array(baselines)[:, 0], np.array(baselines)[:, 1])
                        or i
                        in zip(np.array(baselines)[:, 1], np.array(baselines)[:, 0])
                        for i in zip(antenna1, antenna2)
                    ]
                )
            ] = True

        # Now put all flagged data to nan:
        logger.info("applying selections to data.")
        data[flags] = np.nan

        antennanames = t.ANTENNA.getcol("NAME")

        # Close only if this has been a string
        if isinstance(inset, str):
            t.close()

        return data, flags, uv, antenna1, antenna2, antennanames

    def phazer(
        self,
        inset=None,
        outset=None,
        col="DATA",
        channels=None,
        baselines=None,
        fields=None,
        imsize=512,
        cell=4,
        mode="all",
        pol="parallel",
        threshmode="fit",
        threshold=5.0,
        radrange=0.0,
        angle=0.0,
        flagonlyday=False,
        vampirisms=False,
        avantsoleil=0.0 * units.s,
        apresnuit=0.0 * units.s,
        avantnuit=0.0 * units.s,
        apresoleil=0.0 * units.s,
        horizon=-34.0 * units.arcmin,
        nononsoleil=True,
        uvmin=0.0,
        uvmax=None,
        show=None,
        showdir=".",
        dryrun=True,
    ):
        """Flag Measurement Set based on scalarly averaged data

        Input:
        inset (str or list of str)        : Input data set(s)
        outset (None, str, or list of str): Name of output data set or None, in which case outset = inset, in case of a list, must have the same length as inset
        col (str)                         : Column name to base flagging on (e.g. 'DATA' or 'CORRECTED')
        channels (array)                  : dtype = bool array with True for channels to base the analysis on "False" channels will be ignored
        baselines (array)      : nx2 array with antenna pairs for baselines to base the analysis on
        fields (int)           : Fields to select or None if all fields should be used
        imsize (int)           : Size of image in pixels
        cell (float)           : Size of cell in arcsec
        mode (str)             : Flagging based on 'all' data, repeated per 'antenna', or repeated per 'baseline'
        pol (str)              : Polarization selection, Stokes 'i', or Stokes 'q'
        threshmode (str)       : Method to determine sigma, 'fit': fit Gaussian at the max to determine sigma, standard deviation otherwise
        threshold (float)      : Distance from average beyond which data are flagged in units of sigma
        radrange (float)       : Each selected point is expanded in a wedge with this radial range
        angle (float)          : Each selected point is expanded in a wedge with this angular
        vampirisms (bool)      : Evaluate only daytime data
        avantsoleil (float)    : Time to be evaluated before sunrise in astropy units (defaults to 30 minutes)
        apresnuit   (float)    : Time to be evaluated after sunrise in astropy units (defaults to 60 minutes)
        avantnuit   (float)    : Time to be evaluated before sunset in astropy units (defaults to 60 minutes)
        apresoleil (float)     : Time to be evaluated after sunset in astropy units (defaults to 30 minutes)
        horizon (astropy angle): Height above horizon of the sun to define sunset in astropy units (defaults to -34 arcmin)
        nononsoleil (bool)     : Apply only on time windows around sunrise and sunset or on all day time data (if True, which is the default)
        uvmin (float)          : Restrict analysis to visibilities with a baseline b with uvmax > b > uvmin
        uvmax (float)          : Restrict analysis to visibilities with a baseline b with uvmax > b > uvmin
        flagonlyday (bool)     : Flag only data taken at "day" time, as defined by avantsoleil, apresnuit, avantnuit, apresoleil, nononsoleil
        horizon (astropy angle): Height above horizon of the sun to define sunset in astropy units (defaults to -34 arcmin)
        show (bool)            : Show histogram and cutoff line in a viewgraph
        showdir (str)          : Directory to put viewgraphs in
        dryrun (bool)          : Do not apply flags, but (e.g. produce viewgraphs only)

        Takes a number of input visibilities (column given by col) and
        selects a sub-set using the selection criteria col, channels
        (selecting channel ranges), baselines (a list of selected
        baselines), and fields (list of selected fields). Then grids
        the visibilities according to the corresponding image
        dimensions, where imsize is the size of the image in pixels
        and cell is the size of the cell in arcsec ( uv cell in lambda
        is 1./(imsize*cell*np.pi/(3600.*180.)), and the size in lambda
        is 1./(cell*np.pi/(3600.*180.)), such that uvmax can be chosen
        to be 1./(2*cell*np.pi/(3600.*180.)) ). In this process the
        assumed frequency to express uv coordinates in units of
        wavelength is the average frequency in the data set. Notice
        that this is not precise for a large bandwith. The
        visibilities are converted to Stokes parameters according to
        the parameter 'pol', then vectorially gridded onto the
        calculated grid, then the absolutes are calculated
        (alternatively: the PHAses are set to ZERo) and then the
        visibilities are averaged along the frequency axis. Then from
        this data product, some visibilities are flagged using a
        clipping technique. This can be done using all baselines in
        one go, all antennas in one go (repeated for each antenna),
        all baselines (repeated per baseline). For these data average
        and standard deviation (rms) of all points in data are
        calculated whose indices are not flagged as True in mask. If
        mode == 'mad', median and standard deviation calculated from
        mad statistics are used. If mode == 'fit' a histogram is
        created and attempt to fit a Gaussian, replacing the rms with
        the fitted dispersion and the average with the centre of the
        Gaussian. Then all data with a positive distance of greater
        than threshold times sigma (either fitted or standard
        deviation) from the mean (or position of fitted Gaussian) are
        flagged (lipped). If mode == 'absolute', all data above the
        absolute value given in threshold are clipped instead. Each
        such flagged data point can be extended by a wedge, inside
        which all data points are flagged.  The radial range of the
        wedge is the radius of the point (distance from origin) plus
        and minus half of radrange, the angular range is given by the
        direction of the centre (position angle with respect to the
        centre) plus and minus angle/2.  Finally the flags are
        expanded in the polarisation and frequency direction and
        applied to the output data, which have to have the dimension
        of the input data. If the output data are of None type, it is
        assumed that the flags should be applied to the input data. If
        the output data have a name, then they are copies of the input
        data sets if they do not exist, otherwise the flags will be
        applied to the output data sets instead of the input data
        sets. If show is set to None, no plots will be produced. If
        show is a string type, hardcopy output plots (hist_$show and
        select_$show) will be produced, one showing the histograms,
        two Gaussians (red: fitted, green: according to mean and
        standard deviation), and the threshold position, and the other
        the uv coverage with the gridded values and the flagged data
        (red) and flagged data before wedge expansion (green).

        """
        logger.info("start.")

        # Open data set as table
        logger.info("opening input files.")

        if inset is None:
            logger.info("No input. Stopping.")
            logger.info("exiting (successfully).")

        if isinstance(inset, str):
            inset = [inset]

        if len(inset) == 1:
            logger.info("reading one data set.")
        else:
            logger.info("reading {:d} data sets.".format(len(inset)))

        # Let's do this the primitive way, first read a data set then append everything else
        logger.info("reading {:s}.".format(inset[0]))

        nrows = [
            0,
        ]
        tutu = self.opensilent(inset[0])
        data, flags, uv, antenna1, antenna2, antennanames = self.readdata(
            tutu,
            col=col,
            fields=fields,
            channels=channels,
            baselines=baselines,
            pol=pol,
        )

        #        print 'flags shape', flags.shape
        #        print 'data shape', data.shape
        #        print 'any?', np.any(flags)

        # This is a list where all visibilities taken by night are set to True, False otherwise)
        if vampirisms or flagonlyday:
            dayflags = self.vampirisms(
                tutu,
                dryrun=True,
                avantsoleil=avantsoleil,
                apresnuit=apresnuit,
                avantnuit=avantnuit,
                apresoleil=apresoleil,
                horizon=horizon,
                nononsoleil=nononsoleil,
                flinvert=True,
            )

        # Now additionally flag all night visibilities if user wants
        if vampirisms:
            logger.info("applying vampirisms to dataset {:s}.".format(inset[0]))

            #            print'finding out about dayflags:'
            #            print np.all(dayflags)
            flags[dayflags, :] = True
            data[flags] = np.nan

        tutu.close()
        nrows.append(data.shape[0])

        for i in tqdm(range(1, len(inset)), desc="Reading data"):
            logger.info("reading {:s}.".format(inset[i]))
            tutu = self.opensilent(inset[i])
            (
                dataplus,
                flagsplus,
                uvplus,
                antenna1plus,
                antenna2plus,
                antennanamesplus,
            ) = self.readdata(
                tutu,
                col=col,
                fields=fields,
                channels=channels,
                pol=pol,
            )
            if vampirisms or flagonlyday:
                dayflagsplus = self.vampirisms(
                    tutu,
                    dryrun=True,
                    avantsoleil=avantsoleil,
                    apresnuit=apresnuit,
                    avantnuit=avantnuit,
                    apresoleil=apresoleil,
                    horizon=horizon,
                    nononsoleil=nononsoleil,
                    flinvert=True,
                )

            if vampirisms:
                logger.info("applying vampirisms to dataset {:s}.".format(inset[i]))
                # This flags all visibilities taken by night (sets those visibs to True)

                flagsplus[dayflagsplus, :] = True
                dataplus[flagsplus] = np.nan
            tutu.close()

            data = np.concatenate((data, dataplus), axis=0)
            flags = np.concatenate((flags, flagsplus), axis=0)
            if vampirisms or flagonlyday:
                dayflags = np.concatenate((dayflags, dayflagsplus), axis=0)
            uv = np.concatenate((uv, uvplus), axis=0)
            antenna1 = np.concatenate((antenna1, antenna1plus), axis=0)
            antenna2 = np.concatenate((antenna2, antenna2plus), axis=0)

            # Antenna names is different. Just check if they are the same
            if not np.all(antennanames == antennanamesplus):
                logger.warning(
                    """
                It appears that the antennas in data sets differ.
                This means that baseline selection (using parameter baselines) should not be used.
                This means that only model 'all' should be used.
                """
                )
            nrows.append(data.shape[0])

        logger.info("gridding visibilities (vector sum) and then building")
        logger.info("scalar average of amplitudes along velocity axis.")
        duv = 1.0 / (imsize * cell * np.pi / (3600.0 * 180.0))  # UV cell in lambda
        u = uv[:, 0]
        v = uv[:, 1]

        umin = u.min()  # Minimum in u
        vmin = v.min()  # Minimum in v
        umax = u.max()  # Maximum in u
        vmax = v.max()  # Maximum in v

        # Now care about uvrange. Caution! This is a dead statement!!!
        # uvflags = (u*0).astype(bool)

        if uvmax != None:
            umin = -uvmax
            umax = uvmax
            vmin = -uvmax
            vmax = uvmax
        #            uvflags = u*u+v*v > uvmax*uvmax

        # Flag everything outside uvrange
        #        uvflags += u*u+v*v < uvmin*uvmin
        #        data[uvflags,:] = np.nan

        logger.info(
            "approximate minimum u is {0:.0f} and \nthe maximum u is {1:.0f}\napproximate minimum v is {2:.0f} and \nthe maximum v is {3:.0f}".format(
                umin, umax, vmin, vmax
            )
        )
        umin, umax = np.floor(umin), np.ceil(
            umax
        )  # Make sure that all visibilities are included in grid in the next step
        vmin, vmax = np.floor(vmin), np.ceil(
            vmax
        )  # Make sure that all visibilities are included in grid in the next step
        ugrid = np.arange(
            umin, umax, duv
        )  # Notice that umax is not necessarily contained in an array like this, hence the step before
        vgrid = np.arange(
            vmin, vmax, duv
        )  # Notice that vmax is not necessarily contained in an array like this, hence the step before

        # Check if all uv coordinates are somewhere in the grid
        # print umin, ugrid[0], umax, ugrid[-1]
        # print vmin, vgrid[0], vmax, vgrid[-1]

        # Griddedvis are for the viewgraph
        if show != None:
            griddedvis = np.zeros((ugrid.size, vgrid.size), dtype=float)

        # For the sake of efficiency create an array that replaces data
        #        nmdata = np.ones(data[:,0].size, dtype = float)

        nmdata = np.zeros(data[:, 0].size, dtype=float)

        # Keeping track of central uv coordinates, required for histogram later on
        gruvcoord = (
            np.append(np.copy(nmdata), np.copy(nmdata))
            .reshape((2, nmdata.shape[0]))
            .transpose()
        )
        # Caution!!! The following would not create an independent copy but is equivalent to grucoord = nmdata. This also works for sub-arrays.
        # grucoord = nmdata[:]

        ### The following three lines belong to test 1
        # testdata = np.zeros(data[:,0].size, dtype = bool)
        # print 'Is any value of the boolean array testdata True (should be False)?'
        # print np.any(testdata)
        ###

        ### The following line belongs to test 3: plot gridded visibs
        k = 0
        ###

        collaflags = np.all(flags, axis=1)
        for uu in tqdm(ugrid, desc="Looping over u"):
            for vv in tqdm(vgrid, desc="Looping over v", leave=False):
                #                active_visibs = (u > uu)*(u <= (uu+duv))*(v > vv)*(v <= (vv+duv))
                if uvmax == None:
                    active_visibs = (
                        (u > uu)
                        * (u <= (uu + duv))
                        * (v > vv)
                        * (v <= (vv + duv))
                        * (u * u + v * v > uvmin * uvmin)
                    )
                else:
                    active_visibs = (
                        (u > uu)
                        * (u <= (uu + duv))
                        * (v > vv)
                        * (v <= (vv + duv))
                        * (u * u + v * v < uvmax * uvmax)
                        * (u * u + v * v > uvmin * uvmin)
                    )

                active_visibs[collaflags] = False
                if np.any(active_visibs):
                    # gruvcoord[active_visibs,:] = uu, vv # Central pixel coordinate
                    # Central pixel becomes important when doing wedges
                    gruvcoord[active_visibs, :] = (
                        uu + duv / 2.0,
                        vv + duv / 2.0,
                    )  # Central pixel coordinate

                    ### The following line belongs to test 1
                    # testdata[active_visibs] = True
                    ###
                    scav = np.nanmean(
                        np.abs(np.nansum(data[active_visibs], axis=0))
                    )  # Scalar average of amplitude of vectorial sum of visibilities in cell
                    nmdata[
                        active_visibs
                    ] = scav  # set all visibilities in that cell to same cell value
                    ### The following 4 lines belong to test 3: plot gridded visibs
                    # if k > 100:
                    #    print 'Please find and confirm pixel coordinates and values in the greyscale plot: u: %f v: %f value: %f' % (uu+duv/2, vv+duv/2, scav)
                    #    k = 0
                    # k = k+1
                    ###

                    if show != None:
                        griddedvis[ugrid == uu, vgrid == vv] = scav  # For plotting

        # This is the scalar average in frequency
        data = nmdata

        ### The following two lines belong to test 2
        # print '3: shape'
        # print data.shape
        ###

        ### The following 6 lines belong to test 1
        # print 'Have all visibilities been addressed?'
        # print np.all(testdata)
        # x = (gruvcoord == 0.)
        # print 'Has any visibility been assigned a cell coordinate of 0 (Possible but unlikely)?'
        # print np.any(x)
        # sys.exit()
        ###

        if show != None:
            logger.info(
                "Plotting gridded scalar average of amplitudes along velocity axis."
            )
            plt.imshow(
                np.flip(griddedvis, axis=0).transpose(),
                vmin=np.nanmin(griddedvis),
                vmax=np.nanmax(griddedvis),
                cmap="cubehelix_r",
                origin=("lower"),
                interpolation="nearest",
                extent=[ugrid.max() + duv, ugrid.min(), vgrid.min(), vgrid.max() + duv],
            )
            plt.colorbar()
            plt.xlabel("u / $\lambda$")
            plt.ylabel("v/ $\lambda$")
            if isinstance(show, str):
                savefile = os.path.join(showdir, "griddedvis_" + show)
                plt.savefig(savefile, dpi=300, bbox_inches="tight")
                plt.close()
            else:
                plt.show()
                plt.close()

        # Now build the mask, depending on the mode
        logger.info("clipping data based on scalar averaging")

        # The flags are in the data (using nan operations), so we can neglect them here.
        flags = np.zeros(data.shape, dtype=bool)
        if flagonlyday:
            unflags = dayflags
        else:
            unflags = None

        if mode == "all":
            logger.info("mode 'all', filtering all data at once.")
            if show == None:
                ax = None
            else:
                ax = plt.subplot(1, 1, 1)
            newflags = self.histoclip(
                data,
                flags,
                gruvcoord,
                unflags=unflags,
                threshmode=threshmode,
                threshold=threshold,
                ax=ax,
            )
        else:
            newflags = np.zeros(data.shape, dtype=bool)
            antennas = np.unique(np.append(antenna1, antenna2))
            if mode == "antenna":
                logger.info("Phazer, mode 'antenna', filtering data per antenna.")
                if show != None:
                    nplotsx = int(np.ceil(np.sqrt(antennas.size)))
                    i = 0
                for antenna in antennas:
                    logger.info(
                        "filtering antenna {0:d}: {1:s}".format(
                            antenna, t.ANTENNA.getcol("NAME")[antenna]
                        )
                    )
                    passedflags = np.zeros(data.shape, dtype=bool)
                    select = antenna1 != antenna
                    select &= antenna2 != antenna
                    passedflags[select, :] |= True
                    if show != None:
                        title = "Ant " + antennanames[antenna]
                        ax = plt.subplot(nplotsx, nplotsx, i)
                    else:
                        ax = None
                    newflags |= self.histoclip(
                        data,
                        passedflags,
                        gruvcoord,
                        unflags=unflags,
                        threshmode=threshmode,
                        threshold=threshold,
                        ax=ax,
                        title=title,
                    )
                    i = i + 1
            else:
                logger.info("mode 'baseline', filtering data per antenna.")
                antennas1 = np.unique(antenna1)
                antennas2 = np.unique(antenna2)
                pairs = np.unique(np.column_stack((antenna1, antenna2)))
                # Let's guess this
                if show != None:
                    nplotsx = int(np.ceil(np.sqrt(pairs.size)))
                    i = 0
                for pair in pairs:
                    if pair[0] != pair[1]:
                        logger.info(
                            "Filtering baseline between antenna {0:d}: {1:s} and {2:d}: {3:s}".format(
                                pair[0],
                                antennanames[pair[0]],
                                pair[1],
                                antennanames[pair[1]],
                            )
                        )
                        passedflags = np.zeros(data.shape, dtype=bool)
                        select = antenna1 != pair[0]
                        select &= antenna2 != pair[1]
                        passedflags[select, :] |= True
                        if show != None:
                            title = (
                                "Pair "
                                + antennanames[pair[0]]
                                + ","
                                + antennanames[pair[1]]
                            )
                            ax = plt.subplot(nplotsx, nplotsy, i)
                        else:
                            ax = None
                        newflags |= self.histoclip(
                            data,
                            passedflags,
                            grucoord,
                            grvcoord,
                            unflags=unflags,
                            threshmode=threshmode,
                            threshold=threshold,
                            ax=ax,
                            title=title,
                        )
                    i = i + 1
        if show != None:
            if isinstance(show, str):
                plt.savefig(
                    showdir + "/" + "histo_" + show, dpi=300, bbox_inches="tight"
                )
                plt.close()
            else:
                plt.show()
                plt.close()

        if show != None:
            patches = []

        # Extend the new flags, first make a copy of the flags
        if radrange > 0.0 and angle > 0.0:
            logger.info(
                "extending flags to nearby pixels in the uv-plane using radrange: {0:.0f} and angle: {1:.0f}".format(
                    radrange, angle
                )
            )
            flaggeduv = uv[np.column_stack((newflags, newflags))]
            flaggeduv = flaggeduv.reshape(flaggeduv.size // 2, 2)
            befflaggeduv = flaggeduv.copy()

            logger.info("processing {:d} points.".format(flaggeduv.size // 2))
            for i in range(flaggeduv.size // 2):
                if i % 500 == 0:
                    logger.info("extended {:d} points.".format(i))
                thepath = self.wedge_around_centre(flaggeduv[i, :], radrange, angle)
                if flagonlyday:
                    newflags[self.selwith_path(thepath, uv) * unflags] = True
                else:
                    newflags[self.selwith_path(thepath, uv)] = True
                if show != None:
                    patches.append(
                        PathPatch(thepath, facecolor="orange", lw=0, alpha=0.1)
                    )

        # Plot data and flags
        if show != None:
            logger.info(
                "plotting flagged data positions onto gridded and averaged visibilities."
            )
            # ax = plt.imshow(np.flip(griddedvis,axis=0).transpose(),vmin=np.nanmin(griddedvis),vmax=np.nanmax(griddedvis),cmap='Greys', origin=('lower'),interpolation='nearest', extent = [ugrid.max()+duv, ugrid.min(), vgrid.min(), vgrid.max()+duv])
            average = np.nanmean(griddedvis)
            stdev = np.nanstd(griddedvis)
            fig = plt.figure()
            ax = plt.subplot(1, 1, 1)
            # plt.imshow(np.flip(griddedvis,axis=0).transpose(),vmin=np.maximum(np.nanmin(griddedvis),average-threshold*stdev),vmax=np.minimum(np.nanmax(griddedvis),average+threshold*stdev),cmap='Greys', origin=('lower'),interpolation='nearest', extent = [ugrid.max()+duv, ugrid.min(), vgrid.min(), vgrid.max()+duv])
            # plt.xlabel('u')
            # plt.ylabel('v')
            im = ax.imshow(
                np.flip(griddedvis, axis=0).transpose(),
                vmin=np.maximum(np.nanmin(griddedvis), average - threshold * stdev),
                vmax=np.minimum(np.nanmax(griddedvis), average + threshold * stdev),
                cmap="cubehelix_r",
                origin=("lower"),
                interpolation="nearest",
                extent=[ugrid.max() + duv, ugrid.min(), vgrid.min(), vgrid.max() + duv],
            )
            fig.colorbar(im)
            ax.set_xlabel("u / $\lambda$")
            ax.set_ylabel("v / $\lambda$")
            for patch in patches:
                ax.add_patch(patch)
            flaggeduv = uv[np.column_stack((newflags, newflags))]
            flaggeduv = flaggeduv.reshape(flaggeduv.size // 2, 2)
            #            print uv[:,0].shape
            #            print newflags.shape
            selc = (
                np.logical_not(newflags)
                * (uv[:, 0] <= umax)
                * (uv[:, 1] <= vmax)
                * (uv[:, 0] >= umin)
                * (uv[:, 1] >= vmin)
            )
            notflaggeduv = uv[np.column_stack((selc, selc))]
            notflaggeduv = notflaggeduv.reshape(notflaggeduv.size // 2, 2)
            # Restrict uvrange to maximum of uvmax and befflaggeduv
            ####

            ax.plot(
                notflaggeduv[:, 0],
                notflaggeduv[:, 1],
                ".b",
                markersize=0.3,
                label="Not flagged",
                rasterized=True,
            )
            ax.plot(
                flaggeduv[:, 0],
                flaggeduv[:, 1],
                ".r",
                markersize=0.3,
                label="Flagged",
                zorder=10,
            )
            ax.legend()
            if radrange > 0.0 and angle > 0.0:
                ax.plot(befflaggeduv[:, 0], befflaggeduv[:, 1], ".g", markersize=0.3)
            if isinstance(show, str):
                plt.savefig(
                    showdir + "/" + "select_" + show, dpi=300, bbox_inches="tight"
                )
                plt.close()
            else:
                plt.show()
                plt.close()

        if isinstance(outset, str):
            outset = [outset]

        if outset == None:
            outset = inset
        else:
            if len(outset) != len(inset):
                raise (
                    "number of outsets is not equal to number of insets, cowardly stopping application of flags."
                )

        for i in range(len(outset)):
            if tables.tableexists(outset[i]):
                logger.info("opening data set {:s}".format(outset[i]))
                if not dryrun:
                    tout = self.opensilent(outset[i], readonly=False)
                else:
                    logger.info("it's a simulation (dry run)")
            else:
                logger.info(
                    "data set {0:s} does not exist. Copying it from data set {1:s}".format(
                        outset[i], inset[i]
                    )
                )
                if not dryrun:
                    t = self.opensilent(inset[i])
                    tout = t.copy(outset[i])
                    tout.close()
                    t.close()
                    tout = self.opensilent(outset[i], readonly=False)
                else:
                    logger.info("it's a simulation (dry run)")

            # Now apply newflags to the data
            logger.info("applying new flags.")
            if not dryrun:
                flags = tout.getcol("FLAG")
                flags[newflags[nrows[i] : nrows[i + 1]], :, :] = True
            else:
                logger.info("it's a simulation (dry run)")

            logger.info("writing flags.")
            if not dryrun:
                tout.putcol("FLAG", flags)
                tout.close()
            else:
                logger.info("it's a simulation (dry run).")
        logger.info("exiting (successfully).")
        return

    def astropy_to_pyephemtime(self, astropytime):
        return astropytime.mjd - 15019.5

    def vampirisms(
        self,
        inset,
        lat=None,
        lon=None,
        hei=None,
        dryrun=True,
        avantsoleil=0.0 * units.s,
        apresnuit=0.0 * units.s,
        avantnuit=0.0 * units.s,
        apresoleil=0.0 * units.s,
        horizon=-34.0 * units.arcmin,
        nononsoleil=True,
        flinvert=False,
    ):
        """Identifies times in a data set where sun is up or down and provides flags

        Input:
        inset (str)            : Input data set or already open pyrap file handle
        lon (astropy angle)    : Longitude of observatory in astropy units (defaults to what is found in the data set)
        lat (astropy angle)    : Latitude of observatory in astropy units (defaults to what is found in the data set)
        hei (astropy length)   : Elevation of observatory in astropy units (defaults to what is found in the data set)
        dryrun (str)           : Flag data if True, only write comments otherwise (defaults to True)
        avantsoleil (float)    : Time to be flagged before sunrise in astropy units (defaults to 0)
        apresnuit   (float)    : Time to be flagged after sunrise in astropy units (defaults to 0)
        avantnuit   (float)    : Time to be flagged before sunset in astropy units (defaults to 0)
        apresoleil (float)     : Time to be flagged after sunset in astropy units (defaults to 0)
        nononsoleil (bool)     : Flag all day time (defaults to True)
        horizon (astropy angle): Height above horizon of the sun to define sunset in astropy units (defaults to -34 arcmin)
        flinvert (bool)        : Invert flags before applying/returning them (defaults to False)

        Output: binary table with flags

        Takes data set inset as input data set and calculates sunrise
        and -set times, under the assumption of terrestrial longitude
        lon, latitude lat, elevation hei in astropy units. Then
        creates a binary array with flags (True if flagged), which
        gets returned at the end. The flags are set for times from
        sunrise-avantsoleil to sunrise+apresnuit and sunset-avantnuit
        to sunset+apresoleil. If nononsoleil is True, also the day
        times between sunrise and sunset are flagged. With horizon the
        height above the horizon is defined, crossing which defines
        sunset and sunrise. Flinvert inverts the flags before return,
        such that they are True where they were False before and vice
        versa, i.e. the times around sunrise and sunset and
        potentially in-between (in day time) are not flagged, while
        the rest is. If dryrun is set to False, the flags are applied
        to the data.

        """

        logger.info("start.")

        if dryrun:
            logger.info("this is a dry run. Flags will not be applied.")
            drypre = "Because of dry run not"
        else:
            drypre = ""
        if avantsoleil != 0.0:
            logger.info(
                "{0:s} flagging starts {1:s} before sunrise.".format(
                    drypre, avantsoleil
                )
            )
        else:
            logger.info("{0:s} flagging starts at sunrise".format(drypre))
        if not nononsoleil:
            if apresnuit != 0.0:
                logger.info(
                    "{0:s} flagging ends {1:s} after sunrise.".format(drypre, apresnuit)
                )
            else:
                logger.info("{:s} flagging ends at sunrise".format(drypre))
            if avantnuit != 0.0:
                logger.info(
                    "{0:s} flagging starts {1:s} before sunset.".format(
                        drypre, avantnuit
                    )
                )
        if apresoleil != 0.0:
            logger.info(
                "{0:s} flagging ends {1:s} after sunset.".format(drypre, apresoleil)
            )
        else:
            logger.info("{0:s} flagging ends at sunset".format(drypre))

        if horizon != 0.0:
            logger.info(
                "we think that the sun has really set (oh how I hate it!) when its centre is {:s} below the horizon".format(
                    -horizon
                )
            )

        # We really don't want to hear about this
        if isinstance(inset, str):
            logger.info("opening visibility file {:s}.".format(inset))
        else:
            logger.info("opening visibility file {:s}.".format(inset.name()))

        # This is either a string or a data set, it will return the right thing
        if dryrun:
            t = self.opensilent(inset=inset)
        else:
            t = self.opensilent(inset=inset, readonly=False)

        antennapos = t.ANTENNA.getcol("POSITION")
        telgeo = np.average(antennapos, axis=0) * units.meter

        # In theory the units could be different, but they aren't
        telunit = t.ANTENNA.POSITION.QuantumUnits
        for i in telunit:
            if i != "m":
                raise ("Unknown unit in visibility file.")
        telpos = coordinates.EarthLocation(x=telgeo[0], y=telgeo[1], z=telgeo[2])
        lone = telpos.geodetic.lon
        if lon != None:
            lone = lon
        late = telpos.geodetic.lat
        if lat != None:
            late = lat
        heie = telpos.geodetic.height
        if hei != None:
            heie = hei
        telpos = coordinates.EarthLocation(lon=lone, lat=late, height=heie)

        logger.info(
            "It appears that the observatory latitude is {0:s}, the longitude {1:s}, and the height {2:s}".format(
                telpos.geodetic.lon, telpos.geodetic.lat, telpos.geodetic.height
            )
        )

        # Read time stamps
        logger.info("reading time stamps.")
        dd = t.getcol("TIME") / (24.0 * 3600.0)
        times = time.Time(dd, format="mjd", scale="utc")
        mindd = np.amin(dd) - t.getcol("INTERVAL")[0] / (2.0 * 24.0 * 3600.0)
        obstart = time.Time(mindd, format="mjd", scale="utc")
        # obstart = np.amin(times)
        eobstart = ephem.Date(self.astropy_to_pyephemtime(obstart))
        maxdd = np.amax(dd) + t.getcol("INTERVAL")[-1] / (2.0 * 24.0 * 3600.0)
        obsend = time.Time(maxdd, format="mjd", scale="utc")
        # obsend = np.amax(times)
        eobsend = ephem.Date(self.astropy_to_pyephemtime(obsend))
        logger.info("the observation started at {:s} (UTC)".format(obstart.iso))
        logger.info("the observation ended at {:s} (UTC)".format(obsend.iso))

        etimes = self.astropy_to_pyephemtime(times)

        # Pyephem stuff
        etel = ephem.Observer()
        etel.lon = telpos.geodetic.lon.to(units.rad).value
        etel.lat = telpos.geodetic.lat.to(units.rad).value
        etel.elevation = telpos.geodetic.height.to(units.m).value
        etel.horizon = horizon.to(units.rad).value
        esun = ephem.Sun()

        # Now find the status at the beginning of the observations
        etel.date = eobstart

        # Here, esti is the next sunrise and eeti is the next sunset
        esti = etel.next_rising(esun)
        eeti = etel.next_setting(esun)

        ncross = 0

        if esti > eeti:
            # From here on, esti is the start of a daylight period during the observation and eeti the end of it
            esti = eobstart
            logger.info(
                "at the beginning of the observation the sun (urgh!) was above the horizon. Not a good time."
            )
        else:
            logger.info(
                "at the beginning of the observation the sun (baah!) was b-lo the horizon. This is our time! Hoahahaha! Ha!"
            )
            esti = etel.next_rising(esun)
            if esti < eobsend:
                ncross += 1
                logger.info(
                    "the sun (yuck!) rose at {:s} (UTC)".format(
                        esti.datetime().strftime("%Y-%m-%d, %H:%M:%S")
                    )
                )

        flags = np.zeros(dd.size, dtype=bool)

        while (esti - float(avantsoleil.to(units.d).value)) < eobsend:
            etel.date = esti
            eeti = etel.next_setting(esun)

            if eeti < eobsend:
                ncross += 1
                logger.info(
                    "the sun (hrgh!) set at {:s} (UTC), time to rise, Ha! HA, HAHA!".format(
                        eeti.datetime().strftime("%Y-%m-%d, %H:%M:%S")
                    )
                )

            if nononsoleil:
                # Only one bracket, add times
                estiapp = float(esti) - float(avantsoleil.to(units.d).value)
                eetiapp = float(eeti) + float(apresoleil.to(units.d).value)
                estihad = max(estiapp, eobstart)
                eetihad = min(eetiapp, eobsend)
                logger.info(
                    "{0:s} flagging between {1:s} (UTC) and {2:s} (UTC).".format(
                        drypre,
                        ephem.Date(estihad).datetime().strftime("%Y-%m-%d, %H:%M:%S"),
                        ephem.Date(eetihad).datetime().strftime("%Y-%m-%d, %H:%M:%S"),
                    )
                )
                flags = flags + (etimes >= float(estiapp)) * (float(eetiapp) >= etimes)
            else:
                # Two brackets, add times
                estiapp = float(esti) - float(avantsoleil.to(units.d).value)
                eetiapp = float(esti) + float(apresnuit.to(units.d).value)
                estiapp2 = float(eeti) - float(avantnuit.to(units.d).value)
                eetiapp2 = float(eeti) + float(apresoleil.to(units.d).value)
                estihad = max(estiapp, eobstart)
                eetihad = min(eetiapp2, eobsend)
                logger.info(
                    "{0:s} flagging between {1:s} (UTC) and {2:s} (UTC) and".format(
                        drypre,
                        ephem.Date(estihad).datetime().strftime("%Y-%m-%d, %H:%M:%S"),
                        ephem.Date(eetiapp).datetime().strftime("%Y-%m-%d, %H:%M:%S"),
                    )
                )
                if estiapp2 < eobsend:
                    logger.info(
                        "{0:s} flagging between {1:s} (UTC) and {2:s} (UTC).".format(
                            drypre,
                            ephem.Date(estiapp2)
                            .datetime()
                            .strftime("%Y-%m-%d, %H:%M:%S"),
                            ephem.Date(eetihad)
                            .datetime()
                            .strftime("%Y-%m-%d, %H:%M:%S"),
                        )
                    )
                flags = flags + (etimes >= float(estiapp)) * (float(eetiapp) >= etimes)
                flags = flags + (etimes >= float(estiapp2)) * (
                    float(eetiapp2) >= etimes
                )

            # Get next sunrise by setting the current time to the current sunset and requesting next sunrise
            etel.date = eeti
            esti = etel.next_rising(esun)
            if esti < eobsend:
                ncross += 1
                logger.info(
                    "the sun (aargh!) rose at {:s}".format(
                        esti.datetime().strftime("%Y-%m-%d, %H:%M:%S")
                    )
                )

        addendum = ""
        if ncross > 1:
            logger.info(
                "the sun crossed the horizon {:d} times during the observation".format(
                    ncross
                )
            )
        else:
            if ncross > 0:
                logger.info(
                    "the sun (yargl!) crossed the horizon once during the observation"
                )
            else:
                logger.info(
                    "the sun (woe!) never crossed the horizon during the observation"
                )
                addendum = "still "

        if esti > eeti:
            logger.info(
                "at the end of the observation the sun (Uuuh!) was {:s} up.".format(
                    addendum
                )
            )
        else:
            logger.info(
                "at the end of the observation it was {:s} a beautiful night.".format(
                    addendum
                )
            )

        # Invert flags
        if flinvert:
            logger.info(
                "inverting flags, that means {:s} flag everything in the night (terrible)!".format(
                    drypre
                )
            )
            flags = np.logical_not(flags)

        # Now apply flags
        logger.info("{:s} applying flags to data.".format(drypre))
        if not dryrun:
            oflags = t.getcol("FLAG")
            oflags[flags, :, :] = True
            t.putcol("FLAG", oflags)

        logger.info("finis.")

        if isinstance(t, str):
            t.close()

        return flags


# if __name__ == "__main__":
#     a = np.zeros((767), dtype=bool)
#     a[1:35] = True
#     mysb = Sunblocker(verb=True)
#     mysb.phazer(
#         inset=["yoyo.ms"],
#         outset=["yoyout.ms"],
#         channels=a,
#         imsize=512,
#         cell=4,
#         pol="i",
#         threshold=4.0,
#         mode="all",
#         radrange=0,
#         angle=0,
#         show="test.pdf",
#         dryrun=False,
#     )
