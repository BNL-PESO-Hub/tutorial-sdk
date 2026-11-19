import pytest
from tutorial_sdk.spec import (
    TutorialSpec,
    RuntimeSpec,
    DependencySpec,
    ContentSpec,
    EntrypointSpec,
    BuildSpec,
)
from tutorial_sdk.errors import ConfigError


def test_spec_defaults():
    spec = TutorialSpec(name="test-tutorial")
    assert spec.name == "test-tutorial"
    assert spec.version == "0.1.0"
    assert spec.display_title == "test-tutorial"
    assert spec.build.base_image == "python:3.12-slim"
    assert isinstance(spec.runtime, RuntimeSpec)
    assert isinstance(spec.dependencies, DependencySpec)
    assert isinstance(spec.content, ContentSpec)
    assert isinstance(spec.entrypoint, EntrypointSpec)


def test_spec_invalid_name():
    with pytest.raises(ValueError):
        TutorialSpec(name="")
    with pytest.raises(ValueError):
        TutorialSpec(name="   ")


def test_spec_load_and_write(tmp_path):
    yaml_path = tmp_path / "tutorial.yml"
    spec = TutorialSpec(
        name="hello-world",
        title="Hello World Tutorial",
        description="A great tutorial",
        build=BuildSpec(base_image="python:3.10-slim"),
    )
    spec.write(yaml_path)

    assert yaml_path.exists()

    loaded = TutorialSpec.load(yaml_path)
    assert loaded.name == "hello-world"
    assert loaded.display_title == "Hello World Tutorial"
    assert loaded.description == "A great tutorial"
    assert loaded.build.base_image == "python:3.10-slim"


def test_spec_load_missing():
    with pytest.raises(ConfigError) as exc_info:
        TutorialSpec.load("non_existent_file_xyz.yml")
    assert "Unable to read" in str(exc_info.value)


def test_to_manifest_dict():
    spec = TutorialSpec(name="demo")
    manifest = spec.to_manifest_dict(image="demo:v1")
    assert manifest["name"] == "demo"
    assert manifest["image"] == "demo:v1"
    assert "runtime" in manifest
    assert "content" in manifest
    assert "dependencies" in manifest


# ---------------------------------------------------------------------------
# build.backend
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("value", ["auto", "docker-sdk", "docker-cli"])
def test_build_backend_values(tmp_path, value):
    path = tmp_path / "tutorial.yml"
    path.write_text(f"name: demo\nbuild:\n  backend: {value}\n")
    spec = TutorialSpec.load(path)
    assert spec.build.backend == value
    spec.write(path)
    assert f"backend: {value}" in path.read_text()


def test_build_backend_unknown_is_config_error(tmp_path):
    path = tmp_path / "tutorial.yml"
    path.write_text("name: demo\nbuild:\n  backend: podman\n")
    with pytest.raises(ConfigError, match="build.backend"):
        TutorialSpec.load(path)


def test_build_backend_unset_is_not_written(tmp_path):
    # Files written by this version stay readable by earlier ones,
    # whose BuildSpec rejects unknown keys.
    spec = TutorialSpec(name="demo")
    assert spec.build.backend is None
    path = tmp_path / "tutorial.yml"
    spec.write(path)
    assert "backend" not in path.read_text()


def test_build_backend_choices_match_selection():
    from typing import get_args

    from tutorial_sdk.backend import BACKEND_CHOICES
    from tutorial_sdk.spec import BackendName

    assert set(get_args(BackendName)) == set(BACKEND_CHOICES)
    assert BuildSpec().backend is None
