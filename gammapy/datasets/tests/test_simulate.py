# Licensed under a 3-clause BSD style license - see LICENSE.rst
import pytest
import numpy as np
from numpy.testing import assert_allclose
import astropy.units as u
from astropy.coordinates import EarthLocation, SkyCoord
from astropy.io import fits
from astropy.table import Table
from astropy.time import Time
from regions import PointSkyRegion
from gammapy.data import GTI, DataStore, Observation
from gammapy.data.pointing import FixedPointingInfo, PointingMode
from gammapy.datasets import MapDataset, MapDatasetEventSampler
from gammapy.datasets.tests.test_map import get_map_dataset
from gammapy.irf import load_irf_dict_from_file
from gammapy.makers import MapDatasetMaker
from gammapy.maps import MapAxis, RegionNDMap, WcsGeom
from gammapy.modeling.models import (
    ConstantSpectralModel,
    FoVBackgroundModel,
    GaussianSpatialModel,
    LightCurveTemplateTemporalModel,
    Models,
    PointSpatialModel,
    PowerLawSpectralModel,
    SkyModel,
)
from gammapy.utils.testing import requires_data

LOCATION = EarthLocation(lon="-70d18m58.84s", lat="-24d41m0.34s", height="2000m")


@pytest.fixture()
def models():
    spatial_model = GaussianSpatialModel(
        lon_0="0 deg", lat_0="0 deg", sigma="0.2 deg", frame="galactic"
    )

    spectral_model = PowerLawSpectralModel(amplitude="1e-11 cm-2 s-1 TeV-1")

    t_max = 1000 * u.s

    time = np.arange(t_max.value) * u.s
    tau = u.Quantity("2e2 s")
    norm = np.exp(-time / tau)

    table = Table()
    table["TIME"] = time
    table["NORM"] = norm / norm.max()
    t_ref = Time("2000-01-01")
    table.meta = dict(MJDREFI=t_ref.mjd, MJDREFF=0, TIMEUNIT="s", TIMESYS="utc")
    temporal_model = LightCurveTemplateTemporalModel.from_table(table)

    model = SkyModel(
        spatial_model=spatial_model,
        spectral_model=spectral_model,
        temporal_model=temporal_model,
        name="test-source",
    )

    bkg_model = FoVBackgroundModel(dataset_name="test")
    return [model, bkg_model]


@pytest.fixture()
def model_alternative():
    spatial_model1 = GaussianSpatialModel(
        lon_0="0 deg", lat_0="0 deg", sigma="0.2 deg", frame="galactic"
    )

    spectral_model = PowerLawSpectralModel(amplitude="1e-11 cm-2 s-1 TeV-1")

    mod1 = SkyModel(
        spatial_model=spatial_model1,
        spectral_model=spectral_model,
        name="test-source",
    )

    spatial_model2 = GaussianSpatialModel(
        lon_0="0.5 deg", lat_0="0.5 deg", sigma="0.2 deg", frame="galactic"
    )

    mod2 = SkyModel(
        spatial_model=spatial_model2,
        spectral_model=spectral_model,
        name="test-source2",
    )

    spatial_model3 = GaussianSpatialModel(
        lon_0="0.5 deg", lat_0="0.0 deg", sigma="0.2 deg", frame="galactic"
    )

    mod3 = SkyModel(
        spatial_model=spatial_model3,
        spectral_model=spectral_model,
        name="test-source3",
    )

    bkg_model = FoVBackgroundModel(dataset_name="test")

    model2 = Models([mod1, mod2, bkg_model, mod3])
    return model2


@pytest.fixture()
@requires_data()
def enedip_temporal_model(models):
    models[0].spatial_model = PointSpatialModel(
        lon_0="0 deg", lat_0="0 deg", frame="galactic"
    )
    models[0].spectral_model = ConstantSpectralModel(const="1 cm-2 s-1 TeV-1")

    nbin = 10
    energy_axis = MapAxis.from_energy_bounds(
        energy_min=1 * u.TeV, energy_max=10 * u.TeV, nbin=nbin, name="energy"
    )

    time_min = np.arange(0, 1000, 10) * u.s
    time_max = np.arange(10, 1010, 10) * u.s
    edges = np.append(time_min, time_max[-1])
    time_axis = MapAxis.from_edges(edges=edges, name="time", interp="lin")

    data = np.ones((nbin, len(time_min))) * 1e-12 * u.cm**-2 * u.s**-1 * u.TeV**-1
    m = RegionNDMap.create(
        region=PointSkyRegion(center=models[0].spatial_model.position),
        axes=[energy_axis, time_axis],
        data=np.array(data),
    )
    t_ref = Time(51544.00074287037, format="mjd", scale="tt")
    temporal_model = LightCurveTemplateTemporalModel(m, t_ref=t_ref)
    models[0].temporal_model = temporal_model

    return models[0]


