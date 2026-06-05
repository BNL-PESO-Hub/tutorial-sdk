"""Dependency extraction, normalisation, and version detection."""

import ast
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

from .constants import (
    _EXTRAS_RE,
    _INSTALL_RE,
    _MODULE_TO_PACKAGE,
    _PKG_TOKEN_RE,
    _PYTHON_VERSION,
    _PYTHON_VERSION_STR,
    _VERSION_SPEC_RE,
)


@dataclass
class _PyprojectInfo:
    """Metadata extracted from pyproject.toml.

    Attributes:
        project_name: The ``[project].name`` value,
            or ``None`` if absent.
        dependencies: Package specs from
            ``[project].dependencies``.
        optional_deps: Package specs from all
            ``[project].optional-dependencies`` groups.
        all_package_names: PEP 503-normalised bare
            names built from *dependencies*,
            *optional_deps*, and *project_name*.
    """

    project_name: str | None = None
    dependencies: set[str] = field(default_factory=set)
    optional_deps: set[str] = field(default_factory=set)
    all_package_names: set[str] = field(
        default_factory=set,
    )


def _normalize_package_name(raw: str) -> str:
    """PEP 503-normalise a package spec to a bare name.

    Strips extras brackets, version specifiers, and
    normalises separators (``-``, ``_``, ``.``) to ``-``.

    Args:
        raw: A package spec string such as
            ``"my_pkg[extra]>=1.0"``.

    Returns:
        The normalised bare name, e.g. ``"my-pkg"``.
    """

    name = _EXTRAS_RE.sub("", raw).strip()
    name = _VERSION_SPEC_RE.sub("", name).strip()
    return re.sub(r"[-_.]+", "-", name).lower()


def _consolidate_deps(deps: set[str]) -> set[str]:
    """Merge entries that share the same base package.

    Multiple entries like ``pkg``, ``pkg[a]``,
    ``pkg[b,c]`` are collapsed into a single
    ``pkg[a,b,c]`` with extras merged and the first
    version constraint preserved.

    Args:
        deps: Set of package spec strings, possibly
            containing duplicates with different extras.

    Returns:
        A new set with duplicate base packages merged.
    """

    groups: dict[str, list[str]] = {}
    for dep in deps:
        norm = _normalize_package_name(dep)
        groups.setdefault(norm, []).append(dep)

    result: set[str] = set()
    for entries in groups.values():
        if len(entries) == 1:
            result.add(entries[0])
            continue

        all_extras: set[str] = set()
        version_spec = ""
        raw_name = ""

        for entry in entries:
            extras_match = _EXTRAS_RE.search(entry)
            if extras_match:
                inner = extras_match.group(0)[1:-1]
                for e in inner.split(","):
                    e = e.strip()
                    if e:
                        all_extras.add(e)

            name_part = _EXTRAS_RE.sub(
                "",
                entry,
            ).strip()
            ver = _VERSION_SPEC_RE.search(name_part)
            if ver:
                if not version_spec:
                    version_spec = ver.group(0)
                name_part = name_part[: ver.start()].strip()

            if not raw_name:
                raw_name = name_part

        consolidated = raw_name
        if all_extras:
            consolidated += f"[{','.join(sorted(all_extras))}]"
        if version_spec:
            consolidated += version_spec
        result.add(consolidated)

    return result


def _imports_from_code(code: str) -> set[str]:
    """Parse import statements and map to packages."""

    modules: set[str] = set()
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return modules

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                top = alias.name.split(".")[0]
                modules.add(top)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                top = node.module.split(".")[0]
                modules.add(top)

    # Filter stdlib and map to PyPI names.
    stdlib = sys.stdlib_module_names
    packages: set[str] = set()
    for mod in modules:
        if mod in stdlib:
            continue
        pkg = _MODULE_TO_PACKAGE.get(mod, mod)
        packages.add(pkg)

    return packages


def _deps_from_install_commands(
    code: str,
) -> set[str]:
    """Parse pip/conda/uv install lines.

    Handles ``%pip install``, ``!pip install``,
    ``%conda install``, and ``!uv pip install``
    magic and shell commands.  Surrounding quotes
    on package specs are stripped.

    Args:
        code: Source code string from a notebook
            code cell.

    Returns:
        Set of extracted package spec strings.
    """

    packages: set[str] = set()
    for match in _INSTALL_RE.finditer(code):
        args = match.group(1)
        for token in args.split():
            # Skip flags like --quiet, -U, etc.
            if token.startswith("-"):
                continue
            # Strip surrounding quotes.
            token = token.strip("\"'")
            m = _PKG_TOKEN_RE.match(token)
            if m:
                packages.add(m.group(1))
    return packages


