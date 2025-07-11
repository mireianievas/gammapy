# Licensed under a 3-clause BSD style license - see LICENSE.rst
import warnings
import pytest
import yaml
from numpy.testing import assert_allclose
from gammapy.utils.scripts import (
    get_images_paths,
    make_path,
    read_yaml,
    recursive_merge_dicts,
    requires_module,
    to_yaml,
    write_yaml,
)


@pytest.mark.xfail
def test_get_images_paths():
    assert any("images" in str(p) for p in get_images_paths())


def test_recursive_merge_dicts():
    old = dict(a=42, b=dict(c=43, e=44))
    update = dict(d=99, b=dict(g=98, c=50))

    new = recursive_merge_dicts(old, update)
    assert new["b"]["c"] == 50
    assert new["b"]["e"] == 44
    assert new["b"]["g"] == 98
    assert new["a"] == 42
    assert new["d"] == 99


def test_read_write_yaml_checksum(tmp_path):
    data = to_yaml({"b": 1234, "a": "other"})
    path = make_path(tmp_path / "test.yaml")
    write_yaml(data, path, sort_keys=False, checksum=True)

    yaml_content = path.read_text()
    assert "checksum: " in yaml_content

    with warnings.catch_warnings():
        warnings.simplefilter("error")
        res = read_yaml(path, checksum=True)
    assert "checksum" not in res.keys()

    yaml_content = yaml_content.replace("1234", "12345")
    bad = make_path(tmp_path) / "bad_checksum.yaml"
    bad.write_text(yaml_content)
    with pytest.warns(UserWarning):
        read_yaml(bad, checksum=True)

    res["checksum"] = "bad"
    yaml_str = yaml.dump(data, sort_keys=True, default_flow_style=False)
    path.write_text(yaml_str)

    with pytest.warns(UserWarning):
        read_yaml(bad, checksum=True)


def test_requires_module():
    class MyClass:
        @requires_module("math")
        def method(self):
            import math

            return math.sqrt(9)

        @requires_module("nonexistent_module")
        def method_unavailable(self):
            return "Should not be called"

        @requires_module("math")
        @property
        def prop(self):
            import math

            return math.sqrt(16)

        @requires_module("nonexistent_module")
        @property
        def prop_unavailable(self):
            return "Should not be called"

    result = MyClass()

    assert_allclose(result.method(), 3.0)

    with pytest.raises(
        ImportError,
        match="The 'nonexistent_module' module is required to use this method.",
    ):
        result.method_unavailable()

    assert_allclose(result.prop, 4.0)

    with pytest.raises(
        ImportError,
        match="The 'nonexistent_module' module is required to use this property.",
    ):
        _ = result.prop_unavailable

    with pytest.raises(
        TypeError, match="requires_module can only be used on methods or properties."
    ):

        @requires_module("nonexistent_module")
        class InvalidUsage:
            pass
