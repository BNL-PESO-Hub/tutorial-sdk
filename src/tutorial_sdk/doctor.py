"""Environment diagnostics behind ``tutorial-sdk doctor``.

:func:`diagnose` reports which container backends can be used on this
host, which one a command would select (and which rule decided it),
the Docker client, daemon, and endpoint each backend uses, whether the
daemon still serves the classic builder that docker-py builds with,
and the containers carrying tutorial-sdk's managed-container label.
It is read-only: nothing is started, built, or removed.
"""

from __future__ import annotations

import platform
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from importlib import metadata
from pathlib import Path
from typing import Any

from .backend import (
    MANAGED_LABEL,
    MANAGED_LABEL_VALUE,
    BackendInfo,
    DockerCliBackend,
    ManagedContainer,
    _load_sdk_backend,
    preference_source,
)
from .errors import BackendError, ConfigError
from .spec import TutorialSpec

_SOURCE_LABELS = {
    "argument": "--backend",
    "TUTORIAL_SDK_BACKEND": "TUTORIAL_SDK_BACKEND",
    "configuration": "build.backend",
    "default": "auto",
}


@dataclass(frozen=True)
class BackendStatus:
    """Whether a backend can be selected, and what it talks to.

    ``usable`` follows backend selection: ``docker-sdk`` is usable when
    docker-py is installed and the daemon answers a ping, ``docker-cli``
    when the ``docker`` binary is on PATH (its daemon may still be
    unreachable; see ``info.daemon_error``).
    """

    name: str
    usable: bool
    info: BackendInfo
    reason: str | None = None


@dataclass(frozen=True)
class Selection:
    """The backend a command would use with the same inputs.

    Attributes:
        requested: Name the selection precedence picked (``auto``,
            ``docker-cli``, ``docker-sdk``), or ``None`` if a value
            was invalid.
        source: Which rule decided (``--backend``,
            ``TUTORIAL_SDK_BACKEND``, ``build.backend``, ``auto``).
        backend: Backend that would be used, or ``None`` if the
            command would fail to select one.
        reason: Human-readable explanation.
    """

    requested: str | None
    source: str | None
    backend: str | None
    reason: str


@dataclass(frozen=True)
class DoctorReport:
    """Everything ``tutorial-sdk doctor`` prints."""

    sdk_version: str
    python_version: str
    platform: str
    config_path: str | None
    config_backend: str | None
    backends: tuple[BackendStatus, ...]
    selection: Selection
    containers: tuple[ManagedContainer, ...] | None = None
    containers_error: str | None = None
    notes: tuple[str, ...] = field(default=())

    @property
    def ok(self) -> bool:
        """Whether a backend is selected and can reach its daemon."""
        status = self.status(self.selection.backend)
        return (
            status is not None
            and status.usable
            and status.info.daemon_error is None
        )

    def status(self, name: str | None) -> BackendStatus | None:
        """Return the status of the backend ``name``, if reported."""
        for status in self.backends:
            if status.name == name:
                return status
        return None

    def to_dict(self) -> dict[str, Any]:
        """Return the report as JSON-serialisable data."""
        data = asdict(self)
        data["ok"] = self.ok
        data["label"] = f"{MANAGED_LABEL}={MANAGED_LABEL_VALUE}"
        for container in data["containers"] or ():
            if container["created"] is not None:
                container["created"] = container["created"].isoformat()
        return data

    def render_text(self, now: datetime | None = None) -> str:
        """Render the report for a terminal.

        Args:
            now: Reference time for container ages (default: now).
        """
        now = now or datetime.now(timezone.utc)
        lines = [
            f"tutorial-sdk {self.sdk_version} | Python "
            f"{self.python_version} | {self.platform}",
        ]
        if self.config_path is not None:
            backend = self.config_backend or "unset"
            lines.append(
                f"Config: {self.config_path} (build.backend: {backend})"
            )
        lines.append("")
        lines.append("Backends")
        for status in self.backends:
            lines.extend(_render_backend(status))
        lines.append("")
        lines.append("Selection")
        selected = self.selection.backend or "none"
        lines.append(f"  {selected}: {self.selection.reason}")
        lines.append(
            "  (precedence: --backend > TUTORIAL_SDK_BACKEND > "
            "build.backend > auto)"
        )
        lines.append("")
        lines.append(
            f"Containers labelled {MANAGED_LABEL}={MANAGED_LABEL_VALUE}"
        )
        if self.containers_error is not None:
            lines.append(f"  not listed: {self.containers_error}")
        elif not self.containers:
            lines.append("  none")
        else:
            for container in self.containers:
                age = _age(container.created, now)
                lines.append(
                    f"  {container.id[:12]}  {container.state:<8} "
                    f"{age:>4}  {container.image}  {container.name}"
                )
            lines.append(
                "  A listed container is not necessarily orphaned; remove "
                "one with `docker rm -f <id>`."
            )
        if self.notes:
            lines.append("")
            lines.extend(f"Note: {note}" for note in self.notes)
        return "\n".join(lines) + "\n"


