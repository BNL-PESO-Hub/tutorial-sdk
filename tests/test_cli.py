import json
from unittest.mock import patch, MagicMock

import pytest

from tutorial_sdk.backend import BACKEND_CHOICES
from tutorial_sdk.cli import main
from tutorial_sdk.spec import TutorialSpec

from .mock_backend import MockBackend


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
        backend=None,
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
        backend=None,
    )


# ---------------------------------------------------------------------------
# --backend and image push
# ---------------------------------------------------------------------------


def _write_spec(tmp_path, **build):
    spec_path = tmp_path / "tutorial.yml"
    TutorialSpec.model_validate({"name": "demo", "build": build}).write(
        spec_path
    )
    return spec_path


@pytest.mark.parametrize(
    ("argv", "method", "expected"),
    [
        (["build"], "build", {"image": None, "platform": None}),
        (["run"], "run", {"image": None, "port": 8888, "shell": False}),
        (["validate", "--container"], "validate", {"container": True}),
    ],
)
@pytest.mark.parametrize("choice", BACKEND_CHOICES)
def test_backend_flag_is_forwarded(tmp_path, argv, method, expected, choice):
    spec_path = _write_spec(tmp_path)
    target = f"tutorial_sdk.project.TutorialProject.{method}"
    with patch(target) as mocked:
        mocked.return_value = MagicMock(returncode=0, passed=True)
        mocked.return_value.render_json.return_value = "{}"
        main([*argv, "--config", str(spec_path), "--backend", choice])
    kwargs = mocked.call_args.kwargs
    assert kwargs["backend"] == choice
    for key, value in expected.items():
        assert kwargs[key] == value


@pytest.mark.parametrize("command", ["build", "run", "validate", "ci"])
def test_backend_flag_rejects_unknown_values(tmp_path, command, capsys):
    with pytest.raises(SystemExit) as info:
        main([command, "--backend", "podman"])
    assert info.value.code == 2
    assert "invalid choice: 'podman'" in capsys.readouterr().err


def _run_ci(tmp_path, monkeypatch, mock_backend, *argv, **build):
    spec_path = _write_spec(tmp_path, **build)
    selected = []

    def mock_get_backend(backend=None, *, fallback_name=None):
        selected.append((backend, fallback_name))
        return mock_backend

    monkeypatch.setattr("tutorial_sdk.cli.get_backend", mock_get_backend)
    monkeypatch.setenv("GITHUB_OUTPUT", str(tmp_path / "out.txt"))
    code = main(["ci", "--config", str(spec_path), *argv])
    report = tmp_path / "tutorial-build.json"
    build_report = json.loads(report.read_text()) if report.exists() else None
    log = (tmp_path / "tutorial-sdk.log").read_text()
    return code, selected, build_report, log


def test_ci_all_uses_one_backend_and_pushes(tmp_path, monkeypatch):
    backend = MockBackend(push_digest="sha256:feed")
    code, selected, build_report, log = _run_ci(
        tmp_path,
        monkeypatch,
        backend,
        "--mode",
        "all",
        "--push",
        "--image",
        "reg.io/demo:1",
        "--backend",
        "docker-sdk",
        backend="docker-cli",
    )
    assert code == 0
    assert selected == [("docker-sdk", "docker-cli")]
    assert backend.names() == [
        "build_image",
        "push_image",
        "run_detached",
        "wait_running",
        "exec",
        "remove",
    ]
    assert backend.calls[1] == ("push_image", "reg.io/demo:1")
    assert build_report["pushed"] is True
    assert build_report["digest"] == "sha256:feed"
    assert "Pushing image reg.io/demo:1..." in log
    assert "Image reg.io/demo:1 pushed successfully." in log


def test_ci_push_failure_is_logged_not_fatal(tmp_path, monkeypatch):
    backend = MockBackend(push_error="denied: requested access")
    code, _, build_report, log = _run_ci(
        tmp_path, monkeypatch, backend, "--mode", "publish", "--push"
    )
    assert code == 0
    assert build_report["pushed"] is False
    assert build_report["digest"] is None
    assert "Push failed: denied: requested access" in log


def test_ci_publish_without_push_flag_does_not_push(tmp_path, monkeypatch):
    backend = MockBackend()
    code, selected, build_report, _ = _run_ci(
        tmp_path, monkeypatch, backend, "--mode", "publish"
    )
    assert code == 0
    assert selected == [(None, None)]
    assert "push_image" not in backend.names()
    assert build_report["pushed"] is False


def test_ci_without_backend_reports_build_failure(tmp_path, monkeypatch):
    from tutorial_sdk.errors import BackendUnavailableError

    spec_path = _write_spec(tmp_path)

    def unavailable(backend=None, *, fallback_name=None):
        raise BackendUnavailableError("No container backend is available")

    monkeypatch.setattr("tutorial_sdk.cli.get_backend", unavailable)
    code = main(["ci", "--config", str(spec_path), "--mode", "build"])
    assert code == 3
    build_report = json.loads((tmp_path / "tutorial-build.json").read_text())
    assert build_report["status"] == "failure"
    log = (tmp_path / "tutorial-sdk.log").read_text()
    assert "Build failed: No container backend is available" in log


def test_ci_validate_mode_needs_no_backend(tmp_path, monkeypatch):
    spec_path = _write_spec(tmp_path)

    def forbidden(*args, **kwargs):
        raise AssertionError("validate mode must not select a backend")

    monkeypatch.setattr("tutorial_sdk.cli.get_backend", forbidden)
    assert main(["ci", "--config", str(spec_path)]) == 0


# ---------------------------------------------------------------------------
# --build-output
# ---------------------------------------------------------------------------


def _echo_backend(**kwargs):
    return MockBackend(build_lines=("Step 1/2 : FROM base", "done"), **kwargs)


def test_ci_build_output_streams_to_stdout_and_log(
    tmp_path, monkeypatch, capsys
):
    backend = _echo_backend()
    code, _, _, log = _run_ci(
        tmp_path, monkeypatch, backend, "--mode", "build", "--build-output"
    )
    assert code == 0
    assert backend.build_callbacks[0] is not None
    out = capsys.readouterr().out
    assert "Building container image...\nStep 1/2 : FROM base\ndone\n" in out
    assert "Step 1/2 : FROM base\ndone\nContainer built in" in log


def test_ci_without_build_output_stays_silent(tmp_path, monkeypatch, capsys):
    backend = _echo_backend()
    code, _, _, log = _run_ci(tmp_path, monkeypatch, backend, "--mode", "build")
    assert code == 0
    assert backend.build_callbacks == [None]
    assert "Step 1/2" not in capsys.readouterr().out
    assert "Step 1/2" not in log


def test_ci_build_output_failure(tmp_path, monkeypatch):
    backend = _echo_backend(build_error="Docker build failed")
    code, _, build_report, log = _run_ci(
        tmp_path, monkeypatch, backend, "--mode", "build", "--build-output"
    )
    assert code == 3
    assert build_report["status"] == "failure"
    assert "done\nBuild failed: Docker build failed" in log


def test_build_output_flag(tmp_path, monkeypatch, capsys):
    spec_path = _write_spec(tmp_path)
    backend = _echo_backend()
    monkeypatch.setattr(
        "tutorial_sdk.builder.docker.get_backend",
        lambda backend_=None, **kwargs: backend,
    )
    assert main(["build", "--config", str(spec_path), "--build-output"]) == 0
    assert capsys.readouterr().out == "Step 1/2 : FROM base\ndone\n"

    assert main(["build", "--config", str(spec_path)]) == 0
    assert capsys.readouterr().out == ""
    assert backend.build_callbacks[1] is None
