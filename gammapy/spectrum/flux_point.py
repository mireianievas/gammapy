# Licensed under a 3-clause BSD style license - see LICENSE.rst
"""Differential and integral flux point computations."""
from __future__ import absolute_import, division, print_function, unicode_literals
import logging
from collections import OrderedDict
import numpy as np
from astropy.table import Table
from astropy import units as u
from astropy.io.registry import IORegistryError
from gammapy.utils.scripts import make_path

from ..utils.fits import table_from_row_data
from ..utils.energy import Energy, EnergyBounds
from ..spectrum.powerlaw import power_law_flux
from ..spectrum.models import PowerLaw

__all__ = [
    'compute_flux_points_dnde',
    'FluxPointEstimator',
    'FluxPoints',
    'SEDLikelihoodProfile',
]

log = logging.getLogger(__name__)


REQUIRED_COLUMNS = {'dnde': ['e_ref', 'dnde'],
                    'flux': ['e_min', 'e_max', 'flux'],
                    'eflux': ['e_min', 'e_max', 'eflux']}

OPTIONAL_COLUMNS = {'dnde': ['dnde_err', 'dnde_errp', 'dnde_errn',
                             'dnde_ul', 'is_ul'],
                    'flux': ['flux_err', 'flux_errp', 'flux_errn',
                             'flux_ul', 'is_ul'],
                    'eflux': ['eflux_err', 'eflux_errp', 'eflux_errn',
                              'eflux_ul', 'is_ul']}

DEFAULT_UNIT = {'dnde': u.Unit('ph cm-2 s-1 TeV-1'),
                'flux': u.Unit('ph cm-2 s-1'),
                'eflux': u.Unit('erg cm-2 s-1')}


