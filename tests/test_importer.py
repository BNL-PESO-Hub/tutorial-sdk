import json

import pytest

from tutorial_sdk.scaffold.discovery import (
    _deps_from_install_commands,
    _deps_from_notebook,
    _deps_from_pyproject,
    _deps_from_requirements,
    _detect_python_version,
    _imports_from_code,
    _parse_pyproject,
)
from tutorial_sdk.scaffold.importer import ProjectImporter
from tutorial_sdk.spec import TutorialSpec


def _make_notebook(cells=None):
    """Return a minimal .ipynb JSON string."""
    if cells is None:
        cells = []
    nb = {
        "cells": cells,
        "metadata": {},
        "nbformat": 4,
        "nbformat_minor": 5,
    }
    return json.dumps(nb)


def _code_cell(source):
    """Return a code cell dict with *source* lines."""
    if isinstance(source, str):
        source = [source]
    return {
        "cell_type": "code",
        "execution_count": None,
        "metadata": {},
        "outputs": [],
        "source": source,
    }


# ---- Notebook discovery ----


def test_discover_notebooks(tmp_path):
    nb_dir = tmp_path / "notebooks"
    nb_dir.mkdir()
    (nb_dir / "01-intro.ipynb").write_text(_make_notebook())
    (nb_dir / "02-analysis.ipynb").write_text(_make_notebook())

    # Checkpoint dirs should be skipped.
    ckpt = nb_dir / ".ipynb_checkpoints"
    ckpt.mkdir()
    (ckpt / "01-intro-checkpoint.ipynb").write_text(
        _make_notebook(),
    )

    importer = ProjectImporter()
    result = importer._discover(tmp_path)

    assert len(result["notebooks"]) == 2
    assert "notebooks/01-intro.ipynb" in result["notebooks"]
    assert any("checkpoint" not in nb for nb in result["notebooks"])


def test_discover_nested_notebooks(tmp_path):
    deep = tmp_path / "a" / "b"
    deep.mkdir(parents=True)
    (deep / "deep.ipynb").write_text(_make_notebook())
    (tmp_path / "top.ipynb").write_text(_make_notebook())

    result = ProjectImporter()._discover(tmp_path)
    assert len(result["notebooks"]) == 2


# ---- Script / data / doc discovery ----


def test_discover_scripts_in_notebook_dirs(tmp_path):
    nb_dir = tmp_path / "notebooks"
    nb_dir.mkdir()
    (nb_dir / "01.ipynb").write_text(_make_notebook())
    (nb_dir / "helper.py").write_text("# helper")

    # Scripts outside notebook dirs should be ignored.
    (tmp_path / "setup.py").write_text("# setup")

    result = ProjectImporter()._discover(tmp_path)
    assert "notebooks/helper.py" in result["scripts"]
    assert "setup.py" not in result["scripts"]


def test_discover_data_in_notebook_dirs(tmp_path):
    nb_dir = tmp_path / "notebooks"
    nb_dir.mkdir()
    (nb_dir / "01.ipynb").write_text(_make_notebook())

    data_dir = nb_dir / "data"
    data_dir.mkdir()
    (data_dir / "train.csv").write_text("a,b\n1,2")
    (data_dir / "config.json").write_text("{}")

    # Data outside notebook dirs should be ignored.
    (tmp_path / "outside.csv").write_text("x")

    result = ProjectImporter()._discover(tmp_path)
    assert "notebooks/data/train.csv" in result["data"]
    assert "notebooks/data/config.json" in result["data"]
    assert "outside.csv" not in result["data"]


def test_discover_docs(tmp_path):
    nb_dir = tmp_path / "notebooks"
    nb_dir.mkdir()
    (nb_dir / "01.ipynb").write_text(_make_notebook())
    (nb_dir / "notes.md").write_text("# Notes")

    # Top-level README is always included.
    (tmp_path / "README.md").write_text("# Project")

    result = ProjectImporter()._discover(tmp_path)
    assert "README.md" in result["docs"]
    assert "notebooks/notes.md" in result["docs"]


