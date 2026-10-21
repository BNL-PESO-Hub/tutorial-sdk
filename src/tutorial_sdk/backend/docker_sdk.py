"""docker-py container backend.

Implements ``ContainerBackend`` on top of the Docker Engine API via
the optional ``docker`` package (``pip install
'tutorial-sdk[docker]'``).  The package is imported lazily inside
``DockerSdkBackend.__init__`` so that importing this module never
requires it.

Image builds and pushes (``build_image``, ``push_image``), the
container runtime surface (``run_detached``, ``is_running``,
``wait_running``, ``logs``, ``exec``, ``remove``), foreground runs
(``run_foreground``), and ``copy_in`` / ``stream_logs`` are
implemented natively via docker-py.  Two cases go through an internal
``DockerCliBackend`` instead:

* interactive shell runs (``run_foreground(..., interactive_shell=True)``),
  because docker-py has no terminal attach;
* builds of Dockerfiles that need BuildKit (see
  :func:`requires_buildkit`), because the Engine API build endpoint
  that docker-py uses runs the classic builder.  When the ``docker``
  binary is missing, such builds are still attempted natively.

All ``docker.errors.*``, ``requests``, and ``urllib3`` exceptions are
translated into the backend error hierarchy here; nothing above this
module ever sees a docker-py type.
"""

from __future__ import annotations

import codecs
import io
import os
import re
import signal
import sys
import tarfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any, Callable, Iterator, TextIO

from ..errors import BackendError, BackendUnavailableError
from .base import (
    ContainerHandle,
    ExecResult,
    ImageBuildResult,
    ImagePushResult,
    LogCallback,
    RunExit,
    observe_running,
)
from .docker_cli import DockerCliBackend, foreground_command
from .errors import (
    ContainerStartFailed,
    CopyFailed,
    ImageBuildFailed,
    ImagePushFailed,
)

if TYPE_CHECKING:
    from docker import DockerClient
    from docker.models.containers import Container

#: Exit code reported when the daemon rejects an exec request.  It
#: mirrors the ``docker exec`` CLI, which exits 1 on daemon errors
#: such as "container is not running".
_DAEMON_ERROR_EXIT_CODE = 1

#: Exit code of a foreground run the daemon refused to create or
#: start (missing image, port already allocated, ...).  ``docker run``
#: exits 125 when the error comes from the daemon itself.
_RUN_ERROR_EXIT_CODE = 125

#: Seconds a container may take to exit after the first forwarded
#: signal before it is killed (the ``docker stop`` default).
_STOP_GRACE = 10.0

#: Seconds to keep draining container output after it exits.
_DRAIN_TIMEOUT = 5.0

#: Interval at which the foreground run checks for signals and exit.
_POLL_INTERVAL = 0.1

#: Signals forwarded to a foreground container, like ``docker run``.
_FORWARDED_SIGNALS = (signal.SIGINT, signal.SIGTERM)

#: ``RUN``/``COPY``/``ADD`` flags that only BuildKit understands.
_BUILDKIT_FLAGS = frozenset(
    {
        "checksum",
        "chmod",
        "exclude",
        "keep-git-dir",
        "link",
        "mount",
        "network",
        "parents",
        "secret",
        "security",
        "ssh",
        "unpack",
    }
)

#: Parser directives (``# syntax=...``, ``# check=...``).
_BUILDKIT_DIRECTIVE = re.compile(r"#\s*(?:syntax|check)\s*=", re.IGNORECASE)

#: Heredoc opening (``<<EOF``, ``<<-"EOF"``) in ``RUN``/``COPY``/``ADD``.
_HEREDOC = re.compile(r"<<-?\s*[\"']?[A-Za-z_][A-Za-z0-9_]*")

#: Automatic platform build arguments defined only by BuildKit.
_BUILDKIT_ARGS = re.compile(
    r"\$\{?(?:BUILD|TARGET)(?:PLATFORM|OS|ARCH|VARIANT)\b"
)

_LINE_BREAK = re.compile(r"\r\n|\r|\n")


