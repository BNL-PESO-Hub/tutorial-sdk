"""In-memory test doubles for container-backend consumers.

``MockBackend`` implements the ``ContainerBackend`` protocol
without touching Docker, recording every call so tests can assert
on orchestration logic (ordering, cleanup, arguments) in
isolation.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Iterator

from tutorial_sdk.backend.base import (
    ContainerHandle,
    ExecResult,
    ImageBuildResult,
    ImagePushResult,
    RunExit,
)
from tutorial_sdk.backend.errors import (
    ContainerStartFailed,
    ImageBuildFailed,
    ImagePushFailed,
)


def make_spec(
    name: str = "demo",
    image: str | None = None,
    dockerfile: str = "Dockerfile",
    cache: bool = True,
    expose_port: int = 8888,
    backend: str | None = None,
) -> SimpleNamespace:
    """Build a duck-typed stand-in for ``TutorialSpec``."""
    spec = SimpleNamespace(
        name=name,
        build=SimpleNamespace(
            image=image,
            dockerfile=dockerfile,
            cache=cache,
            backend=backend,
        ),
        runtime=SimpleNamespace(expose_port=expose_port),
    )
    spec.effective_image = image or f"{name}:latest"
    spec.resolve_image = lambda override=None: (
        override or spec.build.image or f"{spec.name}:latest"
    )
    return spec


class MockBackend:
    """Scriptable, call-recording ``ContainerBackend``."""

    name = "mock"

    def __init__(
        self,
        *,
        build_error: str | None = None,
        push_error: str | None = None,
        push_digest: str | None = "sha256:pushed",
        start_error: str | None = None,
        stays_running: bool = True,
        exec_result: ExecResult | None = None,
        logs_text: str = "boom",
        run_returncode: int = 0,
        build_lines: tuple[str, ...] = (),
    ) -> None:
        self.build_error = build_error
        self.push_error = push_error
        self.push_digest = push_digest
        self.start_error = start_error
        self.stays_running = stays_running
        self.exec_result = exec_result or ExecResult(0, "4.2.0")
        self.logs_text = logs_text
        self.run_returncode = run_returncode
        self.build_lines = build_lines
        self.build_callbacks: list = []
        self.calls: list[tuple] = []

    # -- protocol implementation ------------------------------------

    def available(self) -> bool:
        return True

    def build_image(
        self,
        context: Path,
        dockerfile: Path,
        tag: str,
        *,
        no_cache: bool = False,
        platform: str | None = None,
        log_callback=None,
    ) -> ImageBuildResult:
        self.calls.append(
            ("build_image", context, dockerfile, tag, no_cache, platform)
        )
        self.build_callbacks.append(log_callback)
        if log_callback is not None:
            for line in self.build_lines:
                log_callback(line)
        if self.build_error is not None:
            raise ImageBuildFailed(self.build_error, logs=("l1", "l2"))
        return ImageBuildResult(image=tag, image_id="sha256:abc", logs=())

    def push_image(self, tag: str, *, log_callback=None) -> ImagePushResult:
        self.calls.append(("push_image", tag))
        if self.push_error is not None:
            raise ImagePushFailed(self.push_error)
        return ImagePushResult(image=tag, digest=self.push_digest)

    def run_detached(
        self,
        image: str,
        *,
        name: str | None = None,
        ports: dict[int, int] | None = None,
        remove: bool = False,
    ) -> ContainerHandle:
        self.calls.append(("run_detached", image, name, ports, remove))
        if self.start_error is not None:
            raise ContainerStartFailed(self.start_error)
        return ContainerHandle(
            id="cid123", name=name or "cid123", backend=self.name
        )

    def is_running(self, container: ContainerHandle) -> bool:
        self.calls.append(("is_running", container.name))
        return self.stays_running

    def wait_running(
        self,
        container: ContainerHandle,
        timeout: float = 2.0,
        poll: float = 0.25,
    ) -> bool:
        self.calls.append(("wait_running", container.name, timeout))
        return self.stays_running

    def logs(self, container: ContainerHandle) -> str:
        self.calls.append(("logs", container.name))
        return self.logs_text

    def exec(
        self,
        container: ContainerHandle,
        command: list[str],
    ) -> ExecResult:
        self.calls.append(("exec", container.name, tuple(command)))
        return self.exec_result

    def remove(
        self,
        container: ContainerHandle,
        *,
        force: bool = True,
    ) -> None:
        self.calls.append(("remove", container.name, force))

    def run_foreground(
        self,
        image: str,
        *,
        ports: dict[int, int] | None = None,
        interactive_shell: bool = False,
    ) -> RunExit:
        self.calls.append(("run_foreground", image, ports, interactive_shell))
        command = ["docker", "run", "--rm"]
        for cport, hport in (ports or {}).items():
            command.extend(["-p", f"{hport}:{cport}"])
        if interactive_shell:
            command.extend(["-it", "--entrypoint", "/bin/sh"])
        command.append(image)
        return RunExit(
            image=image,
            command=tuple(command),
            returncode=self.run_returncode,
        )

    def copy_in(
        self,
        container: ContainerHandle,
        src: Path,
        dest: str,
    ) -> None:
        self.calls.append(("copy_in", container.name, src, dest))

    def stream_logs(self, container: ContainerHandle) -> Iterator[str]:
        self.calls.append(("stream_logs", container.name))
        yield from self.logs_text.splitlines()

    # -- assertion helpers -------------------------------------------

    def names(self) -> list[str]:
        """Return the ordered list of called method names."""
        return [c[0] for c in self.calls]
