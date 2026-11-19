"""Exception hierarchy for tutorial-sdk."""


class TutorialSdkError(Exception):
    """Base class for SDK errors."""


class ConfigError(TutorialSdkError):
    """Raised when a tutorial specification is invalid."""


class BuildError(TutorialSdkError):
    """Raised when a container build fails."""


class ValidationError(TutorialSdkError):
    """Raised when validation cannot complete."""


class ScaffoldError(TutorialSdkError):
    """Raised when scaffolding cannot be created."""


class BackendError(TutorialSdkError):
    """Base class for container backend failures.

    Backend-specific failures (build, start, copy) derive from this
    in ``tutorial_sdk.backend.errors``.
    """


class BackendUnavailableError(BackendError):
    """The container backend cannot be used in this environment.

    Raised when the Docker binary is missing from ``PATH``, the
    optional ``docker`` package is not installed, or the daemon
    is unreachable.
    """