def requires_buildkit(dockerfile: str) -> bool:
    """Return ``True`` if a Dockerfile uses BuildKit-only features.

    docker-py builds through the Engine API's classic builder, which
    rejects or misreads parser directives (``# syntax=``,
    ``# check=``), BuildKit-only ``RUN``/``COPY``/``ADD`` flags
    (``--mount``, ``--link``, ``--chmod``, ...), heredocs, and the
    automatic platform arguments (``$BUILDPLATFORM``,
    ``$TARGETARCH``, ...).  The check is deliberately conservative:
    a false positive only routes the build through the Docker CLI.

    Args:
        dockerfile: Dockerfile text.

    Returns:
        Whether the Dockerfile needs BuildKit.
    """
    raw_lines = dockerfile.splitlines()

    # Parser directives may only appear before anything else.
    for line in raw_lines:
        stripped = line.strip()
        if not stripped.startswith("#"):
            break
        if _BUILDKIT_DIRECTIVE.match(stripped):
            return True

    non_comment = "\n".join(
        line for line in raw_lines if not line.lstrip().startswith("#")
    )
    lines = non_comment.replace("\\\r\n", " ").replace("\\\n", " ").splitlines()

    for line in lines:
        words = line.split()
        if not words:
            continue
        if words[0].upper() in {"RUN", "COPY", "ADD"}:
            for word in words[1:]:
                if not word.startswith("--"):
                    break
                flag = word[2:].split("=", 1)[0].lower()
                if flag in _BUILDKIT_FLAGS:
                    return True
            if _HEREDOC.search(line):
                return True
        if _BUILDKIT_ARGS.search(line):
            return True
    return False


class _LineCollector:
    """Turn streamed build/push output into lines.

    Lines are collected for results and error reports and, when a
    callback is given, forwarded to it as they complete.  As in the
    Docker CLI's text output, carriage returns, line feeds, and CRLF
    pairs all end a line.
    """

    def __init__(self, callback: LogCallback | None) -> None:
        self.lines: list[str] = []
        self._callback = callback
        self._pending = ""

    def feed(self, text: str) -> None:
        """Add raw output text, emitting every completed line."""
        buffer = self._pending + text
        # Keep a trailing CR: it may be the first half of a CRLF.
        held = "\r" if buffer.endswith("\r") else ""
        if held:
            buffer = buffer[:-1]
        *complete, rest = _LINE_BREAK.split(buffer)
        self._pending = rest + held
        for line in complete:
            self._emit(line)

    def status(self, chunk: dict[str, Any]) -> None:
        """Emit a status message, skipping progress-bar updates."""
        if chunk.get("progress"):
            return
        status = str(chunk.get("status") or "")
        ident = chunk.get("id")
        self.flush()
        self._emit(f"{ident}: {status}" if ident else status)

    def flush(self) -> None:
        """Emit any incomplete trailing line."""
        rest, self._pending = self._pending.rstrip("\r"), ""
        if rest:
            self._emit(rest)

    def _emit(self, line: str) -> None:
        self.lines.append(line)
        if self._callback is not None:
            self._callback(line)


def _stream_error(chunk: dict[str, Any]) -> str | None:
    """Return the error message of a build/push stream chunk, if any."""
    if "error" not in chunk and "errorDetail" not in chunk:
        return None
    detail = chunk.get("errorDetail") or {}
    message = chunk.get("error") or detail.get("message")
    return str(message).strip() if message else "unknown error"


def _close(stream: Any) -> None:
    """Close a docker-py response stream, if it can be closed."""
    close = getattr(stream, "close", None)
    if close is not None:
        close()


def _daemon_message(exc: Exception) -> str:
    """Render a docker-py error the way the Docker CLI prints it."""
    explanation = getattr(exc, "explanation", None)
    if explanation:
        if isinstance(explanation, bytes):
            explanation = explanation.decode("utf-8", "replace")
        return f"Error response from daemon: {explanation}".strip()
    return str(exc).strip()


def _decode(data: bytes | None) -> str:
    """Decode an exec/log byte stream, tolerating bad UTF-8."""
    return (data or b"").decode("utf-8", "replace").strip()