class FluxPoints(object):
    """
    Flux point object.

    For a complete documentation see :ref:`gadf:flux-points`, for an usage
    example see :ref:`flux-point-computation`.

    Parameters
    ----------
    table : `~astropy.table.Table`
        Input data table, with the following minimal required columns:

        * Format `'dnde'`: `'dnde'` and `'e_ref'`
        * Format `'flux'`: `'flux'` and `'e_ref'`
        * Format `'eflux'`: `'eflux'` and `'e_ref'`

    Examples
    --------

    >>> from gammapy.spectrum import FluxPoints
    >>> filename = '$GAMMAPY_EXTRA/test_datasets/spectrum/flux_points/flux_points.fits'
    >>> flux_points = FluxPoints.read(filename)
    >>> flux_points.show()

    """
    def __init__(self, table):
        # validate that the table is a valid representation of the given
        # flux point sed type
        self.table = self._validate_table(table)

    @property
    def sed_type(self):
        """
        Flux points sed type.

        Returns
        -------
        sed_type : str
            Can be either 'dnde', 'flux' or 'eflux'.
        """
        return self.table.meta['SED_TYPE']

    @staticmethod
    def _guess_sed_type(table):
        """
        Guess sed type from table content.
        """
        valid_sed_types = list(REQUIRED_COLUMNS.keys())
        for sed_type in valid_sed_types:
            required = set(REQUIRED_COLUMNS[sed_type])
            if required.issubset(table.colnames):
                return sed_type

    @staticmethod
    def _guess_sed_type_from_unit(unit):
        """
        Guess sed type from unit.
        """
        for sed_type, default_unit in DEFAULT_UNIT.items():
            if unit.is_equivalent(default_unit):
                return sed_type

    def _validate_table(self, table):
        """
        Validate input flux point table.
        """
        sed_type = table.meta['SED_TYPE']
        required = set(REQUIRED_COLUMNS[sed_type])

        if not required.issubset(table.colnames):
            missing = required.difference(table.colnames)
            raise ValueError("Missing columns for sed type '{0}':"
                             " {1}".format(sed_type, missing))
        return table

    def _get_y_energy_unit(self, y_unit):
        """
        Get energy part of the given y unit.
        """
        try:
            return [_ for _ in y_unit.bases if _.physical_type == 'energy'][0]
        except IndexError:
            return u.Unit('TeV')

    def plot(self, ax=None, sed_type=None, energy_unit='TeV', y_unit=None,
             energy_power=0, **kwargs):
        """
        Plot flux points

        Parameters
        ----------
        ax : `~matplotlib.axes.Axes`
            Axis object to plot on.
        sed_type : ['dnde', 'flux', 'eflux']
            Which sed type to plot.
        energy_unit : str, `~astropy.units.Unit`, optional
            Unit of the energy axis
        y_unit : str, `~astropy.units.Unit`, optional
            Unit of the flux axis
        energy_power : int
            Power of energy to multiply y axis with
        kwargs : dict
            Keyword arguments passed to :func:`~matplotlib.pyplot.errorbar`

        Returns
        -------
        ax : `~matplotlib.axes.Axes`
            Axis object
        """
        import matplotlib.pyplot as plt

        if ax is None:
            ax = plt.gca()

        sed_type = sed_type or self.sed_type
        y_unit = u.Unit(y_unit or DEFAULT_UNIT[sed_type])

        y = self.table[sed_type].quantity.to(y_unit)
        x = self.e_ref.to(energy_unit)

        # get errors and ul
        is_ul = self._is_ul
        x_err_all = self._plot_get_x_err(sed_type)
        y_err_all = self._plot_get_y_err(sed_type)

        # handle energy power
        e_unit = self._get_y_energy_unit(y_unit)
        y_unit = y.unit * e_unit ** energy_power
        y = (y * np.power(x, energy_power)).to(y_unit)

        y_err, x_err = None, None

        if y_err_all:
            y_errn = (y_err_all[0] * np.power(x, energy_power)).to(y_unit)
            y_errp = (y_err_all[1] * np.power(x, energy_power)).to(y_unit)
            y_err = (y_errn[~is_ul].to(y_unit).value,
                     y_errp[~is_ul].to(y_unit).value)

        if x_err_all:
            x_errn, x_errp = x_err_all
            x_err = (x_errn[~is_ul].to(energy_unit).value,
                     x_errp[~is_ul].to(energy_unit).value)

        # set flux points plotting defaults
        kwargs.setdefault('marker', 'None')
        kwargs.setdefault('ls', 'None')

        ebar = ax.errorbar(x[~is_ul].value, y[~is_ul].value, yerr=y_err,
                           xerr=x_err, **kwargs)

        if is_ul.any():
            if x_err_all:
                x_errn, x_errp = x_err_all
                x_err = (x_errn[is_ul].to(energy_unit).value,
                         x_errp[is_ul].to(energy_unit).value)

            y_ul = self.table[sed_type + '_ul'].quantity
            y_ul = (y_ul * np.power(x, energy_power)).to(y_unit)

            # set ul plotting defaults
            ul_kwargs = {'marker': 'v',
                         'label': None}

            kwargs.setdefault('ms', 10)
            kwargs.setdefault('mec', 'None')
            kwargs.setdefault('c', ebar[0].get_color())
            kwargs.update(ul_kwargs)

            ax.errorbar(x[is_ul].value, y_ul[is_ul].value, xerr=x_err, **kwargs)

        ax.set_xscale('log', nonposx='clip')
        ax.set_yscale('log', nonposy='clip')
        return ax

    def _plot_get_x_err(self, sed_type):
        try:
            e_min = self.table['e_min'].quantity
            e_max = self.table['e_max'].quantity
            e_ref = self.e_ref
            x_err = ((e_ref - e_min), (e_max - e_ref))
        except KeyError:
            x_err = None
        return x_err

    def _plot_get_y_err(self, sed_type):
        try:
            # assymmetric error
            y_errn = self.table[sed_type + '_errn'].quantity
            y_errp = self.table[sed_type + '_errp'].quantity
            y_err = (y_errn, y_errp)
        except KeyError:
            try:
                # symmetric error
                y_err = self.table[sed_type + '_err'].quantity
                y_err = (y_err, y_err)
            except KeyError:
                # no error at all
                y_err = None
        return y_err

    @property
    def _is_ul(self):
        try:
            return self.table['is_ul'].data.astype('bool')
        except KeyError:
            return np.isnan(self.table[self.sed_type])

    def show(self, figsize=(8, 5), **kwargs):
        """
        Show flux points.

        Parameters
        ----------
        figsize : tuple
            Figure size
        kwargs : dict
            Keyword arguments passed to `FluxPoints.plot()`.

        Returns
        -------
        ax : `~matplotlib.axes.Axes`
            Plotting axes object.
        """
        import matplotlib.pyplot as plt

        fig = plt.figure(figsize=figsize)
        ax = fig.add_subplot(111)
        self.plot(ax=ax, **kwargs)
        return ax

    def __str__(self):
        """
        String representation of the flux points class.
        """
        info = ''
        info += "Flux points of type '{}'".format(self.sed_type)
        return info

    def info(self):
        """
        Print flux points info.
        """
        print(self)

    @classmethod
    def read(cls, filename, **kwargs):
        """
        Read flux points.

        Parameters
        ----------
        filename : str
            Filename
        kwargs : dict
            Keyword arguments passed to `~astropy.table.Table.read`.

        """
        filename = make_path(filename)
        try:
            table = Table.read(str(filename), **kwargs)
        except IORegistryError:
            kwargs.setdefault('format', 'ascii.ecsv')
            table = Table.read(str(filename), **kwargs)

        if 'SED_TYPE' not in table.meta.keys():
            sed_type = cls._guess_sed_type(table)
            table.meta['SED_TYPE'] = sed_type

        return cls(table=table)

    def write(self, filename, **kwargs):
        """
        Write flux points.

        Parameters
        ----------
        filename : str
            Filename
        kwargs : dict
            Keyword arguments passed to `~astropy.table.Table.write`.
        """
        filename = make_path(filename)
        try:
            self.table.write(str(filename), **kwargs)
        except IORegistryError:
            kwargs.setdefault('format', 'ascii.ecsv')
            self.table.write(str(filename), **kwargs)

    # TODO: handle with Energy or EnergyBounds classes?
    @property
    def e_ref(self):
        """
        Reference energy.

        Defined by `e_ref` column in `FluxPoints.table` or computed as log
        center, if `e_min` and `e_max` columns are present in `FluxPoints.table`.

        Returns
        -------
        e_ref : `~astropy.units.Quantity`
            Reference energy.
        """
        try:
            return self.table['e_ref'].quantity
        except KeyError:
            e_ref = np.sqrt(self.e_min * self.e_max)
            return e_ref

    # TODO: handle with Energy or EnergyBounds classes?
    @property
    def e_min(self):
        """
        Lower bound of energy bin.

        Defined by `e_min` column in `FluxPoints.table`.

        Returns
        -------
        e_min : `~astropy.units.Quantity`
            Lower bound of energy bin.
        """
        return self.table['e_min'].quantity

    # TODO: handle with Energy or EnergyBounds classes?
    @property
    def e_max(self):
        """
        Upper bound of energy bin.

        Defined by `e_max` column in `FluxPoints.table`.

        Returns
        -------
        e_max : `~astropy.units.Quantity`
            Upper bound of energy bin.
        """
        return self.table['e_max'].quantity


