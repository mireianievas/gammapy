# Licensed under a 3-clause BSD style license - see LICENSE.rst
import logging
from pathlib import Path
import pytest
from numpy.testing import assert_allclose
import astropy.units as u
from astropy.coordinates import SkyCoord
from regions import CircleSkyRegion
from pydantic import ValidationError
from gammapy.datasets import MapDataset, SpectrumDatasetOnOff
from gammapy.maps import WcsGeom, WcsNDMap
from gammapy.modeling.models import DatasetModels
from gammapy.utils.testing import requires_data
from gammapy.workflow import Workflow, WorkflowConfig

CONFIG_PATH = Path(__file__).resolve().parent / ".." / "config"
MODEL_FILE = CONFIG_PATH / "model.yaml"
MODEL_FILE_1D = CONFIG_PATH / "model-1d.yaml"


def get_example_config(which):
    """Example config: which can be 1d or 3d."""
    return WorkflowConfig.read(CONFIG_PATH / f"example-{which}.yaml")


def test_init():
    cfg = {"general": {"outdir": "test"}}
    workflow = Workflow(cfg)
    assert workflow.config.general.outdir == "test"
    with pytest.raises(TypeError):
        Workflow("spam")


def test_update_config():
    workflow = Workflow(WorkflowConfig())
    data = {"general": {"outdir": "test"}}
    config = WorkflowConfig(**data)
    workflow.update_config(config)
    assert workflow.config.general.outdir == "test"

    workflow = Workflow(WorkflowConfig())
    data = """
    general:
        outdir: test
    """
    workflow.update_config(data)
    assert workflow.config.general.outdir == "test"

    workflow = Workflow(WorkflowConfig())
    with pytest.raises(TypeError):
        workflow.update_config(0)


def test_get_observations_no_datastore():
    config = WorkflowConfig()
    workflow = Workflow(config)
    workflow.config.observations.datastore = "other"
    with pytest.raises(FileNotFoundError):
        workflow.get_observations()


@requires_data()
def test_get_observations_all():
    config = WorkflowConfig()
    workflow = Workflow(config)
    workflow.config.observations.datastore = "$GAMMAPY_DATA/cta-1dc/index/gps/"
    workflow.get_observations()
    assert len(workflow.observations) == 4


@requires_data()
def test_get_observations_obs_ids():
    config = WorkflowConfig()
    workflow = Workflow(config)
    workflow.config.observations.datastore = "$GAMMAPY_DATA/cta-1dc/index/gps/"
    workflow.config.observations.obs_ids = ["110380"]
    workflow.get_observations()
    assert len(workflow.observations) == 1


@requires_data()
def test_get_observations_obs_cone():
    config = WorkflowConfig()
    workflow = Workflow(config)
    workflow.config.observations.datastore = "$GAMMAPY_DATA/hess-dl3-dr1"
    workflow.config.observations.obs_cone = {
        "frame": "icrs",
        "lon": "83d",
        "lat": "22d",
        "radius": "5d",
    }
    workflow.get_observations()
    assert len(workflow.observations) == 4


@requires_data()
def test_get_observations_obs_file(tmp_path):
    config = WorkflowConfig()
    workflow = Workflow(config)
    workflow.get_observations()
    filename = tmp_path / "obs_ids.txt"
    filename.write_text("20136\n47829\n")
    workflow.config.observations.obs_file = filename
    workflow.get_observations()
    assert len(workflow.observations) == 2


@requires_data()
def test_get_observations_obs_time(tmp_path):
    config = WorkflowConfig()
    workflow = Workflow(config)
    workflow.config.observations.obs_time = {
        "start": "2004-03-26",
        "stop": "2004-05-26",
    }
    workflow.get_observations()
    assert len(workflow.observations) == 40
    workflow.config.observations.obs_ids = [0]
    with pytest.raises(KeyError):
        workflow.get_observations()


@requires_data()
def test_get_observations_missing_irf():
    config = WorkflowConfig()
    workflow = Workflow(config)
    workflow.config.observations.datastore = "$GAMMAPY_DATA/joint-crab/dl3/magic/"
    workflow.config.observations.obs_ids = ["05029748"]
    workflow.config.observations.required_irf = ["aeff", "edisp"]
    workflow.get_observations()
    assert len(workflow.observations) == 1


