"""Local build orchestration."""

from pathlib import Path

from .docker import BuildResult, DockerBuilder
from ..generator import (
    DevcontainerGenerator,
    DockerfileGenerator,
    ManifestGenerator,
)
from ..spec import TutorialSpec

DEFAULT_DEVCONT_NAME: str = ".devcontainer/devcontainer.json"
DEFAULT_MANIFEST_NAME: str = "tutorial-manifest.json"


class LocalBuilder:
    """Generate local build artifacts before invoking Docker."""

    def __init__(
        self,
        spec: TutorialSpec,
        root: str | Path = ".",
    ) -> None:
        """Create a local builder.

        Args:
            spec: Parsed tutorial specification.
            root: Tutorial project root directory.
        """

        self.spec = spec
        self.root = Path(root)

    def write_dockerfile(self, path: str | Path | None = None) -> None:
        """Write the generated Dockerfile.

        Args:
            path: Destination path.  Defaults to the
                path configured in the build spec.
        """

        dockerfile = Path(path or self.spec.build.dockerfile)
        if not dockerfile.is_absolute():
            dockerfile = self.root / dockerfile
        dockerfile.write_text(
            DockerfileGenerator(
                self.spec,
                self.root,
            ).render()
        )

    def write_manifest(
        self,
        path: str | Path = DEFAULT_MANIFEST_NAME,
    ) -> None:
        """Write the tutorial manifest.

        Args:
            path: Destination path for the JSON
                manifest file.
        """

        manifest = Path(path)
        if not manifest.is_absolute():
            manifest = self.root / manifest
        ManifestGenerator(self.spec).write(manifest)

    def write_devcontainer(
        self,
        path: str | Path = DEFAULT_DEVCONT_NAME,
    ) -> None:
        """Write devcontainer configuration.

        Args:
            path: Destination path for the
                devcontainer JSON configuration
                file.
        """

        target = Path(path)
        if not target.is_absolute():
            target = self.root / target
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(DevcontainerGenerator(self.spec).render_json())

    def prepare(self) -> None:
        """Write generated files configured by the tutorial spec."""

        self.write_dockerfile()
        if self.spec.build.export_manifest:
            self.write_manifest()
        if self.spec.build.export_devcontainer:
            self.write_devcontainer()

    def build(
        self,
        image: str | None = None,
        no_cache: bool | None = None,
        platform: str | None = None,
    ) -> BuildResult:
        """Prepare artifacts and run Docker build.

        Args:
            image: Override image tag.
            no_cache: If ``True``, disable Docker layer
                caching.
            platform: Target platform.

        Returns:
            A ``BuildResult`` from Docker.
        """

        self.prepare()
        return DockerBuilder(self.spec, self.root).build(
            image=image, no_cache=no_cache, platform=platform
        )