@pytest.fixture(scope="session")
def dataset():
    energy_axis = MapAxis.from_bounds(
        1, 10, nbin=3, unit="TeV", name="energy", interp="log"
    )

    geom = WcsGeom.create(
        skydir=(0, 0), binsz=0.05, width="5 deg", frame="galactic", axes=[energy_axis]
    )

    etrue_axis = energy_axis.copy(name="energy_true")
    geom_true = geom.to_image().to_cube(axes=[etrue_axis])

    dataset = get_map_dataset(
        geom=geom, geom_etrue=geom_true, edisp="edispmap", name="test"
    )
    dataset.background /= 400

    dataset.gti = GTI.create(
        start=0 * u.s, stop=1000 * u.s, reference_time=Time("2000-01-01").tt
    )

    return dataset


@requires_data()
def test_evaluate_timevar_source(enedip_temporal_model, dataset):
    dataset.models = enedip_temporal_model
    evaluator = dataset.evaluators["test-source"]

    sampler = MapDatasetEventSampler(random_state=0)
    npred = sampler._evaluate_timevar_source(dataset, evaluator)

    assert_allclose(np.shape(npred.data), (3, 1999, 1, 1))

    assert_allclose(npred.data[:, 10, 0, 0], [0.024827, 0.110424, 0.321845], rtol=2e-4)
    assert_allclose(npred.data[:, 50, 0, 0], [0.024827, 0.110424, 0.321845], rtol=2e-4)

    filename = "$GAMMAPY_DATA/gravitational_waves/GW_example_DC_map_file.fits.gz"
    temporal_model = LightCurveTemplateTemporalModel.read(filename, format="map")
    temporal_model.t_ref.value = 51544.00074287037
    dataset.models[0].temporal_model = temporal_model
    evaluator = dataset.evaluators["test-source"]

    sampler = MapDatasetEventSampler(random_state=0)
    npred = sampler._evaluate_timevar_source(dataset, evaluator)

    assert_allclose(
        npred.data[:, 1000, 0, 0] / 1e-13,
        [0.038113, 0.033879, 0.010938],
        rtol=2e-4,
    )


@requires_data()
def test_sample_coord_time_energy(dataset, enedip_temporal_model):
    enedip_temporal_model.spatial_model = None
    enedip_temporal_model.spectral_model = ConstantSpectralModel(
        const="1 cm-2 s-1 TeV-1"
    )
    dataset.models = enedip_temporal_model
    evaluator = dataset.evaluators["test-source"]
    sampler = MapDatasetEventSampler(random_state=0)
    with pytest.raises(TypeError):
        sampler._sample_coord_time_energy(dataset, evaluator)

    enedip_temporal_model.spatial_model = GaussianSpatialModel()
    enedip_temporal_model.spectral_model = ConstantSpectralModel(
        const="1 cm-2 s-1 TeV-1"
    )
    dataset.models = enedip_temporal_model
    evaluator = dataset.evaluators["test-source"]
    sampler = MapDatasetEventSampler(random_state=0)
    with pytest.raises(TypeError):
        sampler._sample_coord_time_energy(dataset, evaluator)

    enedip_temporal_model.spatial_model = PointSpatialModel(
        lon_0="0 deg", lat_0="0 deg", frame="galactic"
    )
    dataset.models = enedip_temporal_model
    evaluator = dataset.evaluators["test-source"]
    sampler = MapDatasetEventSampler(random_state=0)
    events = sampler._sample_coord_time_energy(dataset, evaluator)

    assert_allclose(len(events), 918)

    assert_allclose(
        [events[0][0], events[0][1], events[0][2], events[0][3]],
        [568.238666, 7.687285, 266.404988, -28.936178],
        rtol=1e-6,
    )