@requires_data()
def test_set_models():
    config = get_example_config("3d")
    workflow = Workflow(config)
    workflow.get_observations()
    workflow.get_datasets()
    models_str = Path(MODEL_FILE).read_text()
    workflow.set_models(models=models_str)
    assert isinstance(workflow.models, DatasetModels)
    assert len(workflow.models) == 2
    assert workflow.models.names == ["source", "stacked-bkg"]
    with pytest.raises(TypeError):
        workflow.set_models(0)

    new_source = workflow.models["source"].copy(name="source2")
    workflow.set_models(models=[new_source], extend=False)
    assert len(workflow.models) == 2
    assert workflow.models.names == ["source2", "stacked-bkg"]


@requires_data()
def test_workflow_1d():
    cfg = """
    observations:
        datastore: $GAMMAPY_DATA/hess-dl3-dr1
        obs_ids: [23523, 23526]
        obs_time: {
            start: [J2004.92654346, J2004.92658453, J2004.92663655],
            stop: [J2004.92658453, J2004.92663655, J2004.92670773]
        }
    datasets:
        type: 1d
        background:
            method: reflected
        geom:
            axes:
                energy_true: {min: 0.01 TeV, max: 300 TeV, nbins: 109}
        on_region: {frame: icrs, lon: 83.633 deg, lat: 22.014 deg, radius: 0.11 deg}
        safe_mask:
            methods: [aeff-default, edisp-bias]
            parameters: {bias_percent: 10.0}
        containment_correction: false
    flux_points:
        energy: {min: 1 TeV, max: 50 TeV, nbins: 4}
    light_curve:
        energy_edges: {min: 1 TeV, max: 50 TeV, nbins: 1}
        time_intervals: {
            start: [J2004.92654346, J2004.92658453, J2004.92663655],
            stop: [J2004.92658453, J2004.92663655, J2004.92670773]
        }
    """
    config = get_example_config("1d")
    workflow = Workflow(config)
    workflow.update_config(cfg)
    workflow.get_observations()
    workflow.get_datasets()
    workflow.read_models(MODEL_FILE_1D)
    workflow.run_fit()
    workflow.get_flux_points()
    workflow.get_light_curve()

    assert len(workflow.datasets) == 3
    table = workflow.flux_points.data.to_table(sed_type="dnde")

    assert len(table) == 4
    dnde = table["dnde"].quantity
    assert dnde.unit == "cm-2 s-1 TeV-1"

    assert_allclose(dnde[0].value, 8.116854e-12, rtol=1e-2)
    assert_allclose(dnde[2].value, 3.444475e-14, rtol=1e-2)

    axis = workflow.light_curve.geom.axes["time"]
    assert axis.nbin == 3
    assert_allclose(axis.time_min.mjd, [53343.92, 53343.935, 53343.954])

    flux = workflow.light_curve.flux.data[:, :, 0, 0]
    assert_allclose(flux, [[1.688954e-11], [2.347870e-11], [1.604152e-11]], rtol=1e-4)


@requires_data()
def test_geom_workflow_1d():
    cfg = """
    observations:
        datastore: $GAMMAPY_DATA/hess-dl3-dr1
        obs_ids: [23523]
    datasets:
        type: 1d
        background:
            method: reflected
        on_region: {frame: icrs, lon: 83.633 deg, lat: 22.014 deg, radius: 0.11 deg}
        geom:
            axes:
                energy: {min: 0.1 TeV, max: 30 TeV, nbins: 20}
                energy_true: {min: 0.03 TeV, max: 100 TeV, nbins: 50}
        containment_correction: false
    flux_points:
        energy: {min: 1 TeV, max: 50 TeV, nbins: 4}
    """
    config = get_example_config("1d")
    workflow = Workflow(config)
    workflow.update_config(cfg)
    workflow.get_observations()
    workflow.get_datasets()

    assert len(workflow.datasets) == 1

    axis = workflow.datasets[0].exposure.geom.axes["energy_true"]
    assert axis.nbin == 50
    assert_allclose(axis.edges[0].to_value("TeV"), 0.03)
    assert_allclose(axis.edges[-1].to_value("TeV"), 100)


