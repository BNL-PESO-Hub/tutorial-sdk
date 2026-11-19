"""Local runtime launcher."""

from dataclasses import dataclass
from pathlib import Path

from ..backend import ContainerBackend, get_backend
from ..spec import TutorialSpec


@dataclass(frozen=True)
class RunResult:
    """Result of starting a tutorial runtime."""

    image: str
    command: tuple[str, ...]
    returncode: int


class RuntimeLauncher:
    """Launch tutorial images locally with Docker."""

    def __init__(
        self,
        spec: TutorialSpec,
        root: str | Path = ".",
        backend: ContainerBackend | str | None = None,
    ) -> None:
        """Create a runtime launcher.

        Args:
            spec: Parsed tutorial specification.
            root: Tutorial project root directory.
            backend: Container backend instance, or a backend name
                (``"auto"``, ``"docker-cli"``, ``"docker-sdk"``)
                taking precedence over ``TUTORIAL_SDK_BACKEND`` and
                ``build.backend``.  Defaults to that selection
                (see :func:`tutorial_sdk.backend.get_backend`).
        """

        self.spec = spec
        self.root = Path(root)
        self.backend = backend

    def run(
        self,
        image: str | None = None,
        port: int = 8888,
        shell: bool = False,
    ) -> RunResult:
        """Run the configured tutorial image.

        Args:
            image: Optional image tag override.
            port: Host port to bind.
            shell: If ``True``, override the entrypoint
                with an interactive shell.

        Returns:
            A ``RunResult`` with the exit code.

        Raises:
            BackendUnavailableError: If no container backend
                is available.
            ConfigError: If the selected backend name is
                unknown.
        """

        image = self.spec.resolve_image(image)
        backend = get_backend(
            self.backend, fallback_name=self.spec.build.backend
        )

        exit_info = backend.run_foreground(
            image,
            ports={self.spec.runtime.expose_port: port},
            interactive_shell=shell,
        )
        return RunResult(
            image=exit_info.image,
            command=exit_info.command,
            returncode=exit_info.returncode,
        )