def _port_bindings(ports: dict[int, int] | None) -> dict[str, int]:
    """Translate ``{container: host}`` mappings for docker-py."""
    return {
        f"{container_port}/tcp": host_port
        for container_port, host_port in (ports or {}).items()
    }


def _connect(docker: Any) -> DockerClient:
    """Create a client from the environment or the active context.

    ``docker.from_env()`` honours ``DOCKER_HOST`` but, unlike the
    CLI, ignores Docker contexts.  Docker Desktop, Colima, and
    OrbStack often expose the daemon only through a context socket,
    so when ``DOCKER_HOST`` is unset and the default socket fails,
    retry with the current context's (non-TLS) endpoint.
    """
    try:
        return docker.from_env()
    except docker.errors.DockerException:
        if os.environ.get("DOCKER_HOST"):
            raise
        host = _context_host()
        if not host:
            raise
        return docker.DockerClient(base_url=host)


def _context_host() -> str | None:
    """Return the current Docker context's plain endpoint, if any.

    Only non-TLS endpoints that differ from the default socket are
    returned; anything else is left to ``DOCKER_HOST``.
    """
    try:
        from docker.context import ContextAPI

        context = ContextAPI.get_context(
            os.environ.get("DOCKER_CONTEXT") or None
        )
        default = ContextAPI.DEFAULT_CONTEXT.Host
    except Exception:  # best-effort discovery only
        return None
    if context is None or context.TLSConfig:
        return None
    host = context.Host
    return host if host and host != default else None


def _tar_single(src: Path, arcname: str) -> bytes:
    """Pack ``src`` as ``arcname`` into an in-memory tar archive.

    Ownership is reset to ``root:root``, matching ``docker cp``.
    """

    def _as_root(info: tarfile.TarInfo) -> tarfile.TarInfo:
        info.uid = info.gid = 0
        info.uname = info.gname = "root"
        return info

    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        archive.add(str(src), arcname=arcname, filter=_as_root)
    return buffer.getvalue()


@contextmanager
def _trap_signals() -> Iterator[list[int]]:
    """Record SIGINT/SIGTERM instead of acting on them, for the duration.

    Yields the list the received signal numbers are appended to.  The
    handler only records, so a signal can never interrupt the run
    half-way through a step; the caller polls the list instead.

    Handlers can only be installed from the main thread; elsewhere
    nothing is installed (signals are delivered to the main thread
    only).  Signals the process ignores (``SIG_IGN``) or whose handler
    was not installed from Python are left alone.  Previous handlers
    are restored on exit.
    """
    received: list[int] = []
    if threading.current_thread() is not threading.main_thread():
        yield received
        return

    def _record(signum: int, frame: Any) -> None:
        received.append(signum)

    previous = {}
    try:
        for signum in _FORWARDED_SIGNALS:
            handler = signal.getsignal(signum)
            if handler is not None and handler is not signal.SIG_IGN:
                previous[signum] = handler
                signal.signal(signum, _record)
        yield received
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)


class _Worker(threading.Thread):
    """Daemon thread that records its target's result or exception."""

    def __init__(self, target: Callable[[], Any], name: str) -> None:
        super().__init__(name=name, daemon=True)
        self._target_fn = target
        self.result: Any = None
        self.error: BaseException | None = None

    def run(self) -> None:
        try:
            self.result = self._target_fn()
        except BaseException as exc:  # re-raised by the caller
            self.error = exc


def _byte_writer(sink: TextIO) -> Callable[..., None]:
    """Return a writer forwarding raw container output to ``sink``.

    Bytes go to the underlying binary buffer when there is one, so
    output is passed through unchanged (partial lines, progress
    bars); otherwise they are decoded incrementally.
    """
    buffer = getattr(sink, "buffer", None)
    if buffer is not None:
        sink.flush()

        def _write_bytes(data: bytes, final: bool = False) -> None:
            if data:
                buffer.write(data)
                buffer.flush()

        return _write_bytes

    decoder = codecs.getincrementaldecoder("utf-8")("replace")

    def _write_text(data: bytes, final: bool = False) -> None:
        text = decoder.decode(data, final=final)
        if text:
            sink.write(text)
            sink.flush()

    return _write_text


