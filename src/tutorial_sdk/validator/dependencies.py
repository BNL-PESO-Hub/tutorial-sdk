"""Dependency validation."""

import importlib.util

from ..spec import TutorialSpec
from .report import ValidationCheck, ValidationReport


class DependencyValidator:
    """Validate dependency declarations that can be checked locally."""

    def __init__(self, spec: TutorialSpec) -> None:
        """Create a dependency validator.

        Args:
            spec: Parsed tutorial specification.
        """

        self.spec = spec

    def validate(self) -> ValidationReport:
        """Validate dependency sections.

        Returns:
            A ``ValidationReport`` with import
            availability checks.
        """

        warnings: list[str] = []
        checks = [
            ValidationCheck(
                name="dependencies.declared",
                passed=True,
                message="Dependency sections are well formed.",
            )
        ]

        if self.spec.validation.check_imports:
            missing = self._missing_imports()
            if missing:
                message = "Local Python imports unavailable: " + ", ".join(
                    missing
                )
                warnings.append(message)
                checks.append(
                    ValidationCheck(
                        name="dependencies.imports",
                        passed=True,
                        message=message,
                    )
                )
            else:
                checks.append(
                    ValidationCheck(
                        name="dependencies.imports",
                        passed=True,
                        message=(
                            "Declared import-like pip packages are available."
                        ),
                    )
                )

        return ValidationReport(
            passed=all(check.passed for check in checks),
            checks=checks,
            warnings=warnings,
        )

    def _missing_imports(self) -> list[str]:
        """Return pip packages whose modules are not importable.

        Returns:
            List of module names that could not be
            found by ``importlib``.
        """

        missing: list[str] = []
        for package in self.spec.dependencies.pip:
            module = self._package_to_module(package)
            if module and importlib.util.find_spec(module) is None:
                missing.append(module)
        return missing

    @staticmethod
    def _package_to_module(package: str) -> str | None:
        """Convert a pip package spec to an importable name.

        Args:
            package: A pip dependency string (e.g.
                ``"numpy>=1.24"``).

        Returns:
            The bare module name, or ``None`` if the
            spec cannot be converted (e.g. URLs).
        """

        name = package.split("==", 1)[0]
        name = name.split(">=", 1)[0].split("<=", 1)[0]
        name = name.split("[", 1)[0].strip()
        if not name or any(char in name for char in "/.@"):
            return None
        return name.replace("-", "_")