# ---- Import extraction ----


def test_imports_from_code():
    code = (
        "import numpy as np\n"
        "import os\n"
        "from pathlib import Path\n"
        "from sklearn.model_selection import train_test_split\n"
        "import pandas\n"
    )
    packages = _imports_from_code(code)

    assert "numpy" in packages
    assert "scikit-learn" in packages
    assert "pandas" in packages
    # stdlib should be filtered
    assert "os" not in packages
    assert "pathlib" not in packages


def test_imports_from_code_syntax_error():
    code = "import foo\nthis is not python {{{"
    result = _imports_from_code(code)
    # Should return empty on parse failure, not crash.
    assert isinstance(result, set)


# ---- Install command parsing ----


def test_pip_install_magic():
    code = "%pip install torch torchvision --quiet\n"
    result = _deps_from_install_commands(code)
    assert "torch" in result
    assert "torchvision" in result


def test_pip_install_shell():
    code = "!pip install requests>=2.28\n"
    result = _deps_from_install_commands(code)
    assert "requests>=2.28" in result


def test_conda_install():
    code = "%conda install scipy numpy\n"
    result = _deps_from_install_commands(code)
    assert "scipy" in result
    assert "numpy" in result


def test_uv_pip_install():
    code = "!uv pip install httpx\n"
    result = _deps_from_install_commands(code)
    assert "httpx" in result


# ---- requirements.txt parsing ----


def test_deps_from_requirements(tmp_path):
    req = tmp_path / "requirements.txt"
    req.write_text(
        "numpy>=1.24\npandas\n# comment\n-e ./local_pkg\nscikit-learn>=1.0\n"
    )
    result = _deps_from_requirements(tmp_path)
    assert "numpy>=1.24" in result
    assert "pandas" in result
    assert "scikit-learn>=1.0" in result


def test_deps_from_requirements_subdir(tmp_path):
    req_dir = tmp_path / "requirements"
    req_dir.mkdir()
    (req_dir / "base.txt").write_text("flask\n")
    (req_dir / "dev.txt").write_text("pytest\n")

    result = _deps_from_requirements(tmp_path)
    assert "flask" in result
    assert "pytest" in result


# ---- pyproject.toml parsing ----


def test_deps_from_pyproject(tmp_path):
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        '[project]\nname = "my-project"\n'
        'dependencies = ["requests>=2.28", "click"]\n'
    )
    result = _deps_from_pyproject(tmp_path)
    assert "requests>=2.28" in result
    assert "click" in result


def test_deps_from_pyproject_missing(tmp_path):
    result = _deps_from_pyproject(tmp_path)
    assert result == set()


# ---- Python version detection ----


def test_detect_python_version_file(tmp_path):
    (tmp_path / ".python-version").write_text("3.12.1\n")
    version = _detect_python_version(tmp_path)
    assert version == "3.12"


def test_detect_python_version_pyproject(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nrequires-python = ">=3.10"\n'
    )
    version = _detect_python_version(tmp_path)
    assert version == "3.10"


def test_detect_python_version_default(tmp_path):
    version = _detect_python_version(tmp_path)
    assert version == "3.11"


# ---- Notebook dependency extraction ----


def test_deps_from_notebook(tmp_path):
    nb = tmp_path / "test.ipynb"
    nb.write_text(
        _make_notebook(
            [
                _code_cell("import numpy as np\nimport pandas as pd\n"),
                _code_cell(
                    "%pip install seaborn\n",
                ),
            ]
        )
    )
    result = _deps_from_notebook(nb)
    assert "numpy" in result
    assert "pandas" in result
    assert "seaborn" in result


# ---- Full scan ----


