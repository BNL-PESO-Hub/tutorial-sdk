"""Unit tests for ``tutorial-sdk doctor`` (no daemon needed)."""

import itertools
import json
from datetime import datetime, timedelta, timezone
from unittest import mock

import pytest

import tutorial_sdk.backend as backend_pkg
from tutorial_sdk import doctor
from tutorial_sdk.backend import (
    MANAGED_LABEL,
    BackendInfo,
    DockerCliBackend,
    ManagedContainer,
    get_backend,
)
from tutorial_sdk.cli import main
from tutorial_sdk.errors import BackendError, BackendUnavailableError

NOW = datetime(2026, 10, 22, 15, 0, 0, tzinfo=timezone.utc)

SDK_MISSING = (
    "The 'docker-sdk' backend is not available: No module named 'docker'."
)


class SdkStandIn:
    """Duck-typed ``DockerSdkBackend`` for doctor."""

    name = "docker-sdk"

    def __init__(self, containers=(), list_error=None, info=None):
        self.containers = tuple(containers)
        self.list_error = list_error
        self.info = info or BackendInfo(
            name="docker-sdk",
            client_version="7.2.0",
            server_version="29.9.1",
            api_version="1.56",
            endpoint="unix:///var/run/docker.sock",
            endpoint_source="default",
            classic_builder=True,
        )
        self.listed = 0

    def describe(self):
        return self.info

    def list_managed(self):
        self.listed += 1
        if self.list_error is not None:
            raise self.list_error
        return self.containers


CLI_INFO = BackendInfo(
    name="docker-cli",
    client_version="29.9.1",
    server_version="29.9.1",
    api_version="1.56",
    endpoint="unix:///var/run/docker.sock",
    endpoint_source="context default",
)


@pytest.fixture
def host(monkeypatch):
    """Configure what doctor finds: ``host(sdk=..., cli=...)``."""

    def configure(
        sdk=True,
        cli=True,
        sdk_backend=None,
        cli_info=CLI_INFO,
        cli_containers=(),
    ):
        stand_in = sdk_backend or SdkStandIn()

        def load():
            if sdk:
                return stand_in
            raise BackendUnavailableError(SDK_MISSING)

        monkeypatch.setattr(doctor, "_load_sdk_backend", load)
        monkeypatch.setattr(backend_pkg, "_load_sdk_backend", load)
        monkeypatch.setattr(
            "shutil.which", lambda name: "/usr/bin/docker" if cli else None
        )
        monkeypatch.setattr(
            DockerCliBackend,
            "describe",
            lambda self: cli_info if cli else BackendInfo(name="docker-cli"),
        )
        monkeypatch.setattr(
            DockerCliBackend, "list_managed", lambda self: tuple(cli_containers)
        )
        return stand_in

    return configure


def test_auto_prefers_sdk(host):
    host()
    report = doctor.diagnose()
    assert report.selection.backend == "docker-sdk"
    assert report.selection.source == "auto"
    assert report.selection.reason == (
        "auto: docker-py is installed and the daemon answered"
    )
    assert report.ok
    assert [s.name for s in report.backends] == ["docker-sdk", "docker-cli"]
    assert all(s.usable for s in report.backends)


def test_auto_falls_back_to_cli_with_reason(host):
    host(sdk=False)
    report = doctor.diagnose()
    assert report.selection.backend == "docker-cli"
    assert SDK_MISSING in report.selection.reason
    sdk = report.status("docker-sdk")
    assert not sdk.usable
    assert sdk.reason == SDK_MISSING
    assert report.ok


def test_nothing_available(host):
    host(sdk=False, cli=False)
    report = doctor.diagnose()
    assert report.selection.backend is None
    assert not report.ok
    assert report.containers is None
    assert report.containers_error == "no backend can reach a Docker daemon"


def test_explicit_cli_without_binary_is_not_ok(host):
    host(cli=False)
    report = doctor.diagnose("docker-cli")
    # The command would select docker-cli and then fail to run docker.
    assert report.selection.backend == "docker-cli"
    assert report.selection.source == "--backend"
    assert "not found on PATH" in report.selection.reason
    assert not report.ok


def test_cli_daemon_unreachable_is_not_ok(host):
    host(
        sdk=False,
        cli_info=BackendInfo(name="docker-cli", daemon_error="Cannot connect"),
    )
    report = doctor.diagnose()
    assert report.selection.backend == "docker-cli"
    assert not report.ok
    assert report.containers_error == "no backend can reach a Docker daemon"
    assert "daemon: unreachable: Cannot connect" in report.render_text(NOW)


