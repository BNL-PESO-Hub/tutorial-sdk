"""Unit tests for DockerCliBackend and backend selection.

``subprocess.run`` is mocked, so these tests verify the exact
docker commands issued — guaranteeing the relocation from the old
modules is behavior-preserving — without needing a daemon.
"""

import os
import subprocess
import types
from pathlib import Path
from unittest import mock

import pytest

from tutorial_sdk.backend import DockerCliBackend, get_backend
from tutorial_sdk.backend.base import ContainerHandle
from tutorial_sdk.backend.errors import (
    ContainerStartFailed,
    CopyFailed,
    ImageBuildFailed,
)
from tutorial_sdk.errors import (
    BackendError,
    BackendUnavailableError,
    ConfigError,
    TutorialSdkError,
)


def completed(returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(
        args=[], returncode=returncode, stdout=stdout, stderr=stderr
    )


def handle(name="c1"):
    return ContainerHandle(id="cid", name=name, backend="docker-cli")


# ---------------------------------------------------------------------------
# DockerCliBackend command construction
# ---------------------------------------------------------------------------


def test_build_image_command_and_flags():
    backend = DockerCliBackend()
    with mock.patch("subprocess.run", return_value=completed()) as run:
        backend.build_image(
            context=Path("/proj"),
            dockerfile=Path("/proj/Dockerfile"),
            tag="demo:latest",
            no_cache=True,
            platform="linux/amd64",
        )
    run.assert_called_once()
    assert run.call_args.args[0] == [
        "docker", "build",
        "-f", "/proj/Dockerfile",
        "-t", "demo:latest",
        "--no-cache",
        "--platform", "linux/amd64",
        "/proj",
    ]


def test_build_image_failure_uses_stderr_then_fallback():
    backend = DockerCliBackend()
    with mock.patch(
        "subprocess.run",
        return_value=completed(returncode=1, stderr="ERROR: boom"),
    ):
        with pytest.raises(ImageBuildFailed, match="ERROR: boom"):
            backend.build_image(Path("."), Path("Dockerfile"), "t")

    with mock.patch(
        "subprocess.run",
        return_value=completed(returncode=1, stderr=""),
    ):
        with pytest.raises(ImageBuildFailed, match="Docker build failed"):
            backend.build_image(Path("."), Path("Dockerfile"), "t")


def test_missing_docker_binary_maps_to_unavailable():
    backend = DockerCliBackend()
    with mock.patch("subprocess.run", side_effect=FileNotFoundError):
        with pytest.raises(BackendUnavailableError,
                           match="not installed or not on PATH"):
            backend.build_image(Path("."), Path("Dockerfile"), "t")


def test_run_detached_precleans_and_starts():
    backend = DockerCliBackend()
    responses = [
        completed(),                          # docker rm -f (pre-clean)
        completed(stdout="abcdef0123456789\n"),  # docker run -d
    ]
    with mock.patch("subprocess.run", side_effect=responses) as run:
        h = backend.run_detached(
            "demo:latest", name="tutorial-sdk-val-demo"
        )

    first, second = [c.args[0] for c in run.call_args_list]
    assert first == ["docker", "rm", "-f", "tutorial-sdk-val-demo"]
    assert second == [
        "docker", "run", "-d",
        "--name", "tutorial-sdk-val-demo",
        "demo:latest",
    ]
    assert h.id == "abcdef0123456789"
    assert h.name == "tutorial-sdk-val-demo"


def test_run_detached_failure_raises_with_stderr():
    backend = DockerCliBackend()
    responses = [
        completed(),
        completed(returncode=125, stderr="no such image"),
    ]
    with mock.patch("subprocess.run", side_effect=responses):
        with pytest.raises(ContainerStartFailed, match="no such image"):
            backend.run_detached("missing:latest", name="x")


def test_is_running_parses_inspect():
    backend = DockerCliBackend()
    with mock.patch(
        "subprocess.run", return_value=completed(stdout="true\n")
    ) as run:
        assert backend.is_running(handle()) is True
    assert run.call_args.args[0] == [
        "docker", "inspect", "-f", "{{.State.Running}}", "c1",
    ]
    with mock.patch(
        "subprocess.run", return_value=completed(stdout="false\n")
    ):
        assert backend.is_running(handle()) is False


def test_wait_running_exits_early_when_stopped():
    backend = DockerCliBackend()
    with mock.patch.object(backend, "is_running", return_value=False):
        assert backend.wait_running(handle(), timeout=5.0) is False


def test_logs_prefers_stderr():
    backend = DockerCliBackend()
    with mock.patch(
        "subprocess.run",
        return_value=completed(stdout="out", stderr="err"),
    ):
        assert backend.logs(handle()) == "err"
    with mock.patch(
        "subprocess.run",
        return_value=completed(stdout="out", stderr=""),
    ):
        assert backend.logs(handle()) == "out"


def test_exec_output_selection():
    backend = DockerCliBackend()
    with mock.patch(
        "subprocess.run",
        return_value=completed(stdout="4.2.0\n"),
    ) as run:
        res = backend.exec(handle(), ["jupyter", "lab", "--version"])
    assert run.call_args.args[0] == [
        "docker", "exec", "c1", "jupyter", "lab", "--version",
    ]
    assert (res.exit_code, res.output) == (0, "4.2.0")

    with mock.patch(
        "subprocess.run",
        return_value=completed(returncode=127, stderr="not found"),
    ):
        res = backend.exec(handle(), ["jupyter"])
    assert (res.exit_code, res.output) == (127, "not found")


def test_remove_forces_by_default():
    backend = DockerCliBackend()
    with mock.patch("subprocess.run", return_value=completed()) as run:
        backend.remove(handle())
    assert run.call_args.args[0] == ["docker", "rm", "-f", "c1"]


def test_run_foreground_command_shape():
    backend = DockerCliBackend()
    with mock.patch(
        "subprocess.run", return_value=completed(returncode=0)
    ) as run:
        exit_info = backend.run_foreground(
            "demo:latest", ports={8888: 9999}
        )
    assert run.call_args.args[0] == [
        "docker", "run", "--rm", "-p", "9999:8888", "demo:latest",
    ]
    # Foreground runs must inherit host streams: capture_output is False.
    assert run.call_args.kwargs.get("capture_output", False) is False
    assert exit_info.command == (
        "docker", "run", "--rm", "-p", "9999:8888", "demo:latest",
    )

    with mock.patch(
        "subprocess.run", return_value=completed(returncode=0)
    ) as run:
        backend.run_foreground("demo:latest", ports={8888: 8888},
                               interactive_shell=True)
    assert run.call_args.args[0] == [
        "docker", "run", "--rm", "-p", "8888:8888",
        "-it", "--entrypoint", "/bin/sh", "demo:latest",
    ]


def test_copy_in_command():
    backend = DockerCliBackend()
    with mock.patch("subprocess.run", return_value=completed()) as run:
        backend.copy_in(handle(), Path("/tmp/nb.ipynb"), "/work/nb.ipynb")
    assert run.call_args.args[0] == [
        "docker", "cp", "/tmp/nb.ipynb", "c1:/work/nb.ipynb",
    ]


def test_copy_in_failure_raises_copy_failed():
    backend = DockerCliBackend()
    with mock.patch(
        "subprocess.run",
        return_value=completed(returncode=1, stderr="no such path"),
    ):
        with pytest.raises(CopyFailed, match="no such path"):
            backend.copy_in(handle(), Path("/x"), "/y")


def test_backend_errors_are_sdk_errors():
    assert issubclass(BackendError, TutorialSdkError)
    for exc in (
        BackendUnavailableError,
        ImageBuildFailed,
        ContainerStartFailed,
        CopyFailed,
    ):
        assert issubclass(exc, BackendError)


# ---------------------------------------------------------------------------
# get_backend selection
# ---------------------------------------------------------------------------


def test_explicit_docker_cli():
    assert isinstance(get_backend("docker-cli"), DockerCliBackend)


def test_unknown_preference_raises_config_error():
    with pytest.raises(ConfigError, match="Unknown container backend"):
        get_backend("podman")


def test_docker_sdk_unavailable_when_module_absent():
    # The backend module itself is absent: point to docker-cli, not
    # to an extra that cannot help.
    with mock.patch.dict(
        "sys.modules", {"tutorial_sdk.backend.docker_sdk": None}
    ):
        with pytest.raises(
            BackendUnavailableError,
            match="The 'docker-sdk' backend is not available",
        ) as ei:
            get_backend("docker-sdk")
    assert "tutorial-sdk[docker]" not in str(ei.value)
    assert "use 'docker-cli' instead" in str(ei.value)


def _stub_sdk_module(init_error):
    """A docker_sdk stand-in whose backend fails on construction."""
    module = types.ModuleType("tutorial_sdk.backend.docker_sdk")

    class DockerSdkBackend:
        name = "docker-sdk"

        def __init__(self):
            raise init_error

    module.DockerSdkBackend = DockerSdkBackend
    return module


@pytest.fixture
def sdk_without_docker_package():
    """Backend module present, but docker-py package not installed."""
    err = ModuleNotFoundError("No module named 'docker'", name="docker")
    with mock.patch.dict(
        "sys.modules",
        {"tutorial_sdk.backend.docker_sdk": _stub_sdk_module(err)},
    ):
        yield


def test_lazy_import_failure_is_unavailable(sdk_without_docker_package):
    with pytest.raises(BackendUnavailableError,
                       match="tutorial-sdk\\[docker\\]"):
        get_backend("docker-sdk")


def test_auto_falls_back_when_sdk_ctor_import_fails(
    sdk_without_docker_package,
):
    with mock.patch("shutil.which", return_value="/usr/bin/docker"):
        assert isinstance(get_backend("auto"), DockerCliBackend)


@pytest.fixture
def no_sdk_backend():
    """Simulate docker-py backend module being absent."""
    with mock.patch.dict(
        "sys.modules", {"tutorial_sdk.backend.docker_sdk": None}
    ):
        yield


def test_auto_falls_back_to_cli_when_binary_present(no_sdk_backend):
    with mock.patch("shutil.which", return_value="/usr/bin/docker"):
        assert isinstance(get_backend("auto"), DockerCliBackend)
        assert isinstance(get_backend(None), DockerCliBackend)


def test_auto_raises_when_nothing_available(no_sdk_backend):
    with mock.patch("shutil.which", return_value=None):
        with pytest.raises(BackendUnavailableError,
                           match="No container backend is available"):
            get_backend("auto")


def test_env_var_respected_for_auto(no_sdk_backend):
    with mock.patch.dict(
        os.environ, {"TUTORIAL_SDK_BACKEND": "docker-cli"}
    ):
        assert isinstance(get_backend(None), DockerCliBackend)
    with mock.patch.dict(
        os.environ, {"TUTORIAL_SDK_BACKEND": "docker-sdk"}
    ):
        with pytest.raises(BackendUnavailableError):
            get_backend(None)
    with mock.patch.dict(
        os.environ, {"TUTORIAL_SDK_BACKEND": "bogus"}
    ):
        with pytest.raises(ConfigError):
            get_backend(None)


def test_explicit_preference_beats_env_var():
    with mock.patch.dict(
        os.environ, {"TUTORIAL_SDK_BACKEND": "docker-sdk"}
    ):
        assert isinstance(
            get_backend("docker-cli"), DockerCliBackend
        )


def test_config_used_when_no_flag_or_env(no_sdk_backend):
    assert isinstance(get_backend(config="docker-cli"), DockerCliBackend)
    with pytest.raises(BackendUnavailableError):
        get_backend(config="docker-sdk")


def test_env_var_beats_config(no_sdk_backend):
    with mock.patch.dict(
        os.environ, {"TUTORIAL_SDK_BACKEND": "docker-cli"}
    ):
        assert isinstance(
            get_backend(config="docker-sdk"), DockerCliBackend
        )


def test_auto_defers_at_every_level(no_sdk_backend):
    # "auto" from the flag and the env var falls through to config.
    with mock.patch.dict(os.environ, {"TUTORIAL_SDK_BACKEND": "auto"}):
        assert isinstance(
            get_backend("auto", config="docker-cli"), DockerCliBackend
        )


def test_unknown_config_value_raises_config_error():
    with pytest.raises(ConfigError, match="'podman'"):
        get_backend(config="podman")


def test_errors_reexported_from_backend_package():
    import tutorial_sdk.backend as backend_pkg

    assert backend_pkg.BackendError is BackendError
    assert backend_pkg.BackendUnavailableError is BackendUnavailableError


def test_streaming_build_kills_process_when_callback_fails():
    process = mock.MagicMock()
    process.stdout = iter(["Step 1/2\n", "Step 2/2\n"])

    def callback(line):
        raise RuntimeError("sink closed")

    with mock.patch("subprocess.Popen", return_value=process):
        with pytest.raises(RuntimeError, match="sink closed"):
            DockerCliBackend().build_image(
                Path("."), Path("Dockerfile"), "t", log_callback=callback
            )
    process.kill.assert_called_once()
    process.wait.assert_called()


def test_stream_logs_yields_lines_and_terminates():
    backend = DockerCliBackend()
    process = mock.MagicMock()
    process.stdout = iter(["log line 1\n", "log line 2\n"])
    with mock.patch("subprocess.Popen", return_value=process) as popen:
        lines = list(backend.stream_logs(handle("c1")))
    popen.assert_called_once_with(
        ["docker", "logs", "-f", "c1"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    assert lines == ["log line 1", "log line 2"]
    process.terminate.assert_called_once()
    process.wait.assert_called_once()


def test_popen_missing_docker_binary_maps_to_unavailable():
    backend = DockerCliBackend()
    with mock.patch("subprocess.Popen", side_effect=FileNotFoundError):
        with pytest.raises(
            BackendUnavailableError, match="not installed or not on PATH"
        ):
            list(backend.stream_logs(handle("c1")))