def test_full_scan(tmp_path):
    source = tmp_path / "my-repo"
    nb_dir = source / "notebooks"
    nb_dir.mkdir(parents=True)

    (nb_dir / "01-intro.ipynb").write_text(
        _make_notebook(
            [
                _code_cell(
                    "import numpy as np\nimport matplotlib.pyplot as plt\n"
                ),
            ]
        )
    )
    (nb_dir / "helper.py").write_text("def f(): pass\n")
    (source / "README.md").write_text("# My Repo\n")
    (source / "requirements.txt").write_text("requests\n")

    importer = ProjectImporter()
    project = importer.scan(source)

    spec = project.spec
    assert spec.name == "my-repo"
    assert len(spec.content.notebooks) == 1
    assert "notebooks/helper.py" in spec.content.scripts
    assert "README.md" in spec.content.docs
    assert "numpy" in spec.dependencies.pip
    assert "matplotlib" in spec.dependencies.pip
    assert "requests" in spec.dependencies.pip
    assert "jupyterlab" in spec.dependencies.pip
    assert "ipykernel" in spec.dependencies.pip

    # tutorial.yml should be written.
    target = source.parent / "my-repo_tutorial"
    assert (target / "tutorial.yml").exists()

    # Files should be copied.
    assert (target / "notebooks" / "01-intro.ipynb").exists()


def test_scan_custom_target(tmp_path):
    source = tmp_path / "repo"
    nb_dir = source / "notebooks"
    nb_dir.mkdir(parents=True)
    (nb_dir / "demo.ipynb").write_text(_make_notebook())

    custom = tmp_path / "custom-output"
    project = ProjectImporter().scan(source, custom)

    assert project.root == custom
    assert (custom / "tutorial.yml").exists()


def test_scan_no_notebooks(tmp_path):
    source = tmp_path / "empty-repo"
    source.mkdir()
    (source / "README.md").write_text("# empty")

    with pytest.raises(Exception, match="No Jupyter notebooks"):
        ProjectImporter().scan(source)


def test_scan_nonexistent_dir(tmp_path):
    with pytest.raises(
        Exception,
        match="does not exist",
    ):
        ProjectImporter().scan(tmp_path / "nope")


# ---- Conflict handling ----


def test_scan_existing_tutorial_yml(tmp_path):
    source = tmp_path / "repo"
    nb_dir = source / "notebooks"
    nb_dir.mkdir(parents=True)
    (nb_dir / "demo.ipynb").write_text(_make_notebook())

    # Create target with existing tutorial.yml.
    target = tmp_path / "repo_tutorial"
    target.mkdir()
    spec = TutorialSpec(name="existing-project")
    spec.write(target / "tutorial.yml")

    project = ProjectImporter().scan(source, target)
    assert project.spec.name == "existing-project"


def test_scan_existing_invalid_tutorial_yml(tmp_path):
    source = tmp_path / "repo"
    nb_dir = source / "notebooks"
    nb_dir.mkdir(parents=True)
    (nb_dir / "demo.ipynb").write_text(_make_notebook())

    target = tmp_path / "repo_tutorial"
    target.mkdir()
    (target / "tutorial.yml").write_text("not: [valid: yaml: spec")

    with pytest.raises(Exception, match="invalid"):
        ProjectImporter().scan(source, target)


# ---- Module-to-package mapping ----


def test_module_to_package_mapping():
    code = (
        "import cv2\n"
        "from PIL import Image\n"
        "from sklearn.metrics import accuracy_score\n"
        "import yaml\n"
    )
    packages = _imports_from_code(code)
    assert "opencv-python" in packages
    assert "Pillow" in packages
    assert "scikit-learn" in packages
    assert "PyYAML" in packages


# ---- pyproject optional-dependencies ----


