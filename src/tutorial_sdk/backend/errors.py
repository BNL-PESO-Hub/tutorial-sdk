"""Errors internal to the container backend layer.

These describe backend-specific failures.  Consumers outside
``backend/`` catch them to translate into public errors or
validation reports; they are not part of the top-level
:mod:`tutorial_sdk.errors` surface.  The general-purpose
``BackendError`` and ``BackendUnavailableError`` live there.
"""

from __future__ import annotations

from ..errors import BackendError


class ImageBuildFailed(BackendError):
    """An image build exited unsuccessfully."""

    def __init__(self, message: str, logs: tuple[str, ...] = ()) -> None:
        """Create a build failure.

        Args:
            message: Human-readable failure summary.
            logs: Collected build log lines, when available.
        """
        super().__init__(message)
        self.logs = logs


class ImagePushFailed(BackendError):
    """Pushing an image to its registry failed."""


class ContainerStartFailed(BackendError):
    """A detached container could not be started."""


class CopyFailed(BackendError):
    """Copying files into a container failed."""
