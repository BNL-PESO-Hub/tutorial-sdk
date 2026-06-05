from unittest.mock import patch, MagicMock

from tutorial_sdk.spec import TutorialSpec
from tutorial_sdk.runtime.launcher import (
    RuntimeLauncher,
    RunResult,
)


@patch("subprocess.run")
def test_launcher_run_default(mock_run):
    spec = TutorialSpec(name="run-test")
    launcher = RuntimeLauncher(spec, root=".")

    mock_run.return_value = MagicMock(returncode=0)
    result = launcher.run()

    assert isinstance(result, RunResult)
    assert result.returncode == 0
    assert result.image == "run-test:latest"

    cmd = mock_run.call_args[0][0]
    assert "docker" in cmd
    assert "--rm" in cmd
    assert "-p" in cmd
    assert "8888:8888" in cmd
    assert "run-test:latest" in cmd
    # No shell flags
    assert "-it" not in cmd
    assert "--entrypoint" not in cmd


@patch("subprocess.run")
def test_launcher_run_custom_port(mock_run):
    spec = TutorialSpec(name="port-test")
    launcher = RuntimeLauncher(spec, root=".")

    mock_run.return_value = MagicMock(returncode=0)
    launcher.run(port=9999)

    cmd = mock_run.call_args[0][0]
    assert "9999:8888" in cmd


@patch("subprocess.run")
def test_launcher_run_shell_mode(mock_run):
    spec = TutorialSpec(name="shell-test")
    launcher = RuntimeLauncher(spec, root=".")

    mock_run.return_value = MagicMock(returncode=0)
    launcher.run(shell=True)

    cmd = mock_run.call_args[0][0]
    assert "-it" in cmd
    assert "--entrypoint" in cmd
    assert "/bin/sh" in cmd
    # Image should still be at the end
    assert cmd[-1] == "shell-test:latest"


@patch("subprocess.run")
def test_launcher_run_with_custom_image(mock_run):
    spec = TutorialSpec(
        name="img-test",
        build={"image": "ghcr.io/org/tut:v1"},
    )
    launcher = RuntimeLauncher(spec, root=".")

    mock_run.return_value = MagicMock(returncode=0)
    result = launcher.run()

    assert result.image == "ghcr.io/org/tut:v1"
    cmd = mock_run.call_args[0][0]
    assert "ghcr.io/org/tut:v1" in cmd


@patch("subprocess.run")
def test_launcher_run_with_parameter_image(mock_run):
    spec = TutorialSpec(name="param-test")
    launcher = RuntimeLauncher(spec, root=".")

    mock_run.return_value = MagicMock(returncode=0)
    result = launcher.run(image="parameter-override:v3")

    assert result.image == "parameter-override:v3"
    cmd = mock_run.call_args[0][0]
    assert "parameter-override:v3" in cmd