def test_pyproject_optional_dependencies(tmp_path):
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        '[project]\nname = "my-pkg"\n'
        'dependencies = ["requests"]\n\n'
        "[project.optional-dependencies]\n"
        'dev = ["pytest", "ruff"]\n'
        'ml = ["torch>=2.0"]\n'
    )
    result = _deps_from_pyproject(tmp_path)
    assert "requests" in result
    assert "pytest" in result
    assert "ruff" in result
    assert "torch>=2.0" in result


def test_parse_pyproject_metadata(tmp_path):
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        '[project]\nname = "rhapsody-py"\n'
        'dependencies = ["click"]\n\n'
        "[project.optional-dependencies]\n"
        'extra = ["flask"]\n'
    )
    info = _parse_pyproject(tmp_path)
    assert info.project_name == "rhapsody-py"
    assert "click" in info.dependencies
    assert "flask" in info.optional_deps
    assert "rhapsody-py" in info.all_package_names
    assert "click" in info.all_package_names
    assert "flask" in info.all_package_names


# ---- Cross-check: import vs pyproject name ----


def test_cross_check_import_vs_pyproject_name(tmp_path):
    """Rhapsody scenario: AST import 'rhapsody', pip install
    'rhapsody-py', pyproject name 'rhapsody-py'.  Only
    'rhapsody-py' should appear, not 'rhapsody'."""

    source = tmp_path / "repo"
    nb_dir = source / "notebooks"
    nb_dir.mkdir(parents=True)

    (nb_dir / "demo.ipynb").write_text(
        _make_notebook(
            [
                _code_cell("import rhapsody\nrhapsody.run()\n"),
                _code_cell(
                    '%pip install "rhapsody-py"\n',
                ),
            ]
        )
    )

    pyproject = source / "pyproject.toml"
    pyproject.write_text(
        '[project]\nname = "rhapsody-py"\ndependencies = ["click"]\n'
    )

    importer = ProjectImporter()
    discovery = importer._discover(source)
    pip = discovery["pip"]

    assert "rhapsody-py" in pip
    assert "rhapsody" not in pip
    assert "click" in pip


def test_cross_check_import_prefix_match(tmp_path):
    """Dragon scenario: AST import 'dragon', pyproject
    optional-dep 'dragonhpc==0.13.2'.  'dragonhpc==0.13.2'
    should appear, 'dragon' should not."""

    source = tmp_path / "repo"
    nb_dir = source / "notebooks"
    nb_dir.mkdir(parents=True)

    (nb_dir / "demo.ipynb").write_text(
        _make_notebook(
            [
                _code_cell("from dragon.infrastructure.policy import Policy\n"),
            ]
        )
    )

    pyproject = source / "pyproject.toml"
    pyproject.write_text(
        '[project]\nname = "my-project"\n'
        "dependencies = []\n\n"
        "[project.optional-dependencies]\n"
        'drag = ["dragonhpc==0.13.2"]\n'
    )

    importer = ProjectImporter()
    discovery = importer._discover(source)
    pip = discovery["pip"]

    assert "dragonhpc==0.13.2" in pip
    assert "dragon" not in pip


def test_cross_check_project_name_included(tmp_path):
    """project.name itself should be installed as a dep."""

    source = tmp_path / "repo"
    nb_dir = source / "notebooks"
    nb_dir.mkdir(parents=True)
    (nb_dir / "demo.ipynb").write_text(_make_notebook())

    pyproject = source / "pyproject.toml"
    pyproject.write_text(
        '[project]\nname = "my-cool-lib"\ndependencies = ["requests"]\n'
    )

    importer = ProjectImporter()
    discovery = importer._discover(source)
    pip = discovery["pip"]

    assert "my-cool-lib" in pip
    assert "requests" in pip


