"""Unit tests for DockerCliBackend and backend selection.

``subprocess.run`` is mocked, so these tests verify the exact
docker commands issued — guaranteeing the relocation from the old
modules is behavior-preserving — without needing a daemon.
"""

import os
import signal
import subprocess
import threading
import time
import types
from pathlib import Path
from unittest import mock

import pytest

from tutorial_sdk.backend import DockerCliBackend, docker_cli, get_backend
from tutorial_sdk.backend.base import (
    MANAGED_LABEL,
    MANAGED_LABEL_VALUE,
    ContainerHandle,
    ImagePushResult,
)
from tutorial_sdk.backend.errors import (
    ContainerStartFailed,
    CopyFailed,
    ImageBuildFailed,
    ImagePushFailed,
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


class MockProcess:
    """Stand-in for the ``docker run`` client of a foreground run."""

    def __init__(self, returncode=0):
        self._exit_code = returncode
        self.returncode = None
        self.kills = 0

    def wait(self, timeout=None):
        if self.returncode is None:
            self.returncode = self._exit_code
        return self.returncode

    def poll(self):
        return self.returncode

    def kill(self):
        self.kills += 1
        self.returncode = -9


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
        "docker",
        "build",
        "-f",
        "/proj/Dockerfile",
        "-t",
        "demo:latest",
        "--no-cache",
        "--platform",
        "linux/amd64",
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
        with pytest.raises(
            BackendUnavailableError, match="not installed or not on PATH"
        ):
            backend.build_image(Path("."), Path("Dockerfile"), "t")


def test_run_detached_precleans_and_starts():
    backend = DockerCliBackend()
    responses = [
        completed(),  # docker rm -f (pre-clean)
        completed(stdout="abcdef0123456789\n"),  # docker run -d
    ]
    with mock.patch("subprocess.run", side_effect=responses) as run:
        h = backend.run_detached("demo:latest", name="tutorial-sdk-val-demo")

    first, second = [c.args[0] for c in run.call_args_list]
    assert first == ["docker", "rm", "-f", "tutorial-sdk-val-demo"]
    assert second == [
        "docker",
        "run",
        "-d",
        "--name",
        "tutorial-sdk-val-demo",
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
        "docker",
        "inspect",
        "-f",
        "{{.State.Running}}",
        "c1",
    ]
    with mock.patch("subprocess.run", return_value=completed(stdout="false\n")):
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
        "docker",
        "exec",
        "c1",
        "jupyter",
        "lab",
        "--version",
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
    process = MockProcess()
    with mock.patch("subprocess.Popen", return_value=process) as popen:
        exit_info = backend.run_foreground("demo:latest", ports={8888: 9999})
    executed = popen.call_args.args[0]
    cidfile = executed[executed.index("--cidfile") + 1]
    assert executed == [
        "docker",
        "run",
        "--rm",
        "--cidfile",
        cidfile,
        "--label",
        f"{MANAGED_LABEL}={MANAGED_LABEL_VALUE}",
        "-p",
        "9999:8888",
        "demo:latest",
    ]
    # Foreground runs must inherit host streams: nothing is redirected.
    assert popen.call_args.kwargs == {}
    # The reported command is the canonical one, without bookkeeping.
    assert exit_info.command == (
        "docker",
        "run",
        "--rm",
        "-p",
        "9999:8888",
        "demo:latest",
    )

    process = MockProcess()
    with mock.patch("subprocess.Popen", return_value=process) as popen:
        exit_info = backend.run_foreground(
            "demo:latest", ports={8888: 8888}, interactive_shell=True
        )
    executed = popen.call_args.args[0]
    assert executed[:4] == ["docker", "run", "--rm", "--cidfile"]
    assert executed[5:] == [
        "--label",
        f"{MANAGED_LABEL}={MANAGED_LABEL_VALUE}",
        "-p",
        "8888:8888",
        "-it",
        "--entrypoint",
        "/bin/sh",
        "demo:latest",
    ]
    assert exit_info.command == (
        "docker",
        "run",
        "--rm",
        "-p",
        "8888:8888",
        "-it",
        "--entrypoint",
        "/bin/sh",
        "demo:latest",
    )


def test_copy_in_command():
    backend = DockerCliBackend()
    with mock.patch("subprocess.run", return_value=completed()) as run:
        backend.copy_in(handle(), Path("/tmp/nb.ipynb"), "/work/nb.ipynb")
    assert run.call_args.args[0] == [
        "docker",
        "cp",
        "/tmp/nb.ipynb",
        "c1:/work/nb.ipynb",
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
    with pytest.raises(
        BackendUnavailableError, match="tutorial-sdk\\[docker\\]"
    ):
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
        with pytest.raises(
            BackendUnavailableError, match="No container backend is available"
        ):
            get_backend("auto")


def test_env_var_respected_for_auto(no_sdk_backend):
    with mock.patch.dict(os.environ, {"TUTORIAL_SDK_BACKEND": "docker-cli"}):
        assert isinstance(get_backend(None), DockerCliBackend)
    with mock.patch.dict(os.environ, {"TUTORIAL_SDK_BACKEND": "docker-sdk"}):
        with pytest.raises(BackendUnavailableError):
            get_backend(None)
    with mock.patch.dict(os.environ, {"TUTORIAL_SDK_BACKEND": "bogus"}):
        with pytest.raises(ConfigError):
            get_backend(None)


def test_explicit_preference_beats_env_var():
    with mock.patch.dict(os.environ, {"TUTORIAL_SDK_BACKEND": "docker-sdk"}):
        assert isinstance(get_backend("docker-cli"), DockerCliBackend)


def test_config_used_when_no_flag_or_env(no_sdk_backend):
    assert isinstance(get_backend(fallback_name="docker-cli"), DockerCliBackend)
    with pytest.raises(BackendUnavailableError):
        get_backend(fallback_name="docker-sdk")


def test_env_var_beats_config(no_sdk_backend):
    with mock.patch.dict(os.environ, {"TUTORIAL_SDK_BACKEND": "docker-cli"}):
        assert isinstance(
            get_backend(fallback_name="docker-sdk"), DockerCliBackend
        )


def test_auto_defers_at_every_level(no_sdk_backend):
    # "auto" from the flag and the env var falls through to config.
    with mock.patch.dict(os.environ, {"TUTORIAL_SDK_BACKEND": "auto"}):
        assert isinstance(
            get_backend("auto", fallback_name="docker-cli"), DockerCliBackend
        )


def test_unknown_config_value_raises_config_error():
    with pytest.raises(ConfigError, match="'podman'"):
        get_backend(fallback_name="podman")


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


# ---------------------------------------------------------------------------
# Streaming builds and push_image
# ---------------------------------------------------------------------------


def _streaming_process(lines, returncode=0):
    process = mock.MagicMock()
    process.stdout = iter(lines)
    process.wait.return_value = returncode
    return process


def test_streaming_build_collects_lines_and_reports_failure():
    seen = []
    process = _streaming_process(["#1 load\n", "#2 ERROR\n"], returncode=1)
    with mock.patch("subprocess.Popen", return_value=process) as popen:
        with pytest.raises(ImageBuildFailed) as info:
            DockerCliBackend().build_image(
                Path("/p"), Path("/p/Dockerfile"), "t", log_callback=seen.append
            )
    assert popen.call_args.args[0] == [
        "docker",
        "build",
        "-f",
        "/p/Dockerfile",
        "-t",
        "t",
        "/p",
    ]
    assert str(info.value) == "Docker build failed"
    assert info.value.logs == ("#1 load", "#2 ERROR")
    assert seen == ["#1 load", "#2 ERROR"]


def test_push_image_commands_and_digest():
    backend = DockerCliBackend()
    responses = [
        completed(stdout="pushed\n"),
        completed(stdout="reg.io/org/demo@sha256:abc\n"),
    ]
    with mock.patch("subprocess.run", side_effect=responses) as run:
        result = backend.push_image("reg.io/org/demo:1.0")
    push, inspect = [c.args[0] for c in run.call_args_list]
    assert push == ["docker", "push", "reg.io/org/demo:1.0"]
    assert inspect == [
        "docker",
        "inspect",
        "--format",
        "{{index .RepoDigests 0}}",
        "reg.io/org/demo:1.0",
    ]
    assert result == ImagePushResult(
        image="reg.io/org/demo:1.0", digest="sha256:abc"
    )


@pytest.mark.parametrize(
    "inspect",
    [
        completed(returncode=1, stderr="No such object"),
        completed(stdout="\n"),
        completed(stdout="<no value>\n"),
    ],
    ids=["inspect-fails", "empty", "no-digest"],
)
def test_push_image_digest_unavailable(inspect):
    with mock.patch("subprocess.run", side_effect=[completed(), inspect]):
        result = DockerCliBackend().push_image("demo:1")
    assert result.digest is None


@pytest.mark.parametrize(
    ("stderr", "stdout", "expected"),
    [
        ("denied: no access\n", "", "denied: no access"),
        ("", "error on stdout\n", "error on stdout"),
        ("", "", "Docker push failed"),
    ],
)
def test_push_image_failure_message_fallbacks(stderr, stdout, expected):
    with mock.patch(
        "subprocess.run",
        return_value=completed(returncode=1, stderr=stderr, stdout=stdout),
    ) as run:
        with pytest.raises(ImagePushFailed) as info:
            DockerCliBackend().push_image("demo:1")
        assert str(info.value) == expected
    run.assert_called_once()


def test_streaming_push_forwards_lines():
    seen = []
    process = _streaming_process(["The push refers to\n", "1: digest\n"])
    responses = [completed(stdout="demo@sha256:d1\n")]
    with mock.patch("subprocess.Popen", return_value=process) as popen:
        with mock.patch("subprocess.run", side_effect=responses):
            result = DockerCliBackend().push_image(
                "demo:1", log_callback=seen.append
            )
    assert popen.call_args.args[0] == ["docker", "push", "demo:1"]
    assert seen == ["The push refers to", "1: digest"]
    assert result.digest == "sha256:d1"


def test_streaming_push_failure():
    process = _streaming_process(["denied\n"], returncode=1)
    with mock.patch("subprocess.Popen", return_value=process):
        with pytest.raises(ImagePushFailed, match="Docker push failed"):
            DockerCliBackend().push_image("demo:1", log_callback=print)


def test_push_missing_docker_binary_maps_to_unavailable():
    with mock.patch("subprocess.run", side_effect=FileNotFoundError):
        with pytest.raises(BackendUnavailableError):
            DockerCliBackend().push_image("demo:1")


# ---------------------------------------------------------------------------
# Foreground runs: interrupt handling and cleanup
# ---------------------------------------------------------------------------

CID = "a" * 64


class ScriptedProcess:
    """``docker run`` client whose life is scripted per ``wait`` call.

    ``script`` is called on every ``wait`` with the call number and may
    send signals or end the process by returning an exit code.
    Removing the container with ``docker rm -f`` ends the process with
    137, as it does for a real ``docker run``.
    """

    def __init__(self, script=None, cid=CID):
        self.script = script or (lambda n: None)
        self.cid = cid
        self.returncode = None
        self.kills = 0
        self.calls = 0
        self.cidfile = None

    def spawned(self, command):
        self.command = command
        self.cidfile = Path(command[command.index("--cidfile") + 1])
        assert not self.cidfile.exists()  # docker must create it
        if self.cid is not None:
            self.cidfile.write_text(self.cid)
        return self

    def wait(self, timeout=None):
        if self.returncode is None:
            self.calls += 1
            code = self.script(self.calls)
            if code is not None:
                self.returncode = code
        if self.returncode is not None:
            return self.returncode
        if timeout is None:
            raise AssertionError("blocking wait on a live process")
        time.sleep(min(timeout, 0.01))
        raise subprocess.TimeoutExpired("docker", timeout)

    def poll(self):
        return self.returncode

    def kill(self):
        self.kills += 1
        self.returncode = -9


def interrupt_at(*calls, exit_at=None, code=130):
    """Script sending SIGINT to this process at the given ``wait`` calls."""

    def script(n):
        if n in calls:
            os.kill(os.getpid(), signal.SIGINT)
        if exit_at is not None and n >= exit_at:
            return code
        return None

    return script


@pytest.fixture
def fast(monkeypatch):
    """Shrink the stop policy's timings."""
    monkeypatch.setattr(docker_cli, "STOP_GRACE", 0.3)
    monkeypatch.setattr(docker_cli, "_CID_TIMEOUT", 0.2)
    monkeypatch.setattr(docker_cli, "_REAP_TIMEOUT", 0.2)
    monkeypatch.setattr(docker_cli, "_POLL_INTERVAL", 0.01)


def run_scripted(process, rm_result=None, image="demo:latest"):
    """Run a foreground run against ``process``; record docker commands."""
    commands = []

    def mock_run(command, **kwargs):
        commands.append((command, kwargs))
        if command[:3] == ["docker", "rm", "-f"]:
            if process.returncode is None:
                process.returncode = 137
            return rm_result or completed()
        return completed()

    backend = DockerCliBackend()
    with (
        mock.patch("subprocess.Popen", side_effect=process.spawned),
        mock.patch("subprocess.run", side_effect=mock_run),
    ):
        result = backend.run_foreground(image)
    return result, commands


def test_tracked_command_inserts_bookkeeping_after_rm():
    command = docker_cli.foreground_command("img", {80: 8080}, True)
    tracked = docker_cli.tracked_command(command, Path("/x/cid"))
    assert tracked == [
        "docker",
        "run",
        "--rm",
        "--cidfile",
        "/x/cid",
        "--label",
        f"{MANAGED_LABEL}={MANAGED_LABEL_VALUE}",
        *command[3:],
    ]
    assert tracked[-1] == "img"
    with pytest.raises(ValueError):
        docker_cli.tracked_command(["docker", "build", "."], Path("/x"))


def test_normal_exit_returns_code_and_cleans_up(fast, capsys):
    process = ScriptedProcess(lambda n: 3 if n >= 2 else None)
    result, commands = run_scripted(process)
    assert result.returncode == 3
    assert commands == []  # --rm handles removal; no docker rm
    assert not process.cidfile.parent.exists()  # temp dir removed
    assert capsys.readouterr().err == ""


def test_each_run_gets_its_own_cidfile(fast):
    first, second = ScriptedProcess(lambda n: 0), ScriptedProcess(lambda n: 0)
    run_scripted(first)
    run_scripted(second)
    assert first.cidfile != second.cidfile


def test_interrupt_then_graceful_exit(fast, capsys):
    # Ctrl-C reaches the client; the container stops by itself.
    process = ScriptedProcess(interrupt_at(2, exit_at=4))
    with pytest.raises(KeyboardInterrupt):
        run_scripted(process)
    err = capsys.readouterr().err
    assert "Stopping container (interrupt again to force)..." in err
    assert "Warning" not in err
    assert process.kills == 0


def test_interrupt_safety_net_removes_container_quietly(fast, capsys):
    process = ScriptedProcess(interrupt_at(2, exit_at=3))
    commands = []

    def mock_run(command, **kwargs):
        commands.append((command, kwargs))
        return completed(
            returncode=1,
            stderr=f"Error response from daemon: No such container: {CID}",
        )

    backend = DockerCliBackend()
    with (
        mock.patch("subprocess.Popen", side_effect=process.spawned),
        mock.patch("subprocess.run", side_effect=mock_run),
        pytest.raises(KeyboardInterrupt),
    ):
        backend.run_foreground("demo:latest")
    # The end-of-run safety net removes by ID, in its own session so a
    # further Ctrl-C cannot kill it; "No such container" is success.
    assert commands == [
        (
            ["docker", "rm", "-f", CID],
            {
                "check": False,
                "capture_output": True,
                "text": True,
                "start_new_session": True,
                "timeout": docker_cli._RM_TIMEOUT,
            },
        )
    ]
    assert "Warning" not in capsys.readouterr().err


def test_container_ignoring_interrupt_is_removed_after_grace(fast, capsys):
    process = ScriptedProcess(interrupt_at(2))
    began = time.monotonic()
    with pytest.raises(KeyboardInterrupt):
        run_scripted(process)
    assert time.monotonic() - began >= docker_cli.STOP_GRACE
    assert process.returncode == 137  # ended by the removal
    assert process.kills == 0
    assert "Stopping container" in capsys.readouterr().err


def test_second_interrupt_removes_at_once(fast, monkeypatch):
    monkeypatch.setattr(docker_cli, "STOP_GRACE", 30.0)
    process = ScriptedProcess(interrupt_at(2, 4))
    began = time.monotonic()
    with pytest.raises(KeyboardInterrupt):
        run_scripted(process)
    assert time.monotonic() - began < 5
    assert process.returncode == 137


def test_removal_commands_target_the_cid(fast):
    process = ScriptedProcess(interrupt_at(2, 3))
    issued = []

    def mock_run(command, **kwargs):
        issued.append(command)
        if command[:3] == ["docker", "rm", "-f"] and process.returncode is None:
            process.returncode = 137
        return completed()

    with (
        mock.patch("subprocess.Popen", side_effect=process.spawned),
        mock.patch("subprocess.run", side_effect=mock_run),
        pytest.raises(KeyboardInterrupt),
    ):
        DockerCliBackend().run_foreground("demo:latest")
    assert issued == [["docker", "rm", "-f", CID]]  # removed once


def test_removal_failure_is_reported(fast, capsys):
    process = ScriptedProcess(interrupt_at(2, 3, exit_at=8))
    with pytest.raises(KeyboardInterrupt):
        run_scripted(
            process,
            rm_result=completed(returncode=1, stderr="daemon exploded"),
        )
    err = capsys.readouterr().err
    assert f"Warning: could not remove container {CID}: daemon exploded" in err


def test_unknown_cid_is_reported_and_client_reaped(fast, capsys):
    # docker run never wrote its ID (e.g. still pulling the image).
    process = ScriptedProcess(interrupt_at(2, 3), cid=None)
    with pytest.raises(KeyboardInterrupt):
        run_scripted(process)
    err = capsys.readouterr().err
    assert "could not identify the container" in err
    assert process.kills == 1  # not left running


@pytest.mark.parametrize("content", ["", "not-an-id\n", "abc", "a" * 65])
def test_invalid_cid_counts_as_unknown(tmp_path, content):
    cidfile = tmp_path / "cid"
    cidfile.write_text(content)
    assert docker_cli._read_cid(cidfile) is None
    assert docker_cli._read_cid(tmp_path / "missing") is None
    cidfile.write_text(CID + "\n")
    assert docker_cli._read_cid(cidfile) == CID


def test_client_exiting_without_cid_after_interrupt_is_quiet(fast, capsys):
    # Interrupted before any container existed: nothing to clean up.
    process = ScriptedProcess(interrupt_at(1, exit_at=2), cid=None)
    with pytest.raises(KeyboardInterrupt):
        run_scripted(process)
    assert "Warning" not in capsys.readouterr().err


def test_interrupt_goes_to_previous_handler(fast):
    seen = []
    previous = signal.signal(signal.SIGINT, lambda s, f: seen.append(s))
    try:
        process = ScriptedProcess(interrupt_at(2, exit_at=3, code=0))
        result, _ = run_scripted(process)
        handler_after = signal.getsignal(signal.SIGINT)
    finally:
        signal.signal(signal.SIGINT, previous)
    # Recorded during the run, re-delivered once afterwards.
    assert seen == [signal.SIGINT]
    assert result.returncode == 0
    assert handler_after is not None and handler_after is not previous


def test_default_handler_is_restored(fast):
    before = signal.getsignal(signal.SIGINT)
    run_scripted(ScriptedProcess(lambda n: 0))
    assert signal.getsignal(signal.SIGINT) is before


def test_ignored_sigint_stays_ignored(fast):
    previous = signal.signal(signal.SIGINT, signal.SIG_IGN)
    try:
        process = ScriptedProcess(interrupt_at(2, exit_at=3, code=0))
        result, commands = run_scripted(process)
        assert signal.getsignal(signal.SIGINT) is signal.SIG_IGN
    finally:
        signal.signal(signal.SIGINT, previous)
    assert result.returncode == 0
    assert commands == []


def test_run_from_worker_thread(fast):
    results = []

    def worker():
        results.append(run_scripted(ScriptedProcess(lambda n: 5))[0])

    thread = threading.Thread(target=worker)
    thread.start()
    thread.join(5)
    assert results[0].returncode == 5


def test_unexpected_error_kills_client(fast):
    process = ScriptedProcess()

    def broken_wait(timeout=None):
        raise RuntimeError("boom")

    process.wait = broken_wait
    final_waits = []
    process_kill = process.kill

    def kill():
        process_kill()
        process.wait = lambda timeout=None: final_waits.append(1) or -9

    process.kill = kill
    with pytest.raises(RuntimeError, match="boom"):
        run_scripted(process)
    assert process.returncode == -9
    assert final_waits == [1]


def test_missing_binary_is_unavailable():
    with (
        mock.patch("subprocess.Popen", side_effect=FileNotFoundError("docker")),
        pytest.raises(BackendUnavailableError),
    ):
        DockerCliBackend().run_foreground("demo:latest")


# ---------------------------------------------------------------------------
# Diagnostics
# ---------------------------------------------------------------------------

VERSION_JSON = (
    '{"Client":{"Version":"29.9.1","ApiVersion":"1.56"},'
    '"Server":{"Version":"29.9.1","ApiVersion":"1.56"}}\n'
)


def diag_run(version=None, context=None):
    def mock_run(command, **kwargs):
        if command[:2] == ["docker", "version"]:
            return version or completed(stdout=VERSION_JSON)
        if command[:3] == ["docker", "context", "inspect"]:
            return context or completed(
                stdout="colima\tunix:///Users/me/.colima/docker.sock\n"
            )
        raise AssertionError(command)

    return mock_run


def test_describe_reports_versions_and_context(monkeypatch):
    monkeypatch.delenv("DOCKER_HOST", raising=False)
    with (
        mock.patch("shutil.which", return_value="/usr/bin/docker"),
        mock.patch("subprocess.run", side_effect=diag_run()) as run,
    ):
        info = DockerCliBackend().describe()
    assert info.name == "docker-cli"
    assert info.client_version == "29.9.1"
    assert info.server_version == "29.9.1"
    assert info.api_version == "1.56"
    assert info.endpoint == "unix:///Users/me/.colima/docker.sock"
    assert info.endpoint_source == "context colima"
    assert info.daemon_error is None
    assert info.classic_builder is None
    assert run.call_args_list[0].args[0] == [
        "docker",
        "version",
        "--format",
        "{{json .}}",
    ]


def test_describe_prefers_docker_host(monkeypatch):
    monkeypatch.setenv("DOCKER_HOST", "tcp://10.0.0.5:2375")
    with (
        mock.patch("shutil.which", return_value="/usr/bin/docker"),
        mock.patch("subprocess.run", side_effect=diag_run()) as run,
    ):
        info = DockerCliBackend().describe()
    # DOCKER_HOST overrides any context, so none is inspected.
    assert [c.args[0][1] for c in run.call_args_list] == ["version"]
    assert info.endpoint == "tcp://10.0.0.5:2375"
    assert info.endpoint_source == "DOCKER_HOST"


def test_describe_daemon_down(monkeypatch):
    monkeypatch.delenv("DOCKER_HOST", raising=False)
    down = completed(
        returncode=1,
        stdout='{"Client":{"Version":"29.9.1"},"Server":null}\n',
        stderr="Cannot connect to the Docker daemon at unix:///var/run/docker.sock.",
    )
    with (
        mock.patch("shutil.which", return_value="/usr/bin/docker"),
        mock.patch("subprocess.run", side_effect=diag_run(version=down)),
    ):
        info = DockerCliBackend().describe()
    assert info.client_version == "29.9.1"
    assert info.server_version is None
    assert info.daemon_error.startswith("Cannot connect")


def test_describe_without_binary():
    with mock.patch("shutil.which", return_value=None):
        info = DockerCliBackend().describe()
    assert info.daemon_error == "Docker is not installed or not on PATH."
    assert info.client_version is None


def test_list_managed_filters_by_label():
    out = (
        f"{CID}\tquirky_name\tdemo:latest\trunning\t"
        "2026-10-22 14:53:16 +0000 UTC\n"
        f"{'b' * 64}\tother\tdemo:old\texited\tgarbage\n"
        "malformed line\n"
    )
    with mock.patch(
        "subprocess.run", return_value=completed(stdout=out)
    ) as run:
        found = DockerCliBackend().list_managed()
    command = run.call_args.args[0]
    assert command[:3] == ["docker", "ps", "--all"]
    assert f"label={MANAGED_LABEL}={MANAGED_LABEL_VALUE}" in command
    assert [c.id for c in found] == [CID, "b" * 64]
    assert found[0].name == "quirky_name"
    assert found[0].image == "demo:latest"
    assert found[0].state == "running"
    assert found[0].created.isoformat() == "2026-10-22T14:53:16+00:00"
    assert found[1].created is None


def test_list_managed_failure_raises():
    with mock.patch(
        "subprocess.run",
        return_value=completed(returncode=1, stderr="permission denied"),
    ):
        with pytest.raises(BackendError, match="permission denied"):
            DockerCliBackend().list_managed()


def test_removal_without_binary_is_reported(fast, capsys):
    process = ScriptedProcess(interrupt_at(2, 3, exit_at=8))

    def mock_run(command, **kwargs):
        raise FileNotFoundError("docker")

    with (
        mock.patch("subprocess.Popen", side_effect=process.spawned),
        mock.patch("subprocess.run", side_effect=mock_run),
        pytest.raises(KeyboardInterrupt),
    ):
        DockerCliBackend().run_foreground("demo:latest")
    err = capsys.readouterr().err
    assert f"Warning: could not remove container {CID}: Docker is not" in err


def test_removal_timeout_is_reported(fast, capsys):
    process = ScriptedProcess(interrupt_at(2, 3, exit_at=8))

    def mock_run(command, **kwargs):
        raise subprocess.TimeoutExpired(command, kwargs.get("timeout", 10.0))

    with (
        mock.patch("subprocess.Popen", side_effect=process.spawned),
        mock.patch("subprocess.run", side_effect=mock_run),
        pytest.raises(KeyboardInterrupt),
    ):
        DockerCliBackend().run_foreground("demo:latest")
    err = capsys.readouterr().err
    assert f"Warning: could not remove container {CID}: Command" in err


def test_own_session_uses_process_group_on_windows(monkeypatch):
    monkeypatch.setattr(docker_cli.sys, "platform", "win32")
    with mock.patch("subprocess.run", return_value=completed()) as run:
        DockerCliBackend()._run(["docker", "rm", "-f", CID], own_session=True)
    kwargs = run.call_args.kwargs
    assert "start_new_session" not in kwargs
    assert kwargs["creationflags"] == getattr(
        subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200
    )


def test_describe_tolerates_garbage_and_vanishing_binary(monkeypatch):
    monkeypatch.delenv("DOCKER_HOST", raising=False)
    garbage = completed(returncode=0, stdout="not json\n")
    with (
        mock.patch("shutil.which", return_value="/usr/bin/docker"),
        mock.patch("subprocess.run", side_effect=diag_run(version=garbage)),
    ):
        info = DockerCliBackend().describe()
    assert info.client_version is None
    assert info.daemon_error == "no response from daemon"

    with (
        mock.patch("shutil.which", return_value="/usr/bin/docker"),
        mock.patch("subprocess.run", side_effect=FileNotFoundError("docker")),
    ):
        info = DockerCliBackend().describe()
    assert info.daemon_error == "Docker is not installed or not on PATH."
