"""Plugin interfaces for SDK extension points."""

from typing import Protocol


class ValidatorPlugin(Protocol):
    """Protocol for external validation plugins."""

    name: str

    def validate(self, project: object) -> object:
        """Validate a project and return a report.

        Args:
            project: Project-like object supplied by the SDK.

        Returns:
            Plugin-defined validation result.
        """


class TutorialGenerator(Protocol):
    """Protocol for future tutorial generation strategies."""

    def generate(self, request: object) -> object:
        """Generate tutorial content from a request.

        Args:
            request: Plugin-defined generation request.

        Returns:
            Plugin-defined generation result.
        """
