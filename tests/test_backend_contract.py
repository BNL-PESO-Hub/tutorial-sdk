"""Contract tests: both container backends against a real daemon.

Opt-in (``pytest -m docker``): every test runs once per backend and
asserts the same observable behavior, so ``DockerCliBackend`` and
``DockerSdkBackend`` cannot drift apart.  A backend that is not
usable here (no daemon, no ``docker`` binary, docker-py missing)
is skipped rather than failed.

The test images are built from ``busybox`` (override with
``TUTORIAL_SDK_CONTRACT_BASE_IMAGE``) and need no network once the
base image is present locally.  Push cases use the registry at
``TUTORIAL_SDK_CONTRACT_REGISTRY`` (``host:port``) when set, and
otherwise start one from ``registry:2`` (override with
``TUTORIAL_SDK_CONTRACT_REGISTRY_IMAGE``); they are skipped when no
registry can be had.
"""

from __future__ import annotations

import itertools
import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

import pytest

from tutorial_sdk.backend import DockerCliBackend, get_backend
from tutorial_sdk.backend.errors import (
    ContainerStartFailed,
    CopyFailed,
    ImageBuildFailed,
    ImagePushFailed,
)
from tutorial_sdk.errors import BackendUnavailableError
from tutorial_sdk.validator.container import ContainerValidator

from .mock_backend import make_spec

pytestmark = pytest.mark.docker

BASE_IMAGE = os.environ.get("TUTORIAL_SDK_CONTRACT_BASE_IMAGE", "busybox:1.36")
REGISTRY = os.environ.get("TUTORIAL_SDK_CONTRACT_REGISTRY")
REGISTRY_IMAGE = os.environ.get(
    "TUTORIAL_SDK_CONTRACT_REGISTRY_IMAGE", "registry:2"
)
RUN_ID = uuid.uuid4().hex[:8]

_MOCK_JUPYTER = "#!/bin/sh\\necho 4.2.0\\n"

DOCKERFILES = {
    # Long-running; ships a stand-in `jupyter` for validator parity.
    "sleeper": (
        f"FROM {BASE_IMAGE}\n"
        f"RUN printf '{_MOCK_JUPYTER}' > /bin/jupyter "
        "&& chmod +x /bin/jupyter && mkdir -p /work\n"
        'CMD ["sleep", "300"]\n'
    ),
    # Writes to both streams, then dies ~1 s after start.
    "crasher": (
        f"FROM {BASE_IMAGE}\n"
        'CMD ["sh", "-c", "echo to-stdout; echo to-stderr >&2; '
        'sleep 1; exit 3"]\n'
    ),
    # Emits a few log lines on both streams, then idles.
    "chatty": (
        f"FROM {BASE_IMAGE}\n"
        'CMD ["sh", "-c", "echo line-1; echo line-2 >&2; echo line-3; '
        'sleep 300"]\n'
    ),
    # Foreground runs: writes to both streams and exits 7 at once.
    "exiter": (
        f"FROM {BASE_IMAGE}\n"
        'CMD ["sh", "-c", "echo to-stdout; echo to-stderr >&2; exit 7"]\n'
    ),
    # Foreground runs: alive long enough to inspect, then exits 0.
    "napper": f'FROM {BASE_IMAGE}\nCMD ["sleep", "3"]\n',
    # Foreground runs: shuts down cleanly on SIGINT/SIGTERM.
    "graceful": (
        f"FROM {BASE_IMAGE}\n"
        'CMD ["sh", "-c", "trap \'echo bye; exit 0\' INT TERM; '
        'echo ready; while :; do sleep 0.2; done"]\n'
    ),
    # Foreground runs: silent for longer than a short client timeout.
    "quiet": f'FROM {BASE_IMAGE}\nCMD ["sh", "-c", "sleep 4; echo done"]\n',
}