@requires_data()
def test_mde_sample_sources(dataset, models):
    dataset.models = models
    sampler = MapDatasetEventSampler(random_state=0)
    events = sampler.sample_sources(dataset=dataset)

    assert len(events.table["ENERGY_TRUE"]) == 90
    assert_allclose(events.table["ENERGY_TRUE"][0], 2.383778805, rtol=1e-5)
    assert events.table["ENERGY_TRUE"].unit == "TeV"

    assert_allclose(events.table["RA_TRUE"][0], 266.56408893, rtol=1e-5)
    assert events.table["RA_TRUE"].unit == "deg"

    assert_allclose(events.table["DEC_TRUE"][0], -28.748145, rtol=1e-5)
    assert events.table["DEC_TRUE"].unit == "deg"

    assert_allclose(events.table["TIME"][0], 119.7494479, rtol=1e-5)
    assert events.table["TIME"].unit == "s"

    assert_allclose(events.table["MC_ID"][0], 1, rtol=1e-5)


@requires_data()
def test_mde_sample_weak_src(dataset, models):
    irfs = load_irf_dict_from_file(
        "$GAMMAPY_DATA/cta-1dc/caldb/data/cta/1dc/bcf/South_z20_50h/irf_file.fits"
    )
    livetime = 10.0 * u.hr
    pointing = FixedPointingInfo(
        mode=PointingMode.POINTING,
        fixed_icrs=SkyCoord(0, 0, unit="deg", frame="galactic").icrs,
    )
    obs = Observation.create(
        obs_id=1001,
        pointing=pointing,
        livetime=livetime,
        irfs=irfs,
        location=LOCATION,
    )

    models[0].parameters["amplitude"].value = 1e-25

    dataset.models = models

    sampler = MapDatasetEventSampler(random_state=0)
    events = sampler.run(dataset=dataset, observation=obs)

    assert len(events.table) == 18
    assert_allclose(
        len(np.where(events.table["MC_ID"] == 0)[0]), len(events.table), rtol=1e-5
    )


@requires_data()
def test_mde_sample_background(dataset, models):
    dataset.models = models
    sampler = MapDatasetEventSampler(random_state=0)
    events = sampler.sample_background(dataset=dataset)

    assert len(events.table["ENERGY"]) == 15
    assert_allclose(events.table["ENERGY"][0], 1.894698, rtol=1e-5)
    assert events.table["ENERGY"].unit == "TeV"

    assert_allclose(events.table["RA"][0], 266.571824, rtol=1e-5)
    assert events.table["RA"].unit == "deg"

    assert_allclose(events.table["DEC"][0], -27.979152, rtol=1e-5)
    assert events.table["DEC"].unit == "deg"

    assert events.table["DEC_TRUE"][0] == events.table["DEC"][0]

    assert_allclose(events.table["MC_ID"][0], 0, rtol=1e-5)


@requires_data()
def test_mde_sample_psf(dataset, models):
    dataset.models = models
    sampler = MapDatasetEventSampler(random_state=0)
    events = sampler.sample_sources(dataset=dataset)
    events = sampler.sample_psf(dataset.psf, events)

    assert len(events.table) == 90
    assert_allclose(events.table["ENERGY_TRUE"][0], 2.38377880, rtol=1e-5)
    assert events.table["ENERGY_TRUE"].unit == "TeV"

    assert_allclose(events.table["RA"][0], 266.542912, rtol=1e-5)
    assert events.table["RA"].unit == "deg"

    assert_allclose(events.table["DEC"][0], -28.78829, rtol=1e-5)
    assert events.table["DEC"].unit == "deg"


@requires_data()
def test_mde_sample_edisp(dataset, models):
    dataset.models = models
    sampler = MapDatasetEventSampler(random_state=0)
    events = sampler.sample_sources(dataset=dataset)
    events = sampler.sample_edisp(dataset.edisp, events)

    assert len(events.table) == 90
    assert_allclose(events.table["ENERGY"][0], 2.383778805, rtol=1e-5)
    assert events.table["ENERGY"].unit == "TeV"

    assert_allclose(events.table["RA_TRUE"][0], 266.564088, rtol=1e-5)
    assert events.table["RA_TRUE"].unit == "deg"

    assert_allclose(events.table["DEC_TRUE"][0], -28.7481450, rtol=1e-5)
    assert events.table["DEC_TRUE"].unit == "deg"

    assert_allclose(events.table["MC_ID"][0], 1, rtol=1e-5)


