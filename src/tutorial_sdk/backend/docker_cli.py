"""Docker CLI container backend.

Implements ``ContainerBackend`` by shelling out to the ``docker``
binary.  The command construction in this module is a direct
relocation of the ``subprocess`` logic that previously lived in
``builder/docker.py``, ``validator/container.py``,
``runtime/launcher.py``, and the ``ci`` command's image push in
``cli.py``; observable behavior (commands issued, messages produced)
is preserved.  It is the only module that runs the ``docker``
binary.

Foreground runs add bookkeeping to the ``docker run`` they execute
(``--cidfile`` and the :data:`~tutorial_sdk.backend.base.MANAGED_LABEL`
label) so that the container can be identified and removed when it
outlives an interrupt; ``RunExit.command`` still reports the canonical
command from :func:`foreground_command`.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Iterator

from ..errors import BackendError, BackendUnavailableError
from .base import (
    MANAGED_LABEL,
    MANAGED_LABEL_VALUE,
    STOP_GRACE,
    STOP_HINT,
    BackendInfo,
    ContainerHandle,
    ExecResult,
    ImageBuildResult,
    ImagePushResult,
    LogCallback,
    ManagedContainer,
    RunExit,
    observe_running,
    trap_signals,
)
from .errors import (
    ContainerStartFailed,
    CopyFailed,
    ImageBuildFailed,
    ImagePushFailed,
)

_DOCKER_MISSING_MSG = "Docker is not installed or not on PATH."

#: Interval at which a foreground run checks for interrupts and exit.
_POLL_INTERVAL = 0.1

#: Seconds to wait, after an interrupt, for ``docker run`` to write the
#: container ID before giving up on identifying the container.
_CID_TIMEOUT = 2.0

#: Seconds to wait for the ``docker`` client to exit once its container
#: has been removed, before the client is killed.
_REAP_TIMEOUT = 5.0

#: Seconds to wait for ``docker rm -f`` when cleaning up an
#: interrupted foreground container.
_RM_TIMEOUT = 10.0

#: A container ID as written by ``docker run --cidfile``.
_CONTAINER_ID = re.compile(r"[0-9a-f]{12,64}")

#: ``docker ps`` ``CreatedAt``, e.g. ``2026-10-22 14:53:16 +0000 UTC``.
_PS_CREATED = re.compile(r"(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d) ([+-]\d{4})")


def port_args(ports: dict[int, int] | None) -> list[str]:
    """Translate ``{container: host}`` mappings to ``-p`` flags."""
    args: list[str] = []
    for container_port, host_port in (ports or {}).items():
        args.extend(["-p", f"{host_port}:{container_port}"])
    return args


def foreground_command(
    image: str,
    ports: dict[int, int] | None = None,
    interactive_shell: bool = False,
) -> list[str]:
    """Return the ``docker run`` command for a foreground run.

    Every backend reports this command in ``RunExit.command`` so that
    ``RunResult`` does not depend on the backend in use.

    Args:
        image: Image tag to run.
        ports: Optional port mapping of ``{container_port: host_port}``.
        interactive_shell: If ``True``, override the entrypoint with
            an interactive shell.

    Returns:
        The command as a list of arguments.
    """
    command = ["docker", "run", "--rm"]
    command.extend(port_args(ports))
    if interactive_shell:
        command.extend(["-it", "--entrypoint", "/bin/sh"])
    command.append(image)
    return command


def tracked_command(command: list[str], cidfile: Path) -> list[str]:
    """Add foreground-run bookkeeping to a :func:`foreground_command`.

    Inserts ``--cidfile CIDFILE`` and the managed-container label right
    after ``docker run --rm``, so every other argument (and the image,
    after which options would become container arguments) keeps its
    position.

    Args:
        command: Canonical command from :func:`foreground_command`.
        cidfile: Path ``docker run`` writes the container ID to; it
            must not exist yet.

    Returns:
        The command to execute.
    """
    prefix = ["docker", "run", "--rm"]
    if command[:3] != prefix:
        raise ValueError(f"not a foreground docker run command: {command}")
    return [
        *prefix,
        "--cidfile",
        str(cidfile),
        "--label",
        f"{MANAGED_LABEL}={MANAGED_LABEL_VALUE}",
        *command[3:],
    ]


def _read_cid(cidfile: Path) -> str | None:
    """Return the container ID in ``cidfile``, if it holds a valid one."""
    try:
        text = cidfile.read_text().strip()
    except OSError:
        return None
    return text if _CONTAINER_ID.fullmatch(text) else None


def _parse_created(text: str) -> datetime | None:
    """Parse ``docker ps``'s ``CreatedAt`` column."""
    match = _PS_CREATED.search(text)
    if match is None:
        return None
    return datetime.strptime(
        f"{match.group(1)} {match.group(2)}", "%Y-%m-%d %H:%M:%S %z"
    )


