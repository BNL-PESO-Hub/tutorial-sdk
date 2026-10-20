"""Container runtime validation."""

from pathlib import Path

from ..backend import (
    ContainerBackend,
    ContainerStartFailed,
    get_backend,
)
from ..errors import BackendUnavailableError, ValidationError
from ..spec import TutorialSpec
from .report import ValidationCheck, ValidationReport

#: Seconds to watch a freshly started container before declaring
#: its startup successful (early-exits as soon as it stops).
_STARTUP_WINDOW = 2.0


class ContainerValidator:
    """Validate built container environments."""

    def __init__(
        self,
        spec: TutorialSpec,
        root: str | Path = ".",
        backend: ContainerBackend | None = None,
    ) -> None:
        """Create a container validator.

        Args:
            spec: Parsed tutorial specification.
            root: Tutorial project root directory.
            backend: Optional container backend override.
                Defaults to auto-detection via
                :func:`tutorial_sdk.backend.get_backend`.
        """

        self.spec = spec
        self.root = Path(root)
        self.backend = backend

    def validate(self, image: str | None = None) -> ValidationReport:
        """Verify the built container image starts and has JupyterLab.

        Args:
            image: Override image tag.  Falls back to
                the build spec's configured image.

        Returns:
            A ``ValidationReport`` with container
            start and JupyterLab availability checks.

        Raises:
            ValidationError: If no container backend is
                available (e.g. Docker is not installed).
            ConfigError: If ``TUTORIAL_SDK_BACKEND`` names an
                unknown backend.
        """

        image_name = (
            image or self.spec.build.image or f"{self.spec.name}:latest"
        )
        container_name = f"tutorial-sdk-val-{self.spec.name}"

        try:
            backend = self.backend or get_backend()
        except BackendUnavailableError as exc:
            raise ValidationError(
                "Docker is not installed or not on PATH."
            ) from exc

        checks: list[ValidationCheck] = []
        errors: list[str] = []

        # 1. Attempt to run the container in the background.  Any
        # stale container with the same name is removed first by
        # the backend.
        try:
            handle = backend.run_detached(image_name, name=container_name)
        except ContainerStartFailed as exc:
            msg = f"Failed to start container: {exc}"
            return ValidationReport(
                passed=False,
                checks=[
                    ValidationCheck(
                        name="container.start",
                        passed=False,
                        message=msg,
                    )
                ],
                errors=[msg],
            )
        except BackendUnavailableError as exc:
            raise ValidationError(
                "Docker is not installed or not on PATH."
            ) from exc

        try:
            # Watch initial startup; exits early if the container
            # dies, instead of sleeping a fixed interval.
            if not backend.wait_running(handle, timeout=_STARTUP_WINDOW):
                msg = (
                    "Container stopped immediately. Logs:\n"
                    f"{backend.logs(handle)}"
                )
                checks.append(
                    ValidationCheck(
                        name="container.start",
                        passed=False,
                        message=msg,
                    )
                )
                errors.append(msg)
                return ValidationReport(
                    passed=False,
                    checks=checks,
                    errors=errors,
                )

            checks.append(
                ValidationCheck(
                    name="container.start",
                    passed=True,
                    message="Container started successfully.",
                )
            )

            # 2. Check if JupyterLab executable is available
            # inside the container.
            exec_res = backend.exec(
                handle, ["jupyter", "lab", "--version"]
            )

            if exec_res.exit_code == 0:
                checks.append(
                    ValidationCheck(
                        name="container.jupyterlab",
                        passed=True,
                        message=(
                            "JupyterLab is available (version "
                            f"{exec_res.output})."
                        ),
                    )
                )
            else:
                msg = (
                    "JupyterLab command not available in container: "
                    f"{exec_res.output}"
                )
                checks.append(
                    ValidationCheck(
                        name="container.jupyterlab",
                        passed=False,
                        message=msg,
                    )
                )
                errors.append(msg)

        finally:
            # Make sure we clean up the container under all
            # circumstances.
            backend.remove(handle, force=True)

        return ValidationReport(
            passed=all(c.passed for c in checks),
            checks=checks,
            errors=errors,
        )
