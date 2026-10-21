"""Unit tests for backend consumers, using MockBackend.

These tests require no Docker daemon: they verify that the
builder, container validator, and runtime launcher orchestrate
the ``ContainerBackend`` contract correctly and preserve their
historical public behavior (messages, check names, cleanup).
"""

from pathlib import Path
from types import SimpleNamespace

import pytest

from tutorial_sdk.backend import get_backend
from tutorial_sdk.backend.base import ExecResult
from tutorial_sdk.builder.docker import BuildResult, DockerBuilder
from tutorial_sdk.errors import BuildError
from tutorial_sdk.project import TutorialProject
from tutorial_sdk.runtime.launcher import RuntimeLauncher
from tutorial_sdk.spec import TutorialSpec
from tutorial_sdk.validator.container import ContainerValidator

from .mock_backend import MockBackend, make_spec


# ---------------------------------------------------------------------------
# DockerBuilder
# ---------------------------------------------------------------------------


def test_build_defaults_to_name_latest_tag():
    backend = MockBackend()
    spec = make_spec(name="demo", image=None)
    result = DockerBuilder(spec, root="/proj", backend=backend).build()

    assert isinstance(result, BuildResult)
    assert result.image == "demo:latest"
    assert result.dockerfile == Path("/proj/Dockerfile")
    assert result.digest == "sha256:abc"
    assert result.status == "success"

    call = backend.calls[0]
    assert call[0] == "build_image"
    assert call[1] == Path("/proj")  # context
    assert call[2] == Path("/proj/Dockerfile")  # dockerfile
    assert call[3] == "demo:latest"  # tag


def test_build_uses_spec_image_and_explicit_override():
    backend = MockBackend()
    spec = make_spec(image="ghcr.io/org/demo:1.0")
    builder = DockerBuilder(spec, backend=backend)

    assert builder.build().image == "ghcr.io/org/demo:1.0"
    assert builder.build(image="other:tag").image == "other:tag"


def test_build_cache_resolution():
    spec_cached = make_spec(cache=True)
    spec_uncached = make_spec(cache=False)

    backend = MockBackend()
    DockerBuilder(spec_cached, backend=backend).build()
    assert backend.calls[-1][4] is False  # no_cache

    backend = MockBackend()
    DockerBuilder(spec_uncached, backend=backend).build()
    assert backend.calls[-1][4] is True

    # Explicit argument overrides the spec in both directions.
    backend = MockBackend()
    DockerBuilder(spec_cached, backend=backend).build(no_cache=True)
    assert backend.calls[-1][4] is True

    backend = MockBackend()
    DockerBuilder(spec_uncached, backend=backend).build(no_cache=False)
    assert backend.calls[-1][4] is False


def test_build_passes_platform():
    backend = MockBackend()
    DockerBuilder(make_spec(), backend=backend).build(platform="linux/amd64")
    assert backend.calls[-1][5] == "linux/amd64"


def test_build_failure_raises_public_build_error():
    backend = MockBackend(build_error="step 3/7 failed")
    with pytest.raises(BuildError, match="step 3/7 failed"):
        DockerBuilder(make_spec(), backend=backend).build()


# ---------------------------------------------------------------------------
# ContainerValidator
# ---------------------------------------------------------------------------


def test_validate_happy_path():
    backend = MockBackend(exec_result=ExecResult(0, "4.2.0"))
    report = ContainerValidator(
        make_spec(name="demo"), backend=backend
    ).validate()

    assert report.passed is True
    assert [c.name for c in report.checks] == [
        "container.start",
        "container.jupyterlab",
    ]
    assert report.checks[0].message == "Container started successfully."
    assert report.checks[1].message == (
        "JupyterLab is available (version 4.2.0)."
    )
    assert report.errors == []

    # Stale-name pre-clean is the backend's duty; the validator
    # must start, watch, exec, then always remove.
    assert backend.names() == [
        "run_detached",
        "wait_running",
        "exec",
        "remove",
    ]
    start = backend.calls[0]
    assert start[1] == "demo:latest"
    assert start[2] == "tutorial-sdk-val-demo"


def test_validate_start_failure_reports_without_cleanup():
    backend = MockBackend(start_error="no such image")
    report = ContainerValidator(make_spec(), backend=backend).validate()

    assert report.passed is False
    assert report.checks[0].name == "container.start"
    assert report.checks[0].passed is False
    assert "Failed to start container: no such image" in (
        report.checks[0].message
    )
    assert report.errors == [report.checks[0].message]
    assert "remove" not in backend.names()


def test_validate_container_dies_immediately_includes_logs():
    backend = MockBackend(stays_running=False, logs_text="kaboom")
    report = ContainerValidator(make_spec(), backend=backend).validate()

    assert report.passed is False
    assert report.checks[0].name == "container.start"
    assert "Container stopped immediately. Logs:\nkaboom" in (
        report.checks[0].message
    )
    # Cleanup must still happen.
    assert backend.names()[-1] == "remove"


def test_validate_missing_jupyterlab():
    backend = MockBackend(
        exec_result=ExecResult(127, "exec: jupyter: not found")
    )
    report = ContainerValidator(make_spec(), backend=backend).validate()

    assert report.passed is False
    assert report.checks[0].passed is True
    assert report.checks[1].name == "container.jupyterlab"
    assert report.checks[1].passed is False
    assert "JupyterLab command not available in container: " in (
        report.checks[1].message
    )
    assert backend.names()[-1] == "remove"


def test_validate_cleans_up_when_exec_raises():
    class ExplodingBackend(MockBackend):
        def exec(self, container, command):
            raise RuntimeError("daemon hiccup")

    backend = ExplodingBackend()
    with pytest.raises(RuntimeError):
        ContainerValidator(make_spec(), backend=backend).validate()
    assert backend.names()[-1] == "remove"


