import json

from tutorial_sdk.spec import TutorialSpec
from tutorial_sdk.generator.manifest import ManifestGenerator


def test_manifest_render_dict():
    spec = TutorialSpec(name="manifest-test")
    gen = ManifestGenerator(spec)
    result = gen.render_dict()
    assert result["name"] == "manifest-test"
    assert "runtime" in result
    assert "content" in result
    assert "dependencies" in result


def test_manifest_render_dict_with_image():
    spec = TutorialSpec(name="img-test")
    gen = ManifestGenerator(spec)
    result = gen.render_dict(image="my-app:v2")
    assert result["image"] == "my-app:v2"


def test_manifest_render_json():
    spec = TutorialSpec(name="json-test")
    gen = ManifestGenerator(spec)
    rendered = gen.render_json()
    payload = json.loads(rendered)
    assert payload["name"] == "json-test"


def test_manifest_write(tmp_path):
    spec = TutorialSpec(name="write-test")
    gen = ManifestGenerator(spec)
    out = tmp_path / "tutorial-manifest.json"
    gen.write(out)
    assert out.exists()
    payload = json.loads(out.read_text())
    assert payload["name"] == "write-test"
