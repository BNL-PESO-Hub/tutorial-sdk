"""Docker image builder adapter."""

import subprocess
from dataclasses import dataclass
from pathlib import Path

from ..errors import BuildError
from ..spec import TutorialSpec


@dataclass(frozen=True)
class BuildResult:
    """Result of a container build attempt."""

    image: str
    dockerfile: Path
    pushed: bool = False
    digest: str | None = None
    status: str = "success"


class DockerBuilder:
    """Build tutorial images with Docker."""

    def __init__(
        self,
        spec: TutorialSpec,
        root: str | Path = ".",
    ) -> None:
        """Create a Docker builder.

        Args:
            spec: Parsed tutorial specification.
            root: Tutorial project root directory.
        """

        self.spec = spec
        self.root = Path(root)

    def build(
        self,
        image: str | None = None,
        no_cache: bool | None = None,
        platform: str | None = None,
    ) -> BuildResult:
        """Run ``docker build`` for the tutorial.

        Args:
            image: Override image tag.
            no_cache: If ``True``, disable Docker layer
                caching.
            platform: Target platform (e.g.
                ``linux/amd64``).

        Returns:
            A ``BuildResult`` on success.

        Raises:
            BuildError: If the build exits non-zero.
        """

        image = image or self.spec.build.image or f"{self.spec.name}:latest"
        dockerfile = self.root / self.spec.build.dockerfile
        command = [
            "docker",
            "build",
            "-f",
            str(dockerfile),
            "-t",
            image,
        ]

        if no_cache is None:
            cache_enabled = self.spec.build.cache
        else:
            cache_enabled = not no_cache
        if not cache_enabled:
            command.append("--no-cache")
        if platform:
            command.extend(["--platform", platform])
        command.append(str(self.root))

        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
        )
        if completed.returncode != 0:
            raise BuildError(completed.stderr.strip() or "Docker build failed")
        return BuildResult(
            image=image,
            dockerfile=dockerfile,
        )