def test_validate_image_override():
    backend = MockBackend()
    ContainerValidator(make_spec(image="cfg:1"), backend=backend).validate(
        image="cli:2"
    )
    assert backend.calls[0][1] == "cli:2"


# ---------------------------------------------------------------------------
# RuntimeLauncher
# ---------------------------------------------------------------------------


def test_run_maps_ports_and_returns_result():
    backend = MockBackend(run_returncode=0)
    spec = make_spec(name="demo", expose_port=8888)
    result = RuntimeLauncher(spec, backend=backend).run(port=9999)

    assert result.image == "demo:latest"
    assert result.returncode == 0
    assert "-p" in result.command
    assert "9999:8888" in result.command

    call = backend.calls[0]
    assert call[0] == "run_foreground"
    assert call[2] == {8888: 9999}
    assert call[3] is False


def test_run_shell_mode():
    backend = MockBackend()
    RuntimeLauncher(make_spec(), backend=backend).run(shell=True)
    assert backend.calls[0][3] is True


def test_run_propagates_exit_code():
    backend = MockBackend(run_returncode=130)
    result = RuntimeLauncher(make_spec(), backend=backend).run()
    assert result.returncode == 130


# ---------------------------------------------------------------------------
# Backend selection by name (--backend / build.backend)
# ---------------------------------------------------------------------------


@pytest.fixture
def selections(monkeypatch):
    """Record ``get_backend`` calls made by the backend consumers."""
    calls = []
    selected = MockBackend()

    def mock_get_backend(backend=None, *, fallback_name=None):
        calls.append((backend, fallback_name))
        return selected

    for consumer in (
        "builder.docker",
        "validator.container",
        "runtime.launcher",
    ):
        monkeypatch.setattr(
            f"tutorial_sdk.{consumer}.get_backend", mock_get_backend
        )
    return SimpleNamespace(calls=calls, backend=selected)


@pytest.mark.parametrize("preference", [None, "auto", "docker-cli"])
def test_consumers_pass_name_and_spec_config(selections, preference):
    spec = make_spec(backend="docker-sdk")
    DockerBuilder(spec, backend=preference).build()
    ContainerValidator(spec, backend=preference).validate()
    RuntimeLauncher(spec, backend=preference).run()
    assert selections.calls == [(preference, "docker-sdk")] * 3
    assert selections.backend.names() == [
        "build_image",
        "run_detached",
        "wait_running",
        "exec",
        "remove",
        "run_foreground",
    ]


def test_backend_instances_bypass_selection(monkeypatch):
    monkeypatch.setenv("TUTORIAL_SDK_BACKEND", "docker-sdk")
    backend = MockBackend()
    DockerBuilder(make_spec(backend="docker-cli"), backend=backend).build()
    assert backend.names() == ["build_image"]


def test_get_backend_returns_instance_unchanged(monkeypatch):
    monkeypatch.setenv("TUTORIAL_SDK_BACKEND", "docker-sdk")
    instance = MockBackend()
    assert get_backend(instance, fallback_name="docker-cli") is instance


def test_unavailable_named_backend_is_a_build_error(monkeypatch):
    def missing_binary(*args, **kwargs):
        raise FileNotFoundError("docker")

    monkeypatch.setattr("subprocess.run", missing_binary)
    with pytest.raises(BuildError, match="not installed or not on PATH"):
        DockerBuilder(make_spec(), backend="docker-cli").build()


def _project(tmp_path, backend=None):
    build = {"backend": backend} if backend else {}
    spec = TutorialSpec.model_validate({"name": "demo", "build": build})
    return TutorialProject(spec, tmp_path, tmp_path / "tutorial.yml")


def test_project_methods_forward_backend(tmp_path):
    backend = MockBackend()
    project = _project(tmp_path)
    assert project.build(backend=backend).image == "demo:latest"
    report = project.validate(container=True, backend=backend)
    assert [c.name for c in report.checks][-2:] == [
        "container.start",
        "container.jupyterlab",
    ]
    assert project.run(port=9000, backend=backend).returncode == 0
    assert backend.names() == [
        "build_image",
        "run_detached",
        "wait_running",
        "exec",
        "remove",
        "run_foreground",
    ]
    # The project build generated its Dockerfile first.
    assert (tmp_path / "Dockerfile").exists()


@pytest.mark.parametrize(
    ("flag", "env", "config", "expected"),
    [
        (None, None, "docker-cli", "docker-cli"),
        (None, None, None, "docker-sdk"),  # auto prefers docker-py
        (None, "docker-sdk", "docker-cli", "docker-sdk"),
        ("docker-cli", "docker-sdk", "docker-sdk", "docker-cli"),
        ("auto", "docker-cli", "docker-sdk", "docker-cli"),
        ("auto", None, "docker-cli", "docker-cli"),
    ],
)
def test_selection_precedence_end_to_end(
    tmp_path, monkeypatch, flag, env, config, expected
):
    """``--backend`` > ``TUTORIAL_SDK_BACKEND`` > ``build.backend``."""
    import tutorial_sdk.backend as backend_pkg

    chosen = []

    class Named(MockBackend):
        def __init__(self, name):
            super().__init__()
            self.name = name

        def build_image(self, *args, **kwargs):
            chosen.append(self.name)
            return super().build_image(*args, **kwargs)

    monkeypatch.setattr(
        backend_pkg, "_load_sdk_backend", lambda: Named("docker-sdk")
    )
    monkeypatch.setattr(
        backend_pkg, "DockerCliBackend", lambda: Named("docker-cli")
    )
    if env:
        monkeypatch.setenv("TUTORIAL_SDK_BACKEND", env)
    _project(tmp_path, config).build(backend=flag)
    assert chosen == [expected]