def test_environment_and_config_sources(host, tmp_path, monkeypatch):
    host()
    config = tmp_path / "tutorial.yml"
    config.write_text("name: demo\nbuild:\n  backend: docker-cli\n")
    report = doctor.diagnose(config=config)
    assert report.config_path == str(config)
    assert report.config_backend == "docker-cli"
    assert report.selection.backend == "docker-cli"
    assert report.selection.source == "build.backend"

    monkeypatch.setenv("TUTORIAL_SDK_BACKEND", "docker-sdk")
    report = doctor.diagnose(config=config)
    assert report.selection.source == "TUTORIAL_SDK_BACKEND"
    assert report.selection.backend == "docker-sdk"

    report = doctor.diagnose("docker-cli", config=config)
    assert report.selection.source == "--backend"
    # "auto" on the command line defers, exactly like get_backend.
    report = doctor.diagnose("auto", config=config)
    assert report.selection.source == "TUTORIAL_SDK_BACKEND"


def test_missing_config_is_ignored(host, tmp_path):
    host()
    report = doctor.diagnose(config=tmp_path / "absent.yml")
    assert report.config_path is None
    assert report.notes == ()


def test_unreadable_config_is_noted(host, tmp_path):
    host()
    config = tmp_path / "tutorial.yml"
    config.write_text("name: demo\nbuild:\n  backend: podman\n")
    report = doctor.diagnose(config=config)
    assert report.config_backend is None
    assert report.selection.backend == "docker-sdk"
    assert "build.backend is ignored" in report.notes[0]


def test_invalid_environment_value(host, monkeypatch):
    host()
    monkeypatch.setenv("TUTORIAL_SDK_BACKEND", "podman")
    report = doctor.diagnose()
    assert report.selection.backend is None
    assert "Unknown container backend 'podman'" in report.selection.reason
    assert not report.ok


@pytest.mark.parametrize(
    "flag, env, config, sdk, cli",
    list(
        itertools.product(
            [None, "auto", "docker-cli", "docker-sdk"],
            [None, "docker-cli", "docker-sdk"],
            [None, "docker-cli", "docker-sdk"],
            [True, False],
            [True, False],
        )
    ),
)
def test_selection_matches_get_backend(
    host, monkeypatch, flag, env, config, sdk, cli
):
    host(sdk=sdk, cli=cli)
    if env:
        monkeypatch.setenv("TUTORIAL_SDK_BACKEND", env)
    try:
        selected = get_backend(flag, fallback_name=config).name
    except BackendUnavailableError:
        selected = None
    selection = doctor._select(
        flag,
        config,
        doctor._sdk_status()[1],
        doctor._cli_status(DockerCliBackend()),
    )
    assert selection.backend == selected


def test_containers_listed_through_selected_backend(host):
    container = ManagedContainer(
        id="e" * 64,
        name="quirky",
        image="demo:latest",
        state="running",
        created=NOW - timedelta(minutes=5),
    )
    stand_in = host(sdk_backend=SdkStandIn(containers=[container]))
    report = doctor.diagnose()
    assert stand_in.listed == 1
    assert report.containers == (container,)
    text = report.render_text(NOW)
    assert "eeeeeeeeeeee  running    5m  demo:latest  quirky" in text
    assert "not necessarily orphaned" in text


def test_containers_listed_by_cli_when_selected(host):
    container = ManagedContainer("c" * 64, "n", "img", "exited", None)
    stand_in = host(cli_containers=[container])
    report = doctor.diagnose("docker-cli")
    assert stand_in.listed == 0
    assert report.containers == (container,)
    assert "  ?  img  n" in report.render_text(NOW)


def test_container_listing_failure_is_reported(host):
    host(sdk_backend=SdkStandIn(list_error=BackendError("denied")))
    report = doctor.diagnose()
    assert report.containers is None
    assert report.containers_error == "denied"
    assert "not listed: denied" in report.render_text(NOW)
    assert report.ok  # inventory problems do not fail doctor


def test_describe_failure_becomes_a_note(host):
    stand_in = SdkStandIn()
    stand_in.describe = mock.Mock(side_effect=RuntimeError("weird"))
    host(sdk_backend=stand_in)
    report = doctor.diagnose()
    assert report.status("docker-sdk").info.notes == (
        "could not describe backend: weird",
    )


