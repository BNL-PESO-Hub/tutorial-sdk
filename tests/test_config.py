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
    assert spec.build.base_image == "python:3.11-slim"
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
