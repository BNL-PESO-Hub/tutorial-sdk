"""Import existing projects as tutorial-sdk projects."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

from ..config import DEFAULT_CONFIG
from ..errors import ConfigError, ScaffoldError
from ..spec import (
    BuildSpec,
    ContentSpec,
    DependencySpec,
    EntrypointSpec,
    RuntimeSpec,
    TutorialSpec,
    ValidationSpec,
)
from .constants import _DATA_EXTENSIONS, _DOC_EXTENSIONS
from .discovery import (
    _detect_python_version,
    _extract_dependencies,
)

if TYPE_CHECKING:
    from ..project import TutorialProject


class ProjectImporter:
    """Discover and import an existing project as a tutorial.

    Importing copies discovered notebooks and related assets into a target
    directory, detects Python dependencies, and writes a populated
    tutorial YAML configuration file.
    """

    def scan(
        self,
        source: str | Path,
        target: str | Path | None = None,
    ) -> TutorialProject:
        """Scan *source* and create a tutorial project at *target*.

        Args:
            source: Path to an existing directory containing
                notebooks and associated files.
            target: Optional target directory for the new
                tutorial project.  Defaults to
                ``{source.name}_tutorial`` next to *source*.

        Returns:
            A fully initialised ``TutorialProject``.

        Raises:
            ScaffoldError: If *source* does not exist or
                contains no notebooks.
        """

        from ..project import TutorialProject

        source = Path(source).resolve()
        if not source.is_dir():
            raise ScaffoldError(f"Source directory does not exist: {source}")

        # Determine target directory.
        if target is None:
            target = source.parent / f"{source.name}_tutorial"
        target = Path(target).resolve()

        # If default config file already exists in target,
        # validate it and report rather than overwriting.
        config_path = target / DEFAULT_CONFIG
        if config_path.exists():
            return self._validate_existing(
                config_path,
                target,
            )

        # Discover content from source.
        discovery = self._discover(source)

        if not discovery["notebooks"]:
            raise ScaffoldError(f"No Jupyter notebooks found in {source}")

        # Derive project name from source directory name.
        project_name = source.name.replace(" ", "-").lower()

        # Detect Python version.
        python_version = _detect_python_version(source)

        # Build the spec.
        spec = self._build_spec(
            project_name,
            discovery,
            python_version,
        )

        # Create target directory and copy files.
        target.mkdir(parents=True, exist_ok=True)
        copied_paths = self._copy_files(
            source,
            target,
            discovery,
        )

        # Write default config file (tutorial YAML file).
        spec = self._rewrite_paths(spec, copied_paths)
        spec.write(config_path)

        # Create README if missing.
        readme = target / "README.md"
        if not readme.exists():
            readme.write_text(f"# {spec.display_title}\n\n{spec.description}\n")

        # Print summary.
        self._print_summary(spec, discovery, target)

        return TutorialProject(spec, target, config_path)

    def scan_url(
        self,
        url: str,
        target: str | Path | None = None,
        remove_clone: bool = False,
    ) -> TutorialProject:
        """Clone a remote repository and scan it.

        Args:
            url: Git-compatible clone URL.
            target: Optional target directory for the project.
            remove_clone: If ``True``, delete the cloned
                repository after importing.  When ``False``
                (the default), the clone is kept for
                inspection.

        Returns:
            A ``TutorialProject`` imported from the clone.
        """

        # Derive repo name from URL.
        repo_name = url.rstrip("/").rsplit("/", 1)[-1].removesuffix(".git")

        if target is None:
            target = Path.cwd() / f"{repo_name}_tutorial"
        target = Path(target).resolve()

        # Clone into a sibling directory of the target.
        clone_dir = target.parent / repo_name
        if clone_dir.exists():
            print(f"Clone directory already exists: {clone_dir}")
        else:
            try:
                subprocess.run(
                    ["git", "clone", "--depth", "1", url, str(clone_dir)],
                    capture_output=True,
                    text=True,
                    check=True,
                )
            except FileNotFoundError:
                raise ScaffoldError(
                    "git is required for --url / --github "
                    "but was not found on PATH."
                )
            except subprocess.CalledProcessError as exc:
                raise ScaffoldError(
                    f"Failed to clone {url}: {exc.stderr.strip()}"
                )

        try:
            result = self.scan(clone_dir, target)
        finally:
            if remove_clone and clone_dir.exists():
                shutil.rmtree(clone_dir)
                print(f"Removed clone: {clone_dir}")

        if not remove_clone:
            print(
                f"Clone kept at: {clone_dir}\n"
                f"  Use --remove-clone to delete it "
                f"after import."
            )

        return result

    def scan_github(
        self,
        org_repo: str,
        target: str | Path | None = None,
        remove_clone: bool = False,
    ) -> TutorialProject:
        """Import a GitHub repository.

        Args:
            org_repo: ``ORG/REPO`` shorthand.
            target: Optional target directory.
            remove_clone: If ``True``, delete the cloned
                repository after importing.

        Returns:
            A ``TutorialProject``.
        """

        url = f"https://github.com/{org_repo}.git"
        return self.scan_url(
            url,
            target,
            remove_clone=remove_clone,
        )

    # ---- discovery helpers ----

    def _discover(
        self,
        source: Path,
    ) -> dict[str, list[str]]:
        """Walk *source* and classify files."""

        notebooks: list[str] = []
        notebook_dirs: set[Path] = set()

        # Step 1: find all notebooks.
        for nb in sorted(source.rglob("*.ipynb")):
            # Skip checkpoint directories.
            if ".ipynb_checkpoints" in nb.parts:
                continue
            rel = str(nb.relative_to(source))
            notebooks.append(rel)
            notebook_dirs.add(nb.parent)

        # Step 2: discover scripts, data, docs scoped to
        # notebook directories and their subdirectories.
        scripts: list[str] = []
        data: list[str] = []
        docs: list[str] = []

        # Expand notebook_dirs to include all subdirectories.
        search_dirs = set(notebook_dirs)
        for nd in list(notebook_dirs):
            for child in nd.rglob("*"):
                if child.is_dir():
                    search_dirs.add(child)

        # Also include subdirs named 'data', 'docs', 'src',
        # 'scripts' if they live under notebook dirs.
        for d in sorted(search_dirs):
            for item in sorted(d.iterdir()):
                if not item.is_file():
                    continue
                if ".ipynb_checkpoints" in item.parts:
                    continue
                rel = str(item.relative_to(source))
                suffix = item.suffix.lower()
                if suffix == ".py":
                    scripts.append(rel)
                elif suffix in _DATA_EXTENSIONS:
                    data.append(rel)
                elif suffix in _DOC_EXTENSIONS and item.name != DEFAULT_CONFIG:
                    docs.append(rel)

        # Also pick up top-level README.
        for name in ("README.md", "README.rst", "README.txt"):
            readme = source / name
            if readme.exists():
                rel = str(readme.relative_to(source))
                if rel not in docs:
                    docs.insert(0, rel)

        # Step 3: extract dependencies.
        pip_deps = _extract_dependencies(
            source,
            notebooks,
        )

        return {
            "notebooks": notebooks,
            "scripts": scripts,
            "data": data,
            "docs": docs,
            "pip": sorted(pip_deps),
        }

    # ---- conflict handling ----

    def _validate_existing(
        self,
        config_path: Path,
        root: Path,
    ) -> TutorialProject:
        """Validate an existing default config file."""

        from ..project import TutorialProject

        try:
            spec = TutorialSpec.load(config_path)
        except ConfigError as exc:
            raise ScaffoldError(
                f"Existing {DEFAULT_CONFIG} at {config_path} "
                f"is invalid: {exc}\n"
                f"Fix or remove it before re-importing."
            )
        print(
            f"Found existing {DEFAULT_CONFIG} at "
            f"{config_path}\n"
            f"  Project: {spec.name} "
            f"(v{spec.version})\n"
            f"  Notebooks: "
            f"{len(spec.content.notebooks)}\n"
            f"  Dependencies: "
            f"{len(spec.dependencies.pip)} pip\n"
            f"Validation passed — no changes made."
        )
        return TutorialProject(spec, root, config_path)

    # ---- file organisation ----

    def _copy_files(
        self,
        source: Path,
        target: Path,
        discovery: dict[str, list[str]],
    ) -> dict[str, str]:
        """Copy discovered files into the target.

        Returns a mapping from original relative path to the
        new relative path inside *target*.
        """

        mapping: dict[str, str] = {}

        # Copy notebooks, preserving their relative structure.
        for nb in discovery["notebooks"]:
            src = source / nb
            dst = target / nb
            dst.parent.mkdir(parents=True, exist_ok=True)
            if not dst.exists():
                shutil.copy2(src, dst)
            mapping[nb] = nb

        # Copy scripts.
        for sc in discovery["scripts"]:
            src = source / sc
            dst = target / sc
            dst.parent.mkdir(parents=True, exist_ok=True)
            if not dst.exists():
                shutil.copy2(src, dst)
            mapping[sc] = sc

        # Copy data files.
        for df in discovery["data"]:
            src = source / df
            dst = target / df
            dst.parent.mkdir(parents=True, exist_ok=True)
            if not dst.exists():
                shutil.copy2(src, dst)
            mapping[df] = df

        # Copy docs.
        for doc in discovery["docs"]:
            src = source / doc
            dst = target / doc
            dst.parent.mkdir(parents=True, exist_ok=True)
            if not dst.exists():
                shutil.copy2(src, dst)
            mapping[doc] = doc

        return mapping

    def _rewrite_paths(
        self,
        spec: TutorialSpec,
        mapping: dict[str, str],
    ) -> TutorialSpec:
        """Update spec paths to match the copied layout."""

        # Currently paths are preserved, so this is a no-op,
        # but it provides a hook for future reorganisation.
        return spec

    def _build_spec(
        self,
        name: str,
        discovery: dict[str, list[str]],
        python_version: str,
    ) -> TutorialSpec:
        """Build a TutorialSpec from discovered content."""

        first_nb = discovery["notebooks"][0] if discovery["notebooks"] else None

        return TutorialSpec(
            name=name,
            title=name.replace("-", " ").replace("_", " ").title(),
            description=(
                f"Tutorial project imported from existing "
                f"repository ({len(discovery['notebooks'])} "
                f"notebook(s) discovered)."
            ),
            runtime=RuntimeSpec(python=python_version),
            dependencies=DependencySpec(
                pip=discovery["pip"],
            ),
            content=ContentSpec(
                notebooks=discovery["notebooks"],
                scripts=discovery["scripts"],
                data=discovery["data"],
                docs=discovery["docs"],
            ),
            build=BuildSpec(),
            validation=ValidationSpec(),
            entrypoint=EntrypointSpec(
                kind="jupyterlab",
                default_notebook=first_nb,
            ),
        )

    def _print_summary(
        self,
        spec: TutorialSpec,
        discovery: dict[str, list[str]],
        target: Path,
    ) -> None:
        """Print a human-readable import summary."""

        print(f"Imported project: {spec.display_title}")
        print(f"  Target: {target}")
        print(f"  Notebooks: {len(discovery['notebooks'])}")
        if discovery["scripts"]:
            print(f"  Scripts:   {len(discovery['scripts'])}")
        if discovery["data"]:
            print(f"  Data:      {len(discovery['data'])}")
        if discovery["docs"]:
            print(f"  Docs:      {len(discovery['docs'])}")
        print(f"  Pip deps:  {len(discovery['pip'])}")
        if discovery["pip"]:
            print(
                f"    {', '.join(discovery['pip'][:10])}"
                + (
                    f" (+{len(discovery['pip']) - 10} more)"
                    if len(discovery["pip"]) > 10
                    else ""
                )
            )
        print(f"  Python:    {spec.runtime.python}")
        print(f"  Config:    {target / DEFAULT_CONFIG}")
        print(
            "\nWARNING: Some Python packages may require system-level "
            "compilation tools (e.g. gcc) to build from source.\n"
            f"If the installation fails, manually add required packages "
            f"(like 'build-essential' and/or 'python3-dev') to the "
            f"'apt' list in the '{DEFAULT_CONFIG}' file."
        )
