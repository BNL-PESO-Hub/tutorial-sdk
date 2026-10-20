"""docker-py container backend.

Implements ``ContainerBackend`` on top of the Docker Engine API via
the optional ``docker`` package (``pip install
'tutorial-sdk[docker]'``).  The package is imported lazily inside
``DockerSdkBackend.__init__`` so that importing this module never
requires it.

The container runtime surface (``run_detached``, ``is_running``,
``wait_running``, ``logs``, ``exec``, ``remove``) plus ``copy_in`` /
``stream_logs`` are implemented natively via docker-py.
``build_image`` and ``run_foreground`` delegate to an internal
``DockerCliBackend``, ensuring this backend can be used across all
operations while preserving CLI streaming and interactive TTY semantics.

All ``docker.errors.*`` and ``requests`` exceptions are translated
into the backend error hierarchy here; nothing above this module
ever sees a docker-py type.
"""

from __future__ import annotations

import codecs
import io
import os
import tarfile
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any, Iterator

from ..errors import BackendError, BackendUnavailableError
from .base import (
    ContainerHandle,
    ExecResult,
    ImageBuildResult,
    LogCallback,
    RunExit,
    observe_running,
)
from .docker_cli import DockerCliBackend
from .errors import ContainerStartFailed, CopyFailed

if TYPE_CHECKING:
    from docker import DockerClient
    from docker.models.containers import Container

#: Exit code reported when the daemon rejects an exec request.  It
#: mirrors the ``docker exec`` CLI, which exits 1 on daemon errors
#: such as "container is not running".
_DAEMON_ERROR_EXIT_CODE = 1


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

        self._errors = docker.errors
        self._request_error = requests.exceptions.RequestException
        self._cli = DockerCliBackend()

        if client is not None:
            self._client = client
            return
        try:
            self._client = _connect(docker)
            self._client.ping()
        except (docker.errors.DockerException, self._request_error) as exc:
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
        except (self._errors.DockerException, self._request_error):
            return False

    # -- delegated to the CLI backend ----------------------------------------

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
        """Build an image (delegated to ``DockerCliBackend``)."""
        return self._delegate("image builds").build_image(
            context,
            dockerfile,
            tag,
            no_cache=no_cache,
            platform=platform,
            log_callback=log_callback,
        )

    def run_foreground(
        self,
        image: str,
        *,
        ports: dict[int, int] | None = None,
        interactive_shell: bool = False,
    ) -> RunExit:
        """Run in the foreground (delegated to ``DockerCliBackend``)."""
        return self._delegate("foreground runs").run_foreground(
            image,
            ports=ports,
            interactive_shell=interactive_shell,
        )

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

            port_map = {
                f"{container_port}/tcp": host_port
                for container_port, host_port in (ports or {}).items()
            }
            try:
                started = self._client.containers.run(
                    image,
                    detach=True,
                    name=name,
                    ports=port_map or None,
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
