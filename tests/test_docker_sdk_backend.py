"""Unit tests for DockerSdkBackend against an in-memory docker client.

The mock client raises the real ``docker.errors`` types, so these
tests pin the error translation and output conventions of the
contract without a daemon.  They need the optional ``docker``
package and are skipped on a base install.
"""

import io
import os
import subprocess
import sys
import tarfile
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

docker = pytest.importorskip("docker")
requests = pytest.importorskip("requests")

from tutorial_sdk.backend import (  # noqa: E402
    ContainerBackend,
    DockerCliBackend,
    get_backend,
)
from tutorial_sdk.backend.base import ContainerHandle  # noqa: E402
from tutorial_sdk.backend.docker_sdk import DockerSdkBackend  # noqa: E402
from tutorial_sdk.backend.errors import (  # noqa: E402
    ContainerStartFailed,
    CopyFailed,
)
from tutorial_sdk.errors import (  # noqa: E402
    BackendError,
    BackendUnavailableError,
    ValidationError,
)
from tutorial_sdk.validator.container import (  # noqa: E402
    ContainerValidator,
)

from .mock_backend import make_spec  # noqa: E402

# ---------------------------------------------------------------------------
# Mock docker client
# ---------------------------------------------------------------------------


def not_found(what="c1"):
    return docker.errors.NotFound(
        f"404 Client Error: Not Found ({what})",
        explanation=f"No such container: {what}",
    )


def api_error(explanation):
    return docker.errors.APIError(
        f"500 Server Error ({explanation})", explanation=explanation
    )


class MockStream:
    def __init__(self, chunks):
        self._chunks = iter(chunks)
        self.closed = False

    def __iter__(self):
        return self

    def __next__(self):
        return next(self._chunks)

    def close(self):
        self.closed = True


class MockContainer:
    def __init__(self, cid, name, status="running"):
        self.id = cid
        self.name = name
        self.status = status
        self.stdout = b""
        self.stderr = b""
        self.exec_result = SimpleNamespace(exit_code=0, output=(b"", None))
        self.exec_error = None
        self.remove_error = None
        self.put_error = None
        self.log_chunks = []
        self.stream = None
        self.calls = []

    def remove(self, force=False):
        self.calls.append(("remove", force))
        if self.remove_error is not None:
            raise self.remove_error

    def exec_run(self, cmd, demux=False):
        self.calls.append(("exec_run", list(cmd), demux))
        if self.exec_error is not None:
            raise self.exec_error
        return self.exec_result

    def logs(self, stdout=True, stderr=True, stream=False, follow=False):
        self.calls.append(("logs", stdout, stderr, stream, follow))
        if stream:
            self.stream = MockStream(self.log_chunks)
            return self.stream
        return (self.stdout if stdout else b"") + (
            self.stderr if stderr else b""
        )

    def put_archive(self, path, data):
        self.calls.append(("put_archive", path, data))
        if self.put_error is not None:
            raise self.put_error
        return True


class MockContainers:
    def __init__(self):
        self.by_key = {}
        self.run_calls = []
        self.run_error = None
        self.get_error = None
        self.next_id = "f" * 64

    def add(self, container):
        self.by_key[container.id] = container
        self.by_key[container.name] = container
        return container

    def get(self, key):
        if self.get_error is not None:
            raise self.get_error
        try:
            return self.by_key[key]
        except KeyError:
            raise not_found(key) from None

    def run(self, image, **kwargs):
        self.run_calls.append((image, kwargs))
        if self.run_error is not None:
            raise self.run_error
        name = kwargs.get("name") or "random_name"
        return self.add(MockContainer(self.next_id, name, status="created"))


class MockClient:
    def __init__(self):
        self.containers = MockContainers()
        self.ping_error = None

    def ping(self):
        if self.ping_error is not None:
            raise self.ping_error
        return True


@pytest.fixture
def client():
    return MockClient()


@pytest.fixture
def backend(client):
    return DockerSdkBackend(client=client)


def running(client, cid="cid1", name="c1", status="running"):
    return client.containers.add(MockContainer(cid, name, status))


def handle(cid="cid1", name="c1"):
    return ContainerHandle(id=cid, name=name, backend="docker-sdk")


# ---------------------------------------------------------------------------
# Protocol & availability
# ---------------------------------------------------------------------------


def test_conforms_to_protocol(backend):
    assert isinstance(backend, ContainerBackend)
    assert backend.name == "docker-sdk"