def _daemon_reachable() -> bool:
    try:
        result = subprocess.run(
            ["docker", "info"], capture_output=True, timeout=20
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


@pytest.fixture(scope="session")
def images(tmp_path_factory) -> dict[str, str]:
    """Build the contract images once per session (via the CLI)."""
    if not _daemon_reachable():
        pytest.skip("Docker daemon not reachable via the docker CLI")
    builder = DockerCliBackend()
    tags = {}
    for key, dockerfile in DOCKERFILES.items():
        context = tmp_path_factory.mktemp(key)
        (context / "Dockerfile").write_text(dockerfile)
        tag = f"tutorial-sdk-contract-{key}:{RUN_ID}"
        builder.build_image(context, context / "Dockerfile", tag)
        tags[key] = tag
    yield tags
    subprocess.run(["docker", "rmi", "-f", *tags.values()], capture_output=True)


@pytest.fixture(params=["docker-cli", "docker-sdk"])
def backend(request, images):
    try:
        return get_backend(request.param)
    except BackendUnavailableError as exc:
        pytest.skip(f"{request.param} unavailable: {exc}")


@pytest.fixture
def started(backend):
    """Start containers through ``backend``; always clean them up."""
    handles = []

    def start(image, **kwargs):
        kwargs.setdefault("name", f"tsdk-ct-{uuid.uuid4().hex[:10]}")
        h = backend.run_detached(image, **kwargs)
        handles.append(h)
        return h

    yield start
    for h in handles:
        backend.remove(h, force=True)


def _inspect(name: str) -> dict | None:
    result = subprocess.run(
        ["docker", "inspect", name], capture_output=True, text=True
    )
    if result.returncode != 0:
        return None
    return json.loads(result.stdout)[0]


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


def test_build_image_returns_tag(backend, tmp_path):
    (tmp_path / "Dockerfile").write_text(f"FROM {BASE_IMAGE}\n")
    tag = f"tutorial-sdk-contract-build:{RUN_ID}-{backend.name}"
    try:
        result = backend.build_image(tmp_path, tmp_path / "Dockerfile", tag)
        assert result.image == tag
        assert _inspect(tag) is not None
    finally:
        subprocess.run(["docker", "rmi", "-f", tag], capture_output=True)


def _all_containers() -> list[str]:
    result = subprocess.run(
        ["docker", "ps", "-aq", "--no-trunc"], capture_output=True, text=True
    )
    return result.stdout.split()


def _rmi(*tags: str) -> None:
    subprocess.run(["docker", "rmi", "-f", *tags], capture_output=True)


def test_build_image_reports_image_id(backend, tmp_path):
    (tmp_path / "Dockerfile").write_text(
        f"FROM {BASE_IMAGE}\nLABEL tsdk.case=id-{RUN_ID}\n"
    )
    tag = f"tutorial-sdk-contract-build-id:{RUN_ID}-{backend.name}"
    try:
        result = backend.build_image(tmp_path, tmp_path / "Dockerfile", tag)
        if backend.name == "docker-sdk":
            assert result.image_id == _inspect(tag)["Id"]
        else:
            assert result.image_id is None
    finally:
        _rmi(tag)


def test_build_streams_output_to_callback(backend, tmp_path):
    marker = f"tsdk-out-{RUN_ID}-{backend.name}"
    (tmp_path / "Dockerfile").write_text(
        f"FROM {BASE_IMAGE}\nRUN echo {marker}\n"
    )
    tag = f"tutorial-sdk-contract-build-log:{RUN_ID}-{backend.name}"
    seen: list[str] = []
    try:
        result = backend.build_image(
            tmp_path, tmp_path / "Dockerfile", tag, log_callback=seen.append
        )
        assert any(marker in line for line in seen)
        assert result.logs == tuple(seen)
    finally:
        _rmi(tag)


@pytest.mark.parametrize(
    "streamed", [False, True], ids=["captured", "streamed"]
)
def test_build_failure_carries_output(backend, tmp_path, streamed):
    marker = f"tsdk-fail-{RUN_ID}"
    (tmp_path / "Dockerfile").write_text(
        f"FROM {BASE_IMAGE}\nRUN echo {marker} && exit 3\n"
    )
    seen: list[str] = []
    before = set(_all_containers())
    with pytest.raises(ImageBuildFailed) as info:
        backend.build_image(
            tmp_path,
            tmp_path / "Dockerfile",
            f"tutorial-sdk-contract-build-fail:{RUN_ID}",
            no_cache=True,
            log_callback=seen.append if streamed else None,
        )
    # No intermediate container is left behind.
    assert set(_all_containers()) <= before
    if streamed:
        assert any(marker in line for line in seen)
        assert info.value.logs == tuple(seen)
    else:
        # The output is in the message (and, where the backend can
        # separate it, also in ``logs``).
        assert marker in str(info.value)


def test_build_with_dockerfile_outside_context(backend, tmp_path):
    context = tmp_path / "context"
    context.mkdir()
    (context / "payload.txt").write_text("payload")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "Dockerfile.custom").write_text(
        f"FROM {BASE_IMAGE}\nCOPY payload.txt /payload.txt\n"
        "RUN test -f /payload.txt\n"
    )
    tag = f"tutorial-sdk-contract-build-df:{RUN_ID}-{backend.name}"
    try:
        backend.build_image(context, elsewhere / "Dockerfile.custom", tag)
        assert _inspect(tag) is not None
    finally:
        _rmi(tag)


