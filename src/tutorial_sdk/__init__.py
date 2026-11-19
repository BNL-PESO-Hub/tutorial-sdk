"""Tutorial SDK public API."""

from .builder import BuildResult, LocalBuilder
from .doctor import BackendStatus, DoctorReport, Selection, diagnose
from .errors import (
    BackendError,
    BackendUnavailableError,
    BuildError,
    ConfigError,
    ScaffoldError,
    TutorialSdkError,
    ValidationError,
)
from .project import TutorialProject
from .resolver import ResolvedTutorialProject
from .runtime import RunResult
from .spec import (
    AuthorSpec,
    BuildSpec,
    ContentSpec,
    DependencySpec,
    DockerfileSections,
    EntrypointSpec,
    RuntimeSpec,
    TutorialSpec,
    ValidationSpec,
)
from .validator import ValidationCheck, ValidationReport

__all__ = [
    "AuthorSpec",
    "BackendError",
    "BackendStatus",
    "BackendUnavailableError",
    "BuildError",
    "BuildResult",
    "BuildSpec",
    "ConfigError",
    "ContentSpec",
    "DependencySpec",
    "DockerfileSections",
    "DoctorReport",
    "EntrypointSpec",
    "LocalBuilder",
    "ResolvedTutorialProject",
    "RunResult",
    "RuntimeSpec",
    "ScaffoldError",
    "Selection",
    "TutorialProject",
    "TutorialSdkError",
    "TutorialSpec",
    "ValidationCheck",
    "ValidationError",
    "ValidationReport",
    "ValidationSpec",
    "diagnose",
]
