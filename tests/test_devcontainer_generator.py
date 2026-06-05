import json

from tutorial_sdk.spec import TutorialSpec
from tutorial_sdk.generator.devcontainer import (
    DevcontainerGenerator,
)


def test_devcontainer_render_dict():
    spec = TutorialSpec(name="dc-test", title="DC Test")
    gen = DevcontainerGenerator(spec)
    result = gen.render_dict()
    assert result["name"] == "DC Test"
    assert result["workspaceFolder"] == "/workspace"
    assert 8888 in result["forwardPorts"]
    assert result["build"]["context"] == ".."


def test_devcontainer_render_dict_custom_port():
    spec = TutorialSpec(
        name="port-test",
        runtime={"expose_port": 3000},
    )
    gen = DevcontainerGenerator(spec)
    result = gen.render_dict()
    assert 3000 in result["forwardPorts"]


def test_devcontainer_render_json():
    spec = TutorialSpec(name="json-dc")
    gen = DevcontainerGenerator(spec)
    rendered = gen.render_json()
    payload = json.loads(rendered)
    assert payload["name"] == "json-dc"
    assert payload["workspaceFolder"] == "/workspace"