def test_cross_check_ambiguous_prefix_kept(tmp_path):
    """When multiple pyproject packages match a prefix,
    the AST name is kept (no false replacement)."""

    source = tmp_path / "repo"
    nb_dir = source / "notebooks"
    nb_dir.mkdir(parents=True)

    (nb_dir / "demo.ipynb").write_text(
        _make_notebook(
            [
                _code_cell("import dragon\n"),
            ]
        )
    )

    pyproject = source / "pyproject.toml"
    pyproject.write_text(
        '[project]\nname = "my-project"\n'
        'dependencies = ["dragonhpc", "dragonfly"]\n'
    )

    importer = ProjectImporter()
    discovery = importer._discover(source)
    pip = discovery["pip"]

    # Both pyproject deps present.
    assert "dragonhpc" in pip
    assert "dragonfly" in pip
    # AST name kept because of ambiguous prefix.
    assert "dragon" in pip


# ---- Normalize helper ----


def test_normalize_package_name():
    from tutorial_sdk.scaffold.discovery import (
        _normalize_package_name,
    )

    assert _normalize_package_name("Rhapsody-Py") == ("rhapsody-py")
    assert _normalize_package_name("dragonhpc==0.13.2") == ("dragonhpc")
    assert _normalize_package_name("my_pkg[extra]>=1.0") == ("my-pkg")
    assert _normalize_package_name("PyYAML") == "pyyaml"
    assert _normalize_package_name("scikit.learn") == ("scikit-learn")


# ---- Consolidation ----


def test_consolidate_deps():
    from tutorial_sdk.scaffold.discovery import (
        _consolidate_deps,
    )

    deps = {
        "rhapsody-py",
        "rhapsody-py[dask,radical_pilot,dragon,vllm-dragon]",
        "rhapsody-py[dask]",
        "rhapsody-py[dev,docs,ci,examples,backends]",
    }
    result = _consolidate_deps(deps)

    # Should produce exactly one entry.
    rhapsody_entries = [d for d in result if d.startswith("rhapsody-py")]
    assert len(rhapsody_entries) == 1

    entry = rhapsody_entries[0]
    assert entry.startswith("rhapsody-py[")

    # All extras should be present.
    for extra in [
        "dask",
        "radical_pilot",
        "dragon",
        "vllm-dragon",
        "dev",
        "docs",
        "ci",
        "examples",
        "backends",
    ]:
        assert extra in entry


def test_consolidate_preserves_version():
    from tutorial_sdk.scaffold.discovery import (
        _consolidate_deps,
    )

    deps = {
        "rhapsody-py>=1.0",
        "rhapsody-py[dask]",
    }
    result = _consolidate_deps(deps)

    rhapsody = [d for d in result if d.startswith("rhapsody-py")]
    assert len(rhapsody) == 1
    assert ">=1.0" in rhapsody[0]
    assert "[dask]" in rhapsody[0]


def test_consolidate_no_duplicates_single():
    from tutorial_sdk.scaffold.discovery import (
        _consolidate_deps,
    )

    deps = {"numpy", "pandas", "requests>=2.28"}
    result = _consolidate_deps(deps)
    assert result == deps


def test_consolidate_end_to_end(tmp_path):
    """Multiple pip install commands with different extras
    for the same package should produce one entry."""

    source = tmp_path / "repo"
    nb_dir = source / "notebooks"
    nb_dir.mkdir(parents=True)

    (nb_dir / "demo.ipynb").write_text(
        _make_notebook(
            [
                _code_cell(
                    '%pip install "rhapsody-py[dask]"\n',
                ),
                _code_cell(
                    '%pip install "rhapsody-py[dev,docs]"\n',
                ),
            ]
        )
    )

    pyproject = source / "pyproject.toml"
    pyproject.write_text('[project]\nname = "rhapsody-py"\ndependencies = []\n')

    importer = ProjectImporter()
    discovery = importer._discover(source)
    pip = discovery["pip"]

    rhapsody = [d for d in pip if d.startswith("rhapsody-py")]
    assert len(rhapsody) == 1
    assert "dask" in rhapsody[0]
    assert "dev" in rhapsody[0]
    assert "docs" in rhapsody[0]
