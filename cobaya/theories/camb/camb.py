"""
.. module:: theories.camb

:Synopsis: Managing the CAMB cosmological code
:Author: Jesus Torrado and Antony Lewis

.. |br| raw:: html

   <br />

This module imports and manages the CAMB cosmological code.
It requires CAMB 1.1 or higher.

.. note::

   **If you use this cosmological code, please cite it as:**
   |br|
   A. Lewis, A. Challinor, A. Lasenby,
   *Efficient computation of CMB anisotropies in closed FRW*
   (`arXiv:astro-ph/9911177 <https://arxiv.org/abs/astro-ph/9911177>`_)
   |br|
   C. Howlett, A. Lewis, A. Hall, A. Challinor,
   *CMB power spectrum parameter degeneracies in the era of precision cosmology*
   (`arXiv:1201.3654 <https://arxiv.org/abs/1201.3654>`_)


Usage
-----

If you are using a likelihood that requires some observable from CAMB, simply add CAMB
to the theory block.

You can specify any parameter that CAMB understands in the ``params`` block:

.. code-block:: yaml

   theory:
     camb:
       extra_args:
         [any param that CAMB understands, for FIXED and PRECISION]

   params:
       [any param that CAMB understands, fixed, sampled or derived]


If you want to use your own version of CAMB, you need to specify its location with a
``path`` option inside the ``camb`` block. If you do not specify a ``path``,
CAMB will be loaded from the automatic-install ``modules`` folder, if specified, or
otherwise imported as a globally-installed Python package. Cobaya will print at
initialisation where it is getting CAMB from.

.. _camb_modify:

Modifying CAMB
^^^^^^^^^^^^^^

If you modify CAMB and add new variables, make sure that the variables you create are
exposed in the Python interface (`instructions here
<https://camb.readthedocs.io/en/latest/model.html#camb.model.CAMBparams>`__).
If you follow those instructions you do not need to make any additional modification in
Cobaya.

You can use the :doc:`model wrapper <cosmo_model>` to test your modification by
evaluating observables or getting derived quantities at known points in the parameter
space (set ``debug: True`` to get more detailed information of what exactly is passed to
CAMB).

In your CAMB modification, remember that you can raise a ``CAMBParamRangeError`` or a
``CAMBError`` whenever the computation of any observable would fail, but you do not
expect that observable to be compatible with the data (e.g. at the fringes of the
parameter space). Whenever such an error is raised during sampling, the likelihood is
assumed to be zero, and the run is not interrupted.


Installation
------------

Pre-requisites
^^^^^^^^^^^^^^

**cobaya** calls CAMB using its Python interface, which requires that you compile CAMB
using intel's ifort compiler or the GNU gfortran compiler version 6.4 or later.
To check if you have the latter, type ``gfortran --version`` in the shell,
and the first line should look like

.. code::

   GNU Fortran ([your OS version]) [gfortran version] [release date]

Check that ``[gfortran's version]`` is at least 6.4. If you get an error instead, you need
to install gfortran (contact your local IT service).

CAMB comes with binaries pre-built for Windows, so if you don't need to modify the CAMB
source code, no Fortran compiler is needed.

If you are using Anaconda you can also install a pre-compiled CAMB package from conda
forge using

.. code::

  conda install -c conda-forge camb

Automatic installation
^^^^^^^^^^^^^^^^^^^^^^

If you do not plan to modify CAMB, the easiest way to install it is using the
:doc:`automatic installation script <installation_cosmo>`. Just make sure that
``theory: camb:`` appears in one of the files passed as arguments to the installation
script.


Manual installation (or using your own version)
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

If you are planning to modify CAMB or use an already modified version,
you should not use the automatic installation script. Use the installation method that
best adapts to your needs:

* [**Recommended for staying up-to-date**]
  To install CAMB locally and keep it up-to-date, clone the
  `CAMB repository in Github <https://github.com/cmbant/CAMB>`_
  in some folder of your choice, say ``/path/to/theories/CAMB``:

  .. code:: bash

      $ cd /path/to/theories
      $ git clone --recursive https://github.com/cmbant/CAMB.git
      $ cd CAMB
      $ python setup.py build

  To update to the last changes in CAMB (master), run ``git pull`` from CAMB's folder and
  re-build using the last command. If you do not want to use multiple versions of CAMB,
  you can also make your local installation available to python generally by installing
  it using

.. code:: bash

     $ pip install -e /path/to/CAMB

* [**Recommended for modifying CAMB**]
  First, `fork the CAMB repository in Github <https://github.com/cmbant/CAMB>`_
  (follow `this instructions <https://help.github.com/articles/fork-a-repo/>`_) and then
  follow the same steps as above, substituting the second one with:

  .. code:: bash

      $ git clone --recursive https://[YourGithubUser]@github.com/[YourGithubUser]/CAMB.git

* To use your own version, assuming it's placed under ``/path/to/theories/CAMB``,
  just make sure it is compiled (and that the version on top of which you based your
  modifications is old enough to have the Python interface implemented.

In the cases above, you **must** specify the path to your CAMB installation in
the input block for CAMB (otherwise a system-wide CAMB may be used instead):

.. code:: yaml

   theory:
     camb:
       path: /path/to/theories/CAMB

.. note::

   In any of these methods, if you intent to switch between different versions or
   modifications of CAMB you should not  install CAMB as python package using
   ``python setup.py install --user``, as the official instructions suggest.
"""