def compute_flux_points_dnde(flux_points, model, method='lafferty'):
    """
    Compute differential flux points quantities.

    See: http://adsabs.harvard.edu/abs/1995NIMPA.355..541L for details
    on the `'lafferty'` method.

    Parameters
    ----------
    flux_points : `FluxPoints`
         Input integral flux points.
    model : `~gammapy.spectrum.SpectralModel`
        Spectral model assumption.  Note that the value of the amplitude parameter
        does not matter. Still it is recommended to use something with the right
        scale and units. E.g. `amplitude = 1E-12 * u.Unit('cm-2 s-1 TeV-1')`
    method : {'lafferty', 'log_center', 'table'}
        Flux points `e_ref` estimation method:

            * `'laferty'` Lafferty & Wyatt model-based e_ref
            * `'log_center'` log bin center e_ref
            * `'table'` using column 'e_ref' from input flux_points

    Examples
    --------

    >>> from astropy import units as u
    >>> from gammapy.spectrum import FluxPoints, compute_flux_points_dnde
    >>> from gammapy.spectrum.models import Powerlaw
    >>> filename = '$GAMMAPY_EXTRA/test_datasets/spectrum/flux_points/flux_points.fits'
    >>> flux_points = FluxPoints.read(filename)
    >>> model = PowerLaw(2.2 * u.Unit(''), 1E-12 * u.Unit('cm-2 s-1 TeV-1'), 1 * u.TeV)
    >>> result = compute_flux_points_dnde(flux_points, model=model)


    Returns
    -------
    flux_points : `FluxPoints`
        Flux points including differential quantity columns `dnde`
        and `dnde_err` (optional), `dnde_ul` (optional).

    """
    input_table = flux_points.table
    flux = input_table['flux'].quantity

    try:
        flux_err = input_table['flux_err'].quantity
    except KeyError:
        flux_err = None
    try:
        flux_ul = input_table['flux_ul'].quantity
    except KeyError:
        flux_ul = None

    e_min = flux_points.e_min
    e_max = flux_points.e_max

    # Compute e_ref
    if method == 'table':
        e_ref = input_table['e_ref'].quantity
    elif method == 'log_center':
        e_ref = np.sqrt(e_min * e_max)
    elif method == 'lafferty':
        # set e_ref that it represents the mean dnde in the given energy bin
        e_ref = _e_ref_lafferty(model, e_min, e_max)
    else:
        raise ValueError('Invalid x_method: {0}'.format(x_method))

    dnde = _dnde_from_flux(flux, model, e_ref, e_min, e_max)

    # Add to result table
    table = input_table.copy()
    table['e_ref'] = e_ref
    table['dnde'] = dnde

    if flux_err:
        # TODO: implement better error handling, e.g. MC based method
        table['dnde_err'] = dnde * flux_err / flux

    if flux_ul:
        dnde_ul = _dnde_from_flux(flux_ul, model, e_ref, e_min, e_max)
        table['dnde_ul'] = dnde_ul

    table.meta['SED_TYPE'] = 'dnde'
    return FluxPoints(table)


