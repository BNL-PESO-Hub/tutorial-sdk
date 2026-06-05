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