def test_build_relative_paths_resolve_from_cwd(backend, tmp_path, monkeypatch):
    project = tmp_path / "proj"
    project.mkdir()
    (project / "Dockerfile").write_text(f"FROM {BASE_IMAGE}\n")
    monkeypatch.chdir(tmp_path)
    tag = f"tutorial-sdk-contract-build-rel:{RUN_ID}-{backend.name}"
    try:
        backend.build_image(Path("proj"), Path("proj/Dockerfile"), tag)
        assert _inspect(tag) is not None
    finally:
        _rmi(tag)


def test_build_honours_dockerignore(backend, tmp_path):
    (tmp_path / "kept.txt").write_text("kept")
    (tmp_path / "ignored.txt").write_text("ignored")
    (tmp_path / ".dockerignore").write_text("ignored.txt\n")
    (tmp_path / "Dockerfile").write_text(
        f"FROM {BASE_IMAGE}\nCOPY . /ctx\n"
        "RUN test -f /ctx/kept.txt && test ! -e /ctx/ignored.txt\n"
    )
    tag = f"tutorial-sdk-contract-build-ignore:{RUN_ID}-{backend.name}"
    try:
        backend.build_image(tmp_path, tmp_path / "Dockerfile", tag)
    finally:
        _rmi(tag)


def test_build_buildkit_only_dockerfile(backend, tmp_path):
    # The SDK backend routes this through the Docker CLI.
    (tmp_path / "Dockerfile").write_text(
        f"FROM {BASE_IMAGE}\n"
        "RUN --mount=type=tmpfs,target=/scratch echo x > /scratch/x\n"
    )
    tag = f"tutorial-sdk-contract-build-bk:{RUN_ID}-{backend.name}"
    try:
        backend.build_image(tmp_path, tmp_path / "Dockerfile", tag)
        assert _inspect(tag) is not None
    finally:
        _rmi(tag)


def test_build_missing_context_fails(backend, tmp_path):
    missing = tmp_path / "missing"
    with pytest.raises(ImageBuildFailed, match="unable to prepare context"):
        backend.build_image(
            missing, missing / "Dockerfile", f"tsdk-missing:{RUN_ID}"
        )


def test_run_detached_survives_window(backend, images, started):
    h = started(images["sleeper"])
    assert h.backend == backend.name
    assert h.id and h.name
    assert backend.wait_running(h, timeout=1.0) is True
    assert backend.is_running(h) is True


def test_crash_after_one_second_fails_window(backend, images, started):
    h = started(images["crasher"])
    began = time.monotonic()
    assert backend.wait_running(h, timeout=2.0) is False
    # Observed the exit early, not at the end of a fixed sleep.
    assert time.monotonic() - began < 2.0
    assert backend.is_running(h) is False
    assert backend.logs(h) == "to-stderr"


def test_logs_prefer_stderr_on_mixed_streams(backend, images, started):
    h = started(images["chatty"])
    assert backend.wait_running(h, timeout=0.5)
    # chatty writes "line-2" to stderr, so stderr wins.
    assert backend.logs(h) == "line-2"


def test_stale_name_is_replaced(backend, images, started):
    name = f"tsdk-ct-stale-{RUN_ID}-{backend.name}"
    first = started(images["sleeper"], name=name)
    second = started(images["sleeper"], name=name)
    assert first.id != second.id
    assert backend.is_running(second) is True
    assert _inspect(first.id) is None


def test_ports_are_published(backend, images, started):
    port = _free_port()
    h = started(images["sleeper"], ports={8888: port})
    bindings = _inspect(h.id)["HostConfig"]["PortBindings"]
    assert bindings["8888/tcp"][0]["HostPort"] == str(port)


def test_auto_remove(backend, images, started):
    h = started(images["crasher"], remove=True)
    assert _inspect(h.id)["HostConfig"]["AutoRemove"] is True
    deadline = time.monotonic() + 15
    while _inspect(h.id) is not None and time.monotonic() < deadline:
        time.sleep(0.25)
    assert _inspect(h.id) is None
    assert backend.is_running(h) is False


