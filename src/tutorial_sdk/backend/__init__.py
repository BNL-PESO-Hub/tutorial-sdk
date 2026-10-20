"""Container backend selection.

Exposes :func:`get_backend`, the single entry point through which
``DockerBuilder``, ``ContainerValidator``, and ``RuntimeLauncher``
obtain a container execution backend.

Selection precedence (highest first):

1. Explicit ``preference`` argument (e.g. from a ``--backend``
   CLI flag).
2. The ``TUTORIAL_SDK_BACKEND`` environment variable.
3. The ``config`` argument (e.g. ``build.backend`` from
   ``tutorial.yml``).
4. Auto-detection: prefer the docker-py backend when it can be
   loaded, otherwise fall back to the Docker CLI backend.

At every level, ``None`` and ``"auto"`` mean "no preference" and
defer to the next level.
"""

from __future__ import annotations

import os

from ..errors import BackendError, BackendUnavailableError, ConfigError
from .base import (
    ContainerBackend,
    ContainerHandle,
    ExecResult,
    ImageBuildResult,
    LogCallback,
    RunExit,
)
from .docker_cli import DockerCliBackend
from .errors import ContainerStartFailed, CopyFailed, ImageBuildFailed

__all__ = [
    "BackendError",
    "BackendUnavailableError",
    "ContainerBackend",
    "ContainerHandle",
    "ContainerStartFailed",
    "CopyFailed",
    "DockerCliBackend",
    "ExecResult",
    "ImageBuildFailed",
    "ImageBuildResult",
    "LogCallback",
    "RunExit",
    "get_backend",
]

_ENV_VAR = "TUTORIAL_SDK_BACKEND"
_CHOICES = ("auto", "docker-cli", "docker-sdk")
_SDK_MODULE = f"{__name__}.docker_sdk"


def _load_sdk_backend() -> ContainerBackend:
    """Instantiate the docker-py backend.

    Import-time and construction-time failures alike (backend
    module absent, the lazily imported ``docker`` package missing,
    daemon unreachable) surface as ``BackendUnavailableError`` so
    that auto-detection can fall back to the CLI backend.

    Raises:
        BackendUnavailableError: If the backend cannot be used.
    """
    try:
        from .docker_sdk import DockerSdkBackend
    except ImportError as exc:
        if getattr(exc, "name", None) == _SDK_MODULE:
            raise BackendUnavailableError(
                "The 'docker-sdk' backend is not available; "
                "use 'docker-cli' instead."
            ) from exc
        raise _sdk_package_missing(exc) from exc

    try:
        return DockerSdkBackend()
    except ImportError as exc:
        raise _sdk_package_missing(exc) from exc


def _sdk_package_missing(exc: ImportError) -> BackendUnavailableError:
    """Build the error for a missing docker-py dependency."""
    return BackendUnavailableError(
        f"The 'docker-sdk' backend is not available: {exc}. "
        "Install the optional dependency with "
        "`pip install 'tutorial-sdk[docker]'`."
    )


def _resolve_preference(
    preference: str | None,
    config: str | None,
) -> str:
    """Apply the selection precedence and validate the result."""
    for candidate in (preference, os.environ.get(_ENV_VAR), config):
        if candidate and candidate != "auto":
            if candidate not in _CHOICES:
                raise ConfigError(
                    f"Unknown container backend {candidate!r}; "
                    f"expected one of {', '.join(_CHOICES)}."
                )
            return candidate
    return "auto"


def get_backend(
    preference: str | None = None,
    *,
    config: str | None = None,
) -> ContainerBackend:
    """Return a container backend matching the selection precedence.

    Args:
        preference: Explicit choice, typically from a CLI flag:
            ``"auto"``, ``"docker-cli"``, or ``"docker-sdk"``.
        config: Choice from project configuration (e.g.
            ``build.backend``).  Consulted only when neither
            ``preference`` nor ``TUTORIAL_SDK_BACKEND`` names a
            backend.

    Returns:
        A ready-to-use ``ContainerBackend``.

    Raises:
        ConfigError: If the selected value is not a known choice.
        BackendUnavailableError: If no usable backend exists in
            this environment.
    """
    pref = _resolve_preference(preference, config)

    if pref == "docker-sdk":
        return _load_sdk_backend()

    if pref == "docker-cli":
        return DockerCliBackend()

    # pref == "auto": prefer docker-py when it can be loaded,
    # otherwise fall back to the CLI.
    try:
        return _load_sdk_backend()
    except BackendUnavailableError as exc:
        sdk_reason = str(exc)
    cli = DockerCliBackend()
    if cli.available():
        return cli
    raise BackendUnavailableError(
        "No container backend is available: the 'docker' binary "
        "was not found on PATH. Install Docker and make sure it is "
        f"on PATH. (docker-sdk backend: {sdk_reason})"
    )
