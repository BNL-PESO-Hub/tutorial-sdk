"""Asset and path validation."""

from pathlib import Path

from ..spec import TutorialSpec
from .report import ValidationCheck, ValidationReport


class AssetValidator:
    """Validate declared content assets."""

    def __init__(
        self,
        spec: TutorialSpec,
        root: str | Path = ".",
    ) -> None:
        """Create an asset validator.

        Args:
            spec: Parsed tutorial specification.
            root: Tutorial project root directory.
        """

        self.spec = spec
        self.root = Path(root)

    def validate(self) -> ValidationReport:
        """Check that declared content paths exist.

        Returns:
            A ``ValidationReport`` with path-existence
            checks.
        """

        missing = [
            path
            for path in self.spec.content.all_paths()
            if not (self.root / path).exists()
        ]
        if missing:
            message = "Missing declared paths: " + ", ".join(missing)
            return ValidationReport(
                passed=False,
                checks=[
                    ValidationCheck(
                        name="spec.paths",
                        passed=False,
                        message=message,
                    )
                ],
                errors=[message],
            )

        return ValidationReport(
            passed=True,
            checks=[
                ValidationCheck(
                    name="spec.paths",
                    passed=True,
                    message="All declared paths exist.",
                )
            ],
        )