def test_start_failure_raises(backend):
    with pytest.raises(ContainerStartFailed):
        backend.run_detached(
            f"tutorial-sdk-contract-missing:{RUN_ID}",
            name=f"tsdk-ct-missing-{RUN_ID}",
        )


def test_remove_is_idempotent(backend, images, started):
    h = started(images["sleeper"])
    backend.remove(h, force=True)
    backend.remove(h, force=True)
    assert _inspect(h.id) is None
    assert backend.is_running(h) is False
    assert "No such container" in backend.logs(h)
    res = backend.exec(h, ["true"])
    assert res.exit_code == 1
    assert "No such container" in res.output


# ---------------------------------------------------------------------------
# exec output convention
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("script", "exit_code", "output"),
    [
        ("echo hello; echo noise >&2", 0, "hello"),
        ("echo out; echo err >&2; exit 2", 2, "err"),
        ("echo only-out; exit 4", 4, "only-out"),
    ],
    ids=["success-stdout", "failure-stderr", "failure-stdout"],
)
def test_exec_output_rule(backend, images, started, script, exit_code, output):
    h = started(images["sleeper"])
    res = backend.exec(h, ["sh", "-c", script])
    assert (res.exit_code, res.output) == (exit_code, output)


def test_exec_missing_binary(backend, images, started):
    h = started(images["sleeper"])
    res = backend.exec(h, ["tutorial-sdk-no-such-binary"])
    assert res.exit_code != 0
    assert "executable file not found" in res.output


# ---------------------------------------------------------------------------
# copy_in / stream_logs
# ---------------------------------------------------------------------------


def test_copy_in_file_to_full_path(backend, images, started, tmp_path):
    src = tmp_path / "local-name.txt"
    src.write_text("payload\n")
    h = started(images["sleeper"])
    backend.copy_in(h, src, "/work/notebook.txt")
    res = backend.exec(h, ["cat", "/work/notebook.txt"])
    assert (res.exit_code, res.output) == (0, "payload")


def test_copy_in_directory_to_new_path(backend, images, started, tmp_path):
    (tmp_path / "bundle" / "sub").mkdir(parents=True)
    (tmp_path / "bundle" / "sub" / "a.txt").write_text("A")
    h = started(images["sleeper"])
    backend.copy_in(h, tmp_path / "bundle", "/work/copied")
    res = backend.exec(h, ["cat", "/work/copied/sub/a.txt"])
    assert (res.exit_code, res.output) == (0, "A")


def test_copy_in_missing_parent_fails(backend, images, started, tmp_path: Path):
    src = tmp_path / "f.txt"
    src.write_text("x")
    h = started(images["sleeper"])
    with pytest.raises(CopyFailed):
        backend.copy_in(h, src, "/no/such/dir/f.txt")


def test_stream_logs_yields_lines(backend, images, started):
    h = started(images["chatty"])
    lines = backend.stream_logs(h)
    try:
        first = set(itertools.islice(lines, 3))
    finally:
        lines.close()
    assert first == {"line-1", "line-2", "line-3"}


# ---------------------------------------------------------------------------
# Consumer parity: identical validation reports on both backends
# ---------------------------------------------------------------------------


def test_validator_report_parity_success(backend, images):
    spec = make_spec(name=f"ct-ok-{RUN_ID}-{backend.name}")
    report = ContainerValidator(spec, backend=backend).validate(
        image=images["sleeper"]
    )
    assert report.passed
    assert [(c.name, c.passed, c.message) for c in report.checks] == [
        ("container.start", True, "Container started successfully."),
        (
            "container.jupyterlab",
            True,
            "JupyterLab is available (version 4.2.0).",
        ),
    ]
    assert _inspect(f"tutorial-sdk-val-{spec.name}") is None


def test_validator_report_parity_crash(backend, images):
    spec = make_spec(name=f"ct-crash-{RUN_ID}-{backend.name}")
    report = ContainerValidator(spec, backend=backend).validate(
        image=images["crasher"]
    )
    assert not report.passed
    assert [(c.name, c.passed, c.message) for c in report.checks] == [
        (
            "container.start",
            False,
            "Container stopped immediately. Logs:\nto-stderr",
        ),
    ]
    assert _inspect(f"tutorial-sdk-val-{spec.name}") is None


def test_validator_report_parity_missing_jupyter(backend, images):
    spec = make_spec(name=f"ct-nojl-{RUN_ID}-{backend.name}")
    report = ContainerValidator(spec, backend=backend).validate(
        image=images["chatty"]
    )
    assert not report.passed
    jl = report.checks[1]
    assert jl.name == "container.jupyterlab"
    assert jl.message.startswith(
        "JupyterLab command not available in container: "
    )
    assert "executable file not found" in jl.message