# Python 2/3 compatibility
from __future__ import absolute_import
from __future__ import division

# Global
import sys
import os
import logging
from copy import deepcopy
import numpy as np
from collections import namedtuple, OrderedDict as odict
import numbers
import ctypes

# Local
from cobaya.theories._cosmo import BoltzmannBase
from cobaya.log import LoggedError
from cobaya.install import download_github_release, check_gcc_version
from cobaya.tools import getfullargspec, get_class_methods, get_properties
from cobaya.tools import load_module, VersionCheckError, str_to_list
from cobaya.theory import HelperTheory
from cobaya.conventions import _requires

# Result collector
Collector = namedtuple("collector", ["method", "args", "kwargs"])
Collector.__new__.__defaults__ = (None, [], {})

CAMBOutputs = namedtuple("CAMBOutputs", ["camb_params", "results", "derived"])


class camb(BoltzmannBase):
    # Name of the Class repo/folder and version to download
    camb_repo_name = "cmbant/CAMB"
    camb_repo_version = os.environ.get("CAMB_REPO_VERSION", "master")
    camb_min_gcc_version = "6.4"
    min_camb_version = '1.1'

    def initialize(self):
        """Importing CAMB from the correct path, if given."""
        if not self.path and self.path_install:
            self.path = self.get_path(self.path_install)
        camb_path = None
        if self.path and not os.path.exists(self.path):
            # Fail if this was a directly specified path,
            # or ignore and try to global-import if it came from a path_install
            if self.path_install:
                self.log.info("*local* CAMB not found at " + self.path)
                self.log.info("Importing *global* CAMB.")
            else:
                raise LoggedError(self.log, "*local* CAMB not found at " + self.path)
        elif self.path:
            self.log.info("Importing *local* CAMB from " + self.path)
            if not os.path.exists(self.path):
                raise LoggedError(
                    self.log, "The given folder does not exist: '%s'", self.path)
            camb_path = self.path
            if not os.path.exists(os.path.join(self.path, "setup.py")):
                raise LoggedError(
                    self.log,
                    "Either CAMB is not in the given folder, '%s', or you are using a "
                    "very old version without the Python interface.", self.path)
        else:
            self.log.info("Importing *global* CAMB.")
        try:
            self.camb = load_module("camb", path=camb_path,
                                    min_version=self.min_camb_version)
        except ImportError:
            raise LoggedError(
                self.log, "Couldn't find the CAMB python interface.\n"
                          "Make sure that you have compiled it, and that you either\n"
                          " (a) specify a path (you didn't) or\n"
                          " (b) install the Python interface globally with\n"
                          "     'pip install -e /path/to/camb [--user]'")
        except VersionCheckError as e:
            raise LoggedError(self.log, str(e))
        super(camb, self).initialize()
        self.extra_attrs = {"Want_CMB": False, "Want_cl_2D_array": False,
                            'WantCls': False}
        # Derived parameters that may not have been requested, but will be necessary later
        self.derived_extra = []
        # Some default settings
        self.needs_perts = False
        self.limber = False
        self.non_linear_sources = False
        self.non_linear_pk = False
        self._base_params = None
        self._needs_lensing_cross = False

        power_spectrum = self.camb.CAMBparams.make_class_named(
            self.extra_args.get('initial_power_model',
                                self.camb.initialpower.InitialPowerLaw),
            self.camb.initialpower.InitialPower)
        self.initial_power_args = {}
        self.power_params = []

        nonlin = self.camb.CAMBparams.make_class_named(
            self.extra_args.get('non_linear_model',
                                self.camb.nonlinear.Halofit),
            self.camb.nonlinear.NonLinearModel)
        self.nonlin_args = {}
        self.nonlin_params = []
        for model, args, params in [(nonlin, self.nonlin_args, self.nonlin_params),
                                    (power_spectrum, self.initial_power_args,
                                     self.power_params)]:
            pars = getfullargspec(model.set_params)
            for arg, v in zip(pars.args[1:], pars.defaults[1:]):
                if arg in self.extra_args:
                    args[arg] = self.extra_args.pop(arg)
                elif isinstance(v, numbers.Number) or v is None:
                    params.append(arg)
        self.requires = str_to_list(getattr(self, _requires, []))
        self._transfer_requires = [p for p in self.requires if
                                   p not in self.get_can_support_params()]
        self.requires = [p for p in self.requires if p not in self._transfer_requires]

    def initialize_with_params(self):
        if set(self.input_params).intersection({'r', 'At'}):
            self.extra_attrs["WantTensors"] = True

    def get_can_support_params(self):
        return self.power_params + self.nonlin_params

    def get_allow_agnostic(self):
        return False

    def needs(self, **requirements):
        # Computed quantities required by the likelihoods
        # Note that redshifts below are treated differently for background quantities,
        #   were no additional transfer computation is needed (e.g. H(z)),
        #   and matter-power-related quantities, that require additional computation
        #   and need the redshifts to be passed at CAMBParams instantiation.
        #   Also, we always make sure that those redshifts are sorted in descending order,
        #   since all CAMB related functions return quantities in that implicit order
        # The following super call makes sure that the requirements are properly
        # accumulated, i.e. taking the max of precision requests, etc.
        super(camb, self).needs(**requirements)
        CAMBdata = self.camb.CAMBdata

        for k, v in self._needs.items():
            # Products and other computations
            if k == "Cl":
                self.extra_args["lmax"] = max(
                    max(v.values()), self.extra_args.get("lmax", 0))
                cls = [a.lower() for a in v]
                needs_lensing = set(cls).intersection({"pp", "pt", "pe", "tp", "ep"})
                self.collectors[k] = Collector(
                    method=CAMBdata.get_cmb_power_spectra,
                    kwargs={
                        "spectra": list(set(
                            (self.collectors[k].kwargs.get("spectra", [])
                             if k in self.collectors else []) +
                            ["total"] + (["lens_potential"] if needs_lensing else []))),
                        "raw_cl": False})
                self.needs_perts = True
                self.extra_attrs["Want_CMB"] = True
                self.extra_attrs["WantCls"] = True
                if "pp" in cls and self.extra_args.get(
                        "lens_potential_accuracy") is None:
                    self.extra_args["lens_potential_accuracy"] = 1
                self.non_linear_sources = self.extra_args.get("lens_potential_accuracy",
                                                              1) >= 1
                if set(cls).intersection({"pt", "pe", "tp", "ep"}):
                    self._needs_lensing_cross = True
                if 'TCMB' not in self.derived_extra:
                    self.derived_extra += ['TCMB']
            elif k == "Hubble":
                self.collectors[k] = Collector(
                    method=CAMBdata.h_of_z,
                    kwargs={"z": self._combine_z(k, v)})
            elif k in ("angular_diameter_distance", "comoving_radial_distance"):
                self.collectors[k] = Collector(
                    method=getattr(CAMBdata, k),
                    kwargs={"z": self._combine_z(k, v)})
            elif k == "fsigma8":
                self.add_to_redshifts(v["z"])
                self.collectors[k] = Collector(
                    method=CAMBdata.get_fsigma8,
                    kwargs={})
                self.needs_perts = True
            elif k in ["Pk_interpolator", "Pk_grid"]:
                self.extra_args["kmax"] = max(v["k_max"], self.extra_args.get("kmax", 0))
                self.add_to_redshifts(v["z"])
                v["vars_pairs"] = v["vars_pairs"] or [("delta_tot", "delta_tot")]
                kwargs = deepcopy(v)
                # need to ensure can't have conflicts between requests from
                # different likelihoods. Store results without Hubble units.
                if kwargs.get("hubble_units", False) or kwargs.get("k_hunit", False):
                    raise LoggedError(self.log, "hubble_units and k_hunit must be False"
                                                "for consistency")
                kwargs["hubble_units"] = False
                kwargs["k_hunit"] = False

                for p in "k_max", "z", "vars_pairs":
                    kwargs.pop(p)
                if kwargs["nonlinear"]:
                    self.non_linear_pk = True
                for pair in v["vars_pairs"]:
                    product = ("Pk_grid", kwargs["nonlinear"]) + tuple(sorted(pair))
                    kwargs.update(dict(zip(["var1", "var2"], pair)))
                    self.collectors[product] = Collector(
                        method=CAMBdata.get_linear_matter_power_spectrum, kwargs=kwargs)
                self.needs_perts = True
            elif k == "source_Cl":
                if not getattr(self, "sources", None):
                    self.sources = odict()
                for source, window in v["sources"].items():
                    # If it was already there, BoltzmannBase.needs() has already
                    # checked that old info == new info
                    if source not in self.sources:
                        self.sources[source] = window
                self.limber = v.get("limber", True)
                self.non_linear_sources = self.non_linear_sources or \
                                          v.get("non_linear", False)
                if "lmax" in v:
                    self.extra_args["lmax"] = max(v["lmax"],
                                                  self.extra_args.get("lmax", 0))
                self.needs_perts = True
                self.collectors[k] = Collector(method=CAMBdata.get_source_cls_dict)
                self.extra_attrs["Want_cl_2D_array"] = True
                self.extra_attrs["WantCls"] = True
            elif k == 'CAMBdata':
                # Just get CAMB results object
                self.collectors[k] = None
            elif v is None:
                # General derived parameters
                k_translated = self.translate_param(k)
                if k_translated not in self.derived_extra:
                    self.derived_extra += [k_translated]
                if k == "sigma8":
                    self.extra_attrs["WantTransfer"] = True
                    self.needs_perts = True
            else:
                raise LoggedError(self.log, "This should not be happening. Contact the "
                                            "developers.")
        # Check that there are no repeated parameters between input and extra
        if set(self.input_params).intersection(set(self.extra_args)):
            raise LoggedError(
                self.log,
                "The following parameters appear both as input parameters and as CAMB "
                "extra arguments: %s. Please, remove one of the definitions of each.",
                list(set(self.input_params).intersection(set(self.extra_args))))

        # Computing non-linear corrections
        model = self.camb.model
        self.extra_attrs["NonLinear"] = {
            (True, True): model.NonLinear_both,
            (True, False): model.NonLinear_lens,
            (False, True): model.NonLinear_pk,
            (False, False): False}[(self.non_linear_sources, self.non_linear_pk)]
        # set-set base CAMB params if anything might have changed
        self._base_params = None

        return {'CAMB_transfers':
                    {'non_linear': self.non_linear_sources,
                     'needs_perts': self.needs_perts}}

    def add_to_redshifts(self, z):
        self.extra_args["redshifts"] = np.sort(np.unique(np.concatenate(
            (np.atleast_1d(z), self.extra_args.get("redshifts", [])))))[::-1]

    def _combine_z(self, k, v):
        c = self.collectors.get(k, None)
        if c:
            return np.sort(
                np.unique(np.concatenate((c.kwargs['z'], np.atleast_1d(v['z'])))))
        else:
            return np.sort(np.atleast_1d(v['z']))

    def calculate(self, state, want_derived=True, **params_values_dict):
        try:
            params, results = self.provider.get_CAMB_transfers()
            if self.collectors:
                args = {self.translate_param(p): v for p, v in
                        params_values_dict.items() if p in self.power_params}
                args.update(self.initial_power_args)
                results.Params.InitPower.set_params(**args)
                if self.non_linear_sources or self.non_linear_pk:
                    args = {self.translate_param(p): v for p, v in
                            params_values_dict.items() if p in self.nonlin_params}
                    args.update(self.nonlin_args)
                    results.Params.NonLinearModel.set_params(**args)
                results.power_spectra_from_transfer()
            else:
                results = None
            for product, collector in self.collectors.items():
                if collector:
                    state[product] = \
                        collector.method(results, *collector.args, **collector.kwargs)
                else:
                    state[product] = results
        except self.camb.baseconfig.CAMBError as e:
            if self.stop_at_error:
                self.log.error(
                    "Computation error (see traceback below)! "
                    "Parameters sent to CAMB: %r and %r.\n"
                    "To ignore this kind of errors, make 'stop_at_error: False'.",
                    dict(state["params"]), dict(self.extra_args))
                raise
            else:
                # Assumed to be a "parameter out of range" error.
                self.log.debug("Computation of cosmological products failed. "
                               "Assigning 0 likelihood and going on. "
                               "The output of the CAMB error was %s" % e)
                return False
            # Prepare derived parameters
        intermediates = CAMBOutputs(params, results,
                                    results.get_derived_params() if results else None)
        if want_derived:
            state["derived"] = self._get_derived_output(intermediates)
        # Prepare necessary extra derived parameters
        state["derived_extra"] = {
            p: self._get_derived(p, intermediates) for p in self.derived_extra}

    def _get_derived(self, p, intermediates):
        """
        General function to extract a single derived parameter.

        To get a parameter *from a likelihood* use `get_param` instead.
        """
        if intermediates.derived:
            derived = intermediates.derived.get(p, None)
            if derived is not None:
                return derived
        # Specific calls, if general ones fail:
        if p == "sigma8":
            return intermediates.results.get_sigma8()[-1]
        try:
            return getattr(intermediates.camb_params, p)
        except AttributeError:
            try:
                return getattr(intermediates.results, p)
            except AttributeError:
                return getattr(intermediates.camb_params, "get_" + p, lambda: None)()

    def _get_derived_output(self, intermediates):
        """
        Returns a dictionary of derived parameters with their values,
        using the *current* state (i.e. it should only be called from
        the ``compute`` method).

        To get a parameter *from a likelihood* use `get_param` instead.
        """
        derived = {}
        for p in self.output_params:
            derived[p] = self._get_derived(self.translate_param(p), intermediates)
            if derived[p] is None:
                raise LoggedError(self.log, "Derived param '%s' not implemented"
                                            " in the CAMB interface", p)
        return derived

    def get_Cl(self, ell_factor=False, units="muK2"):
        current_state = self._current_state
        # get C_l^XX from the cosmological code
        try:
            cl_camb = current_state["Cl"]["total"].copy()
        except:
            raise LoggedError(self.log, "No Cl's were computed. Are you sure that you "
                                        "have requested them?")

        temp = current_state['derived_extra']['TCMB']
        units_factors = {"1": 1,
                         "muK2": temp * 1.e6,
                         "K2": temp}
        try:
            units_factor = units_factors[units]
        except KeyError:
            raise LoggedError(self.log, "Units '%s' not recognized. Use one of %s.",
                              units, list(units_factors))

        ls = np.arange(cl_camb.shape[0], dtype=np.int64)
        if not ell_factor:
            # unit conversion and ell_factor. CAMB output is *with* the factors already
            ells_factor = ls[1:] * (ls[1:] + 1)
            cl_camb[1:, :] /= ells_factor[..., np.newaxis]
            cl_camb[1:, :] *= (2 * np.pi) * units_factor ** 2
        elif units_factor != 1:
            cl_camb *= units_factor ** 2

        mapping = {"tt": 0, "ee": 1, "bb": 2, "te": 3, "et": 3}
        cls = {"ell": ls}
        cls.update({sp: cl_camb[:, i] for sp, i in mapping.items()})

        cl_lens = current_state["Cl"].get("lens_potential")
        if cl_lens is not None:
            cls["pp"] = cl_lens[:, 0].copy()
            if not ell_factor:
                cls["pp"][1:] /= ells_factor ** 2 / (2 * np.pi)
            if self._needs_lensing_cross:
                for i, cross in enumerate(['pt', 'pe']):
                    cls[cross] = cl_lens[:, i + 1].copy() * units_factor
                    if not ell_factor:
                        cls[cross][1:] /= ells_factor ** (3. / 2) / (2 * np.pi)
                    cls[cross[::-1]] = cls[cross]
        return cls

    def _get_z_dependent(self, quantity, z):
        if quantity == "fsigma8":
            computed_redshifts = self.extra_args["redshifts"]
            i_kwarg_z = np.concatenate(
                [np.where(computed_redshifts == zi)[0] for zi in np.atleast_1d(z)])
        else:
            computed_redshifts = self.collectors[quantity].kwargs["z"]
            i_kwarg_z = np.searchsorted(computed_redshifts, np.atleast_1d(z))
        return np.array(self._current_state[quantity], copy=True)[i_kwarg_z]

    def get_fsigma8(self, z):
        return self._get_z_dependent("fsigma8", z)

    def get_source_Cl(self):
        # get C_l^XX from the cosmological code
        try:
            cls = deepcopy(self._current_state["source_Cl"])
        except:
            raise LoggedError(
                self.log, "No source Cl's were computed. "
                          "Are you sure that you have requested some source?")
        cls_dict = dict()
        for term, cl in cls.items():
            term_tuple = tuple(
                (lambda x: x if x == "P" else list(self.sources)[int(x) - 1])(
                    _.strip("W")) for _ in term.split("x"))
            cls_dict[term_tuple] = cl
        cls_dict["ell"] = np.arange(cls[list(cls)[0]].shape[0])
        return cls_dict

    def get_CAMBdata(self):
        """
        Get the CAMB result object (must have been requested as a requirement).

        :return: CAMB's `CAMBdata <https://camb.readthedocs.io/en/latest/results.html>`_
                 result instance for the current parameters
        """
        return self._current_state['CAMBdata']

    def get_can_provide_params(self):
        # possible derived parameters for derived_extra, excluding things that are
        # only input parameters.
        params_derived = list(get_class_methods(self.camb.CAMBparams))
        params_derived.remove("custom_source_names")
        fields = []
        for f, tp in self.camb.CAMBparams._fields_:
            if tp is ctypes.c_double and 'max_eta_k' not in f \
                    and f not in ['Alens', 'num_nu_massless']:
                fields.append(f)
        fields += ['omega_de', 'sigma8']  # only parameters from CAMBdata
        properties = get_properties(self.camb.CAMBparams)
        names = self.camb.model.derived_names + properties + fields + params_derived
        for name, mapped in self.renames.items():
            if mapped in names:
                names.append(name)
        # remove any parameters explicitly tagged as input requirements
        return list(
            set(names).difference(set(self._transfer_requires).union(set(self.requires))))

    def get_version(self):
        return self.camb.__version__

    def set(self, params_values_dict, state):
        # Prepare parameters to be passed: this is called from the CambTransfers instance
        args = {self.translate_param(p): v for p, v in params_values_dict.items()}
        # Generate and save
        self.log.debug("Setting parameters: %r and %r",
                       dict(args), dict(self.extra_args))
        try:
            if not self._base_params:
                base_args = args.copy()
                base_args.update(self.extra_args)
                # Remove extra args that might
                # cause an error if the associated product is not requested
                if not self.extra_attrs["WantCls"]:
                    for not_needed in getfullargspec(
                            self.camb.CAMBparams.set_for_lmax).args[1:]:
                        base_args.pop(not_needed, None)
                self._reduced_extra_args = self.extra_args.copy()
                params = self.camb.set_params(**base_args)
                # pre-set the parameters that are not varying
                for non_param_func in ['set_classes', 'set_matter_power', 'set_for_lmax']:
                    for fixed_param in getfullargspec(
                            getattr(self.camb.CAMBparams, non_param_func)).args[1:]:
                        if fixed_param in args:
                            raise LoggedError(self.log,
                                              "Trying to sample fixed theory parameter %s",
                                              fixed_param)
                        self._reduced_extra_args.pop(fixed_param, None)
                if self.extra_attrs:
                    self.log.debug("Setting attributes of CAMBparams: %r",
                                   self.extra_attrs)
                for attr, value in self.extra_attrs.items():
                    if hasattr(params, attr):
                        setattr(params, attr, value)
                    else:
                        raise LoggedError(
                            self.log,
                            "Some of the attributes to be set manually were not "
                            "recognized: %s=%s", attr, value)
                # Sources
                if getattr(self, "sources", None):
                    self.log.debug("Setting sources: %r", self.sources)
                    sources = self.camb.sources
                    source_windows = []
                    for source, window in self.sources.items():
                        function = window.pop("function", None)
                        if function == "spline":
                            source_windows.append(sources.SplinedSourceWindow(**window))
                        elif function == "gaussian":
                            source_windows.append(sources.GaussianSourceWindow(**window))
                        else:
                            raise LoggedError(self.log,
                                              "Unknown source window function type %r",
                                              function)
                        window["function"] = function
                    params.SourceWindows = source_windows
                    params.SourceTerms.limber_windows = self.limber
                self._base_params = params
            else:
                args.update(self._reduced_extra_args)
            return self.camb.set_params(self._base_params.copy(), **args)
        except self.camb.baseconfig.CAMBParamRangeError:
            if self.stop_at_error:
                raise LoggedError(self.log, "Out of bound parameters: %r",
                                  params_values_dict)
            else:
                self.log.debug("Out of bounds parameters. "
                               "Assigning 0 likelihood and going on.")
        except (self.camb.baseconfig.CAMBValueError, self.camb.baseconfig.CAMBError):
            if self.stop_at_error:
                self.log.error(
                    "Error setting parameters (see traceback below)! "
                    "Parameters sent to CAMB: %r and %r.\n"
                    "To ignore this kind of errors, make 'stop_at_error: False'.",
                    dict(state["params"]), dict(self.extra_args))
                raise
        except self.camb.baseconfig.CAMBUnknownArgumentError as e:
            raise LoggedError(
                self.log,
                "Some of the parameters passed to CAMB were not recognized: %s" % str(e))
        return False

    def get_helper_theories(self):
        """
        Transfer functions are computed separately by camb.transfers, then this
        class uses the transfer functions to calculate power spectra (using A_s, n_s etc).
        """
        self._camb_transfers = CambTransfers(self, 'camb.transfers',
                                             dict(stop_at_error=self.stop_at_error),
                                             timing=self.timer)
        setattr(self._camb_transfers, _requires, self._transfer_requires)
        return {'camb.transfers': self._camb_transfers}

    def get_speed(self):
        if self._measured_speed:
            return self._measured_speed
        if not self.non_linear_sources:
            return self.speed * 10
        if {'omk', 'omegak'}.intersection(set(self._camb_transfers.input_params)):
            return self.speed / 1.5
        return self.speed * 3

    @classmethod
    def get_path(cls, path):
        return os.path.realpath(
            os.path.join(path, "code",
                         cls.camb_repo_name[cls.camb_repo_name.find("/") + 1:]))

    @classmethod
    def is_installed(cls, **kwargs):
        import platform
        if not kwargs["code"]:
            return True
        return os.path.isfile(os.path.realpath(
            os.path.join(cls.get_path(kwargs["path"]),
                         "camb", "cambdll.dll" if (
                        platform.system() == "Windows") else "camblib.so")))

    @classmethod
    def install(cls, path=None, force=False, code=True, data=False,
                no_progress_bars=False, **kwargs):
        log = logging.getLogger(cls.__name__)
        if not code:
            log.info("Code not requested. Nothing to do.")
            return True
        log.info("Downloading camb...")
        success = download_github_release(
            os.path.join(path, "code"), cls.camb_repo_name, cls.camb_repo_version,
            no_progress_bars=no_progress_bars, logger=log)
        if not success:
            log.error("Could not download camb.")
            return False
        camb_path = cls.get_path(path)
        log.info("Compiling camb...")
        from subprocess import Popen, PIPE
        process_make = Popen([sys.executable, "setup.py", "build_cluster"],
                             cwd=camb_path, stdout=PIPE, stderr=PIPE)
        out, err = process_make.communicate()
        if process_make.returncode:
            log.info(out)
            log.info(err)
            gcc_check = check_gcc_version(cls.camb_min_gcc_version, error_returns=False)
            if not gcc_check:
                cause = (" Possible cause: it looks like `gcc` does not have the correct "
                         "version number (CAMB requires %s); and `ifort` is also "
                         "probably not available.", cls.camb_min_gcc_version)
            else:
                cause = ""
            log.error("Compilation failed!" + cause)
            return False
        return True