def _e_ref_lafferty(model, e_min, e_max):
    # compute e_ref that the value at e_ref corresponds to the mean value
    # between e_min and e_max
    flux = model.integral(e_min, e_max)
    dnde_mean = flux / (e_max - e_min)
    return model.inverse(dnde_mean)


def _dnde_from_flux(flux, model, e_ref, e_min, e_max):
    # Compute dnde under the assumption that flux equals expected
    # flux from model
    flux_model = model.integral(e_min, e_max)
    dnde_model = model(e_ref)
    return dnde_model * (flux / flux_model)


class FluxPointEstimator(object):
    """
    Flux point estimator.

    Parameters
    ----------
    obs : `~gammapy.spectrum.SpectrumObservation`
        Spectrum observation
    groups : `~gammapy.spectrum.SpectrumEnergyGroups`
        Energy groups (usually output of `~gammapy.spectrum.SpectrumEnergyGroupsMaker`)
    model : `~gammapy.spectrum.models.SpectralModel`
        Global model (usually output of `~gammapy.spectrum.SpectrumFit`)
    """

    def __init__(self, obs, groups, model):
        self.obs = obs
        self.groups = groups
        self.model = model

        self.flux_points = None

    def __str__(self):
        s = 'FluxPointEstimator:\n'
        s += str(self.obs) + '\n'
        s += str(self.groups) + '\n'
        s += str(self.model) + '\n'
        return s

    def compute_points(self):
        meta = OrderedDict(
            method='TODO',
        )
        rows = []
        for group in self.groups:
            if group.bin_type != 'normal':
                log.debug('Skipping energy group:\n{}'.format(group))
                continue

            row = self.compute_flux_point(group)
            rows.append(row)

        self.flux_points = table_from_row_data(rows=rows, meta=meta)

    def compute_flux_point(self, energy_group):
        log.debug('Computing flux point for energy group:\n{}'.format(energy_group))
        model = self.compute_approx_model(
            global_model=self.model,
            energy_range=energy_group.energy_range,
        )

        energy_ref = self.compute_energy_ref(energy_group)

        return self.fit_point(
            model=model, energy_group=energy_group, energy_ref=energy_ref,
        )

    def compute_energy_ref(self, energy_group):
        return energy_group.energy_range.log_center

    @staticmethod
    def compute_approx_model(global_model, energy_range):
        """
        Compute approximate model, to be used in the energy bin.
        """
        # binning = EnergyBounds(binning)
        # low_bins = binning.lower_bounds
        # high_bins = binning.upper_bounds
        #
        # from sherpa.models import PowLaw1D
        #
        # if isinstance(model, models.PowerLaw):
        #     temp = model.to_sherpa()
        #     temp.gamma.freeze()
        #     sherpa_models = [temp] * binning.nbins
        # else:
        #     sherpa_models = [None] * binning.nbins
        #
        # for low, high, sherpa_model in zip(low_bins, high_bins, sherpa_models):
        #     log.info('Computing flux points in bin [{}, {}]'.format(low, high))
        #
        #     # Make PowerLaw approximation for higher order models
        #     if sherpa_model is None:
        #         flux_low = model(low)
        #         flux_high = model(high)
        #         index = powerlaw.power_law_g_from_points(e1=low, e2=high,
        #                                                  f1=flux_low,
        #                                                  f2=flux_high)
        #
        #         log.debug('Approximated power law index: {}'.format(index))
        #         sherpa_model = PowLaw1D('powlaw1d.default')
        #         sherpa_model.gamma = index
        #         sherpa_model.gamma.freeze()
        #         sherpa_model.ref = model.parameters.reference.to('keV')
        #         sherpa_model.ampl = 1e-20
        #return PowerLaw(
        #    index=u.Quantity(2, ''),
        #    amplitude=u.Quantity(1, 'm-2 s-1 TeV-1'),
        #    reference=u.Quantity(1, 'TeV'),
        #)
        return global_model

    def fit_point(self, model, energy_group, energy_ref):
        from gammapy.spectrum import SpectrumFit

        sherpa_model = model.to_sherpa()
        sherpa_model.gamma.freeze()
        fit = SpectrumFit(self.obs, sherpa_model)

        erange = energy_group.energy_range
        # TODO: Notice channels contained in energy_group
        fit.fit_range = erange.min, 0.9999 * erange.max

        log.debug(
            'Calling Sherpa fit for flux point '
            ' in energy range:\n{}'.format(fit)
        )

        fit.fit()

        res = fit.global_result

        energy_err_hi = energy_group.energy_range.max - energy_ref
        energy_err_lo = energy_ref - energy_group.energy_range.min
        diff_flux = res.model(energy_ref).to('m-2 s-1 TeV-1')
        err = res.model_with_uncertainties(energy_ref.to('TeV').value)
        diff_flux_err = err.s * u.Unit('m-2 s-1 TeV-1')

        return OrderedDict(
            energy=energy_ref,
            energy_err_hi=energy_err_hi,
            energy_err_lo=energy_err_lo,
            diff_flux=diff_flux,
            diff_flux_err_hi=diff_flux_err,
            diff_flux_err_lo=diff_flux_err,
        )


class SEDLikelihoodProfile(object):
    """SED likelihood profile.

    See :ref:`gadf:likelihood_sed`.

    TODO: merge this class with the classes in ``fermipy/castro.py``,
    which are much more advanced / feature complete.
    This is just a temp solution because we don't have time for that.
    """

    def __init__(self, table):
        self.table = table

    @classmethod
    def read(cls, filename, **kwargs):
        filename = make_path(filename)
        table = Table.read(str(filename), **kwargs)
        return cls(table=table)

    def __str__(self):
        s = self.__class__.__name__ + '\n'
        s += str(self.table)
        return s

    def plot(self, ax=None):
        import matplotlib.pyplot as plt
        if ax is None:
            ax = plt.gca()

        # TODO
