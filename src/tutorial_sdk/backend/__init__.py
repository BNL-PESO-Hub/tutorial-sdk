"""Container backend selection.

Exposes :func:`get_backend`, the single entry point through which
``DockerBuilder``, ``ContainerValidator``, ``RuntimeLauncher``, and
the ``ci`` command obtain a container execution backend.  It accepts
either a backend name or a ready backend instance, which is returned
unchanged.

Selection precedence (highest first):

1. Explicit ``backend`` argument (e.g. from a ``--backend``
   CLI flag).
2. The ``TUTORIAL_SDK_BACKEND`` environment variable.
3. The ``fallback_name`` argument (e.g. ``build.backend`` from
   ``tutorial.yml``).
4. Auto-detection: prefer the docker-py backend when it can be
   loaded, otherwise fall back to the Docker CLI backend.

At every level, ``None`` and ``"auto"`` mean "no preference" and
defer to the next level.

``DockerSdkBackend`` is exported lazily: importing this package
never imports ``docker_sdk`` (or the optional ``docker`` package).
"""

from __future__ import annotations

import os
from typing import Literal, get_args

from ..errors import BackendError, BackendUnavailableError, ConfigError
from .base import (
    MANAGED_LABEL,
    MANAGED_LABEL_VALUE,
    BackendInfo,
    ContainerBackend,
    ContainerHandle,
    ExecResult,
    ImageBuildResult,
    ImagePushResult,
    LogCallback,
    ManagedContainer,
    RunExit,
)
from .docker_cli import DockerCliBackend
from .errors import (
    ContainerStartFailed,
    CopyFailed,
    ImageBuildFailed,
    ImagePushFailed,
)

__all__ = [
    "BACKEND_CHOICES",
    "MANAGED_LABEL",
    "MANAGED_LABEL_VALUE",
    "BackendError",
    "BackendInfo",
    "BackendName",
    "BackendUnavailableError",
    "ContainerBackend",
    "ContainerHandle",
    "ContainerStartFailed",
    "CopyFailed",
    "DockerCliBackend",
    "DockerSdkBackend",
    "ExecResult",
    "ImageBuildFailed",
    "ImageBuildResult",
    "ImagePushFailed",
    "ImagePushResult",
    "LogCallback",
    "ManagedContainer",
    "RunExit",
    "get_backend",
    "preference_source",
]

_ENV_VAR = "TUTORIAL_SDK_BACKEND"

#: Accepted backend names, for ``--backend``, ``TUTORIAL_SDK_BACKEND``,
#: and ``build.backend``.
BackendName = Literal["auto", "docker-cli", "docker-sdk"]
BACKEND_CHOICES: tuple[str, ...] = get_args(BackendName)
_SDK_MODULE = f"{__name__}.docker_sdk"


def __getattr__(name: str) -> object:
    """Resolve ``DockerSdkBackend`` on first access (PEP 562)."""
    if name == "DockerSdkBackend":
        from .docker_sdk import DockerSdkBackend

        return DockerSdkBackend
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


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
    name: str | None,
    fallback_name: str | None,
) -> str:
    """Apply the selection precedence and validate the result."""
    return preference_source(name, fallback_name=fallback_name)[0]


def preference_source(
    backend: str | None = None,
    *,
    fallback_name: str | None = None,
) -> tuple[str, str]:
    """Return the backend name the selection precedence picks, and why.

    Applies the same precedence as :func:`get_backend` without loading
    any backend.

    Args:
        backend: Explicit backend name (e.g. a ``--backend`` value).
        fallback_name: Backend name from project configuration.

    Returns:
        ``(name, source)``: ``name`` is ``"auto"``, ``"docker-cli"``,
        or ``"docker-sdk"``; ``source`` is ``"argument"``,
        ``"TUTORIAL_SDK_BACKEND"``, ``"configuration"``, or
        ``"default"`` (nothing named a backend).

    Raises:
        ConfigError: If the deciding value is not a known choice.
    """
    candidates = (
        (backend, "argument"),
        (os.environ.get(_ENV_VAR), _ENV_VAR),
        (fallback_name, "configuration"),
    )
    for candidate, source in candidates:
        if candidate and candidate != "auto":
            if candidate not in BACKEND_CHOICES:
                raise ConfigError(
                    f"Unknown container backend {candidate!r}; "
                    f"expected one of {', '.join(BACKEND_CHOICES)}."
                )
            return candidate, source
    return "auto", "default"


def get_backend(
    backend: ContainerBackend | str | None = None,
    *,
    fallback_name: str | None = None,
) -> ContainerBackend:
    """Return a container backend matching the selection precedence.

    Args:
        backend: Explicit choice, typically from a CLI flag or a
            consumer's ``backend`` argument: ``"auto"``,
            ``"docker-cli"``, ``"docker-sdk"``, or a ready
            ``ContainerBackend`` instance.  An instance is the most
            explicit choice and is returned unchanged, bypassing
            ``TUTORIAL_SDK_BACKEND`` and ``fallback_name``.
        fallback_name: Backend name from project configuration
            (e.g. ``build.backend``).  Consulted only when neither
            ``backend`` nor ``TUTORIAL_SDK_BACKEND`` names a
            backend.

    Returns:
        A ready-to-use ``ContainerBackend``.

    Raises:
        ConfigError: If the selected value is not a known choice.
        BackendUnavailableError: If no usable backend exists in
            this environment.
    """
    if isinstance(backend, ContainerBackend):
        return backend

    pref = _resolve_preference(backend, fallback_name)

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
