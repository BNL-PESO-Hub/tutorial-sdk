"""Docker CLI container backend.

Implements ``ContainerBackend`` by shelling out to the ``docker``
binary.  The command construction in this module is a direct
relocation of the ``subprocess`` logic that previously lived in
``builder/docker.py``, ``validator/container.py``, and
``runtime/launcher.py``; observable behavior (commands issued,
messages produced) is preserved.
"""

from __future__ import annotations

import shutil
import subprocess
import time
from pathlib import Path
from typing import Iterator

from ..errors import BackendUnavailableError
from .base import (
    ContainerHandle,
    ExecResult,
    ImageBuildResult,
    LogCallback,
    RunExit,
)
from .errors import ContainerStartFailed, CopyFailed, ImageBuildFailed

_DOCKER_MISSING_MSG = "Docker is not installed or not on PATH."


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
    ) -> subprocess.CompletedProcess:
        """Run a docker command, normalizing missing-binary errors."""
        try:
            return subprocess.run(
                command,
                check=check,
                capture_output=capture,
                text=capture,
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

    @staticmethod
    def _port_args(ports: dict[int, int] | None) -> list[str]:
        """Translate ``{container: host}`` mappings to ``-p`` flags."""
        args: list[str] = []
        for container_port, host_port in (ports or {}).items():
            args.extend(["-p", f"{host_port}:{container_port}"])
        return args

    # -- image build -------------------------------------------------------

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
        lines: list[str] = []
        process = self._popen(command)
        assert process.stdout is not None
        try:
            for line in process.stdout:
                line = line.rstrip("\n")
                lines.append(line)
                log_callback(line)
        except BaseException:
            # Callback failed or the user interrupted: don't leave
            # an orphaned build running.
            process.kill()
            process.wait()
            raise
        returncode = process.wait()
        if returncode != 0:
            raise ImageBuildFailed(
                "Docker build failed",
                logs=tuple(lines),
            )
        return ImageBuildResult(image=tag, logs=tuple(lines))

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
            # Ensure the container doesn't already exist from a
            # stale run.
            self._run(["docker", "rm", "-f", name])

        command = ["docker", "run", "-d"]
        if remove:
            command.append("--rm")
        if name:
            command.extend(["--name", name])
        command.extend(self._port_args(ports))
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
        deadline = time.monotonic() + timeout
        while True:
            if not self.is_running(container):
                return False
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return True
            time.sleep(min(poll, remaining))

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
        """Run ``docker run --rm`` inheriting the host streams."""
        command = ["docker", "run", "--rm"]
        command.extend(self._port_args(ports))
        if interactive_shell:
            command.extend(["-it", "--entrypoint", "/bin/sh"])
        command.append(image)

        completed = self._run(command, capture=False)
        return RunExit(
            image=image,
            command=tuple(command),
            returncode=completed.returncode,
        )

    # -- forward-looking helpers --------------------------------------------

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