def diagnose(
    backend: str | None = None,
    *,
    config: str | Path | None = None,
) -> DoctorReport:
    """Inspect the container backends available on this host.

    Args:
        backend: Backend name to evaluate as a ``--backend`` value
            would be (``None`` = not given).
        config: Optional ``tutorial.yml``; when it exists, its
            ``build.backend`` takes part in the selection.

    Returns:
        The report.  Problems are reported in it; nothing is raised.
    """
    notes: list[str] = []
    config_path, config_backend = _read_config(config, notes)

    sdk_backend, sdk_status = _sdk_status()
    cli_backend = DockerCliBackend()
    cli_status = _cli_status(cli_backend)
    statuses = (sdk_status, cli_status)

    selection = _select(backend, config_backend, sdk_status, cli_status)

    candidates = {"docker-sdk": sdk_backend, "docker-cli": cli_backend}
    order = [selection.backend, "docker-sdk", "docker-cli"]
    lister = None
    for name in order:
        status = next((s for s in statuses if s.name == name), None)
        if status and status.usable and status.info.daemon_error is None:
            lister = candidates[name]
            break
    containers = None
    containers_error = None
    if lister is None:
        containers_error = "no backend can reach a Docker daemon"
    else:
        try:
            containers = lister.list_managed()
        except BackendError as exc:
            containers_error = str(exc)

    return DoctorReport(
        sdk_version=_version("tutorial-sdk") or "unknown",
        python_version=platform.python_version(),
        platform=f"{platform.system()} {platform.machine()}".strip(),
        config_path=config_path,
        config_backend=config_backend,
        backends=statuses,
        selection=selection,
        containers=containers,
        containers_error=containers_error,
        notes=tuple(notes),
    )


def _read_config(
    config: str | Path | None,
    notes: list[str],
) -> tuple[str | None, str | None]:
    """Return the config path and its ``build.backend``, if readable."""
    if config is None:
        return None, None
    path = Path(config)
    if not path.is_file():
        return None, None
    try:
        spec = TutorialSpec.load(path)
    except (ConfigError, OSError) as exc:
        notes.append(
            f"{path} could not be read, so build.backend is ignored: {exc}"
        )
        return str(path), None
    return str(path), spec.build.backend


def _sdk_status() -> tuple[Any, BackendStatus]:
    """Load the docker-py backend, as auto-detection does."""
    try:
        backend = _load_sdk_backend()
    except BackendError as exc:
        info = BackendInfo(name="docker-sdk", client_version=_version("docker"))
        return None, BackendStatus(
            name="docker-sdk", usable=False, info=info, reason=str(exc)
        )
    return backend, BackendStatus(
        name="docker-sdk", usable=True, info=_describe(backend)
    )


