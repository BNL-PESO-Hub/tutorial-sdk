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
        backend: ContainerBackend | None = None,
    ) -> None:
        """Create a runtime launcher.

        Args:
            spec: Parsed tutorial specification.
            root: Tutorial project root directory.
            backend: Optional container backend override.
                Defaults to auto-detection via
                :func:`tutorial_sdk.backend.get_backend`.
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
            ConfigError: If ``TUTORIAL_SDK_BACKEND`` names an
                unknown backend.
        """

        image = image or self.spec.build.image or f"{self.spec.name}:latest"
        backend = self.backend or get_backend()

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