def _pump(stream: Iterator[bytes], sink: TextIO) -> None:
    """Copy a container log stream to ``sink`` until it ends."""
    write = _byte_writer(sink)
    for chunk in stream:
        write(chunk)
    write(b"", final=True)


def _drain(pumps: list[_Worker], streams: list[Any]) -> None:
    """Let output pumps finish (bounded), then close their streams.

    Closing a stream unblocks a pump still reading from it.
    """
    deadline = time.monotonic() + _DRAIN_TIMEOUT
    try:
        for pump in pumps:
            pump.join(max(0.0, deadline - time.monotonic()))
    finally:
        for stream in streams:
            stream.close()


def _report_run_error(exc: Exception) -> int:
    """Print a daemon error to stderr, as ``docker run`` does.

    Returns:
        ``docker run``'s exit code for daemon errors (125).
    """
    print(_daemon_message(exc), file=sys.stderr)
    return _RUN_ERROR_EXIT_CODE


class DockerSdkBackend:
    """Container backend backed by docker-py (Docker Engine API)."""

    name = "docker-sdk"

    def __init__(self, client: DockerClient | None = None) -> None:
        """Connect to the Docker daemon.

        Args:
            client: Pre-built ``docker.DockerClient``.  When omitted,
                one is created from the environment and the daemon
                is pinged.

        Raises:
            ImportError: If the optional ``docker`` package is not
                installed (``get_backend`` reports it as
                ``BackendUnavailableError``).
            BackendUnavailableError: If the daemon is unreachable.
        """
        import docker
        import requests
        import urllib3

        self._errors = docker.errors
        # Streamed responses are read through urllib3 directly, so its
        # errors can surface without a ``requests`` wrapper.
        self._request_error = (
            requests.exceptions.RequestException,
            urllib3.exceptions.HTTPError,
        )
        self._read_timeout = (
            requests.exceptions.ReadTimeout,
            urllib3.exceptions.ReadTimeoutError,
        )
        self._parse_repository_tag = docker.utils.parse_repository_tag
        self._cli = DockerCliBackend()

        if client is not None:
            self._client = client
            return
        try:
            self._client = _connect(docker)
            self._client.ping()
        except (docker.errors.DockerException, *self._request_error) as exc:
            raise BackendUnavailableError(
                f"Cannot connect to the Docker daemon via docker-py: "
                f"{exc}. Check that Docker is running (and DOCKER_HOST "
                "if set), or use the 'docker-cli' backend."
            ) from exc

    # -- internal helpers --------------------------------------------------

    @contextmanager
    def _guard(self, action: str) -> Iterator[None]:
        """Translate leftover docker-py/requests errors at the boundary.

        Methods handle the errors that carry contract meaning
        (``NotFound``, ``APIError``) themselves; anything escaping
        is mapped here so no docker-py type leaks out.
        """
        try:
            yield
        except self._errors.DockerException as exc:
            raise BackendError(
                f"{action} failed: {_daemon_message(exc)}"
            ) from exc
        except self._request_error as exc:
            raise BackendUnavailableError(
                f"{action} failed: cannot reach the Docker daemon: {exc}"
            ) from exc

    def _container(self, container: ContainerHandle) -> Container:
        """Look up the live container object for a handle."""
        return self._client.containers.get(container.id)

    def _delegate(self, action: str) -> DockerCliBackend:
        """Return the CLI backend used for delegated methods.

        Raises:
            BackendUnavailableError: If the ``docker`` binary needed
                for delegation is not on PATH.
        """
        if not self._cli.available():
            raise BackendUnavailableError(
                f"The 'docker-sdk' backend performs {action} through "
                "the Docker CLI, but the 'docker' binary was not found "
                "on PATH. Install the Docker CLI and make sure it is on PATH."
            )
        return self._cli

    # -- availability --------------------------------------------------------

    def available(self) -> bool:
        """Return ``True`` if the Docker daemon answers a ping."""
        try:
            return bool(self._client.ping())
        except (self._errors.DockerException, *self._request_error):
            return False

    # -- images --------------------------------------------------------------

    def build_image(
        self,
        context: Path,
        dockerfile: Path,
        tag: str,
        *,
        no_cache: bool = False,
        platform: str | None = None,
        log_callback: LogCallback | None = None,
    ) -> ImageBuildResult:
        """Build an image through the Engine API.

        The build output is split into lines, collected in the result,
        and forwarded to ``log_callback`` as it arrives.  The image ID
        (``sha256:...``) is reported in ``ImageBuildResult.image_id``.

        A failed build raises ``ImageBuildFailed`` carrying the
        collected lines.  Its message is the builder's error; without
        a ``log_callback`` the build output precedes it, as in the
        message of a captured ``docker build``.

        Dockerfiles that need BuildKit (:func:`requires_buildkit`) are
        built by ``DockerCliBackend`` when the ``docker`` binary is on
        PATH, since the Engine API endpoint runs the classic builder.

        Raises:
            ImageBuildFailed: If the build fails.
            BackendUnavailableError: If the daemon is unreachable.
            BackendError: For other unexpected daemon errors.
        """
        if self._needs_cli_build(dockerfile):
            return self._cli.build_image(
                context,
                dockerfile,
                tag,
                no_cache=no_cache,
                platform=platform,
                log_callback=log_callback,
            )

        if not Path(context).is_dir():
            raise ImageBuildFailed(
                f'unable to prepare context: path "{context}" not found'
            )
        options: dict[str, Any] = {
            # Absolute paths: docker-py resolves a relative Dockerfile
            # against the context, the CLI against the working dir.
            "path": os.path.abspath(context),
            "dockerfile": os.path.abspath(dockerfile),
            "tag": tag,
            "nocache": no_cache,
            # Remove intermediate containers even when a step fails,
            # as BuildKit (the CLI's builder) leaves none behind.
            "rm": True,
            "forcerm": True,
            "decode": True,
        }
        if platform:
            options["platform"] = platform

        output = _LineCollector(log_callback)
        error: str | None = None
        image_id: str | None = None
        with self._guard("Building image"):
            try:
                stream = self._client.api.build(**options)
            except self._errors.APIError as exc:
                raise ImageBuildFailed(_daemon_message(exc)) from exc
            except OSError as exc:  # unreadable context or Dockerfile
                raise ImageBuildFailed(
                    f"unable to prepare context: {exc}"
                ) from exc
            try:
                for chunk in stream:
                    error = _stream_error(chunk)
                    if error is not None:
                        break
                    if "stream" in chunk:
                        output.feed(str(chunk["stream"]))
                    elif "status" in chunk:
                        output.status(chunk)
                    aux = chunk.get("aux")
                    if isinstance(aux, dict) and aux.get("ID"):
                        image_id = str(aux["ID"])
            except self._errors.APIError as exc:
                # The daemon rejected the request (e.g. a missing
                # Dockerfile or an unknown platform).
                error = _daemon_message(exc)
            finally:
                # Also on interrupts: closing the stream drops the
                # connection, which cancels the build on the daemon.
                _close(stream)
            output.flush()
            if error is None and image_id is None:
                image_id = self._image_id(tag)

        logs = tuple(output.lines)
        if error is not None:
            message = error if log_callback else "\n".join((*logs, error))
            raise ImageBuildFailed(message, logs=logs)
        return ImageBuildResult(image=tag, image_id=image_id, logs=logs)

    def _needs_cli_build(self, dockerfile: str | Path) -> bool:
        """Return ``True`` if a build should go through the Docker CLI."""
        try:
            text = Path(dockerfile).read_text(errors="replace")
        except OSError:
            return False  # reported by the native build
        return requires_buildkit(text) and self._cli.available()

    def _image_id(self, tag: str) -> str | None:
        """Return the ID of the local image ``tag``, if it exists."""
        try:
            return self._client.images.get(tag).id
        except self._errors.NotFound:
            return None

    def push_image(
        self,
        tag: str,
        *,
        log_callback: LogCallback | None = None,
    ) -> ImagePushResult:
        """Push an image through the Engine API.

        Credentials come from the Docker configuration (including
        credential helpers), as for ``docker push``.  Status lines
        (one per layer and step, without progress-bar updates) are
        forwarded to ``log_callback``.  The digest is then read from
        the image's first repository digest, as the CLI backend does.

        Raises:
            ImagePushFailed: If the daemon or the registry rejects the
                push, or the daemon reports no progress within the
                client timeout.
            BackendUnavailableError: If the daemon is unreachable.
            BackendError: For other unexpected daemon errors.
        """
        repository, version = self._parse_repository_tag(tag)
        output = _LineCollector(log_callback)
        error: str | None = None
        with self._guard("Pushing image"):
            try:
                stream = self._client.api.push(
                    repository,
                    tag=version or "latest",
                    stream=True,
                    decode=True,
                )
                try:
                    for chunk in stream:
                        error = _stream_error(chunk)
                        if error is not None:
                            break
                        if "status" in chunk:
                            output.status(chunk)
                finally:
                    _close(stream)
            except self._errors.APIError as exc:
                raise ImagePushFailed(_daemon_message(exc)) from exc
            except self._read_timeout as exc:
                raise ImagePushFailed(
                    f"No progress from the Docker daemon while pushing "
                    f"{tag}: {exc}"
                ) from exc
        if error is not None:
            raise ImagePushFailed(error)
        return ImagePushResult(image=tag, digest=self._repo_digest(tag))

    def _repo_digest(self, tag: str) -> str | None:
        """Return the digest of the image's first repository digest.

        Mirrors the CLI backend's ``docker inspect --format
        '{{index .RepoDigests 0}}'``: any lookup failure gives ``None``.
        """
        with self._guard("Inspecting image"):
            try:
                attrs = self._client.images.get(tag).attrs
            except self._errors.APIError:
                return None
        digests = attrs.get("RepoDigests") or []
        if not digests or "@" not in digests[0]:
            return None
        return str(digests[0]).split("@", 1)[1]

    # -- foreground runs -----------------------------------------------------

    def run_foreground(
        self,
        image: str,
        *,
        ports: dict[int, int] | None = None,
        interactive_shell: bool = False,
    ) -> RunExit:
        """Run a container until it exits, streaming its output.

        Mirrors ``docker run --rm -p HOST:CONTAINER IMAGE``: a missing
        image is pulled; the container's stdout and stderr are copied
        to ``sys.stdout`` and ``sys.stderr``; the container is removed
        afterwards in every case.  A daemon error before the container
        runs is printed to ``sys.stderr`` and reported as exit code
        125, like ``docker run``.

        When called from the main thread, SIGINT and SIGTERM are
        forwarded to the container, which then has 10 seconds to exit
        before it is killed; a second signal kills it at once.  The
        container's exit code is returned either way.

        Interactive shells (``interactive_shell=True``) need a
        terminal attach, which docker-py does not provide, and are
        delegated to ``DockerCliBackend``.

        Raises:
            BackendUnavailableError: If the daemon becomes unreachable,
                or an interactive shell is requested and the
                ``docker`` binary is not on PATH.
            BackendError: For other unexpected daemon errors.
        """
        if interactive_shell:
            return self._delegate("interactive shell runs").run_foreground(
                image,
                ports=ports,
                interactive_shell=True,
            )

        command = tuple(foreground_command(image, ports))
        with self._guard("Running container"):
            try:
                container = self._create(image, ports)
            except self._errors.APIError as exc:
                returncode = _report_run_error(exc)
            else:
                try:
                    returncode = self._run_to_exit(container)
                finally:
                    self._discard(container)
        return RunExit(image=image, command=command, returncode=returncode)

    def _create(
        self,
        image: str,
        ports: dict[int, int] | None,
    ) -> Container:
        """Create (not start) a container, pulling a missing image."""
        bindings = _port_bindings(ports)
        options = {"ports": bindings} if bindings else {}
        try:
            return self._client.containers.create(image, **options)
        except self._errors.ImageNotFound:
            print(f"Unable to find image '{image}' locally", file=sys.stderr)
            self._client.images.pull(image)
            return self._client.containers.create(image, **options)

    def _run_to_exit(self, container: Container) -> int:
        """Start ``container``, stream its output, and wait for exit.

        Output is read through two followed log streams (stdout and
        stderr) rather than ``attach``: they replay from the start, so
        nothing is lost before they open, and docker-py disables the
        client read timeout on them, so a container that stays silent
        longer than that timeout keeps streaming.
        """
        waiter = _Worker(container.wait, "tutorial-sdk-wait")
        streams: list[Any] = []
        pumps: list[_Worker] = []
        with _trap_signals() as received:
            try:
                container.start()
            except self._errors.APIError as exc:  # e.g. port in use
                return _report_run_error(exc)
            try:
                for want_stdout, sink in (
                    (True, sys.stdout),
                    (False, sys.stderr),
                ):
                    try:
                        stream = container.logs(
                            stdout=want_stdout,
                            stderr=not want_stdout,
                            stream=True,
                            follow=True,
                        )
                    except self._errors.APIError as exc:
                        # e.g. a logging driver that cannot be read;
                        # warn once (stdout is opened first).
                        if want_stdout:
                            print(
                                f"Container output unavailable: "
                                f"{_daemon_message(exc)}",
                                file=sys.stderr,
                            )
                        continue
                    streams.append(stream)
                    pump = _Worker(
                        lambda s=stream, k=sink: _pump(s, k),
                        "tutorial-sdk-output",
                    )
                    pump.start()
                    pumps.append(pump)
                waiter.start()
                self._supervise(container, waiter, received)
            finally:
                _drain(pumps, streams)

        if waiter.error is not None:
            raise waiter.error
        status = waiter.result or {}
        code = status.get("StatusCode")
        return int(code) if code is not None else _RUN_ERROR_EXIT_CODE

    def _supervise(
        self,
        container: Container,
        waiter: _Worker,
        received: list[int],
    ) -> None:
        """Wait for ``waiter``, forwarding signals and escalating.

        The first signal in ``received`` is forwarded as-is; a second
        one, or the container outliving the grace period, kills it.
        """
        forwarded_at: float | None = None
        killed = False
        while waiter.is_alive():
            if received and not killed:
                signum = received.pop(0)
                if forwarded_at is None:
                    forwarded_at = time.monotonic()
                    print(
                        "Stopping container (interrupt again to force)...",
                        file=sys.stderr,
                    )
                    self._signal(container, signal.Signals(signum).name)
                else:
                    killed = True
                    self._signal(container, None)
                continue
            if (
                forwarded_at is not None
                and not killed
                and time.monotonic() - forwarded_at >= _STOP_GRACE
            ):
                killed = True
                self._signal(container, None)
            waiter.join(_POLL_INTERVAL)

    def _signal(self, container: Container, name: str | None) -> None:
        """Send ``name`` (``None`` = SIGKILL), ignoring a gone container."""
        try:
            if name is None:
                container.kill()
            else:
                container.kill(signal=name)
        except self._errors.APIError:
            pass  # already exited or removed

    def _discard(self, container: Container) -> None:
        """Remove a foreground container, ignoring daemon errors."""
        try:
            container.remove(force=True)
        except self._errors.APIError:
            pass

    # -- container lifecycle -------------------------------------------------

    def run_detached(
        self,
        image: str,
        *,
        name: str | None = None,
        ports: dict[int, int] | None = None,
        remove: bool = False,
    ) -> ContainerHandle:
        """Start a container in the background via the Engine API."""
        errors = self._errors
        with self._guard("Starting container"):
            if name:
                # Replace any stale container with the same name
                # (the CLI backend's blind ``docker rm -f``).
                try:
                    self._client.containers.get(name).remove(force=True)
                except errors.NotFound:
                    pass
                except errors.APIError:
                    pass  # e.g. removal already in progress

            try:
                started = self._client.containers.run(
                    image,
                    detach=True,
                    name=name,
                    ports=_port_bindings(ports) or None,
                    auto_remove=remove,
                )
            except errors.APIError as exc:
                # Includes ImageNotFound and failed implicit pulls.
                raise ContainerStartFailed(_daemon_message(exc)) from exc

        return ContainerHandle(
            id=started.id,
            name=name or started.id[:12],
            backend=self.name,
        )

    def is_running(self, container: ContainerHandle) -> bool:
        """Return ``True`` if the container's state is ``running``."""
        with self._guard("Inspecting container"):
            try:
                return self._container(container).status == "running"
            except self._errors.NotFound:
                return False

    def wait_running(
        self,
        container: ContainerHandle,
        timeout: float = 2.0,
        poll: float = 0.25,
    ) -> bool:
        """Poll container state for the observation window."""
        return observe_running(
            lambda: self.is_running(container), timeout, poll
        )

    def logs(self, container: ContainerHandle) -> str:
        """Return container logs, preferring stderr over stdout."""
        with self._guard("Reading container logs"):
            try:
                target = self._container(container)
                stderr = target.logs(stdout=False, stderr=True)
                if _decode(stderr):
                    return _decode(stderr)
                return _decode(target.logs(stdout=True, stderr=False))
            except self._errors.NotFound as exc:
                # The CLI prints the daemon error on stderr, which
                # its stderr-first rule then returns.
                return _daemon_message(exc)

    def exec(
        self,
        container: ContainerHandle,
        command: list[str],
    ) -> ExecResult:
        """Run a command in the container, demultiplexing streams."""
        with self._guard("Executing in container"):
            try:
                result = self._container(container).exec_run(
                    command, demux=True
                )
            except self._errors.APIError as exc:
                # Not found / not running: the CLI exits 1 with the
                # daemon message on stderr.
                return ExecResult(
                    exit_code=_DAEMON_ERROR_EXIT_CODE,
                    output=_daemon_message(exc),
                )

        stdout, stderr = result.output or (None, None)
        exit_code = result.exit_code if result.exit_code is not None else 0
        if exit_code == 0:
            output = _decode(stdout)
        else:
            output = _decode(stderr) or _decode(stdout)
        return ExecResult(exit_code=exit_code, output=output)

    def remove(
        self,
        container: ContainerHandle,
        *,
        force: bool = True,
    ) -> None:
        """Remove the container, ignoring absence and daemon errors."""
        with self._guard("Removing container"):
            try:
                self._container(container).remove(force=force)
            except self._errors.APIError:
                # NotFound included.  The CLI backend ignores every
                # ``docker rm`` failure; keep the same semantics.
                pass

    # -- file copy and log streaming -----------------------------------------

    def copy_in(
        self,
        container: ContainerHandle,
        src: Path,
        dest: str,
    ) -> None:
        """Copy a host path to ``dest`` via ``put_archive``."""
        src = Path(src)
        if not src.exists():
            raise CopyFailed(f"lstat {src}: no such file or directory")
        target = PurePosixPath(dest)
        if not target.name:
            raise CopyFailed(f"Invalid destination path: {dest!r}")
        data = _tar_single(src, target.name)

        with self._guard("Copying into container"):
            try:
                self._container(container).put_archive(str(target.parent), data)
            except self._errors.APIError as exc:
                raise CopyFailed(_daemon_message(exc)) from exc

    def stream_logs(self, container: ContainerHandle) -> Iterator[str]:
        """Follow container logs line by line (stdout + stderr)."""
        with self._guard("Streaming container logs"):
            stream = self._container(container).logs(stream=True, follow=True)
        decoder = codecs.getincrementaldecoder("utf-8")("replace")
        pending = ""
        try:
            with self._guard("Streaming container logs"):
                for chunk in stream:
                    pending += decoder.decode(chunk)
                    *lines, pending = pending.split("\n")
                    yield from lines
                pending += decoder.decode(b"", final=True)
            if pending:
                yield pending
        finally:
            stream.close()