class CambTransfers(HelperTheory):
    """
    Helper theory class that calculates transfer functions only. The result is cached
    when only initial power spectrum or non-linear model parameters change
    """

    def __init__(self, cobaya_camb, name, info, timing=None):
        self.needs_perts = False
        self.non_linear_sources = False
        super(CambTransfers, self).__init__(info, name, timing=timing)
        self.cobaya_camb = cobaya_camb
        self.camb = cobaya_camb.camb
        self.speed = self.cobaya_camb.speed * 1.5

    def needs(self, **requirements):
        super(CambTransfers, self).needs(**requirements)
        opts = requirements.get('CAMB_transfers')
        if opts:
            self.non_linear_sources = opts['non_linear']
            self.needs_perts = opts['needs_perts']

            # Check that there are no repeated parameters between input and extra
            if set(self.input_params).intersection(set(self.cobaya_camb.extra_args)):
                raise LoggedError(self.log,
                                  "The following parameters appear both as input "
                                  "parameters and as CAMB extra arguments: %s. Please, "
                                  "remove one of the definitions of each.",
                                  list(set(self.input_params).intersection(
                                      set(self.cobaya_camb.extra_args))))

    def get_CAMB_transfers(self):
        return self._current_state['results']

    def calculate(self, state, want_derived=True, **params_values_dict):
        # Set parameters
        camb_params = self.cobaya_camb.set(params_values_dict, state)
        # Failed to set parameters but no error raised
        # (e.g. out of computationally feasible range): lik=0
        if not camb_params:
            return False
        # Compute the transfer functions
        try:
            if self.non_linear_sources:
                # only need time sources if non-linear lensing or other non-linear
                # sources. Not needed just for non-linear PK.
                results = self.camb.get_transfer_functions(camb_params,
                                                           only_time_sources=True)
            else:
                results = self.camb.get_transfer_functions(camb_params) \
                    if self.needs_perts else self.camb.get_background(camb_params)
            state['results'] = (camb_params, results)
        except self.camb.baseconfig.CAMBError as e:
            if self.stop_at_error:
                self.log.error(
                    "Computation error (see traceback below)! "
                    "Parameters sent to CAMB: %r and %r.\n"
                    "To ignore this kind of errors, make 'stop_at_error: False'.",
                    dict(state["params"]), dict(self.cobaya_camb.extra_args))
                raise
            else:
                # Assumed to be a "parameter out of range" error.
                self.log.debug("Computation of cosmological products failed. "
                               "Assigning 0 likelihood and going on. "
                               "The output of the CAMB error was %s" % e)
                return False

    def get_allow_agnostic(self):
        return True

    def initialize_with_params(self):
        # TODO: supports params function could specify list of parameters accepted
        #  (allowing set entries)
        if len(set(self.input_params).intersection(
                {"H0", "cosmomc_theta", "thetastar"})) > 1:
            raise LoggedError(self.log, "Can't pass more than one of H0, "
                                        "theta, cosmomc_theta to CAMB.")
        super(CambTransfers, self).initialize_with_params()