def _deps_from_notebook_split(
    path: Path,
) -> tuple[set[str], set[str]]:
    """Extract imports and install names separately.

    Args:
        path: Absolute path to a ``.ipynb`` file.

    Returns:
        A ``(imports, installs)`` tuple where
        *imports* are AST-derived package names and
        *installs* are packages from pip/conda
        install commands.
    """

    imports: set[str] = set()
    installs: set[str] = set()
    try:
        nb = json.loads(
            path.read_text(encoding="utf-8"),
        )
    except (json.JSONDecodeError, OSError):
        return imports, installs

    cells = nb.get("cells", [])
    for cell in cells:
        if cell.get("cell_type") != "code":
            continue
        source_lines = cell.get("source", [])
        if isinstance(source_lines, list):
            code = "".join(source_lines)
        else:
            code = source_lines

        imports.update(
            _imports_from_code(code),
        )
        installs.update(
            _deps_from_install_commands(code),
        )

    return imports, installs


def _deps_from_notebook(path: Path) -> set[str]:
    """Extract dependencies from a single notebook.

    Args:
        path: Absolute path to a ``.ipynb`` file.

    Returns:
        Union of AST-derived imports and install
        command packages.
    """

    imports, installs = _deps_from_notebook_split(path)
    return imports | installs


def _deps_from_requirements(
    source: Path,
) -> set[str]:
    """Parse requirements.txt and requirements/*.txt."""

    packages: set[str] = set()
    candidates = [source / "requirements.txt"]
    req_dir = source / "requirements"
    if req_dir.is_dir():
        candidates.extend(sorted(req_dir.glob("*.txt")))

    for req_file in candidates:
        if not req_file.exists():
            continue
        for line in req_file.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("-"):
                continue
            m = _PKG_TOKEN_RE.match(line)
            if m:
                packages.add(m.group(1))

    return packages


def _parse_pyproject(
    source: Path,
) -> _PyprojectInfo:
    """Parse pyproject.toml for dependency metadata.

    Reads ``[project].name``, ``[project].dependencies``,
    and ``[project].optional-dependencies``, building a
    normalised name index for cross-referencing.

    Args:
        source: Root directory containing
            ``pyproject.toml``.

    Returns:
        A populated ``_PyprojectInfo`` instance
        (empty if the file is missing or unparseable).
    """

    info = _PyprojectInfo()
    pyproject = source / "pyproject.toml"
    if not pyproject.exists():
        return info

    try:
        if sys.version_info >= _PYTHON_VERSION:
            import tomllib
        else:
            try:
                import tomllib
            except ImportError:
                import tomli as tomllib  # type: ignore[no-redef]

        data = tomllib.loads(
            pyproject.read_text(encoding="utf-8"),
        )
        project = data.get("project", {})

        # [project].name
        info.project_name = project.get("name")

        # [project].dependencies
        for dep in project.get("dependencies", []):
            m = _PKG_TOKEN_RE.match(dep.strip())
            if m:
                info.dependencies.add(m.group(1))

        # [project].optional-dependencies
        opt = project.get(
            "optional-dependencies",
            {},
        )
        for _group, deps in opt.items():
            for dep in deps:
                m = _PKG_TOKEN_RE.match(
                    dep.strip(),
                )
                if m:
                    info.optional_deps.add(
                        m.group(1),
                    )

        # Build normalised name index.
        all_raw = info.dependencies | info.optional_deps
        if info.project_name:
            all_raw = all_raw | {info.project_name}
        info.all_package_names = {_normalize_package_name(r) for r in all_raw}
    except Exception:
        pass

    return info


def _deps_from_pyproject(
    source: Path,
) -> set[str]:
    """Parse ``[project]`` dependencies from pyproject.toml.

    Convenience wrapper around :func:`_parse_pyproject`
    that returns the union of core and optional deps.

    Args:
        source: Root directory containing
            ``pyproject.toml``.

    Returns:
        Set of dependency spec strings.
    """

    info = _parse_pyproject(source)
    return info.dependencies | info.optional_deps


