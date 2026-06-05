"""Local runtime launcher."""

import subprocess
from dataclasses import dataclass
from pathlib import Path

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
    ) -> None:
        """Create a runtime launcher.

        Args:
            spec: Parsed tutorial specification.
            root: Tutorial project root directory.
        """

        self.spec = spec
        self.root = Path(root)

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
        """

        image = image or self.spec.build.image or f"{self.spec.name}:latest"
        command = [
            "docker",
            "run",
            "--rm",
            "-p",
            f"{port}:{self.spec.runtime.expose_port}",
        ]
        if shell:
            command.extend(["-it", "--entrypoint", "/bin/sh"])
        command.append(image)

        completed = subprocess.run(command, check=False)
        return RunResult(
            image=image,
            command=tuple(command),
            returncode=completed.returncode,
        )