@requires_data()
def test_event_det_coords(dataset, models):
    irfs = load_irf_dict_from_file(
        "$GAMMAPY_DATA/cta-1dc/caldb/data/cta/1dc/bcf/South_z20_50h/irf_file.fits"
    )
    livetime = 1.0 * u.hr
    pointing = FixedPointingInfo(
        mode=PointingMode.POINTING,
        fixed_icrs=SkyCoord(0, 0, unit="deg", frame="galactic").icrs,
    )
    obs = Observation.create(
        obs_id=1001,
        pointing=pointing,
        livetime=livetime,
        irfs=irfs,
        location=LOCATION,
    )

    dataset.models = models
    sampler = MapDatasetEventSampler(random_state=0)
    events = sampler.run(dataset=dataset, observation=obs)

    assert len(events.table) == 99
    assert_allclose(events.table["DETX"][0], -1.15531813, rtol=1e-5)
    assert events.table["DETX"].unit == "deg"

    assert_allclose(events.table["DETY"][0], -1.3343611, rtol=1e-5)
    assert events.table["DETY"].unit == "deg"


@requires_data()
def test_mde_run(dataset, models):
    irfs = load_irf_dict_from_file(
        "$GAMMAPY_DATA/cta-1dc/caldb/data/cta/1dc/bcf/South_z20_50h/irf_file.fits"
    )
    livetime = 1.0 * u.hr
    pointing = FixedPointingInfo(
        mode=PointingMode.POINTING,
        fixed_icrs=SkyCoord(0, 0, unit="deg", frame="galactic").icrs,
    )
    obs = Observation.create(
        obs_id=1001,
        pointing=pointing,
        livetime=livetime,
        irfs=irfs,
        location=LOCATION,
    )

    dataset.models = models
    sampler = MapDatasetEventSampler(random_state=0)
    events = sampler.run(dataset=dataset, observation=obs)

    dataset_bkg = dataset.copy(name="new-dataset")
    dataset_bkg.models = [FoVBackgroundModel(dataset_name=dataset_bkg.name)]

    events_bkg = sampler.run(dataset=dataset_bkg, observation=obs)

    assert len(events.table) == 99
    assert_allclose(events.table["ENERGY"][0], 4.406880, rtol=1e-5)
    assert_allclose(events.table["RA"][0], 265.0677009, rtol=1e-5)
    assert_allclose(events.table["DEC"][0], -30.2640157, rtol=1e-5)

    assert len(events_bkg.table) == 21
    assert_allclose(events_bkg.table["ENERGY"][0], 1.5462581456, rtol=1e-5)
    assert_allclose(events_bkg.table["RA"][0], 265.77338329, rtol=1e-5)
    assert_allclose(events_bkg.table["DEC"][0], -30.701417442, rtol=1e-5)
    assert_allclose(events_bkg.table["MC_ID"][0], 0, rtol=1e-5)

    meta = events.table.meta

    assert meta["HDUCLAS1"] == "EVENTS"
    assert meta["EXTNAME"] == "EVENTS"
    assert (
        meta["HDUDOC"]
        == "https://github.com/open-gamma-ray-astro/gamma-astro-data-formats"
    )
    assert meta["HDUVERS"] == "0.2"
    assert meta["HDUCLASS"] == "GADF"
    assert meta["OBS_ID"] == 1001
    assert_allclose(meta["TSTART"], 0.0)
    assert_allclose(meta["TSTOP"], 3600.0)
    assert_allclose(meta["ONTIME"], 3600.0)
    assert_allclose(meta["LIVETIME"], 3600.0)
    assert_allclose(meta["DEADC"], 1.0)
    assert_allclose(meta["RA_PNT"], 266.4049882865447)
    assert_allclose(meta["DEC_PNT"], -28.936177761791473)
    assert meta["EQUINOX"] == "J2000"
    assert meta["RADECSYS"] == "icrs"
    assert "Gammapy" in meta["CREATOR"]
    assert meta["EUNIT"] == "TeV"
    assert meta["EVTVER"] == ""
    assert meta["OBSERVER"] == "Gammapy user"
    assert meta["DSTYP1"] == "TIME"
    assert meta["DSUNI1"] == "s"
    assert meta["DSVAL1"] == "TABLE"
    assert meta["DSREF1"] == ":GTI"
    assert meta["DSTYP2"] == "ENERGY"
    assert meta["DSUNI2"] == "TeV"
    assert ":" in meta["DSVAL2"]
    assert meta["DSTYP3"] == "POS(RA,DEC)     "
    assert "CIRCLE" in meta["DSVAL3"]
    assert meta["DSUNI3"] == "deg             "
    assert meta["NDSKEYS"] == " 3 "
    assert_allclose(meta["RA_OBJ"], 266.4049882865447)
    assert_allclose(meta["DEC_OBJ"], -28.936177761791473)
    assert_allclose(meta["TELAPSE"], 1000.0)
    assert_allclose(meta["MJDREFI"], 51544)
    assert_allclose(meta["MJDREFF"], 0.0007428703684126958)
    assert meta["TIMEUNIT"] == "s"
    assert meta["TIMESYS"] == "tt"
    assert meta["TIMEREF"] == "LOCAL"
    assert meta["DATE-OBS"] == "2000-01-01"
    assert meta["DATE-END"] == "2000-01-01"
    assert meta["CONV_DEP"] == 0
    assert meta["CONV_RA"] == 0
    assert meta["CONV_DEC"] == 0
    assert meta["MID00000"] == 0
    assert meta["MMN00000"] == "test-bkg"
    assert meta["MID00001"] == 1
    assert meta["NMCIDS"] == 2
    assert_allclose(float(meta["ALT_PNT"]), float("-13.5345076464"), rtol=1e-7)
    assert_allclose(float(meta["AZ_PNT"]), float("228.82981620065763"), rtol=1e-7)
    assert meta["ORIGIN"] == "Gammapy"
    assert meta["TELESCOP"] == "CTA"
    assert meta["INSTRUME"] == "1DC"
    assert meta["N_TELS"] == ""
    assert meta["TELLIST"] == ""