def _cli_status(backend: DockerCliBackend) -> BackendStatus:
    """Report the Docker CLI backend."""
    info = _describe(backend)
    if not backend.available():
        return BackendStatus(
            name=backend.name,
            usable=False,
            info=info,
            reason="the 'docker' binary was not found on PATH",
        )
    return BackendStatus(name=backend.name, usable=True, info=info)


def _describe(backend: Any) -> BackendInfo:
    """Call ``backend.describe()``, turning surprises into a note."""
    try:
        return backend.describe()
    except Exception as exc:  # diagnostics must not crash the report
        return BackendInfo(
            name=backend.name, notes=(f"could not describe backend: {exc}",)
        )


def _select(
    backend: str | None,
    config_backend: str | None,
    sdk: BackendStatus,
    cli: BackendStatus,
) -> Selection:
    """Mirror :func:`tutorial_sdk.backend.get_backend` on probed state."""
    try:
        requested, source = preference_source(
            backend, fallback_name=config_backend
        )
    except ConfigError as exc:
        return Selection(None, None, None, str(exc))
    label = _SOURCE_LABELS[source]

    if requested == "docker-sdk":
        if sdk.usable:
            return Selection(requested, label, "docker-sdk", f"set by {label}")
        return Selection(
            requested, label, None, f"set by {label}, but {sdk.reason}"
        )
    if requested == "docker-cli":
        reason = f"set by {label}"
        if not cli.usable:
            reason += f", but {cli.reason}"
        return Selection(requested, label, "docker-cli", reason)
    if sdk.usable:
        return Selection(
            requested,
            label,
            "docker-sdk",
            "auto: docker-py is installed and the daemon answered",
        )
    if cli.usable:
        return Selection(
            requested,
            label,
            "docker-cli",
            f"auto: docker-sdk is unavailable ({sdk.reason})",
        )
    return Selection(
        requested,
        label,
        None,
        f"auto: no backend is available ({cli.reason}; {sdk.reason})",
    )


def _render_backend(status: BackendStatus) -> list[str]:
    """Render one backend's block."""
    info = status.info
    state = "usable" if status.usable else "unavailable"
    lines = [f"  {status.name:<11} {state}"]
    if status.reason:
        lines.append(f"      {status.reason}")
    client = "docker-py" if status.name == "docker-sdk" else "Docker CLI"
    parts = []
    if info.client_version:
        parts.append(f"{client} {info.client_version}")
    if info.server_version:
        api = f" (API {info.api_version})" if info.api_version else ""
        parts.append(f"Docker Engine {info.server_version}{api}")
    if parts:
        lines.append("      " + " | ".join(parts))
    if info.endpoint or info.endpoint_source:
        endpoint = info.endpoint or "(platform default)"
        source = f" ({info.endpoint_source})" if info.endpoint_source else ""
        lines.append(f"      endpoint: {endpoint}{source}")
    if info.daemon_error and status.usable:
        lines.append(f"      daemon: unreachable: {info.daemon_error}")
    if info.classic_builder is not None:
        served = "served" if info.classic_builder else "NOT served"
        lines.append(
            f"      classic builder (used for docker-py builds): {served}"
        )
    lines.extend(f"      {note}" for note in info.notes)
    return lines


def _age(created: datetime | None, now: datetime) -> str:
    """Format the time since ``created`` compactly (``42s``, ``3h``)."""
    if created is None:
        return "?"
    seconds = max(0, int((now - created).total_seconds()))
    for unit, size in (("d", 86400), ("h", 3600), ("m", 60)):
        if seconds >= size:
            return f"{seconds // size}{unit}"
    return f"{seconds}s"


def _version(distribution: str) -> str | None:
    """Return an installed distribution's version, if any."""
    try:
        return metadata.version(distribution)
    except metadata.PackageNotFoundError:
        return None


__all__ = [
    "BackendStatus",
    "DoctorReport",
    "Selection",
    "diagnose",
]
