"""Container backend abstraction.

Defines the ``ContainerBackend`` protocol that all execution
backends implement, the small result dataclasses exchanged across
that boundary, and the helpers and constants the backends share.
The backend errors live in :mod:`tutorial_sdk.errors`
(``BackendError``, ``BackendUnavailableError``) and
:mod:`tutorial_sdk.backend.errors`.

Higher layers (``DockerBuilder``, ``ContainerValidator``,
``RuntimeLauncher``) depend only on this module; concrete backends
(such as ``DockerCliBackend`` and ``DockerSdkBackend``) live behind
it.  No backend-specific types (``subprocess`` results, docker-py
objects) may leak through this interface.
"""

from __future__ import annotations

import signal
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterator, Protocol, runtime_checkable

#: Callback invoked with each build or push log line as it is produced.
LogCallback = Callable[[str], None]

#: Label key marking containers started by tutorial-sdk foreground runs
#: (``tutorial-sdk run``), on every backend.  ``tutorial-sdk doctor``
#: lists containers by this label; it marks ownership, not that a
#: container is orphaned.
MANAGED_LABEL = "org.bnl-peso-hub.tutorial-sdk.managed"

#: Value of :data:`MANAGED_LABEL`.
MANAGED_LABEL_VALUE = "true"

#: Seconds a foreground container may take to exit after an interrupt
#: before it is force-removed (the ``docker stop`` default).
STOP_GRACE = 10.0

#: Printed to stderr when a foreground run is first interrupted.
STOP_HINT = "Stopping container (interrupt again to force)..."


@dataclass(frozen=True)
class ImageBuildResult:
    """Outcome of a successful image build."""

    image: str
    image_id: str | None = None
    logs: tuple[str, ...] = ()


@dataclass(frozen=True)
class ImagePushResult:
    """Outcome of a successful image push.

    ``digest`` is the registry digest (``sha256:...``) read from the
    image's first repository digest after the push, or ``None`` when
    it cannot be determined.
    """

    image: str
    digest: str | None = None


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
    """Outcome of a foreground (blocking) container run.

    ``command`` is the equivalent ``docker run`` invocation, reported
    identically by every backend.
    """

    image: str
    command: tuple[str, ...]
    returncode: int


@dataclass(frozen=True)
class ManagedContainer:
    """A container carrying the :data:`MANAGED_LABEL` label.

    ``state`` is the Docker state (``running``, ``exited``,
    ``created``, ...); ``created`` is ``None`` when the creation time
    cannot be parsed.
    """

    id: str
    name: str
    image: str
    state: str
    created: datetime | None = None


@dataclass(frozen=True)
class BackendInfo:
    """Diagnostic description of a backend and the daemon it uses.

    Not part of the ``ContainerBackend`` protocol: ``DockerCliBackend``
    and ``DockerSdkBackend`` provide it through ``describe()`` for
    ``tutorial-sdk doctor``.

    Attributes:
        name: Backend name.
        client_version: Docker CLI version (``docker-cli``) or
            docker-py version (``docker-sdk``).
        server_version: Docker Engine version, if the daemon answered.
        api_version: Engine API version, if the daemon answered.
        endpoint: Daemon address the backend talks to, if known.
        endpoint_source: Where ``endpoint`` comes from
            (``DOCKER_HOST``, ``context <name>``, ``default``).
        daemon_error: Why the daemon could not be queried, if it
            could not.
        classic_builder: Whether the Engine API build endpoint still
            runs the classic builder (``docker-sdk`` only; ``None``
            when not probed).
        notes: Further remarks (probe failures and the like).
    """

    name: str
    client_version: str | None = None
    server_version: str | None = None
    api_version: str | None = None
    endpoint: str | None = None
    endpoint_source: str | None = None
    daemon_error: str | None = None
    classic_builder: bool | None = None
    notes: tuple[str, ...] = ()


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
            ImageBuildFailed: If the build fails.  Its ``logs``
                carry the build output collected so far.
            BackendUnavailableError: If the backend cannot run.
        """
        ...

    def push_image(
        self,
        tag: str,
        *,
        log_callback: LogCallback | None = None,
    ) -> ImagePushResult:
        """Push the local image ``tag`` to its registry.

        Registry credentials come from the Docker configuration, as
        for ``docker push``.  A tag without an explicit version
        pushes ``:latest``.

        Args:
            tag: Image reference to push (``[registry/]repo[:tag]``).
            log_callback: Optional sink receiving push progress
                lines as they are produced.

        Returns:
            An ``ImagePushResult`` with the registry digest, when
            it can be read.

        Raises:
            ImagePushFailed: If the push fails.
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

        The container's stdout and stderr go to the caller's stdout
        and stderr, and the container is removed on exit, including
        after an interrupt (``--rm`` semantics).  Interrupting the run
        (SIGINT; the ``docker-sdk`` backend also handles SIGTERM) asks
        the container to stop; a container still
        running :data:`STOP_GRACE` seconds later, or when the run is
        interrupted again, is killed and removed.  Whether the
        interrupt then propagates or the exit code is returned is
        backend-specific.  Foreground containers carry the
        :data:`MANAGED_LABEL` label.

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
        """Copy a host file or directory into a container.

        ``dest`` is the full destination path of the copied file or
        directory inside the container, and its parent directory
        must already exist.  Callers must not rely on copying
        *into* an existing directory: the CLI backend follows
        ``docker cp`` and nests ``src`` inside it, which other
        backends need not do.

        Raises:
            CopyFailed: If the copy cannot be performed.
        """
        ...

    def stream_logs(self, container: ContainerHandle) -> Iterator[str]:
        """Yield container log lines as they are produced."""
        ...


def observe_running(
    probe: Callable[[], bool],
    timeout: float,
    poll: float,
) -> bool:
    """Run the ``wait_running`` observation window over ``probe``.

    Shared by all backends so the contract semantics cannot drift:
    returns ``False`` as soon as ``probe()`` reports the container
    is not running, and ``True`` only if it still runs when the
    ``timeout`` window ends.  It never returns early on success.

    Args:
        probe: Zero-argument callable returning the running state.
        timeout: Total observation window in seconds.
        poll: Interval between probes in seconds.

    Returns:
        Whether the container survived the window.
    """
    deadline = time.monotonic() + timeout
    while True:
        if not probe():
            return False
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return True
        time.sleep(min(poll, remaining))


@contextmanager
def trap_signals(signums: tuple[int, ...]) -> Iterator[list[int]]:
    """Record the given signals instead of acting on them, for the duration.

    Shared by the backends' foreground runs.  Yields the list the
    received signal numbers are appended to.  The handler only
    records, so a signal can never interrupt a run half-way through a
    step; the caller polls the list instead.

    Handlers can only be installed from the main thread; elsewhere
    nothing is installed (signals are delivered to the main thread
    only).  Signals the process ignores (``SIG_IGN``) or whose handler
    was not installed from Python are left alone.  Previous handlers
    are restored on exit.

    Args:
        signums: Signal numbers to record.
    """
    received: list[int] = []
    if threading.current_thread() is not threading.main_thread():
        yield received
        return

    def _record(signum: int, frame: Any) -> None:
        received.append(signum)

    previous = {}
    try:
        for signum in signums:
            handler = signal.getsignal(signum)
            if handler is not None and handler is not signal.SIG_IGN:
                previous[signum] = handler
                signal.signal(signum, _record)
        yield received
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)
