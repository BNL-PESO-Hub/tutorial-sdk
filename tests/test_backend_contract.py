"""Contract tests: both container backends against a real daemon.

Opt-in (``pytest -m docker``): every test runs once per backend and
asserts the same observable behavior, so ``DockerCliBackend`` and
``DockerSdkBackend`` cannot drift apart.  A backend that is not
usable here (no daemon, no ``docker`` binary, docker-py missing)
is skipped rather than failed.

The test images are built from ``busybox`` (override with
``TUTORIAL_SDK_CONTRACT_BASE_IMAGE``) and need no network once the
base image is present locally.
"""

from __future__ import annotations

import itertools
import json
import os
import socket
import subprocess
import time
import uuid
from pathlib import Path

import pytest

from tutorial_sdk.backend import DockerCliBackend, get_backend
from tutorial_sdk.backend.errors import ContainerStartFailed, CopyFailed
from tutorial_sdk.errors import BackendUnavailableError
from tutorial_sdk.validator.container import ContainerValidator

from .mock_backend import make_spec

pytestmark = pytest.mark.docker

BASE_IMAGE = os.environ.get("TUTORIAL_SDK_CONTRACT_BASE_IMAGE", "busybox:1.36")
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
