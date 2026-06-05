"""Built-in scaffolding templates."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from ..config import DEFAULT_CONFIG
from ..errors import ScaffoldError
from ..spec import (
    BuildSpec,
    ContentSpec,
    DependencySpec,
    EntrypointSpec,
    RuntimeSpec,
    TutorialSpec,
    ValidationSpec,
)
from .constants import _EMPTY_NOTEBOOK, _TEMPLATE_CONFIGS

if TYPE_CHECKING:
    from ..project import TutorialProject


class ProjectScaffolder:
    """Create starter tutorial projects.

    The scaffolder writes a named template directory,
    creates a default tutorial YAML configuration file,
    and returns a loaded ``TutorialProject``.
    """

    SUPPORTED = set(_TEMPLATE_CONFIGS)

    def scaffold(
        self,
        template: str,
        path: str | Path,
        name: str | None = None,
    ) -> TutorialProject:
        """Create a tutorial project from a template.

        Args:
            template: Template name (one of
                :attr:`SUPPORTED`).
            path: Target directory for the project.
            name: Optional project name override.
                Defaults to the directory basename.

        Returns:
            A fully initialised ``TutorialProject``.

        Raises:
            ScaffoldError: If *template* is not
                recognised.
        """

        from ..project import TutorialProject

        if template not in self.SUPPORTED:
            raise ScaffoldError(f"Unknown template: {template}")

        cfg = _TEMPLATE_CONFIGS[template]
        root = Path(path)
        root.mkdir(parents=True, exist_ok=True)
        project_name = name or root.name or "my-tutorial"

        # Create directories.
        for dirname in cfg.get("dirs", []):
            (root / dirname).mkdir(
                parents=True,
                exist_ok=True,
            )
        (root / "docs").mkdir(exist_ok=True)

        # Create README.
        readme = root / "README.md"
        if not readme.exists():
            readme.write_text(f"# {project_name}\n")

        # Create notebook stubs.
        all_notebooks = (
            list(cfg.get("notebooks", []))
            + list(cfg.get("exercises", []))
            + list(cfg.get("solutions", []))
        )
        for nb_path in all_notebooks:
            nb_file = root / nb_path
            nb_file.parent.mkdir(
                parents=True,
                exist_ok=True,
            )
            if not nb_file.exists():
                nb_file.write_text(
                    _EMPTY_NOTEBOOK,
                )

        # Determine default notebook for entrypoint.
        first_notebook = cfg["notebooks"][0] if cfg.get("notebooks") else None

        build_overrides = cfg.get("build", {})
        validation_overrides = cfg.get(
            "validation",
            {},
        )

        spec = TutorialSpec(
            name=project_name,
            title=project_name.replace("-", " ").title(),
            description=str(cfg["description"]),
            runtime=RuntimeSpec(),
            dependencies=DependencySpec(
                pip=list(cfg.get("pip", [])),
            ),
            content=ContentSpec(
                notebooks=list(
                    cfg.get("notebooks", []),
                ),
                exercises=list(
                    cfg.get("exercises", []),
                ),
                solutions=list(
                    cfg.get("solutions", []),
                ),
                docs=["README.md"],
            ),
            build=BuildSpec(**build_overrides),
            validation=ValidationSpec(
                **validation_overrides,
            ),
            entrypoint=EntrypointSpec(
                kind="jupyterlab",
                default_notebook=first_notebook,
            ),
        )
        config_path = root / DEFAULT_CONFIG
        if not config_path.exists():
            spec.write(config_path)
        return TutorialProject(spec, root, config_path)
