import json
from unittest.mock import patch, MagicMock

from tutorial_sdk.cli import main
from tutorial_sdk.spec import TutorialSpec


def test_cli_init(tmp_path):
    # Run init in a tmp directory
    target_dir = tmp_path / "new-tutorial"
    code = main(["init", str(target_dir)])
    assert code == 0
    assert (target_dir / "tutorial.yml").exists()
    assert (target_dir / "notebooks/01-introduction.ipynb").exists()


def test_cli_init_from(tmp_path):
    # Set up a source repo with a notebook.
    source = tmp_path / "existing-repo"
    nb_dir = source / "notebooks"
    nb_dir.mkdir(parents=True)
    nb = {
        "cells": [
            {
                "cell_type": "code",
                "execution_count": None,
                "metadata": {},
                "outputs": [],
                "source": ["import numpy as np\n"],
            }
        ],
        "metadata": {},
        "nbformat": 4,
        "nbformat_minor": 5,
    }
    (nb_dir / "demo.ipynb").write_text(json.dumps(nb))

    target = tmp_path / "my-tutorial"
    code = main(
        [
            "init",
            "--from",
            str(source),
            str(target),
        ]
    )
    assert code == 0
    assert (target / "tutorial.yml").exists()
    spec = TutorialSpec.load(target / "tutorial.yml")
    assert len(spec.content.notebooks) == 1
    assert "numpy" in spec.dependencies.pip


def test_cli_init_from_no_notebooks(tmp_path):
    source = tmp_path / "empty-repo"
    source.mkdir()
    (source / "README.md").write_text("# empty")
    target = tmp_path / "output"
    code = main(
        [
            "init",
            "--from",
            str(source),
            str(target),
        ]
    )
    # Should exit with error code 4 (ScaffoldError).
    assert code == 4


def test_cli_inspect(tmp_path):
    spec_path = tmp_path / "tutorial.yml"
    spec = TutorialSpec(name="my-cool-tutorial", title="Cool Title")
    spec.write(spec_path)

    # We patch sys.stdout to capture prints
    with patch("sys.stdout.write") as mock_stdout:
        code = main(["inspect", "--config", str(spec_path)])
        assert code == 0

        # Verify JSON metadata was printed
        printed = "".join(call.args[0] for call in mock_stdout.call_args_list)
        payload = json.loads(printed)
        assert payload["name"] == "my-cool-tutorial"
        assert payload["title"] == "Cool Title"


def test_cli_generate_dockerfile(tmp_path):
    spec_path = tmp_path / "tutorial.yml"
    spec = TutorialSpec(name="my-tutorial")
    spec.write(spec_path)

    output_dockerfile = tmp_path / "Dockerfile.test"
    code = main(
        [
            "generate",
            "dockerfile",
            "--config",
            str(spec_path),
            "--output",
            str(output_dockerfile),
        ]
    )
    assert code == 0
    assert output_dockerfile.exists()
    assert "FROM python:3.11-slim" in output_dockerfile.read_text()


def test_cli_validate(tmp_path):
    spec_path = tmp_path / "tutorial.yml"
    # Content notebook is missing, so validate should fail (exit 1)
    spec = TutorialSpec(
        name="my-tutorial", content={"notebooks": ["notebooks/missing.ipynb"]}
    )
    spec.write(spec_path)

    val_out = tmp_path / "tutorial-validation.json"
    code = main(
        ["validate", "--config", str(spec_path), "--output", str(val_out.name)]
    )
    assert code == 1
    assert val_out.exists()

    # Check that report is recorded as not passed
    report = json.loads(val_out.read_text())
    assert not report["passed"]


def test_cli_scaffold(tmp_path):
    target_dir = tmp_path / "scaffolded"
    code = main(
        [
            "scaffold",
            "notebook-tutorial",
            "--name",
            "advanced-analysis",
            "--path",
            str(target_dir),
        ]
    )
    assert code == 0
    assert (target_dir / "tutorial.yml").exists()


@patch("tutorial_sdk.project.TutorialProject.build")
@patch("tutorial_sdk.project.TutorialProject.validate")
def test_cli_ci_validate_mode(mock_validate, mock_build, tmp_path):
    spec_path = tmp_path / "tutorial.yml"
    spec = TutorialSpec(name="ci-test")
    spec.write(spec_path)

    # Mock validate to succeed
    mock_validate.return_value = MagicMock(passed=True, warnings=[])

    with patch.dict(
        "os.environ",
        {
            "GITHUB_OUTPUT": str(
                tmp_path / "github_output.txt",
            )
        },
    ):
        # Run CI validate mode
        code = main(
            [
                "ci",
                "--mode",
                "validate",
                "--config",
                str(spec_path),
                "--dockerfile",
                "Dockerfile.ci",
            ]
        )
        assert code == 0

        # Verify log file generated
        log_file = tmp_path / "tutorial-sdk.log"
        assert log_file.exists()
        log_text = log_file.read_text()
        assert "Starting tutorial-sdk CI workflow..." in log_text
        assert "Mode: validate" in log_text

        # Verify manifest generated
        assert (tmp_path / "tutorial-manifest.json").exists()

        # Build should not be called in validate-only mode
        mock_build.assert_not_called()


@patch("tutorial_sdk.project.TutorialProject.build")
def test_cli_build(mock_build, tmp_path):
    spec_path = tmp_path / "tutorial.yml"
    spec = TutorialSpec(name="build-test")
    spec.write(spec_path)

    mock_build.return_value = MagicMock()

    code = main(
        [
            "build",
            "--config",
            str(spec_path),
            "--image",
            "custom-image:latest",
        ]
    )
    assert code == 0
    mock_build.assert_called_once_with(
        image="custom-image:latest",
        platform=None,
    )


@patch("tutorial_sdk.project.TutorialProject.run")
def test_cli_run(mock_run, tmp_path):
    spec_path = tmp_path / "tutorial.yml"
    spec = TutorialSpec(name="run-test")
    spec.write(spec_path)

    mock_run.return_value = MagicMock(returncode=0)

    code = main(
        [
            "run",
            "--config",
            str(spec_path),
            "--image",
            "custom-run-image:latest",
            "--port",
            "9000",
            "--shell",
        ]
    )
    assert code == 0
    mock_run.assert_called_once_with(
        image="custom-run-image:latest",
        port=9000,
        shell=True,
    )