# ---------------------------------------------------------------------------
# Foreground runs
# ---------------------------------------------------------------------------


def _containers_of(image: str, *, running: bool = False) -> list[str]:
    command = ["docker", "ps", "-q", "--filter", f"ancestor={image}"]
    if not running:
        command.insert(2, "-a")
    result = subprocess.run(command, capture_output=True, text=True)
    return result.stdout.split()


def _wait_until(predicate, timeout: float = 15.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.1)
    return predicate()


def test_run_foreground_exit_code_and_streams(backend, images, capfd):
    image = images["exiter"]
    result = backend.run_foreground(image)
    assert result.returncode == 7
    assert result.image == image
    assert result.command == ("docker", "run", "--rm", image)
    captured = capfd.readouterr()
    assert "to-stdout" in captured.out
    assert "to-stderr" in captured.err
    assert "to-stderr" not in captured.out
    assert _wait_until(lambda: not _containers_of(image))


def test_run_foreground_publishes_ports(backend, images):
    image, port = images["napper"], _free_port()
    seen: list[str] = []

    def watch():
        def published():
            result = subprocess.run(
                [
                    "docker",
                    "ps",
                    "--filter",
                    f"ancestor={image}",
                    "--format",
                    "{{.Ports}}",
                ],
                capture_output=True,
                text=True,
            )
            seen.append(result.stdout.strip())
            return f":{port}->8888/tcp" in seen[-1]

        _wait_until(published, timeout=10)

    watcher = threading.Thread(target=watch)
    watcher.start()
    result = backend.run_foreground(image, ports={8888: port})
    watcher.join()
    assert result.returncode == 0
    assert result.command == (
        "docker",
        "run",
        "--rm",
        "-p",
        f"{port}:8888",
        image,
    )
    assert any(f":{port}->8888/tcp" in ports for ports in seen), seen
    assert _wait_until(lambda: not _containers_of(image))


def test_run_foreground_missing_image_exits_125(backend):
    image = f"tutorial-sdk-contract-missing:{RUN_ID}"
    assert backend.run_foreground(image).returncode == 125


_CHILD = (
    "import sys\n"
    "from tutorial_sdk.backend import get_backend\n"
    "backend = get_backend(sys.argv[1])\n"
    "result = backend.run_foreground(sys.argv[2])\n"
    "print('RC', result.returncode, flush=True)\n"
)