@requires_data()
def test_exclusion_region(tmp_path):
    config = get_example_config("1d")
    workflow = Workflow(config)
    region = CircleSkyRegion(center=SkyCoord("85d 23d"), radius=1 * u.deg)
    geom = WcsGeom.create(npix=(150, 150), binsz=0.05, skydir=SkyCoord("83d 22d"))
    exclusion_mask = ~geom.region_mask([region])

    filename = tmp_path / "exclusion.fits"
    exclusion_mask.write(filename)
    config.datasets.background.method = "reflected"
    config.datasets.background.exclusion = filename
    workflow.get_observations()
    workflow.get_datasets()
    assert len(workflow.datasets) == 2

    config = get_example_config("3d")
    workflow = Workflow(config)
    workflow.get_observations()
    workflow.get_datasets()
    geom = workflow.datasets[0]._geom
    exclusion_mask = ~geom.region_mask([region])
    filename = tmp_path / "exclusion3d.fits"
    exclusion_mask.write(filename)
    config.datasets.background.exclusion = filename
    workflow.get_datasets()
    assert len(workflow.datasets) == 1


@requires_data()
def test_workflow_1d_stacked_no_fit_range():
    cfg = """
    observations:
        datastore: $GAMMAPY_DATA/hess-dl3-dr1
        obs_cone: {frame: icrs, lon: 83.633 deg, lat: 22.014 deg, radius: 5 deg}
        obs_ids: [23592, 23559]

    datasets:
        type: 1d
        stack: false
        geom:
            axes:
                energy: {min: 0.01 TeV, max: 100 TeV, nbins: 73}
                energy_true: {min: 0.03 TeV, max: 100 TeV, nbins: 50}
        on_region: {frame: icrs, lon: 83.633 deg, lat: 22.014 deg, radius: 0.1 deg}
        containment_correction: true
        background:
            method: reflected
    """
    config = WorkflowConfig.from_yaml(cfg)
    workflow = Workflow(config)
    workflow.update_config(cfg)
    workflow.config.datasets.stack = True
    workflow.get_observations()
    workflow.get_datasets()
    workflow.read_models(MODEL_FILE_1D)
    workflow.run_fit()
    with pytest.raises(ValueError):
        workflow.get_excess_map()

    assert len(workflow.datasets) == 1
    assert_allclose(workflow.datasets["stacked"].counts.data.sum(), 184)
    pars = workflow.models.parameters
    assert_allclose(workflow.datasets[0].mask_fit.data, True)

    assert_allclose(pars["index"].value, 2.76913, rtol=1e-2)
    assert_allclose(pars["amplitude"].value, 5.479729e-11, rtol=1e-2)


@requires_data()
def test_workflow_ring_background():
    config = get_example_config("3d")
    config.datasets.background.method = "ring"
    config.datasets.background.parameters = {"r_in": "0.7 deg", "width": "0.7 deg"}
    config.datasets.geom.axes.energy.nbins = 1
    workflow = Workflow(config)
    workflow.get_observations()
    workflow.get_datasets()
    workflow.get_excess_map()
    assert isinstance(workflow.datasets[0], MapDataset)
    assert_allclose(
        workflow.datasets[0].npred_background().data[0, 10, 10], 0.091799, rtol=1e-2
    )
    assert isinstance(workflow.excess_map["sqrt_ts"], WcsNDMap)
    assert_allclose(workflow.excess_map.npred_excess.data[0, 62, 62], 134.12389)


@requires_data()
def test_workflow_ring_3d():
    config = get_example_config("3d")
    config.datasets.background.method = "ring"
    config.datasets.background.parameters = {"r_in": "0.7 deg", "width": "0.7 deg"}
    workflow = Workflow(config)
    workflow.get_observations()
    with pytest.raises(ValueError):
        workflow.get_datasets()


