"""Container runtime validation."""

import subprocess
import time
from pathlib import Path

from ..errors import ValidationError
from ..spec import TutorialSpec
from .report import ValidationCheck, ValidationReport


class ContainerValidator:
    """Validate built container environments."""

    def __init__(
        self,
        spec: TutorialSpec,
        root: str | Path = ".",
    ) -> None:
        """Create a container validator.

        Args:
            spec: Parsed tutorial specification.
            root: Tutorial project root directory.
        """

        self.spec = spec
        self.root = Path(root)

    def validate(self, image: str | None = None) -> ValidationReport:
        """Verify the built container image starts and has JupyterLab.

        Args:
            image: Override image tag.  Falls back to
                the build spec's configured image.

        Returns:
            A ``ValidationReport`` with container
            start and JupyterLab availability checks.
        """

        image_name = (
            image or self.spec.build.image or f"{self.spec.name}:latest"
        )
        container_name = f"tutorial-sdk-val-{self.spec.name}"

        # Ensure container doesn't already exist from a stale run
        subprocess.run(
            ["docker", "rm", "-f", container_name],
            capture_output=True,
            check=False,
        )

        checks = []
        errors = []

        # 1. Attempt to run the container in the background
        start_cmd = [
            "docker",
            "run",
            "-d",
            "--name",
            container_name,
            image_name,
        ]
        try:
            start_res = subprocess.run(
                start_cmd,
                capture_output=True,
                text=True,
            )
        except FileNotFoundError:
            raise ValidationError(
                "Docker is not installed or not on PATH."
            )

        if start_res.returncode != 0:
            msg = f"Failed to start container: {start_res.stderr.strip()}"
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

        try:
            # Sleep a bit to allow initial startup processes
            time.sleep(2)

            # Check if container is still running
            inspect_cmd = [
                "docker",
                "inspect",
                "-f",
                "{{.State.Running}}",
                container_name,
            ]
            inspect_res = subprocess.run(
                inspect_cmd,
                capture_output=True,
                text=True,
            )
            is_running = inspect_res.stdout.strip() == "true"

            if not is_running:
                # Capture logs to report failure reason
                logs_res = subprocess.run(
                    ["docker", "logs", container_name],
                    capture_output=True,
                    text=True,
                )
                msg = (
                    "Container stopped immediately. Logs:\n"
                    f"{logs_res.stderr.strip() or logs_res.stdout.strip()}"
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
            exec_cmd = [
                "docker",
                "exec",
                container_name,
                "jupyter",
                "lab",
                "--version",
            ]
            exec_res = subprocess.run(
                exec_cmd,
                capture_output=True,
                text=True,
            )

            if exec_res.returncode == 0:
                checks.append(
                    ValidationCheck(
                        name="container.jupyterlab",
                        passed=True,
                        message=(
                            "JupyterLab is available (version "
                            f"{exec_res.stdout.strip()})."
                        ),
                    )
                )
            else:
                msg = (
                    "JupyterLab command not available in container: "
                    f"{exec_res.stderr.strip() or exec_res.stdout.strip()}"
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
            # Make sure we clean up the container under all circumstances
            subprocess.run(
                ["docker", "rm", "-f", container_name],
                capture_output=True,
                check=False,
            )

        return ValidationReport(
            passed=all(c.passed for c in checks),
            checks=checks,
            errors=errors,
        )
