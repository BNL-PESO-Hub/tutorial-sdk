"""Resolve tutorial specifications against a project directory."""

from dataclasses import dataclass
from pathlib import Path

from .config import DEFAULT_CONFIG
from .spec import TutorialSpec


@dataclass(frozen=True)
class ResolvedTutorialProject:
    """A specification with project-relative paths resolved."""

    spec: TutorialSpec
    root: Path
    config_path: Path
    content_paths: tuple[Path, ...]
    missing_paths: tuple[Path, ...]

    @property
    def image(self) -> str:
        """Return the configured or default image tag."""

        return self.spec.build.image or f"{self.spec.name}:latest"


class TutorialResolver:
    """Resolve content and generated artifact paths."""

    def __init__(self, spec: TutorialSpec, root: str | Path) -> None:
        """Create a resolver.

        Args:
            spec: Parsed tutorial specification.
            root: Tutorial project root directory.
        """

        self.spec = spec
        self.root = Path(root)

    def resolve(
        self,
        config_path: str | Path = DEFAULT_CONFIG,
    ) -> ResolvedTutorialProject:
        """Resolve declared content files.

        Args:
            config_path: Path to the configuration file
                associated with this project.

        Returns:
            A ``ResolvedTutorialProject`` containing
            absolute content paths and missing path details.
        """

        content_paths = tuple(
            self.root / path for path in self.spec.content.all_paths()
        )
        missing_paths = tuple(
            path for path in content_paths if not path.exists()
        )
        return ResolvedTutorialProject(
            spec=self.spec,
            root=self.root,
            config_path=Path(config_path),
            content_paths=content_paths,
            missing_paths=missing_paths,
        )