def _reconcile_deps(
    import_names: set[str],
    install_names: set[str],
    req_deps: set[str],
    pyproject_info: _PyprojectInfo,
) -> set[str]:
    """Cross-reference imports against known packages.

    Authoritative sources (pyproject deps, install
    commands, requirements files) take precedence
    over AST-derived import names.

    Args:
        import_names: AST-derived package names from
            notebook code cells.
        install_names: Package specs from pip/conda
            install commands in notebooks.
        req_deps: Package specs from requirements
            files.
        pyproject_info: Parsed pyproject.toml
            metadata.

    Returns:
        Reconciled set of dependency spec strings.
    """

    # Start with authoritative deps.
    deps: set[str] = set()
    deps.update(pyproject_info.dependencies)
    deps.update(pyproject_info.optional_deps)
    deps.update(req_deps)
    deps.update(install_names)

    # Include project.name as installable dep.
    if pyproject_info.project_name:
        deps.add(pyproject_info.project_name)

    # Build normalised lookup of known names.
    known = {_normalize_package_name(d) for d in deps}
    known.update(pyproject_info.all_package_names)

    # Reconcile each AST-derived import.
    for imp in import_names:
        norm = _normalize_package_name(imp)
        if norm in known:
            # Already covered by an authoritative
            # source — skip the AST name.
            continue

        # Prefix heuristic: if exactly one known
        # name starts with the import name, the
        # AST name is likely a module alias for
        # that package (e.g. "dragon" → "dragonhpc").
        prefix_matches = [k for k in known if k.startswith(norm)]
        if len(prefix_matches) == 1:
            continue

        # No match — keep the AST-derived name.
        deps.add(imp)

    return deps


def _extract_dependencies(
    source: Path,
    notebooks: list[str],
) -> set[str]:
    """Collect pip dependencies from all sources.

    Cross-references notebook imports and install
    commands against pyproject.toml metadata to
    resolve module-name vs PyPI-package mismatches.

    Args:
        source: Root directory of the project being
            imported.
        notebooks: Relative paths to discovered
            notebook files.

    Returns:
        Consolidated set of pip dependency specs.
    """

    # Step 1: parse pyproject.toml.
    pyproject_info = _parse_pyproject(source)

    # Step 2: collect notebook deps (split).
    import_names: set[str] = set()
    install_names: set[str] = set()
    for nb_rel in notebooks:
        nb_path = source / nb_rel
        nb_imports, nb_installs = _deps_from_notebook_split(nb_path)
        import_names.update(nb_imports)
        install_names.update(nb_installs)

    # Step 3: requirements.txt deps.
    req_deps = _deps_from_requirements(source)

    # Step 4: reconcile.
    deps = _reconcile_deps(
        import_names,
        install_names,
        req_deps,
        pyproject_info,
    )

    # Step 5: always ensure core runtime deps.
    deps.update({"jupyterlab", "ipykernel"})

    # Step 6: consolidate duplicate base packages.
    return _consolidate_deps(deps)


def _detect_python_version(
    source: Path,
) -> str:
    """Best-effort Python version detection."""

    # .python-version file.
    pv = source / ".python-version"
    if pv.exists():
        version = pv.read_text().strip().split("\n")[0]
        version = version.strip()
        if version:
            # Normalise to major.minor.
            parts = version.split(".")
            if len(parts) >= 2:
                return f"{parts[0]}.{parts[1]}"
            return version

    # pyproject.toml requires-python.
    pyproject = source / "pyproject.toml"
    if pyproject.exists():
        try:
            if sys.version_info >= _PYTHON_VERSION:
                import tomllib
            else:
                try:
                    import tomllib
                except ImportError:
                    import tomli as tomllib  # type: ignore[no-redef]

            data = tomllib.loads(
                pyproject.read_text(encoding="utf-8"),
            )
            req_py = data.get("project", {}).get(
                "requires-python",
                "",
            )
            if req_py:
                # Extract version digits from e.g. ">=3.NN"
                m = re.search(r"(\d+\.\d+)", req_py)
                if m:
                    return m.group(1)
        except Exception:
            pass

    return _PYTHON_VERSION_STR