@requires_data()
def test_irf_alpha_config(dataset, models):
    irfs = load_irf_dict_from_file(
        "$GAMMAPY_DATA/cta-caldb/Prod5-South-20deg-AverageAz-14MSTs37SSTs.180000s-v0.1.fits.gz"
    )
    livetime = 1.0 * u.hr
    pointing = FixedPointingInfo(
        mode=PointingMode.POINTING,
        fixed_icrs=SkyCoord(0, 0, unit="deg", frame="galactic").icrs,
    )
    obs = Observation.create(
        obs_id=1001,
        pointing=pointing,
        livetime=livetime,
        irfs=irfs,
        location=LOCATION,
    )

    dataset.models = models
    sampler = MapDatasetEventSampler(random_state=0)
    events = sampler.run(dataset=dataset, observation=obs)
    assert events is not None


@requires_data()
def test_mde_run_switchoff(dataset, models):
    irfs = load_irf_dict_from_file(
        "$GAMMAPY_DATA/cta-1dc/caldb/data/cta/1dc/bcf/South_z20_50h/irf_file.fits"
    )
    livetime = 1.0 * u.hr
    pointing = FixedPointingInfo(
        mode=PointingMode.POINTING,
        fixed_icrs=SkyCoord(0, 0, unit="deg", frame="galactic").icrs,
    )
    obs = Observation.create(
        obs_id=1001,
        pointing=pointing,
        livetime=livetime,
        irfs=irfs,
        location=LOCATION,
    )

    dataset.models = models

    dataset.psf = None
    dataset.edisp = None
    dataset.background = None

    sampler = MapDatasetEventSampler(random_state=0)
    events = sampler.run(dataset=dataset, observation=obs)

    assert len(events.table) == 90
    assert_allclose(events.table["ENERGY"][0], 2.3837788, rtol=1e-5)
    assert_allclose(events.table["RA"][0], 266.56408893, rtol=1e-5)
    assert_allclose(events.table["DEC"][0], -28.748145, rtol=1e-5)

    meta = events.table.meta

    assert meta["RA_PNT"] == 266.4049882865447
    assert_allclose(meta["ONTIME"], 3600.0)
    assert meta["OBS_ID"] == 1001
    assert meta["RADECSYS"] == "icrs"


@requires_data()
def test_events_datastore(tmp_path, dataset, models):
    irfs = load_irf_dict_from_file(
        "$GAMMAPY_DATA/cta-1dc/caldb/data/cta/1dc/bcf/South_z20_50h/irf_file.fits"
    )
    livetime = 10.0 * u.hr
    pointing = FixedPointingInfo(
        mode=PointingMode.POINTING,
        fixed_icrs=SkyCoord(0, 0, unit="deg", frame="galactic").icrs,
    )
    obs = Observation.create(
        obs_id=1001,
        pointing=pointing,
        livetime=livetime,
        irfs=irfs,
        location=LOCATION,
    )

    dataset.models = models
    sampler = MapDatasetEventSampler(random_state=0)
    events = sampler.run(dataset=dataset, observation=obs)

    primary_hdu = fits.PrimaryHDU()
    hdu_evt = fits.BinTableHDU(events.table)
    hdu_gti = dataset.gti.to_table_hdu(format="gadf")
    hdu_all = fits.HDUList([primary_hdu, hdu_evt, hdu_gti])
    hdu_all.writeto(str(tmp_path / "events.fits"))

    DataStore.from_events_files([str(tmp_path / "events.fits")])