def test_available_reflects_ping(backend, client):
    assert backend.available() is True
    client.ping_error = requests.exceptions.ConnectionError("refused")
    assert backend.available() is False
    client.ping_error = docker.errors.DockerException("boom")
    assert backend.available() is False


# ---------------------------------------------------------------------------
# run_detached
# ---------------------------------------------------------------------------


def test_run_detached_arguments_and_handle(backend, client):
    h = backend.run_detached(
        "demo:latest", name="val-demo", ports={8888: 9999}, remove=True
    )
    image, kwargs = client.containers.run_calls[0]
    assert image == "demo:latest"
    assert kwargs == {
        "detach": True,
        "name": "val-demo",
        "ports": {"8888/tcp": 9999},
        "auto_remove": True,
    }
    assert h == ContainerHandle(
        id="f" * 64, name="val-demo", backend="docker-sdk"
    )


def test_run_detached_without_name_uses_short_id(backend, client):
    h = backend.run_detached("demo:latest")
    _, kwargs = client.containers.run_calls[0]
    assert kwargs["name"] is None
    assert kwargs["ports"] is None
    assert kwargs["auto_remove"] is False
    assert h.name == ("f" * 64)[:12]


def test_run_detached_precleans_stale_container(backend, client):
    stale = running(client, cid="old", name="val-demo")
    backend.run_detached("demo:latest", name="val-demo")
    assert stale.calls == [("remove", True)]


@pytest.mark.parametrize(
    "error", [None, api_error("removal in progress")], ids=["absent", "busy"]
)
def test_run_detached_ignores_preclean_failures(backend, client, error):
    if error is not None:
        running(client, name="val-demo").remove_error = error
    backend.run_detached("demo:latest", name="val-demo")
    assert len(client.containers.run_calls) == 1


@pytest.mark.parametrize(
    "error",
    [
        docker.errors.ImageNotFound(
            "404", explanation="No such image: missing:latest"
        ),
        api_error("pull access denied for missing"),
    ],
    ids=["image-not-found", "api-error"],
)
def test_run_detached_failure_maps_to_start_failed(backend, client, error):
    client.containers.run_error = error
    with pytest.raises(ContainerStartFailed) as ei:
        backend.run_detached("missing:latest", name="x")
    assert str(ei.value).startswith("Error response from daemon: ")
    assert error.explanation in str(ei.value)


# ---------------------------------------------------------------------------
# State polling
# ---------------------------------------------------------------------------


def test_is_running_states(backend, client):
    running(client)
    assert backend.is_running(handle()) is True
    client.containers.by_key["cid1"].status = "exited"
    assert backend.is_running(handle()) is False
    assert backend.is_running(handle(cid="gone")) is False


def test_is_running_looks_up_by_id(backend, client):
    running(client, cid="cid1", name="other-name")
    assert backend.is_running(handle(cid="cid1", name="c1")) is True


def test_wait_running_exits_early_when_stopped(backend, client):
    running(client, status="exited")
    with mock.patch("time.sleep") as sleep:
        assert backend.wait_running(handle(), timeout=30.0) is False
    sleep.assert_not_called()


def test_wait_running_true_only_after_window(backend, client):
    running(client)
    calls = []
    original = backend.is_running

    def probe(c):
        calls.append(c)
        return original(c)

    with mock.patch.object(backend, "is_running", side_effect=probe):
        assert backend.wait_running(handle(), timeout=0.05, poll=0.01)
    assert len(calls) >= 2  # observed more than once, no early True


# ---------------------------------------------------------------------------
# logs / exec output conventions
# ---------------------------------------------------------------------------


def test_logs_prefers_stderr_then_stdout(backend, client):
    c = running(client)
    c.stdout, c.stderr = b"out\n", b"err\n"
    assert backend.logs(handle()) == "err"
    c.stderr = b"  \n"
    assert backend.logs(handle()) == "out"


def test_logs_for_missing_container_returns_daemon_message(backend):
    assert backend.logs(handle(cid="gone")) == (
        "Error response from daemon: No such container: gone"
    )


@pytest.mark.parametrize(
    ("exit_code", "streams", "expected"),
    [
        (0, (b"4.2.0\n", None), "4.2.0"),
        (0, (b"out\n", b"warn\n"), "out"),
        (2, (b"out\n", b"err\n"), "err"),
        (4, (b"only-out\n", None), "only-out"),
        (1, (None, None), ""),
        (3, (b"\xff\xfe bad\n", None), "�� bad"),
    ],
)
def test_exec_output_rule(backend, client, exit_code, streams, expected):
    c = running(client)
    c.exec_result = SimpleNamespace(exit_code=exit_code, output=streams)
    res = backend.exec(handle(), ["jupyter", "lab", "--version"])
    assert (res.exit_code, res.output) == (exit_code, expected)
    assert c.calls[-1] == ("exec_run", ["jupyter", "lab", "--version"], True)


