from unittest.mock import patch, MagicMock

from tutorial_sdk.spec import TutorialSpec
from tutorial_sdk.builder.local import LocalBuilder


@patch(
    "tutorial_sdk.builder.local.DockerBuilder.build",
)
def test_local_builder_prepare_writes_dockerfile(
    mock_build,
    tmp_path,
):
    spec = TutorialSpec(name="local-test")
    builder = LocalBuilder(spec, root=tmp_path)
    builder.prepare()

    dockerfile = tmp_path / "Dockerfile"
    assert dockerfile.exists()
    content = dockerfile.read_text()
    assert "FROM python:3.12-slim" in content


@patch(
    "tutorial_sdk.builder.local.DockerBuilder.build",
)
def test_local_builder_prepare_writes_manifest(
    mock_build,
    tmp_path,
):
    import json

    from tutorial_sdk.project import TutorialProject

    spec = TutorialSpec(
        name="manifest-prep",
        build={"export_manifest": True},
    )
    builder = LocalBuilder(spec, root=tmp_path)
    builder.prepare()

    manifest = tmp_path / "tutorial-manifest.json"
    assert manifest.exists()
    manifest_payload = json.loads(manifest.read_text())
    inspect_payload = json.loads(TutorialProject(spec, tmp_path).inspect())
    assert manifest_payload["image"] == "manifest-prep:latest"
    assert manifest_payload["image"] == inspect_payload["image"]


def test_local_builder_build_forwards_image_to_manifest(tmp_path):
    import json

    from .mock_backend import MockBackend

    spec = TutorialSpec(name="manifest-override")
    builder = LocalBuilder(spec, root=tmp_path)
    builder.build(image="reg.io/override:v9", backend=MockBackend())

    manifest_payload = json.loads(
        (tmp_path / "tutorial-manifest.json").read_text()
    )
    assert manifest_payload["image"] == "reg.io/override:v9"


@patch(
    "tutorial_sdk.builder.local.DockerBuilder.build",
)
def test_local_builder_prepare_skips_manifest(
    mock_build,
    tmp_path,
):
    spec = TutorialSpec(
        name="no-manifest",
        build={"export_manifest": False},
    )
    builder = LocalBuilder(spec, root=tmp_path)
    builder.prepare()

    manifest = tmp_path / "tutorial-manifest.json"
    assert not manifest.exists()


@patch(
    "tutorial_sdk.builder.local.DockerBuilder.build",
)
def test_local_builder_build_calls_docker(
    mock_build,
    tmp_path,
):
    mock_build.return_value = MagicMock(
        image="local-test:latest",
        dockerfile=tmp_path / "Dockerfile",
        status="success",
    )
    spec = TutorialSpec(name="local-test")
    builder = LocalBuilder(spec, root=tmp_path)
    builder.build(image="my-tag:v1")

    # prepare should have been called (Dockerfile exists)
    assert (tmp_path / "Dockerfile").exists()
    mock_build.assert_called_once_with(
        image="my-tag:v1",
        no_cache=None,
        platform=None,
    )