def test_render_text_layout(host):
    host(
        sdk_backend=SdkStandIn(
            info=BackendInfo(
                name="docker-sdk",
                client_version="7.2.0",
                server_version="30.0.0",
                api_version="1.58",
                endpoint_source="default",
                classic_builder=False,
                notes=("classic builder: Error response from daemon: gone",),
            )
        )
    )
    text = doctor.diagnose().render_text(NOW)
    assert text.splitlines()[0].startswith("tutorial-sdk ")
    assert "  docker-sdk  usable" in text
    assert "      docker-py 7.2.0 | Docker Engine 30.0.0 (API 1.58)" in text
    assert "      endpoint: (platform default) (default)" in text
    assert "classic builder (used for docker-py builds): NOT served" in text
    assert "      classic builder: Error response from daemon: gone" in text
    assert "      Docker CLI 29.9.1 | Docker Engine 29.9.1 (API 1.56)" in text
    assert "endpoint: unix:///var/run/docker.sock (context default)" in text
    assert f"Containers labelled {MANAGED_LABEL}=true\n  none" in text


@pytest.mark.parametrize(
    "delta, expected",
    [
        (timedelta(seconds=7), "7s"),
        (timedelta(minutes=3, seconds=5), "3m"),
        (timedelta(hours=26), "1d"),
        (timedelta(hours=2), "2h"),
        (timedelta(seconds=-5), "0s"),
    ],
)
def test_age(delta, expected):
    assert doctor._age(NOW - delta, NOW) == expected


def test_to_dict_is_json_serialisable(host):
    container = ManagedContainer("c" * 64, "n", "img", "running", NOW)
    host(sdk_backend=SdkStandIn(containers=[container]))
    data = json.loads(json.dumps(doctor.diagnose().to_dict()))
    assert data["ok"] is True
    assert data["selection"]["backend"] == "docker-sdk"
    assert data["containers"][0]["created"] == "2026-10-22T15:00:00+00:00"
    assert data["label"] == f"{MANAGED_LABEL}=true"


def test_cli_doctor_text_and_exit_code(host, capsys, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    host()
    assert main(["doctor"]) == 0
    out = capsys.readouterr().out
    assert "Selection\n  docker-sdk: auto:" in out

    host(sdk=False, cli=False)
    assert main(["doctor"]) == 1


def test_cli_doctor_json_and_backend_flag(host, capsys, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    host()
    assert main(["doctor", "--json", "--backend", "docker-cli"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["selection"]["backend"] == "docker-cli"
    assert data["selection"]["source"] == "--backend"


def test_cli_doctor_reads_config(host, capsys, tmp_path):
    host()
    config = tmp_path / "tutorial.yml"
    config.write_text("name: demo\nbuild:\n  backend: docker-cli\n")
    assert main(["doctor", "--json", "--config", str(config)]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["config_backend"] == "docker-cli"
    assert data["selection"]["source"] == "build.backend"


def test_cli_doctor_explicit_missing_config_fails(host, capsys, tmp_path):
    host()
    missing = tmp_path / "absent.yml"
    assert main(["doctor", "--config", str(missing)]) == 2
    assert "Tutorial specification not found" in capsys.readouterr().err


def test_cli_doctor_rejects_unknown_backend(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["doctor", "--backend", "podman"])
    assert exc.value.code == 2


def test_doctor_is_read_only(host, monkeypatch):
    # Nothing but describe/list_managed is called on the backends.
    stand_in = SdkStandIn()
    host(sdk_backend=stand_in)
    with (
        mock.patch("subprocess.run") as run,
        mock.patch("subprocess.Popen") as popen,
    ):
        doctor.diagnose()
    run.assert_not_called()
    popen.assert_not_called()


def test_render_text_shows_config_and_notes(host, tmp_path):
    host()
    config = tmp_path / "tutorial.yml"
    config.write_text("name: demo\nbuild:\n  backend: podman\n")
    text = doctor.diagnose(config=config).render_text(NOW)
    assert f"Config: {config} (build.backend: unset)" in text
    assert "\nNote: " in text and "build.backend is ignored" in text


def test_version_of_missing_distribution():
    assert doctor._version("surely-not-installed-xyz") is None
