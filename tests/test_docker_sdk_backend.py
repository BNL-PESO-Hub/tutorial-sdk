"""Unit tests for DockerSdkBackend against an in-memory docker client.

The mock client raises the real ``docker.errors`` types, so these
tests pin the error translation and output conventions of the
contract without a daemon.  They need the optional ``docker``
package and are skipped on a base install.
"""

import io
import os
import signal
import subprocess
import sys
import tarfile
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

docker = pytest.importorskip("docker")
requests = pytest.importorskip("requests")
urllib3 = pytest.importorskip("urllib3")

from tutorial_sdk.backend import (  # noqa: E402
    ContainerBackend,
    DockerCliBackend,
    get_backend,
)
from tutorial_sdk.backend import docker_sdk  # noqa: E402
from tutorial_sdk.backend.base import (  # noqa: E402
    MANAGED_LABEL,
    MANAGED_LABEL_VALUE,
    ContainerHandle,
    ImageBuildResult,
    ImagePushResult,
)
from tutorial_sdk.backend.docker_cli import foreground_command  # noqa: E402
from tutorial_sdk.backend.docker_sdk import DockerSdkBackend  # noqa: E402
from tutorial_sdk.backend.errors import (  # noqa: E402
    ContainerStartFailed,
    CopyFailed,
    ImageBuildFailed,
    ImagePushFailed,
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

MANAGED = {MANAGED_LABEL: MANAGED_LABEL_VALUE}

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
        # Foreground-run scripting.
        self.out_chunks = []
        self.err_chunks = []
        self.streams = []
        self.logs_error = None
        self.start_error = None
        self.start_hook = None
        self.exit_status = {"StatusCode": 0}
        self.wait_error = None
        self.wait_hook = None
        self.blocks_until_killed = False
        self.kill_effects = {}  # signal -> exit code that ends wait()
        self.kill_error = None
        self._exited = threading.Event()

    def start(self):
        self.calls.append(("start",))
        if self.start_hook is not None:
            self.start_hook(self)
        if self.start_error is not None:
            raise self.start_error
        self.status = "running"

    def wait(self):
        self.calls.append(("wait",))
        if self.wait_hook is not None:
            self.wait_hook(self)
        if self.wait_error is not None:
            raise self.wait_error
        if self.blocks_until_killed and not self._exited.wait(10):
            raise AssertionError("container was never stopped")
        return self.exit_status

    def kill(self, signal=None):
        self.calls.append(("kill", signal))
        if self.kill_error is not None:
            raise self.kill_error
        if signal in self.kill_effects:
            self.exit_status = {"StatusCode": self.kill_effects[signal]}
            self._exited.set()

    def kills(self):
        return [c[1] for c in self.calls if c[0] == "kill"]

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
        if stream and self.logs_error is not None:
            raise self.logs_error
        if stream:
            if stdout and stderr:
                chunks = self.log_chunks
            else:
                chunks = self.out_chunks if stdout else self.err_chunks
            self.stream = MockStream(chunks)
            self.streams.append(self.stream)
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
        self.create_calls = []
        self.create_errors = []
        self.created = None
        self.prepare = None  # callable scripting each created container
        self.listed = []
        self.list_calls = []

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

    def list(self, **kwargs):
        self.list_calls.append(kwargs)
        if self.get_error is not None:
            raise self.get_error
        return list(self.listed)

    def create(self, image, **kwargs):
        self.create_calls.append((image, kwargs))
        if self.create_errors:
            raise self.create_errors.pop(0)
        self.created = self.add(
            MockContainer(self.next_id, "random_name", status="created")
        )
        if self.prepare is not None:
            self.prepare(self.created)
        return self.created


class MockImages:
    def __init__(self):
        self.pull_calls = []
        self.pull_error = None
        self.by_tag = {}
        self.get_error = None

    def pull(self, repository, **kwargs):
        self.pull_calls.append((repository, kwargs))
        if self.pull_error is not None:
            raise self.pull_error

    def add(self, tag, image_id="sha256:local", repo_digests=()):
        self.by_tag[tag] = SimpleNamespace(
            id=image_id, attrs={"RepoDigests": list(repo_digests)}
        )

    def get(self, name):
        if self.get_error is not None:
            raise self.get_error
        try:
            return self.by_tag[name]
        except KeyError:
            raise docker.errors.ImageNotFound(
                f"No such image: {name}", explanation=f"No such image: {name}"
            ) from None


class MockApi:
    """Low-level API: scripted build and push streams."""

    def __init__(self):
        self.timeout = 60
        self.build_calls = []
        self.build_chunks = []
        self.build_error = None  # raised by build() itself
        self.build_stream_error = None  # raised after the chunks
        self.push_calls = []
        self.push_chunks = []
        self.push_error = None
        self.push_stream_error = None
        self.streams = []

    def _stream(self, chunks, error):
        def generate():
            yield from chunks
            if error is not None:
                raise error

        stream = MockStream(generate())
        self.streams.append(stream)
        return stream

    def build(self, **kwargs):
        self.build_calls.append(kwargs)
        if self.build_error is not None:
            raise self.build_error
        return self._stream(self.build_chunks, self.build_stream_error)

    def push(self, repository, **kwargs):
        self.push_calls.append((repository, kwargs))
        if self.push_error is not None:
            raise self.push_error
        return self._stream(self.push_chunks, self.push_stream_error)


class MockClient:
    def __init__(self):
        self.containers = MockContainers()
        self.images = MockImages()
        self.api = MockApi()
        self.ping_error = None
        self.version_error = None

    def version(self):
        if self.version_error is not None:
            raise self.version_error
        return {"Version": "29.9.1", "ApiVersion": "1.56", "Os": "linux"}

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
# Delegation to the CLI backend (interactive shells)
# ---------------------------------------------------------------------------


def test_shell_delegates_to_cli(backend):
    cli = mock.create_autospec(DockerCliBackend, instance=True)
    cli.available.return_value = True
    backend._cli = cli
    backend.run_foreground("t:1", ports={8888: 9}, interactive_shell=True)
    cli.run_foreground.assert_called_once_with(
        "t:1", ports={8888: 9}, interactive_shell=True
    )


def test_plain_foreground_run_does_not_delegate(backend, client):
    cli = mock.create_autospec(DockerCliBackend, instance=True)
    backend._cli = cli
    backend.run_foreground("t:1")
    cli.run_foreground.assert_not_called()
    assert client.containers.create_calls == [("t:1", {"labels": MANAGED})]


def test_delegation_without_binary_is_unavailable(backend, client, tmp_path):
    (tmp_path / "Dockerfile").write_text("FROM base\n")
    client.images.add("t")
    with mock.patch("shutil.which", return_value=None):
        with pytest.raises(
            BackendUnavailableError, match="interactive shell runs"
        ):
            backend.run_foreground("t", interactive_shell=True)
        # Plain foreground runs, builds, and pushes need no binary.
        assert backend.run_foreground("t").returncode == 0
        build = backend.build_image(tmp_path, tmp_path / "Dockerfile", "t")
        assert build.image == "t"
        assert backend.push_image("t").image == "t"


# ---------------------------------------------------------------------------
# run_foreground (native)
# ---------------------------------------------------------------------------


@pytest.fixture
def guard_sigterm():
    """Fail (instead of killing pytest) if a SIGTERM goes unhandled."""

    def _unhandled(signum, frame):
        raise AssertionError("SIGTERM reached the default handler")

    previous = signal.signal(signal.SIGTERM, _unhandled)
    yield _unhandled
    signal.signal(signal.SIGTERM, previous)


def script(client, **attrs):
    """Apply ``attrs`` to every container ``create`` returns."""

    def prepare(container):
        for key, value in attrs.items():
            setattr(container, key, value)

    client.containers.prepare = prepare


def send_signals(*signums, after_kills=True):
    """Return a ``wait_hook`` that signals this process.

    Each signal after the first is sent once the previous one has
    been turned into a ``kill`` call, as a user pressing Ctrl-C twice.
    """

    def hook(container):
        for i, signum in enumerate(signums):
            if after_kills:
                deadline = time.monotonic() + 5
                while len(container.kills()) < i:
                    assert time.monotonic() < deadline, "signal not handled"
                    time.sleep(0.01)
            os.kill(os.getpid(), signum)

    return hook


def test_run_foreground_streams_and_returns_exit_code(backend, client, capsys):
    script(
        client,
        out_chunks=[b"hel", b"lo\n", b"partial"],
        err_chunks=[b"warn\n"],
        exit_status={"StatusCode": 7},
    )
    result = backend.run_foreground("img:1", ports={8888: 9999})

    assert result.returncode == 7
    assert result.image == "img:1"
    assert result.command == (
        "docker",
        "run",
        "--rm",
        "-p",
        "9999:8888",
        "img:1",
    )
    assert client.containers.create_calls == [
        ("img:1", {"ports": {"8888/tcp": 9999}, "labels": MANAGED})
    ]
    captured = capsys.readouterr()
    assert captured.out == "hello\npartial"
    assert captured.err == "warn\n"

    container = client.containers.created
    names = [c[0] for c in container.calls]
    assert names[0] == "start"
    assert names[-1] == "remove"
    assert ("remove", True) in container.calls
    assert ("logs", True, False, True, True) in container.calls
    assert ("logs", False, True, True, True) in container.calls
    assert all(s.closed for s in container.streams)


def test_run_foreground_command_matches_cli_backend(backend):
    ports = {8888: 9000, 22: 2222}
    result = backend.run_foreground("img:2", ports=ports)
    assert list(result.command) == foreground_command("img:2", ports)


def test_run_foreground_decodes_for_text_only_sinks(
    backend, client, monkeypatch
):
    out = io.StringIO()
    monkeypatch.setattr(sys, "stdout", out)
    script(client, out_chunks=[b"caf\xc3", b"\xa9\n\xc3"])
    backend.run_foreground("img")
    assert out.getvalue() == "café\n\ufffd"


def test_run_foreground_pulls_missing_image(backend, client, capsys):
    client.containers.create_errors = [docker.errors.ImageNotFound("nope")]
    assert backend.run_foreground("lib/img:3").returncode == 0
    assert client.images.pull_calls == [("lib/img:3", {})]
    assert len(client.containers.create_calls) == 2
    assert "Unable to find image 'lib/img:3' locally" in capsys.readouterr().err


def test_run_foreground_failed_pull_returns_125(backend, client, capsys):
    client.containers.create_errors = [docker.errors.ImageNotFound("nope")]
    client.images.pull_error = api_error("pull access denied for img")
    result = backend.run_foreground("img")
    assert result.returncode == 125
    assert result.command == ("docker", "run", "--rm", "img")
    err = capsys.readouterr().err
    assert "Error response from daemon: pull access denied for img" in err
    assert client.containers.created is None  # nothing to clean up


def test_run_foreground_start_failure_returns_125_and_removes(
    backend, client, capsys
):
    script(client, start_error=api_error("port is already allocated"))
    assert backend.run_foreground("img", ports={8888: 80}).returncode == 125
    assert "port is already allocated" in capsys.readouterr().err
    container = client.containers.created
    assert [c[0] for c in container.calls] == ["start", "remove"]


def test_run_foreground_unreadable_logs_still_reports_exit(
    backend, client, capsys
):
    script(
        client,
        logs_error=api_error("logging driver does not support reading"),
        exit_status={"StatusCode": 3},
    )
    assert backend.run_foreground("img").returncode == 3
    err = capsys.readouterr().err
    assert err.count("Container output unavailable:") == 1
    assert "does not support reading" in err


def test_run_foreground_missing_status_code_is_125(backend, client):
    script(client, exit_status={"Error": None})
    assert backend.run_foreground("img").returncode == 125


def test_run_foreground_daemon_lost_is_unavailable(backend, client):
    script(client, wait_error=requests.exceptions.ConnectionError("gone"))
    with pytest.raises(BackendUnavailableError, match="Running container"):
        backend.run_foreground("img")
    assert ("remove", True) in client.containers.created.calls


def test_run_foreground_unexpected_daemon_error(backend, client):
    script(client, wait_error=api_error("boom"))
    with pytest.raises(BackendError, match="Running container failed"):
        backend.run_foreground("img")
    assert ("remove", True) in client.containers.created.calls


def test_run_foreground_remove_errors_are_ignored(backend, client):
    script(client, remove_error=not_found())
    assert backend.run_foreground("img").returncode == 0


@pytest.mark.parametrize(
    ("signum", "name"),
    [(signal.SIGINT, "SIGINT"), (signal.SIGTERM, "SIGTERM")],
)
def test_signal_is_forwarded_and_exit_code_returned(
    backend, client, capsys, guard_sigterm, signum, name
):
    script(
        client,
        blocks_until_killed=True,
        kill_effects={name: 0},
        wait_hook=send_signals(signum),
    )
    result = backend.run_foreground("img")
    assert result.returncode == 0
    container = client.containers.created
    assert container.kills() == [name]
    assert ("remove", True) in container.calls
    assert "Stopping container" in capsys.readouterr().err
    # Handlers are restored afterwards.
    assert signal.getsignal(signal.SIGINT) is signal.default_int_handler
    assert signal.getsignal(signal.SIGTERM) is guard_sigterm


def test_second_signal_kills(backend, client, guard_sigterm):
    script(
        client,
        blocks_until_killed=True,
        kill_effects={None: 137},  # SIGINT is ignored by the container
        wait_hook=send_signals(signal.SIGINT, signal.SIGINT),
    )
    assert backend.run_foreground("img").returncode == 137
    assert client.containers.created.kills() == ["SIGINT", None]


def test_grace_period_expiry_kills(backend, client, monkeypatch):
    monkeypatch.setattr(docker_sdk, "_STOP_GRACE", 0.3)
    script(
        client,
        blocks_until_killed=True,
        kill_effects={None: 137},
        wait_hook=send_signals(signal.SIGINT),
    )
    began = time.monotonic()
    assert backend.run_foreground("img").returncode == 137
    assert client.containers.created.kills() == ["SIGINT", None]
    assert time.monotonic() - began < 5


def test_kill_of_exited_container_is_ignored(backend, client, monkeypatch):
    monkeypatch.setattr(docker_sdk, "_STOP_GRACE", 0.2)

    def hook(container):
        os.kill(os.getpid(), signal.SIGINT)
        deadline = time.monotonic() + 5
        while len(container.kills()) < 2:  # forwarded, then escalated
            assert time.monotonic() < deadline
            time.sleep(0.01)

    script(
        client,
        kill_error=api_error("container is not running"),
        exit_status={"StatusCode": 0},
        wait_hook=hook,
    )
    assert backend.run_foreground("img").returncode == 0


def test_signal_during_start_is_forwarded_once_running(backend, client):
    # Delivered on the main thread while ``start()`` is in progress.
    script(
        client,
        start_hook=lambda c: os.kill(os.getpid(), signal.SIGINT),
        blocks_until_killed=True,
        kill_effects={"SIGINT": 130},
    )
    assert backend.run_foreground("img").returncode == 130
    assert client.containers.created.kills() == ["SIGINT"]


def test_ignored_sigint_stays_ignored(backend, client):
    previous = signal.signal(signal.SIGINT, signal.SIG_IGN)
    try:
        script(
            client,
            exit_status={"StatusCode": 0},
            wait_hook=lambda c: os.kill(os.getpid(), signal.SIGINT),
        )
        assert backend.run_foreground("img").returncode == 0
        assert client.containers.created.kills() == []
        assert signal.getsignal(signal.SIGINT) is signal.SIG_IGN
    finally:
        signal.signal(signal.SIGINT, previous)


def test_run_from_worker_thread_leaves_signals_alone(backend, client):
    before = signal.getsignal(signal.SIGINT)
    seen = {}

    def hook(container):
        seen["handler"] = signal.getsignal(signal.SIGINT)

    script(client, exit_status={"StatusCode": 4}, wait_hook=hook)
    result = {}
    worker = threading.Thread(
        target=lambda: result.update(r=backend.run_foreground("img"))
    )
    worker.start()
    worker.join(10)
    assert result["r"].returncode == 4
    assert seen["handler"] is before


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
        assert isinstance(
            get_backend(fallback_name="docker-sdk"), DockerSdkBackend
        )


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


# ---------------------------------------------------------------------------
# build_image (native)
# ---------------------------------------------------------------------------


def dockerfile(tmp_path, text="FROM base\n"):
    path = tmp_path / "Dockerfile"
    path.write_text(text)
    return path


ERROR_CHUNK = {
    "errorDetail": {"code": 1, "message": "returned a non-zero code: 3"},
    "error": "returned a non-zero code: 3",
}


def test_build_passes_absolute_paths_and_options(
    backend, client, tmp_path, monkeypatch
):
    dockerfile(tmp_path)
    monkeypatch.chdir(tmp_path.parent)
    context = Path(tmp_path.name)
    client.api.build_chunks = [{"aux": {"ID": "sha256:abc"}}]

    result = backend.build_image(
        context,
        context / "Dockerfile",
        "t:1",
        no_cache=True,
        platform="linux/arm64",
    )

    assert client.api.build_calls == [
        {
            "path": os.path.abspath(context),
            "dockerfile": os.path.abspath(context / "Dockerfile"),
            "tag": "t:1",
            "nocache": True,
            "rm": True,
            "forcerm": True,
            "decode": True,
            "platform": "linux/arm64",
        }
    ]
    assert result == ImageBuildResult(
        image="t:1", image_id="sha256:abc", logs=()
    )
    assert client.api.streams[0].closed


def test_build_omits_platform_by_default(backend, client, tmp_path):
    backend.build_image(tmp_path, dockerfile(tmp_path), "t")
    call = client.api.build_calls[0]
    assert "platform" not in call
    assert call["nocache"] is False


def test_build_streams_lines_to_callback(backend, client, tmp_path):
    client.api.build_chunks = [
        {"stream": "Step 1/2 : FROM base\n"},
        {"status": "Pulling from library/base", "id": "latest"},
        {"status": "Downloading", "progress": "[=> ]", "id": "a1"},
        {"stream": " ---> 1f2e\nStep 2/2 : RUN ech"},
        {"stream": "o hi\nhi\r"},
        {"stream": "\nprogress 50%\rprogress 100%\n"},
        {"aux": {"ID": "sha256:abc"}},
        {"stream": "Successfully built 1f2e"},
    ]
    seen = []
    result = backend.build_image(
        tmp_path, dockerfile(tmp_path), "t", log_callback=seen.append
    )
    expected = [
        "Step 1/2 : FROM base",
        "latest: Pulling from library/base",
        " ---> 1f2e",
        "Step 2/2 : RUN echo hi",
        "hi",
        "progress 50%",
        "progress 100%",
        "Successfully built 1f2e",
    ]
    assert seen == expected
    assert result.logs == tuple(expected)
    assert result.image_id == "sha256:abc"


def test_build_reads_image_id_when_not_streamed(backend, client, tmp_path):
    client.images.add("t", image_id="sha256:fromget")
    result = backend.build_image(tmp_path, dockerfile(tmp_path), "t")
    assert result.image_id == "sha256:fromget"
    # A vanished image is not an error: the ID is simply unknown.
    other = backend.build_image(tmp_path, dockerfile(tmp_path), "gone")
    assert other.image_id is None


def test_build_error_carries_logs_and_output(backend, client, tmp_path):
    client.api.build_chunks = [
        {"stream": "Step 1/1 : RUN exit 3\n"},
        {"stream": " ---> Running in 9a\n"},
        ERROR_CHUNK,
        {"stream": "never read\n"},
    ]
    with pytest.raises(ImageBuildFailed) as info:
        backend.build_image(tmp_path, dockerfile(tmp_path), "t")
    logs = ("Step 1/1 : RUN exit 3", " ---> Running in 9a")
    assert info.value.logs == logs
    # Like a captured ``docker build``: the output, then the error.
    assert str(info.value) == "\n".join((*logs, ERROR_CHUNK["error"]))
    assert client.api.streams[0].closed


def test_build_error_with_callback_is_the_error_only(backend, client, tmp_path):
    client.api.build_chunks = [{"stream": "Step 1/1\n"}, ERROR_CHUNK]
    seen = []
    with pytest.raises(ImageBuildFailed) as info:
        backend.build_image(
            tmp_path, dockerfile(tmp_path), "t", log_callback=seen.append
        )
    assert str(info.value) == ERROR_CHUNK["error"]
    assert info.value.logs == ("Step 1/1",)
    assert seen == ["Step 1/1"]


def test_build_error_detail_without_error_field(backend, client, tmp_path):
    client.api.build_chunks = [{"errorDetail": {"message": "boom"}}]
    with pytest.raises(ImageBuildFailed, match="^boom$"):
        backend.build_image(
            tmp_path, dockerfile(tmp_path), "t", log_callback=print
        )


def test_build_request_rejected_by_daemon(backend, client, tmp_path):
    client.api.build_error = api_error("invalid platform")
    with pytest.raises(
        ImageBuildFailed, match="^Error response from daemon: invalid platform$"
    ):
        backend.build_image(tmp_path, dockerfile(tmp_path), "t")


def test_build_error_response_while_streaming(backend, client, tmp_path):
    # A non-chunked error response surfaces while iterating.
    client.api.build_chunks = [{"stream": "Sending context\n"}]
    client.api.build_stream_error = api_error(
        "Cannot locate specified Dockerfile: Dockerfile"
    )
    with pytest.raises(ImageBuildFailed) as info:
        backend.build_image(tmp_path, dockerfile(tmp_path), "t")
    assert str(info.value).endswith(
        "Error response from daemon: Cannot locate specified Dockerfile: "
        "Dockerfile"
    )
    assert info.value.logs == ("Sending context",)


def test_build_unreadable_context(backend, client, tmp_path):
    client.api.build_error = PermissionError(13, "Permission denied")
    with pytest.raises(
        ImageBuildFailed, match="unable to prepare context: .*denied"
    ):
        backend.build_image(tmp_path, dockerfile(tmp_path), "t")


def test_build_missing_context(backend, client, tmp_path):
    missing = tmp_path / "nope"
    with pytest.raises(
        ImageBuildFailed,
        match=f'unable to prepare context: path "{missing}" not found',
    ):
        backend.build_image(missing, missing / "Dockerfile", "t")
    assert client.api.build_calls == []


@pytest.mark.parametrize("interrupt", [KeyboardInterrupt, RuntimeError])
def test_build_interrupt_closes_stream(backend, client, tmp_path, interrupt):
    client.api.build_chunks = [{"stream": "a\n"}, {"stream": "b\n"}]

    def callback(line):
        raise interrupt("stop")

    with pytest.raises(interrupt):
        backend.build_image(
            tmp_path, dockerfile(tmp_path), "t", log_callback=callback
        )
    assert client.api.streams[0].closed


@pytest.mark.parametrize(
    "error",
    [
        requests.exceptions.ConnectionError("daemon gone"),
        urllib3.exceptions.ProtocolError("connection broken"),
    ],
    ids=["requests", "urllib3"],
)
def test_build_daemon_lost_is_unavailable(backend, client, tmp_path, error):
    client.api.build_stream_error = error
    with pytest.raises(BackendUnavailableError, match="Building image failed"):
        backend.build_image(tmp_path, dockerfile(tmp_path), "t")
    assert client.api.streams[0].closed


BUILDKIT_DOCKERFILE = (
    "FROM base\nRUN --mount=type=cache,target=/root/.cache pip install x\n"
)


def test_buildkit_dockerfile_goes_through_cli(backend, client, tmp_path):
    cli = mock.create_autospec(DockerCliBackend, instance=True)
    cli.available.return_value = True
    backend._cli = cli
    path = dockerfile(tmp_path, BUILDKIT_DOCKERFILE)
    backend.build_image(
        tmp_path,
        path,
        "t:1",
        no_cache=True,
        platform="linux/amd64",
        log_callback=print,
    )
    cli.build_image.assert_called_once_with(
        tmp_path,
        path,
        "t:1",
        no_cache=True,
        platform="linux/amd64",
        log_callback=print,
    )
    assert client.api.build_calls == []


def test_buildkit_dockerfile_builds_natively_without_cli(
    backend, client, tmp_path
):
    with mock.patch("shutil.which", return_value=None):
        backend.build_image(
            tmp_path, dockerfile(tmp_path, BUILDKIT_DOCKERFILE), "t"
        )
    assert len(client.api.build_calls) == 1


def test_plain_dockerfile_never_uses_cli(backend, client, tmp_path):
    cli = mock.create_autospec(DockerCliBackend, instance=True)
    cli.available.return_value = True
    backend._cli = cli
    backend.build_image(tmp_path, dockerfile(tmp_path), "t")
    cli.build_image.assert_not_called()


# ---------------------------------------------------------------------------
# push_image (native)
# ---------------------------------------------------------------------------


PUSH_CHUNKS = [
    {"status": "The push refers to repository [reg:5000/org/x]"},
    {"status": "Preparing", "progressDetail": {}, "id": "a1"},
    {
        "status": "Pushing",
        "progressDetail": {"current": 1, "total": 2},
        "progress": "[=>  ]",
        "id": "a1",
    },
    {"status": "Pushed", "progressDetail": {}, "id": "a1"},
    {"status": "1.0: digest: sha256:d1 size: 528"},
    {"progressDetail": {}, "aux": {"Tag": "1.0", "Digest": "sha256:d1"}},
]


def test_push_streams_status_and_reads_digest(backend, client):
    client.api.push_chunks = PUSH_CHUNKS
    client.images.add(
        "reg:5000/org/x:1.0",
        repo_digests=["reg:5000/org/x@sha256:d1", "other/x@sha256:d2"],
    )
    seen = []
    result = backend.push_image("reg:5000/org/x:1.0", log_callback=seen.append)

    assert client.api.push_calls == [
        ("reg:5000/org/x", {"tag": "1.0", "stream": True, "decode": True})
    ]
    assert seen == [
        "The push refers to repository [reg:5000/org/x]",
        "a1: Preparing",
        "a1: Pushed",
        "1.0: digest: sha256:d1 size: 528",
    ]
    assert result == ImagePushResult(
        image="reg:5000/org/x:1.0", digest="sha256:d1"
    )
    assert client.api.streams[0].closed


def test_push_streams_empty_or_none_status(backend, client):
    client.api.push_chunks = [
        {"status": None},
        {"status": ""},
        {"id": "layer1", "status": None},
    ]
    seen = []
    backend.push_image("demo:1", log_callback=seen.append)
    assert seen == ["", "", "layer1: "]


@pytest.mark.parametrize(
    ("tag", "repository"),
    [("demo", "demo"), ("localhost:5000/demo", "localhost:5000/demo")],
)
def test_push_without_version_pushes_latest(backend, client, tag, repository):
    backend.push_image(tag)
    assert client.api.push_calls[0][0] == repository
    assert client.api.push_calls[0][1]["tag"] == "latest"


@pytest.mark.parametrize(
    "digests",
    [None, [], ["no-at-sign"]],
    ids=["image-gone", "no-digests", "malformed"],
)
def test_push_digest_unavailable(backend, client, digests):
    if digests is not None:
        client.images.add("demo:1", repo_digests=digests)
    assert backend.push_image("demo:1").digest is None


def test_push_registry_error_chunk(backend, client):
    client.api.push_chunks = [
        PUSH_CHUNKS[0],
        {
            "errorDetail": {"message": "denied: requested access"},
            "error": "denied: requested access",
        },
    ]
    with pytest.raises(ImagePushFailed, match="^denied: requested access$"):
        backend.push_image("demo:1")
    assert client.api.streams[0].closed


def test_push_rejected_by_daemon(backend, client):
    client.api.push_error = docker.errors.ImageNotFound(
        "404", explanation="No such image: demo:1"
    )
    with pytest.raises(
        ImagePushFailed, match="^Error response from daemon: No such image"
    ):
        backend.push_image("demo:1")


def test_push_read_timeout(backend, client):
    client.api.push_stream_error = urllib3.exceptions.ReadTimeoutError(
        None, None, "Read timed out."
    )
    with pytest.raises(
        ImagePushFailed, match="No progress from the Docker daemon"
    ):
        backend.push_image("demo:1")
    assert client.api.streams[0].closed


def test_push_daemon_lost_is_unavailable(backend, client):
    client.api.push_error = requests.exceptions.ConnectionError("gone")
    with pytest.raises(BackendUnavailableError, match="Pushing image failed"):
        backend.push_image("demo:1")


def test_stream_logs_urllib3_errors_are_unavailable(backend, client):
    c = running(client)
    c.log_chunks = [b"a\n"]
    stream = backend.stream_logs(handle())
    assert next(stream) == "a"

    def broken():
        raise urllib3.exceptions.ProtocolError("connection broken")
        yield  # pragma: no cover

    c.stream._chunks = broken()
    with pytest.raises(BackendUnavailableError):
        next(stream)


# ---------------------------------------------------------------------------
# Managed label and diagnostics
# ---------------------------------------------------------------------------

CLASSIC_PROBE = [
    {"stream": "Step 1/1 : FROM scratch"},
    {"stream": "\n"},
    {"stream": " ---> \n"},
    {
        "error": "No image was generated. Is your Dockerfile empty?",
        "errorDetail": {"message": "No image was generated."},
    },
]


def test_foreground_container_carries_managed_label(backend, client):
    backend.run_foreground("t:1", ports={80: 8080})
    _, options = client.containers.create_calls[0]
    assert options["labels"] == {MANAGED_LABEL: MANAGED_LABEL_VALUE}
    assert options["ports"] == {"80/tcp": 8080}


def test_detached_containers_are_not_labelled(backend, client):
    backend.run_detached("t:1", name="v")
    assert "labels" not in client.containers.run_calls[0][1]


def test_describe_reports_daemon_and_classic_builder(backend, client):
    client.api.build_chunks = CLASSIC_PROBE
    info = backend.describe()
    assert info.name == "docker-sdk"
    assert info.client_version == docker.__version__
    assert info.server_version == "29.9.1"
    assert info.api_version == "1.56"
    assert info.classic_builder is True
    assert info.notes == ()
    assert info.daemon_error is None
    # The probe sends only ``FROM scratch`` and leaves nothing behind.
    (call,) = client.api.build_calls
    assert call["fileobj"].getvalue() == b"FROM scratch\n"
    assert call["forcerm"] is True
    assert client.api.streams[0].closed


def test_describe_without_classic_builder(backend, client):
    client.api.build_error = api_error("builder version 1 is not supported")
    info = backend.describe()
    assert info.classic_builder is False
    assert info.notes == (
        "classic builder: Error response from daemon: "
        "builder version 1 is not supported",
    )


def test_describe_unrecognised_probe_output(backend, client):
    client.api.build_chunks = [{"error": "something else"}]
    info = backend.describe()
    assert info.classic_builder is False
    assert info.notes == ("classic builder: something else",)


def test_describe_probe_losing_daemon(backend, client):
    client.api.build_error = requests.exceptions.ConnectionError("gone")
    info = backend.describe()
    assert info.classic_builder is None
    assert info.notes[0].startswith("classic builder: probe failed:")


def test_describe_daemon_unreachable(backend, client):
    client.version_error = requests.exceptions.ConnectionError("refused")
    info = backend.describe()
    assert info.daemon_error == "refused"
    assert info.server_version is None
    assert info.classic_builder is None


def test_connect_records_endpoint_source(monkeypatch):
    monkeypatch.setenv("DOCKER_HOST", "tcp://127.0.0.1:2375")
    with mock.patch("docker.from_env", return_value=MockClient()):
        backend = DockerSdkBackend()
    info = backend.describe()
    assert (info.endpoint, info.endpoint_source) == (
        "tcp://127.0.0.1:2375",
        "DOCKER_HOST",
    )

    monkeypatch.delenv("DOCKER_HOST")
    with mock.patch("docker.from_env", return_value=MockClient()):
        info = DockerSdkBackend().describe()
    assert info.endpoint_source == "default"
    assert info.endpoint.startswith(("unix://", "npipe://"))


def test_connect_records_context_endpoint(monkeypatch):
    monkeypatch.delenv("DOCKER_HOST", raising=False)
    monkeypatch.setenv("DOCKER_CONTEXT", "colima")
    sock = "unix:///Users/me/.colima/docker.sock"
    with (
        mock.patch(
            "docker.from_env",
            side_effect=docker.errors.DockerException("no default socket"),
        ),
        mock.patch(
            "tutorial_sdk.backend.docker_sdk._context_host", return_value=sock
        ),
        mock.patch("docker.DockerClient", return_value=MockClient()),
    ):
        info = DockerSdkBackend().describe()
    assert (info.endpoint, info.endpoint_source) == (sock, "context colima")


def test_injected_client_has_no_known_endpoint(backend):
    info = backend.describe()
    assert info.endpoint is None
    assert info.endpoint_source is None


def test_list_managed(backend, client):
    labelled = MockContainer("e" * 64, "quirky", status="running")
    labelled.attrs = {
        "Config": {"Image": "demo:latest"},
        "Created": "2026-10-22T14:53:16.123456789Z",
    }
    odd = MockContainer("d" * 64, "odd", status="exited")
    odd.attrs = {"Created": "yesterday"}
    client.containers.listed = [labelled, odd]
    found = backend.list_managed()
    assert client.containers.list_calls == [
        {
            "all": True,
            "filters": {"label": f"{MANAGED_LABEL}={MANAGED_LABEL_VALUE}"},
        }
    ]
    assert [(c.id, c.name, c.image, c.state) for c in found] == [
        ("e" * 64, "quirky", "demo:latest", "running"),
        ("d" * 64, "odd", "", "exited"),
    ]
    assert found[0].created.isoformat() == "2026-10-22T14:53:16+00:00"
    assert found[1].created is None


def test_list_managed_daemon_loss(backend, client):
    client.containers.get_error = requests.exceptions.ConnectionError("x")
    with pytest.raises(BackendUnavailableError):
        backend.list_managed()