def _run_and_interrupt(backend_name: str, image: str, signals: int):
    """Run in a child process group and Ctrl-C it like a terminal does.

    The signal goes to the whole process group, so it reaches the
    ``docker`` CLI child as well as Python, exactly as in a terminal.
    """
    child = subprocess.Popen(
        [sys.executable, "-c", _CHILD, backend_name, image],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    try:
        assert _wait_until(lambda: _containers_of(image, running=True))
        time.sleep(0.5)  # let the run reach its steady state
        for _ in range(signals):
            os.killpg(child.pid, signal.SIGINT)
            time.sleep(0.5)
        out, err = child.communicate(timeout=30)
    finally:
        if child.poll() is None:
            child.kill()
            child.communicate()
    return child.returncode, out, err


def test_ctrl_c_stops_and_removes_container(backend, images):
    image = images["graceful"]
    code, out, err = _run_and_interrupt(backend.name, image, signals=1)
    assert _wait_until(lambda: not _containers_of(image)), "container leaked"
    if backend.name == "docker-sdk":
        # The signal is forwarded, shutdown output is shown, and the
        # container's own exit code is returned.
        assert "bye" in out
        assert "RC 0" in out
        assert code == 0
    else:
        # The docker CLI forwards the signal and Python's
        # KeyboardInterrupt propagates (historical behavior).
        assert "KeyboardInterrupt" in err
        assert code != 0


@pytest.fixture
def sdk_backend(images):
    try:
        return get_backend("docker-sdk")
    except BackendUnavailableError as exc:
        pytest.skip(f"docker-sdk unavailable: {exc}")


def test_sdk_second_ctrl_c_kills_unresponsive_container(sdk_backend, images):
    # ``sleep`` as PID 1 ignores SIGINT, so only the escalation stops it.
    image = images["sleeper"]
    began = time.monotonic()
    code, out, _ = _run_and_interrupt(sdk_backend.name, image, signals=2)
    assert "RC 137" in out
    assert code == 0
    assert time.monotonic() - began < 15
    assert _wait_until(lambda: not _containers_of(image))


def test_sdk_sigterm_is_forwarded(sdk_backend, images, capfd):
    image = images["graceful"]

    def terminate_when_running():
        if _wait_until(lambda: _containers_of(image, running=True)):
            time.sleep(0.5)
            os.kill(os.getpid(), signal.SIGTERM)

    trigger = threading.Thread(target=terminate_when_running)
    trigger.start()
    result = sdk_backend.run_foreground(image)
    trigger.join()
    assert result.returncode == 0
    assert "bye" in capfd.readouterr().out
    assert _wait_until(lambda: not _containers_of(image))


def test_sdk_stream_survives_silence_beyond_client_timeout(
    sdk_backend, images, capfd, monkeypatch
):
    monkeypatch.setattr(sdk_backend._client.api, "timeout", 2)
    result = sdk_backend.run_foreground(images["quiet"])  # silent for 4 s
    assert result.returncode == 0
    assert "done" in capfd.readouterr().out


# ---------------------------------------------------------------------------
# Image push
# ---------------------------------------------------------------------------


def _registry_ready(address: str) -> bool:
    try:
        with urllib.request.urlopen(f"http://{address}/v2/", timeout=2):
            return True
    except urllib.error.HTTPError:
        return True  # it answers (e.g. 401 when auth is enabled)
    except OSError:
        return False


@pytest.fixture(scope="session")
def registry(images):
    """Address (``host:port``) of a registry the daemon can push to."""
    if REGISTRY:
        yield REGISTRY
        return
    name = f"tsdk-ct-registry-{RUN_ID}"
    port = _free_port()
    result = subprocess.run(
        [
            "docker",
            "run",
            "-d",
            "--rm",
            "--name",
            name,
            "-p",
            f"127.0.0.1:{port}:5000",
            REGISTRY_IMAGE,
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        pytest.skip(f"cannot start {REGISTRY_IMAGE}: {result.stderr.strip()}")
    address = f"localhost:{port}"
    try:
        if not _wait_until(lambda: _registry_ready(address), timeout=30):
            pytest.skip(f"registry at {address} did not come up")
        yield address
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True)


@pytest.fixture
def tagged(images):
    """Tag the ``napper`` image under new names; untag afterwards."""
    tags: list[str] = []

    def tag(name: str) -> str:
        subprocess.run(
            ["docker", "tag", images["napper"], name],
            check=True,
            capture_output=True,
        )
        tags.append(name)
        return name

    yield tag
    if tags:
        subprocess.run(["docker", "rmi", *tags], capture_output=True)


@pytest.mark.parametrize(
    "streamed", [False, True], ids=["captured", "streamed"]
)
def test_push_image_reports_digest(backend, registry, tagged, streamed):
    tag = tagged(f"{registry}/tsdk-contract-push:{RUN_ID}-{backend.name}")
    seen: list[str] = []
    result = backend.push_image(
        tag, log_callback=seen.append if streamed else None
    )
    assert result.image == tag
    assert result.digest and result.digest.startswith("sha256:")
    assert any(
        f"@{result.digest}" in digest for digest in _inspect(tag)["RepoDigests"]
    )
    if streamed:
        # Both backends show the registry's "digest: sha256:..." line.
        assert any(f"digest: {result.digest}" in line for line in seen)


def test_push_without_version_pushes_latest(backend, registry, tagged):
    repository = f"{registry}/tsdk-contract-latest-{RUN_ID}-{backend.name}"
    tagged(f"{repository}:latest")
    result = backend.push_image(repository)
    assert result.digest and result.digest.startswith("sha256:")


def test_push_missing_local_image_fails(backend, registry):
    with pytest.raises(ImagePushFailed):
        backend.push_image(f"{registry}/tsdk-contract-absent:{RUN_ID}")


_UNREACHABLE_REGISTRY_ERRORS = (
    "connection refused",
    "context deadline exceeded",
    "Client.Timeout exceeded",
)


def test_push_unreachable_registry_fails(backend, tagged):
    tag = tagged(f"localhost:{_free_port()}/tsdk-contract-nowhere:{RUN_ID}")
    began = time.monotonic()
    with pytest.raises(ImagePushFailed) as info:
        backend.push_image(tag)
    # Both backends report the daemon's message verbatim.
    message = str(info.value)
    assert any(err in message for err in _UNREACHABLE_REGISTRY_ERRORS)
    assert time.monotonic() - began < 60