def test_exec_daemon_rejection_mirrors_cli(backend, client):
    running(client).exec_error = docker.errors.APIError(
        "409", explanation="container cid1 is not running"
    )
    res = backend.exec(handle(), ["true"])
    assert res.exit_code == 1
    assert res.output == (
        "Error response from daemon: container cid1 is not running"
    )
    res = backend.exec(handle(cid="gone"), ["true"])
    assert (res.exit_code, res.output) == (
        1,
        "Error response from daemon: No such container: gone",
    )


# ---------------------------------------------------------------------------
# remove
# ---------------------------------------------------------------------------


def test_remove_passes_force(backend, client):
    c = running(client)
    backend.remove(handle())
    backend.remove(handle(), force=False)
    assert c.calls == [("remove", True), ("remove", False)]


@pytest.mark.parametrize(
    "error", [not_found(), api_error("removal in progress")]
)
def test_remove_ignores_daemon_errors(backend, client, error):
    running(client).remove_error = error
    backend.remove(handle())  # does not raise
    backend.remove(handle(cid="gone"))


# ---------------------------------------------------------------------------
# Error translation at the boundary
# ---------------------------------------------------------------------------


def test_connection_errors_become_unavailable(backend, client):
    client.containers.get_error = requests.exceptions.ConnectionError(
        "Connection aborted"
    )
    with pytest.raises(BackendUnavailableError, match="cannot reach"):
        backend.is_running(handle())
    with pytest.raises(BackendUnavailableError):
        backend.remove(handle())


def test_unexpected_docker_errors_become_backend_errors(backend, client):
    client.containers.get_error = docker.errors.DockerException("weird")
    with pytest.raises(BackendError, match="weird") as ei:
        backend.logs(handle())
    assert not isinstance(ei.value, docker.errors.DockerException)


def test_validator_maps_unavailable_sdk_errors(client):
    client.containers.run_error = requests.exceptions.ConnectionError("x")
    validator = ContainerValidator(
        make_spec(), backend=DockerSdkBackend(client=client)
    )
    with pytest.raises(ValidationError):
        validator.validate()


# ---------------------------------------------------------------------------
# copy_in / stream_logs
# ---------------------------------------------------------------------------


def _members(data):
    with tarfile.open(fileobj=io.BytesIO(data)) as archive:
        return {m.name: m for m in archive.getmembers()}


def test_copy_in_file_uses_parent_and_basename(backend, client, tmp_path):
    src = tmp_path / "local.ipynb"
    src.write_text("{}")
    c = running(client)
    backend.copy_in(handle(), src, "/work/nb.ipynb")
    _, path, data = c.calls[-1]
    assert path == "/work"
    members = _members(data)
    assert list(members) == ["nb.ipynb"]
    assert (members["nb.ipynb"].uid, members["nb.ipynb"].gid) == (0, 0)


def test_copy_in_directory(backend, client, tmp_path):
    (tmp_path / "src" / "sub").mkdir(parents=True)
    (tmp_path / "src" / "sub" / "a.txt").write_text("a")
    c = running(client)
    backend.copy_in(handle(), tmp_path / "src", "/work/dst")
    _, path, data = c.calls[-1]
    assert path == "/work"
    assert set(_members(data)) == {"dst", "dst/sub", "dst/sub/a.txt"}


def test_copy_in_errors(backend, client, tmp_path):
    with pytest.raises(CopyFailed, match="no such file or directory"):
        backend.copy_in(handle(), tmp_path / "missing", "/work/x")
    src = tmp_path / "f"
    src.write_text("x")
    with pytest.raises(CopyFailed, match="Invalid destination"):
        backend.copy_in(handle(), src, "/")
    running(client).put_error = docker.errors.NotFound(
        "404", explanation="Could not find the file /nope in container"
    )
    with pytest.raises(CopyFailed, match="Could not find the file /nope"):
        backend.copy_in(handle(), src, "/nope/x")
    with pytest.raises(CopyFailed, match="No such container"):
        backend.copy_in(handle(cid="gone"), src, "/work/x")