@requires_data()
def test_MC_ID(model_alternative):
    irfs = load_irf_dict_from_file(
        "$GAMMAPY_DATA/cta-1dc/caldb/data/cta/1dc/bcf/South_z20_50h/irf_file.fits"
    )
    livetime = 0.1 * u.hr
    skydir = SkyCoord(0, 0, unit="deg", frame="galactic")
    pointing = FixedPointingInfo(mode=PointingMode.POINTING, fixed_icrs=skydir.icrs)
    obs = Observation.create(
        obs_id=1001,
        pointing=pointing,
        livetime=livetime,
        irfs=irfs,
        location=LOCATION,
    )

    energy_axis = MapAxis.from_energy_bounds(
        "1.0 TeV", "10 TeV", nbin=10, per_decade=True
    )
    energy_axis_true = MapAxis.from_energy_bounds(
        "0.5 TeV", "20 TeV", nbin=20, per_decade=True, name="energy_true"
    )
    migra_axis = MapAxis.from_bounds(0.5, 2, nbin=150, node_type="edges", name="migra")

    geom = WcsGeom.create(
        skydir=skydir,
        width=(2, 2),
        binsz=0.06,
        frame="icrs",
        axes=[energy_axis],
    )

    empty = MapDataset.create(
        geom,
        energy_axis_true=energy_axis_true,
        migra_axis=migra_axis,
        name="test",
    )
    maker = MapDatasetMaker(selection=["exposure", "background", "psf", "edisp"])
    dataset = maker.run(empty, obs)

    dataset.models = model_alternative
    sampler = MapDatasetEventSampler(random_state=0)
    events = sampler.run(dataset=dataset, observation=obs)

    assert len(events.table) == 215
    assert len(np.where(events.table["MC_ID"] == 0)[0]) == 40

    meta = events.table.meta
    assert meta["MID00000"] == 0
    assert meta["MMN00000"] == "test-bkg"
    assert meta["MID00001"] == 1
    assert meta["MID00002"] == 2
    assert meta["MID00003"] == 3
    assert meta["NMCIDS"] == 4


@requires_data()
def test_MC_ID_NMCID(model_alternative):
    irfs = load_irf_dict_from_file(
        "$GAMMAPY_DATA/cta-1dc/caldb/data/cta/1dc/bcf/South_z20_50h/irf_file.fits"
    )
    livetime = 0.1 * u.hr
    skydir = SkyCoord(0, 0, unit="deg", frame="galactic")
    pointing = FixedPointingInfo(mode=PointingMode.POINTING, fixed_icrs=skydir.icrs)
    obs = Observation.create(
        obs_id=1001,
        pointing=pointing,
        livetime=livetime,
        irfs=irfs,
        location=LOCATION,
    )

    energy_axis = MapAxis.from_energy_bounds(
        "1.0 TeV", "10 TeV", nbin=10, per_decade=True
    )
    energy_axis_true = MapAxis.from_energy_bounds(
        "0.5 TeV", "20 TeV", nbin=20, per_decade=True, name="energy_true"
    )
    migra_axis = MapAxis.from_bounds(0.5, 2, nbin=150, node_type="edges", name="migra")

    geom = WcsGeom.create(
        skydir=skydir,
        width=(2, 2),
        binsz=0.06,
        frame="icrs",
        axes=[energy_axis],
    )

    empty = MapDataset.create(
        geom,
        energy_axis_true=energy_axis_true,
        migra_axis=migra_axis,
        name="test",
    )
    maker = MapDatasetMaker(selection=["exposure", "background", "psf", "edisp"])
    dataset = maker.run(empty, obs)

    model_alternative[0].spectral_model.parameters["amplitude"].value = 1e-16
    dataset.models = model_alternative
    sampler = MapDatasetEventSampler(random_state=0)
    events = sampler.run(dataset=dataset, observation=obs)

    assert len(events.table) == 47
    assert len(np.where(events.table["MC_ID"] == 0)[0]) == 47

    meta = events.table.meta
    assert meta["MID00000"] == 0
    assert meta["MMN00000"] == "test-bkg"
    assert meta["MID00001"] == 1
    assert meta["MID00002"] == 2
    assert meta["MID00003"] == 3
    assert meta["NMCIDS"] == 4