@requires_data()
def test_workflow_no_bkg_1d(caplog):
    config = get_example_config("1d")
    workflow = Workflow(config)
    with caplog.at_level(logging.WARNING):
        workflow.get_observations()
        workflow.get_datasets()
        assert not isinstance(workflow.datasets[0], SpectrumDatasetOnOff)
        assert "No background maker set. Check configuration." in [
            _.message for _ in caplog.records
        ]


@requires_data()
def test_workflow_no_bkg_3d(caplog):
    config = get_example_config("3d")
    config.datasets.background.method = None
    workflow = Workflow(config)
    with caplog.at_level(logging.WARNING):
        workflow.get_observations()
        workflow.get_datasets()
        assert isinstance(workflow.datasets[0], MapDataset)
        assert "No background maker set. Check configuration." in [
            _.message for _ in caplog.records
        ]


@requires_data()
def test_workflow_3d():
    config = get_example_config("3d")
    workflow = Workflow(config)
    workflow.get_observations()
    workflow.get_datasets()
    workflow.read_models(MODEL_FILE)
    workflow.datasets["stacked"].background_model.spectral_model.tilt.frozen = False
    workflow.run_fit()
    workflow.get_flux_points()

    assert len(workflow.datasets) == 1
    assert len(workflow.models.parameters) == 8
    res = workflow.models.parameters
    assert res["amplitude"].unit == "cm-2 s-1 TeV-1"

    table = workflow.flux_points.data.to_table(sed_type="dnde")
    assert len(table) == 2
    dnde = table["dnde"].quantity

    assert_allclose(dnde[0].value, 1.2722e-11, rtol=1e-2)
    assert_allclose(dnde[-1].value, 4.054128e-13, rtol=1e-2)
    assert_allclose(res["index"].value, 2.772814, rtol=1e-2)
    assert_allclose(res["tilt"].value, -0.133436, rtol=1e-2)


@requires_data()
def test_workflow_3d_joint_datasets():
    config = get_example_config("3d")
    config.datasets.stack = False
    workflow = Workflow(config)
    workflow.get_observations()
    workflow.get_datasets()
    assert len(workflow.datasets) == 2

    assert_allclose(
        workflow.datasets[0].background_model.spectral_model.norm.value,
        1.031743694988066,
        rtol=1e-6,
    )
    assert_allclose(
        workflow.datasets[0].background_model.spectral_model.tilt.value,
        0.0,
        rtol=1e-6,
    )
    assert_allclose(
        workflow.datasets[1].background_model.spectral_model.norm.value,
        0.9776349021876344,
        rtol=1e-6,
    )


@requires_data()
def test_usage_errors():
    config = get_example_config("1d")
    workflow = Workflow(config)
    with pytest.raises(RuntimeError):
        workflow.get_datasets()
    with pytest.raises(RuntimeError):
        workflow.read_datasets()
    with pytest.raises(RuntimeError):
        workflow.write_datasets()
    with pytest.raises(TypeError):
        workflow.read_models()
    with pytest.raises(RuntimeError):
        workflow.write_models()
    with pytest.raises(RuntimeError):
        workflow.run_fit()
    with pytest.raises(RuntimeError):
        workflow.get_flux_points()
    with pytest.raises(ValidationError):
        workflow.config.datasets.type = "None"


@requires_data()
def test_datasets_io(tmpdir):
    config = get_example_config("3d")

    workflow = Workflow(config)
    workflow.get_observations()
    workflow.get_datasets()
    models_str = Path(MODEL_FILE).read_text()
    workflow.models = models_str

    config.general.datasets_file = tmpdir / "datasets.yaml"
    config.general.models_file = tmpdir / "models.yaml"
    workflow.write_datasets()
    workflow = Workflow(config)
    workflow.read_datasets()
    assert len(workflow.datasets.models) == 2
    assert workflow.models.names == ["source", "stacked-bkg"]

    workflow.models[0].parameters["index"].value = 3
    workflow.write_models()
    workflow = Workflow(config)
    workflow.read_datasets()
    assert len(workflow.datasets.models) == 2
    assert workflow.models.names == ["source", "stacked-bkg"]
    assert workflow.models[0].parameters["index"].value == 3