def test_stream_logs_splits_lines_across_chunks(backend, client):
    c = running(client)
    snowman = "☃".encode()
    c.log_chunks = [
        b"li",
        b"ne 1\nline",
        b" 2 " + snowman[:1],
        snowman[1:] + b"\n",
        b"tail",
    ]
    lines = list(backend.stream_logs(handle()))
    assert lines == ["line 1", "line 2 ☃", "tail"]
    assert ("logs", True, True, True, True) in c.calls
    assert c.stream.closed


def test_stream_logs_closes_stream_on_early_exit(backend, client):
    c = running(client)
    c.log_chunks = [b"a\nb\nc\n"]
    gen = backend.stream_logs(handle())
    assert next(gen) == "a"
    gen.close()
    assert c.stream.closed


# ---------------------------------------------------------------------------
# Delegation to the CLI backend (build_image, run_foreground)
# ---------------------------------------------------------------------------


def test_build_and_foreground_delegate_to_cli(backend):
    cli = mock.create_autospec(DockerCliBackend, instance=True)
    cli.available.return_value = True
    backend._cli = cli
    callback = print
    backend.build_image(
        Path("/p"),
        Path("/p/Dockerfile"),
        "t:1",
        no_cache=True,
        platform="linux/amd64",
        log_callback=callback,
    )
    cli.build_image.assert_called_once_with(
        Path("/p"),
        Path("/p/Dockerfile"),
        "t:1",
        no_cache=True,
        platform="linux/amd64",
        log_callback=callback,
    )
    backend.run_foreground("t:1", ports={8888: 9}, interactive_shell=True)
    cli.run_foreground.assert_called_once_with(
        "t:1", ports={8888: 9}, interactive_shell=True
    )


def test_delegation_without_binary_is_unavailable(backend):
    with mock.patch("shutil.which", return_value=None):
        with pytest.raises(BackendUnavailableError, match="'docker' binary"):
            backend.build_image(Path("."), Path("Dockerfile"), "t")
        with pytest.raises(BackendUnavailableError, match="Docker CLI"):
            backend.run_foreground("t")


# ---------------------------------------------------------------------------
# Construction & selection
# ---------------------------------------------------------------------------


def test_construction_pings_daemon():
    client = MockClient()
    with mock.patch("docker.from_env", return_value=client):
        backend = DockerSdkBackend()
    assert backend._client is client


@pytest.mark.parametrize(
    "error",
    [
        docker.errors.DockerException("Error while fetching server API"),
        requests.exceptions.ConnectionError("refused"),
    ],
)
def test_unreachable_daemon_is_unavailable(error, monkeypatch):
    monkeypatch.setenv("DOCKER_HOST", "unix:///nope.sock")
    client = MockClient()
    client.ping_error = error
    with mock.patch("docker.from_env", return_value=client):
        with pytest.raises(BackendUnavailableError, match="docker-cli"):
            DockerSdkBackend()
    with mock.patch("docker.from_env", side_effect=error):
        with pytest.raises(BackendUnavailableError):
            DockerSdkBackend()


def test_context_endpoint_fallback(monkeypatch):
    monkeypatch.delenv("DOCKER_HOST", raising=False)
    client = MockClient()
    with (
        mock.patch(
            "docker.from_env",
            side_effect=docker.errors.DockerException("no default socket"),
        ),
        mock.patch(
            "tutorial_sdk.backend.docker_sdk._context_host",
            return_value="unix:///Users/me/.docker/run/docker.sock",
        ),
        mock.patch("docker.DockerClient", return_value=client) as ctor,
    ):
        backend = DockerSdkBackend()
    ctor.assert_called_once_with(
        base_url="unix:///Users/me/.docker/run/docker.sock"
    )
    assert backend._client is client


def test_no_context_fallback_when_docker_host_set(monkeypatch):
    monkeypatch.setenv("DOCKER_HOST", "tcp://127.0.0.1:1")
    with (
        mock.patch(
            "docker.from_env",
            side_effect=docker.errors.DockerException("refused"),
        ),
        mock.patch(
            "tutorial_sdk.backend.docker_sdk._context_host"
        ) as context_host,
    ):
        with pytest.raises(BackendUnavailableError):
            DockerSdkBackend()
    context_host.assert_not_called()


def test_get_backend_selects_sdk():
    with mock.patch("docker.from_env", return_value=MockClient()):
        assert isinstance(get_backend("docker-sdk"), DockerSdkBackend)
        assert isinstance(get_backend("auto"), DockerSdkBackend)
        assert isinstance(get_backend(config="docker-sdk"), DockerSdkBackend)


