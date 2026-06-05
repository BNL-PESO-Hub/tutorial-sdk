"""High-level tutorial project API."""

import json
from pathlib import Path

from .config import DEFAULT_CONFIG
from .builder import BuildResult, LocalBuilder
from .resolver import ResolvedTutorialProject, TutorialResolver
from .runtime import RunResult, RuntimeLauncher
from .spec import TutorialSpec
from .validator import (
    AssetValidator,
    ContainerValidator,
    DependencyValidator,
    NotebookValidator,
    ValidationReport,
)


class TutorialProject:
    """High-level API for tutorial projects."""

    def __init__(
        self,
        spec: TutorialSpec,
        root: str | Path = ".",
        config_path: str | Path = DEFAULT_CONFIG,
    ) -> None:
        """Create a tutorial project object.

        Args:
            spec: Parsed tutorial specification.
            root: Project root directory.
            config_path: Path to the tutorial
                YAML configuration file.
        """

        self.spec = spec
        self.root = Path(root)
        self.config_path = Path(config_path)

    @classmethod
    def load(cls, path: str | Path) -> "TutorialProject":
        """Load a tutorial project from a YAML specification.

        Args:
            path: Path to a tutorial YAML file.

        Returns:
            A ``TutorialProject`` rooted in the config
            file's parent directory.
        """

        config_path = Path(path)
        spec = TutorialSpec.load(config_path)
        return cls(
            spec,
            config_path.parent,
            config_path,
        )

    @classmethod
    def init(cls, path: str | Path = ".") -> "TutorialProject":
        """Create a minimal tutorial project skeleton.

        Args:
            path: Target directory for the new project.

        Returns:
            A ``TutorialProject`` with a ``minimal``
            template applied.
        """

        from .scaffold import ProjectScaffolder

        return ProjectScaffolder().scaffold(
            "minimal",
            Path(path),
        )

    @classmethod
    def init_from(
        cls,
        source: str | Path,
        target: str | Path | None = None,
    ) -> "TutorialProject":
        """Import an existing directory as a tutorial project.

        Args:
            source: Path to an existing directory containing
                notebooks and associated files.
            target: Optional target directory for the new
                tutorial project.

        Returns:
            A fully initialised ``TutorialProject``.
        """

        from .scaffold import ProjectImporter

        return ProjectImporter().scan(
            source,
            target,
        )

    @classmethod
    def init_from_url(
        cls,
        url: str,
        target: str | Path | None = None,
        remove_clone: bool = False,
    ) -> "TutorialProject":
        """Import a remote repository as a tutorial project.

        Args:
            url: Git-compatible clone URL.
            target: Optional target directory.
            remove_clone: If ``True``, delete the cloned
                repository after importing.

        Returns:
            A ``TutorialProject``.
        """

        from .scaffold import ProjectImporter

        return ProjectImporter().scan_url(
            url,
            target,
            remove_clone=remove_clone,
        )

    @classmethod
    def init_from_github(
        cls,
        org_repo: str,
        target: str | Path | None = None,
        remove_clone: bool = False,
    ) -> "TutorialProject":
        """Import a GitHub repository as a tutorial project.

        Args:
            org_repo: ``ORG/REPO`` shorthand.
            target: Optional target directory.
            remove_clone: If ``True``, delete the cloned
                repository after importing.

        Returns:
            A ``TutorialProject``.
        """

        from .scaffold import ProjectImporter

        return ProjectImporter().scan_github(
            org_repo,
            target,
            remove_clone=remove_clone,
        )

    def resolve(self) -> ResolvedTutorialProject:
        """Resolve the project content graph.

        Returns:
            A ``ResolvedTutorialProject`` with content
            and missing paths populated.
        """

        return TutorialResolver(
            self.spec,
            self.root,
        ).resolve(self.config_path)

    def validate(
        self,
        strict: bool = False,
        container: bool = False,
        image: str | None = None,
    ) -> ValidationReport:
        """Run configured validation checks.

        Args:
            strict: If ``True``, promote warnings to
                failures.
            container: If ``True``, include container
                runtime validation.
            image: Override image tag for container
                validation.

        Returns:
            Combined ``ValidationReport``.
        """

        reports = [
            AssetValidator(self.spec, self.root).validate(),
            NotebookValidator(self.spec, self.root).validate(),
            DependencyValidator(self.spec).validate(),
        ]
        if container:
            reports.append(
                ContainerValidator(
                    self.spec,
                    self.root,
                ).validate(image=image)
            )
        report = ValidationReport.combine(reports)
        if strict and report.warnings:
            report = report.model_copy(update={"passed": False})
        return report

    def inspect(self) -> str:
        """Return resolved tutorial metadata as JSON.

        Returns:
            Pretty-printed JSON string.
        """

        resolved = self.resolve()
        payload = self.spec.to_manifest_dict(image=resolved.image)
        payload["missing_paths"] = [
            str(path.relative_to(self.root)) for path in resolved.missing_paths
        ]
        return json.dumps(payload, indent=2) + "\n"

    def build(
        self,
        image: str | None = None,
        no_cache: bool | None = None,
        platform: str | None = None,
    ) -> BuildResult:
        """Generate a Dockerfile and build the container image.

        Args:
            image: Optional image tag override.
            no_cache: If ``True``, disable Docker
                layer caching.
            platform: Target platform (e.g.
                ``linux/amd64``).

        Returns:
            A ``BuildResult`` with the image tag and
            Dockerfile path.
        """

        return LocalBuilder(
            self.spec,
            self.root,
        ).build(
            image=image,
            no_cache=no_cache,
            platform=platform,
        )

    def run(
        self, image: str | None = None, port: int = 8888, shell: bool = False
    ) -> RunResult:
        """Run the tutorial container locally.

        Args:
            image: Optional image tag override.
            port: Host port to bind.
            shell: If ``True``, override entrypoint
                with an interactive shell.

        Returns:
            A ``RunResult`` with the container exit
            code.
        """

        return RuntimeLauncher(
            self.spec,
            self.root,
        ).run(
            image=image,
            port=port,
            shell=shell,
        )
