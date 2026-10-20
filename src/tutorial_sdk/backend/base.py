"""Container backend abstraction.

Defines the ``ContainerBackend`` protocol that all execution
backends implement, the small result dataclasses exchanged across
that boundary, and the backend error hierarchy.

Higher layers (``DockerBuilder``, ``ContainerValidator``,
``RuntimeLauncher``) depend only on this module; concrete backends
(such as ``DockerCliBackend`` and ``DockerSdkBackend``) live behind
it.  No backend-specific types (``subprocess`` results, docker-py
objects) may leak through this interface.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator, Protocol, runtime_checkable

#: Callback invoked with each build log line as it is produced.
LogCallback = Callable[[str], None]


@dataclass(frozen=True)
class ImageBuildResult:
    """Outcome of a successful image build."""

    image: str
    image_id: str | None = None
    logs: tuple[str, ...] = ()


@dataclass(frozen=True)
class ContainerHandle:
    """Opaque reference to a container managed by a backend."""

    id: str
    name: str
    backend: str


@dataclass(frozen=True)
class ExecResult:
    """Outcome of a command executed inside a container.

    ``output`` carries stdout on success and stderr (falling back
    to stdout) on failure, matching the historical reporting
    behavior of ``ContainerValidator``.
    """

    exit_code: int
    output: str


@dataclass(frozen=True)
class RunExit:
    """Outcome of a foreground (blocking) container run."""

    image: str
    command: tuple[str, ...]
    returncode: int


@runtime_checkable
class ContainerBackend(Protocol):
    """Protocol implemented by container execution backends."""

    name: str

    def available(self) -> bool:
        """Return ``True`` if this backend can run on this host."""
        ...

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
        """Build an image from ``context`` using ``dockerfile``.

        Args:
            context: Build context directory.
            dockerfile: Path to the Dockerfile.
            tag: Image tag to apply.
            no_cache: If ``True``, disable layer caching.
            platform: Optional target platform
                (e.g. ``linux/amd64``).
            log_callback: Optional sink receiving build log
                lines as they are produced.

        Returns:
            An ``ImageBuildResult`` on success.

        Raises:
            ImageBuildFailed: If the build exits non-zero.
            BackendUnavailableError: If the backend cannot run.
        """
        ...

    def run_detached(
        self,
        image: str,
        *,
        name: str | None = None,
        ports: dict[int, int] | None = None,
        remove: bool = False,
    ) -> ContainerHandle:
        """Start a container in the background.

        Any stale container with the same ``name`` is removed
        first.

        Args:
            image: Image tag to run.
            name: Optional container name.
            ports: Optional port mapping of
                ``{container_port: host_port}``.
            remove: If ``True``, auto-remove on exit.

        Returns:
            A ``ContainerHandle`` for the started container.

        Raises:
            ContainerStartFailed: If the container fails to
                start.
            BackendUnavailableError: If the backend cannot run.
        """
        ...

    def is_running(self, container: ContainerHandle) -> bool:
        """Return ``True`` if the container is currently running."""
        ...

    def wait_running(
        self,
        container: ContainerHandle,
        timeout: float = 2.0,
        poll: float = 0.25,
    ) -> bool:
        """Watch a container for up to ``timeout`` seconds.

        Returns ``False`` as soon as the container is observed
        not running, and ``True`` if it is still running when the
        observation window ends.  This replaces fixed startup
        sleeps with early-exit polling.

        Args:
            container: Handle of the container to watch.
            timeout: Total observation window in seconds.
            poll: Interval between state checks in seconds.

        Returns:
            Whether the container survived the window.
        """
        ...

    def logs(self, container: ContainerHandle) -> str:
        """Return container logs (stderr preferred over stdout)."""
        ...

    def exec(
        self,
        container: ContainerHandle,
        command: list[str],
    ) -> ExecResult:
        """Execute ``command`` inside a running container."""
        ...

    def remove(
        self,
        container: ContainerHandle,
        *,
        force: bool = True,
    ) -> None:
        """Remove a container, ignoring absence errors."""
        ...

    def run_foreground(
        self,
        image: str,
        *,
        ports: dict[int, int] | None = None,
        interactive_shell: bool = False,
    ) -> RunExit:
        """Run a container in the foreground until it exits.

        The container is removed on exit (``--rm`` semantics).
        Standard streams are inherited from the calling process
        so interactive sessions behave like ``docker run``.

        Args:
            image: Image tag to run.
            ports: Optional port mapping of
                ``{container_port: host_port}``.
            interactive_shell: If ``True``, override the
                entrypoint with an interactive shell.

        Returns:
            A ``RunExit`` with the container exit code.

        Raises:
            BackendUnavailableError: If the backend cannot run.
        """
        ...

    def copy_in(
        self,
        container: ContainerHandle,
        src: Path,
        dest: str,
    ) -> None:
        """Copy a host file or directory into a container."""
        ...

    def stream_logs(self, container: ContainerHandle) -> Iterator[str]:
        """Yield container log lines as they are produced."""
        ...