def test_auto_falls_back_to_cli_when_daemon_unreachable(monkeypatch):
    monkeypatch.setenv("DOCKER_HOST", "unix:///nope.sock")
    with (
        mock.patch(
            "docker.from_env",
            side_effect=docker.errors.DockerException("refused"),
        ),
        mock.patch("shutil.which", return_value="/usr/bin/docker"),
    ):
        assert isinstance(get_backend("auto"), DockerCliBackend)


def test_lazy_package_export():
    import tutorial_sdk.backend as backend_pkg

    assert backend_pkg.DockerSdkBackend is DockerSdkBackend
    with pytest.raises(AttributeError):
        backend_pkg.NoSuchBackend  # noqa: B018


def test_importing_sdk_does_not_import_docker():
    code = (
        "import sys, tutorial_sdk, tutorial_sdk.backend, "
        "tutorial_sdk.backend.docker_sdk; "
        "assert 'docker' not in sys.modules, 'docker imported eagerly'"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": os.pathsep.join(sys.path)},
    )
    assert result.returncode == 0, result.stderr


# ---------------------------------------------------------------------------
# Consumer parity: ContainerValidator over the SDK backend
# ---------------------------------------------------------------------------


def test_validator_success_report(client):
    backend = DockerSdkBackend(client=client)
    client.containers.next_id = "a" * 64

    def run(image, **kwargs):
        c = client.containers.add(MockContainer("a" * 64, kwargs["name"]))
        c.exec_result = SimpleNamespace(exit_code=0, output=(b"4.2.0\n", None))
        return c

    client.containers.run = run
    with mock.patch("time.sleep"):
        report = ContainerValidator(make_spec(), backend=backend).validate()
    assert report.passed
    assert [(c.name, c.message) for c in report.checks] == [
        ("container.start", "Container started successfully."),
        ("container.jupyterlab", "JupyterLab is available (version 4.2.0)."),
    ]
    assert client.containers.by_key["a" * 64].calls[-1] == ("remove", True)


def test_validator_crash_report_includes_stderr_logs(client):
    backend = DockerSdkBackend(client=client)

    def run(image, **kwargs):
        c = client.containers.add(
            MockContainer("b" * 64, kwargs["name"], status="exited")
        )
        c.stdout, c.stderr = b"to-stdout\n", b"to-stderr\n"
        return c

    client.containers.run = run
    report = ContainerValidator(make_spec(), backend=backend).validate()
    assert not report.passed
    assert report.checks[0].name == "container.start"
    assert report.checks[0].message == (
        "Container stopped immediately. Logs:\nto-stderr"
    )
    assert client.containers.by_key["b" * 64].calls[-1] == ("remove", True)


def _use_context(monkeypatch, tmp_path, host, tls=False):
    """Write a Docker context the way the docker CLI stores it."""
    import hashlib
    import json

    monkeypatch.setenv("DOCKER_CONFIG", str(tmp_path))
    monkeypatch.delenv("DOCKER_CONTEXT", raising=False)
    context_id = hashlib.sha256(b"ctx").hexdigest()
    meta = tmp_path / "contexts" / "meta" / context_id
    meta.mkdir(parents=True)
    (meta / "meta.json").write_text(
        json.dumps(
            {
                "Name": "ctx",
                "Metadata": {},
                "Endpoints": {"docker": {"Host": host, "SkipTLSVerify": False}},
            }
        )
    )
    if tls:
        certs = tmp_path / "contexts" / "tls" / context_id / "docker"
        certs.mkdir(parents=True)
        for name in ("ca.pem", "cert.pem", "key.pem"):
            (certs / name).write_text("dummy")
    (tmp_path / "config.json").write_text('{"currentContext": "ctx"}')


def test_context_host_reads_current_context(monkeypatch, tmp_path):
    from tutorial_sdk.backend.docker_sdk import _context_host

    sock = "unix:///Users/me/.docker/run/docker.sock"
    _use_context(monkeypatch, tmp_path, sock)
    assert _context_host() == sock
    monkeypatch.setenv("DOCKER_CONTEXT", "default")
    assert _context_host() is None


def test_context_host_ignores_default_and_tls(monkeypatch, tmp_path):
    from tutorial_sdk.backend.docker_sdk import _context_host

    monkeypatch.setenv("DOCKER_CONFIG", str(tmp_path))
    (tmp_path / "config.json").write_text("{}")
    assert _context_host() is None  # empty config: default context
    _use_context(monkeypatch, tmp_path, "tcp://10.0.0.1:2376", tls=True)
    assert _context_host() is None
    monkeypatch.setenv("DOCKER_CONTEXT", "does-not-exist")
    assert _context_host() is None