class DockerCliBackend:
    """Container backend backed by the ``docker`` CLI."""

    name = "docker-cli"

    def available(self) -> bool:
        """Return ``True`` if the ``docker`` binary is on PATH."""
        return shutil.which("docker") is not None

    # -- internal helpers --------------------------------------------------

    def _run(
        self,
        command: list[str],
        *,
        check: bool = False,
        capture: bool = True,
        own_session: bool = False,
        timeout: float | None = None,
    ) -> subprocess.CompletedProcess:
        """Run a docker command, normalizing missing-binary errors.

        ``own_session`` starts the command in a new session (or a new
        process group on Windows), so that a Ctrl-C typed in the
        terminal does not reach it (used for cleanup commands issued
        while handling an interrupt).
        """
        options: dict[str, object] = {}
        if own_session:
            if sys.platform == "win32":
                options["creationflags"] = getattr(
                    subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200
                )
            else:
                options["start_new_session"] = True
        if timeout is not None:
            options["timeout"] = timeout
        try:
            return subprocess.run(
                command,
                check=check,
                capture_output=capture,
                text=capture,
                **options,
            )
        except FileNotFoundError as exc:
            raise BackendUnavailableError(_DOCKER_MISSING_MSG) from exc

    def _popen(
        self,
        command: list[str],
    ) -> subprocess.Popen:
        """Spawn a streaming docker process.

        Normalizes missing-binary errors to ``BackendUnavailableError``.
        """
        try:
            return subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
        except FileNotFoundError as exc:
            raise BackendUnavailableError(_DOCKER_MISSING_MSG) from exc

    def _stream(
        self,
        command: list[str],
        log_callback: LogCallback,
    ) -> tuple[int, tuple[str, ...]]:
        """Run a docker command, echoing each output line to a callback.

        Stderr is merged into stdout.  If the callback raises or the
        user interrupts, the docker process is killed rather than left
        running.

        Returns:
            The exit code and the collected output lines.
        """
        lines: list[str] = []
        process = self._popen(command)
        assert process.stdout is not None
        try:
            for line in process.stdout:
                line = line.rstrip("\n")
                lines.append(line)
                log_callback(line)
        except BaseException:
            process.kill()
            process.wait()
            raise
        return process.wait(), tuple(lines)

    # -- images ------------------------------------------------------------

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
        """Run ``docker build`` for the given context.

        When ``log_callback`` is given, output is streamed line by
        line (stderr merged into stdout).
        """
        command = ["docker", "build", "-f", str(dockerfile), "-t", tag]
        if no_cache:
            command.append("--no-cache")
        if platform:
            command.extend(["--platform", platform])
        command.append(str(context))

        if log_callback is None:
            completed = self._run(command)
            if completed.returncode != 0:
                raise ImageBuildFailed(
                    completed.stderr.strip() or "Docker build failed",
                    logs=tuple(completed.stdout.splitlines()),
                )
            return ImageBuildResult(
                image=tag,
                logs=tuple(completed.stdout.splitlines()),
            )

        # Streaming variant: echo each line to the callback while
        # collecting it for the result / failure report.
        returncode, lines = self._stream(command, log_callback)
        if returncode != 0:
            raise ImageBuildFailed("Docker build failed", logs=lines)
        return ImageBuildResult(image=tag, logs=lines)

    def push_image(
        self,
        tag: str,
        *,
        log_callback: LogCallback | None = None,
    ) -> ImagePushResult:
        """Run ``docker push``, then read the digest with ``docker inspect``.

        Without ``log_callback`` the output is captured and a failure
        reports ``docker push``'s stderr; with it, output is streamed
        line by line (stderr merged into stdout).
        """
        command = ["docker", "push", tag]
        if log_callback is None:
            result = self._run(command)
            if result.returncode != 0:
                message = (
                    result.stderr.strip()
                    or result.stdout.strip()
                    or "Docker push failed"
                )
                raise ImagePushFailed(message)
        else:
            returncode, _ = self._stream(command, log_callback)
            if returncode != 0:
                raise ImagePushFailed("Docker push failed")
        return ImagePushResult(image=tag, digest=self._repo_digest(tag))

    def _repo_digest(self, tag: str) -> str | None:
        """Return the digest of the image's first repository digest."""
        result = self._run(
            [
                "docker",
                "inspect",
                "--format",
                "{{index .RepoDigests 0}}",
                tag,
            ]
        )
        if result.returncode != 0:
            return None
        raw = result.stdout.strip()
        if "@" not in raw:
            return None
        return raw.split("@", 1)[1]

    # -- container lifecycle -----------------------------------------------

    def run_detached(
        self,
        image: str,
        *,
        name: str | None = None,
        ports: dict[int, int] | None = None,
        remove: bool = False,
    ) -> ContainerHandle:
        """Start a container with ``docker run -d``."""
        if name:
            # Ensure the container doesn't already exist from a stale run.
            self._run(["docker", "rm", "-f", name])

        command = ["docker", "run", "-d"]
        if remove:
            command.append("--rm")
        if name:
            command.extend(["--name", name])
        command.extend(port_args(ports))
        command.append(image)

        result = self._run(command)
        if result.returncode != 0:
            raise ContainerStartFailed(result.stderr.strip())
        container_id = result.stdout.strip()
        return ContainerHandle(
            id=container_id,
            name=name or container_id[:12],
            backend=self.name,
        )

    def is_running(self, container: ContainerHandle) -> bool:
        """Check ``.State.Running`` via ``docker inspect``."""
        result = self._run(
            [
                "docker",
                "inspect",
                "-f",
                "{{.State.Running}}",
                container.name,
            ]
        )
        return result.stdout.strip() == "true"

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
        result = self._run(["docker", "logs", container.name])
        return result.stderr.strip() or result.stdout.strip()

    def exec(
        self,
        container: ContainerHandle,
        command: list[str],
    ) -> ExecResult:
        """Run a command in the container via ``docker exec``."""
        result = self._run(["docker", "exec", container.name, *command])
        if result.returncode == 0:
            output = result.stdout.strip()
        else:
            output = result.stderr.strip() or result.stdout.strip()
        return ExecResult(exit_code=result.returncode, output=output)

    def remove(
        self,
        container: ContainerHandle,
        *,
        force: bool = True,
    ) -> None:
        """Remove the container with ``docker rm``."""
        command = ["docker", "rm"]
        if force:
            command.append("-f")
        command.append(container.name)
        self._run(command)

    # -- foreground execution ----------------------------------------------

    def run_foreground(
        self,
        image: str,
        *,
        ports: dict[int, int] | None = None,
        interactive_shell: bool = False,
    ) -> RunExit:
        """Run ``docker run --rm`` inheriting the host streams.

        The ``docker`` process shares the terminal, so Ctrl-C reaches
        it directly and it forwards the signal to the container.  The
        run then gives the container :data:`STOP_GRACE` seconds to
        exit; if it is still running then, or on a second Ctrl-C, it
        is removed with ``docker rm -f``.  Afterwards the interrupt is
        re-raised, so under Python's default handler a
        ``KeyboardInterrupt`` propagates to the caller.

        The executed command adds ``--cidfile`` and the managed label
        (see :func:`tracked_command`); ``RunExit.command`` is the
        canonical :func:`foreground_command`.
        """
        command = foreground_command(image, ports, interactive_shell)

        with tempfile.TemporaryDirectory(prefix="tutorial-sdk-run-") as tmp:
            cidfile = Path(tmp) / "container.id"
            with trap_signals((signal.SIGINT,)) as received:
                process = self._spawn(tracked_command(command, cidfile))
                returncode = self._supervise(process, cidfile, received)
                interrupted = bool(received)
        if interrupted:
            # Re-deliver the interrupt to the restored handler
            # (``KeyboardInterrupt`` by default).
            signal.raise_signal(signal.SIGINT)
        return RunExit(
            image=image,
            command=tuple(command),
            returncode=returncode,
        )

    def _spawn(self, command: list[str]) -> subprocess.Popen:
        """Start a foreground docker process inheriting the host streams."""
        try:
            return subprocess.Popen(command)
        except FileNotFoundError as exc:
            raise BackendUnavailableError(_DOCKER_MISSING_MSG) from exc

    def _supervise(
        self,
        process: subprocess.Popen,
        cidfile: Path,
        received: list[int],
    ) -> int:
        """Wait for a foreground ``docker run``, enforcing the stop policy.

        ``received`` collects the interrupts delivered meanwhile.  The
        first one starts the grace period (the terminal has already
        delivered it to the ``docker`` client, which forwards it to the
        container, so it is not sent again); a second one, or the end
        of the grace period, removes the container.  After an
        interrupt, the container is removed at the end in any case, in
        case the client exited before the container did.

        Returns:
            The ``docker`` client's exit code.
        """
        deadline: float | None = None
        removed_at: float | None = None
        try:
            while True:
                try:
                    returncode = process.wait(timeout=_POLL_INTERVAL)
                    break
                except subprocess.TimeoutExpired:
                    pass
                now = time.monotonic()
                if removed_at is not None:
                    if now - removed_at >= _REAP_TIMEOUT:
                        process.kill()
                    continue
                if received and deadline is None:
                    print(STOP_HINT, file=sys.stderr)
                    deadline = now + STOP_GRACE
                if len(received) >= 2 or (
                    deadline is not None and now >= deadline
                ):
                    self._remove_tracked(process, cidfile)
                    removed_at = time.monotonic()
        finally:
            if process.poll() is None:  # unexpected error while waiting
                process.kill()
                process.wait()
        if received and removed_at is None:
            self._remove_tracked(process, cidfile, quiet=True)
        return returncode

    def _remove_tracked(
        self,
        process: subprocess.Popen,
        cidfile: Path,
        *,
        quiet: bool = False,
    ) -> None:
        """Force-remove the container of a foreground run, by its ID.

        Waits (bounded) for ``docker run`` to write the ID.  Problems
        are reported on stderr as warnings; a container that is
        already gone counts as removed.

        Args:
            process: The ``docker run`` client.
            cidfile: Its ``--cidfile`` path.
            quiet: Do not warn when the container cannot be
                identified because the client exited without creating
                one (the end-of-run safety net).
        """
        deadline = time.monotonic() + _CID_TIMEOUT
        cid = _read_cid(cidfile)
        while cid is None and time.monotonic() < deadline:
            if process.poll() is not None:
                cid = _read_cid(cidfile)
                break
            time.sleep(0.05)
            cid = _read_cid(cidfile)
        if cid is None:
            if not (quiet and process.poll() is not None):
                print(
                    "Warning: could not identify the container started by "
                    "`docker run`, so it could not be removed; "
                    "`tutorial-sdk doctor` lists tutorial-sdk containers.",
                    file=sys.stderr,
                )
            return
        try:
            result = self._run(
                ["docker", "rm", "-f", cid],
                own_session=True,
                timeout=_RM_TIMEOUT,
            )
        except (BackendUnavailableError, subprocess.TimeoutExpired) as exc:
            print(
                f"Warning: could not remove container {cid}: {exc}",
                file=sys.stderr,
            )
            return
        error = (result.stderr or "").strip()
        if result.returncode != 0 and "No such container" not in error:
            print(
                f"Warning: could not remove container {cid}: {error}",
                file=sys.stderr,
            )

    # -- file copy and log streaming ----------------------------------------

    def copy_in(
        self,
        container: ContainerHandle,
        src: Path,
        dest: str,
    ) -> None:
        """Copy a host path into the container via ``docker cp``."""
        result = self._run(
            ["docker", "cp", str(src), f"{container.name}:{dest}"]
        )
        if result.returncode != 0:
            raise CopyFailed(result.stderr.strip() or "docker cp failed")

    def stream_logs(self, container: ContainerHandle) -> Iterator[str]:
        """Follow container logs line by line."""
        process = self._popen(["docker", "logs", "-f", container.name])
        assert process.stdout is not None
        try:
            for line in process.stdout:
                yield line.rstrip("\n")
        finally:
            process.terminate()
            process.wait()

    # -- diagnostics ---------------------------------------------------------

    def describe(self) -> BackendInfo:
        """Describe the Docker CLI and the daemon it talks to.

        Used by ``tutorial-sdk doctor``; never raises.
        """
        if not self.available():
            return BackendInfo(name=self.name, daemon_error=_DOCKER_MISSING_MSG)
        host = os.environ.get("DOCKER_HOST")
        try:
            version = self._run(["docker", "version", "--format", "{{json .}}"])
            # DOCKER_HOST overrides the context, as for every docker call.
            context = None
            if not host:
                context = self._run(
                    [
                        "docker",
                        "context",
                        "inspect",
                        "--format",
                        "{{.Name}}\t{{.Endpoints.docker.Host}}",
                    ]
                )
        except BackendUnavailableError as exc:
            return BackendInfo(name=self.name, daemon_error=str(exc))

        try:
            data = json.loads(version.stdout.strip().splitlines()[0])
        except (IndexError, ValueError):
            data = {}
        client = data.get("Client") or {}
        server = data.get("Server") or {}
        daemon_error = None
        if version.returncode != 0 or not server:
            daemon_error = version.stderr.strip() or "no response from daemon"

        if context is None:
            endpoint, source = host, "DOCKER_HOST"
        else:
            name, _, address = context.stdout.strip().partition("\t")
            endpoint = address or None
            source = f"context {name}" if name else None
        return BackendInfo(
            name=self.name,
            client_version=client.get("Version"),
            server_version=server.get("Version"),
            api_version=server.get("ApiVersion"),
            endpoint=endpoint,
            endpoint_source=source,
            daemon_error=daemon_error,
        )

    def list_managed(self) -> tuple[ManagedContainer, ...]:
        """List containers carrying the managed-container label.

        Raises:
            BackendError: If ``docker ps`` fails.
            BackendUnavailableError: If the binary is missing.
        """
        result = self._run(
            [
                "docker",
                "ps",
                "--all",
                "--no-trunc",
                "--filter",
                f"label={MANAGED_LABEL}={MANAGED_LABEL_VALUE}",
                "--format",
                "{{.ID}}\t{{.Names}}\t{{.Image}}\t{{.State}}\t{{.CreatedAt}}",
            ]
        )
        if result.returncode != 0:
            raise BackendError(result.stderr.strip() or "docker ps failed")
        containers = []
        for line in result.stdout.splitlines():
            fields = line.split("\t")
            if len(fields) != 5:
                continue
            cid, name, image, state, created = fields
            containers.append(
                ManagedContainer(
                    id=cid,
                    name=name,
                    image=image,
                    state=state,
                    created=_parse_created(created),
                )
            )
        return tuple(containers)
