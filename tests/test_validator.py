import json
from unittest.mock import MagicMock, patch

from tutorial_sdk.backend import DockerCliBackend
from tutorial_sdk.spec import (
    TutorialSpec,
    ContentSpec,
    DependencySpec,
    ValidationSpec,
)
from tutorial_sdk.validator.assets import AssetValidator
from tutorial_sdk.validator.dependencies import DependencyValidator
from tutorial_sdk.validator.notebooks import NotebookValidator
from tutorial_sdk.validator.container import ContainerValidator


def test_asset_validator(tmp_path):
    # Setup some test paths
    (tmp_path / "notebooks").mkdir()
    nb_path = tmp_path / "notebooks/01-intro.ipynb"
    nb_path.write_text("{}")

    spec = TutorialSpec(
        name="test",
        content=ContentSpec(
            notebooks=["notebooks/01-intro.ipynb"],
            data=["data/sample.csv"],  # Missing path
        ),
    )

    validator = AssetValidator(spec, root=tmp_path)
    report = validator.validate()

    assert not report.passed
    assert "Missing declared paths" in report.errors[0]

    # Write the missing path
    (tmp_path / "data").mkdir()
    (tmp_path / "data/sample.csv").write_text("a,b,c")

    report2 = validator.validate()
    assert report2.passed
    assert "All declared paths exist." in report2.checks[0].message


def test_asset_validator_uses_resolver(tmp_path, monkeypatch):
    from tutorial_sdk.resolver import ResolutionResult, TutorialResolver

    calls = []
    spec = TutorialSpec(
        name="test",
        content=ContentSpec(notebooks=["notebooks/missing.ipynb"]),
    )

    def spy_resolve(self, config_path="tutorial.yml"):
        calls.append((self.spec, self.root))
        return ResolutionResult(
            spec=self.spec,
            root=self.root,
            config_path=self.root / config_path,
            content_paths=(self.root / "notebooks/missing.ipynb",),
            missing_paths=(self.root / "notebooks/missing.ipynb",),
        )

    monkeypatch.setattr(TutorialResolver, "resolve", spy_resolve)
    report = AssetValidator(spec, root=tmp_path).validate()
    assert calls == [(spec, tmp_path)]
    assert not report.passed
    assert report.errors == ["Missing declared paths: notebooks/missing.ipynb"]


def test_asset_validator_handles_missing_absolute_path(tmp_path):
    spec = TutorialSpec(
        name="test",
        content=ContentSpec(notebooks=["/nonexistent/abs_missing.ipynb"]),
    )
    report = AssetValidator(spec, root=tmp_path).validate()
    assert not report.passed
    assert report.errors == [
        "Missing declared paths: /nonexistent/abs_missing.ipynb"
    ]


def test_dependency_validator():
    spec = TutorialSpec(
        name="test",
        dependencies=DependencySpec(
            pip=["pytest>=8.0", "nonexistent-module-xyz123"]
        ),
    )
    validator = DependencyValidator(spec)
    report = validator.validate()

    # nonexistent-module-xyz123 should be missing, causing a warning
    assert report.passed  # MVP only emits warnings for missing dependencies
    assert len(report.warnings) == 1
    assert "nonexistent_module_xyz123" in report.warnings[0]


def test_notebook_validator(tmp_path):
    (tmp_path / "notebooks").mkdir()
    nb_clean = tmp_path / "notebooks/clean.ipynb"
    nb_clean.write_text(
        json.dumps(
            {
                "cells": [
                    {"outputs": [{"output_type": "stream", "text": "hello"}]}
                ],
                "metadata": {},
                "nbformat": 4,
                "nbformat_minor": 5,
            }
        )
    )

    nb_dirty = tmp_path / "notebooks/dirty.ipynb"
    nb_dirty.write_text(
        json.dumps(
            {
                "cells": [
                    {
                        "outputs": [
                            {
                                "output_type": "error",
                                "ename": "NameError",
                                "evalue": "xyz",
                            }
                        ]
                    }
                ],
                "metadata": {},
                "nbformat": 4,
                "nbformat_minor": 5,
            }
        )
    )

    spec = TutorialSpec(
        name="test",
        content=ContentSpec(
            notebooks=["notebooks/clean.ipynb", "notebooks/dirty.ipynb"]
        ),
        validation=ValidationSpec(require_clean_execution=True),
    )

    validator = NotebookValidator(spec, root=tmp_path)
    report = validator.validate()

    assert not report.passed
    assert "Notebook execution errors found" in report.errors[0]
    assert "dirty.ipynb: cell 0" in report.errors[0]


@patch("subprocess.run")
@patch.object(DockerCliBackend, "wait_running", return_value=True)
def test_container_validator_success(mock_wait, mock_run):
    spec = TutorialSpec(name="my-app")
    validator = ContainerValidator(spec, backend=DockerCliBackend())

    # 1. rm -f (stale pre-clean) -> returncode 0
    # 2. run -d -> returncode 0, stdout container_id
    # (wait_running is stubbed; polling is covered elsewhere)
    # 3. exec jupyter lab --version -> returncode 0, stdout "4.0.0"
    # 4. rm -f (cleanup final) -> returncode 0

    r1 = MagicMock(returncode=0, stdout="")
    r2 = MagicMock(returncode=0, stdout="container123\n", stderr="")
    r3 = MagicMock(returncode=0, stdout="4.0.0\n", stderr="")
    r4 = MagicMock(returncode=0, stdout="")

    mock_run.side_effect = [r1, r2, r3, r4]

    report = validator.validate()
    assert report.passed
    assert len(report.checks) == 2
    assert report.checks[0].name == "container.start"
    assert report.checks[0].passed
    assert report.checks[1].name == "container.jupyterlab"
    assert report.checks[1].passed


@patch("subprocess.run")
def test_container_validator_fail_start(mock_run):
    spec = TutorialSpec(name="my-app")
    validator = ContainerValidator(spec, backend=DockerCliBackend())

    # 1. rm -f (cleanup) -> returncode 0
    # 2. run -d -> returncode 1, stderr "docker daemon not running"

    r1 = MagicMock(returncode=0, stdout="")
    r2 = MagicMock(returncode=1, stdout="", stderr="docker daemon not running")

    mock_run.side_effect = [r1, r2]

    report = validator.validate()
    assert not report.passed
    assert len(report.checks) == 1
    assert report.checks[0].name == "container.start"
    assert not report.checks[0].passed
    assert "docker daemon not running" in report.errors[0]
