"""Deterministic Dockerfile generation."""

from pathlib import Path

from ..spec import TutorialSpec


class DockerfileGenerator:
    """Render Dockerfiles from tutorial specifications."""

    def __init__(
        self,
        spec: TutorialSpec,
        root: str | Path = ".",
    ) -> None:
        """Create a Dockerfile generator.

        Args:
            spec: Parsed tutorial specification.
            root: Tutorial project root for custom section files.
        """

        self.spec = spec
        self.root = Path(root)

    def render(self) -> str:
        """Render the Dockerfile text.

        Returns:
            Complete Dockerfile contents ending with a trailing newline.
        """

        lines = [
            f"FROM {self.spec.build.base_image}",
            "",
            self._label("title", self.spec.display_title),
            self._label("version", self.spec.version),
            self._label("description", self.spec.description),
            "",
            "ENV PYTHONDONTWRITEBYTECODE=1",
            "ENV PYTHONUNBUFFERED=1",
            "",
        ]

        if self.spec.dependencies.apt:
            lines.extend(self._render_apt())

        lines.extend(self._read_custom_section("before_dependencies"))

        if self.spec.dependencies.pip or self.spec.dependencies.local:
            lines.extend(
                [
                    "RUN python -m venv --system-site-packages /opt/venv",
                    'ENV PATH="/opt/venv/bin:$PATH"',
                    "",
                ]
            )

        if self.spec.dependencies.pip:
            lines.extend(self._render_pip())

        if self.spec.dependencies.conda:
            lines.extend(self._render_conda_note())

        lines.extend(self._read_custom_section("after_dependencies"))
        lines.extend(["WORKDIR /workspace", ""])

        if self.spec.build.copy_repo:
            lines.extend(["COPY . /workspace", ""])
        else:
            lines.extend(self._render_content_copies())

        if self.spec.dependencies.local:
            lines.extend(self._render_local_dependencies())

        if self.spec.runtime.jupyterlab:
            lines.extend([f"EXPOSE {self.spec.runtime.expose_port}", ""])

        lines.extend(self._read_custom_section("before_entrypoint"))
        lines.extend(self._render_entrypoint())
        return "\n".join(lines).rstrip() + "\n"

    def _render_apt(self) -> list[str]:
        """Render ``apt-get install`` instructions.

        Returns:
            Dockerfile lines, or empty list if no apt
            packages are declared.
        """

        packages = [
            pkg.strip() for pkg in self.spec.dependencies.apt if pkg.strip()
        ]
        if not packages:
            return []
        lines = ["RUN apt-get update && apt-get install -y \\"]
        for i, package in enumerate(packages):
            if i == len(packages) - 1:
                lines.append(f"    {package} && \\")
            else:
                lines.append(f"    {package} \\")
        lines.extend(["    rm -rf /var/lib/apt/lists/*", ""])
        return lines

    def _render_pip(self) -> list[str]:
        """Render ``pip install`` instructions.

        Returns:
            Dockerfile lines, or empty list if no pip
            packages are declared.
        """

        packages = [
            pkg.strip() for pkg in self.spec.dependencies.pip if pkg.strip()
        ]
        if not packages:
            return []
        packages_str = " ".join(packages)
        return [
            "RUN python -m pip install --upgrade pip setuptools && \\",
            f"    python -m pip install --no-cache-dir {packages_str}",
            "",
        ]

    def _render_conda_note(self) -> list[str]:
        """Render a commented conda placeholder.

        Conda packages are listed as comments since the
        base image may not include conda.

        Returns:
            Commented Dockerfile lines.
        """

        packages = [
            pkg.strip() for pkg in self.spec.dependencies.conda if pkg.strip()
        ]
        if not packages:
            return []
        packages_str = " ".join(packages)
        return [
            "# Conda dependencies are declared but no conda base image was",
            "# requested. Install them when a conda-capable image is used.",
            f"# conda install -y {packages_str}",
            "",
        ]

    def _render_local_dependencies(self) -> list[str]:
        """Render ``pip install`` for local packages.

        Returns:
            Dockerfile lines for local dependency
            installations.
        """

        lines: list[str] = []
        for dependency in self.spec.dependencies.local:
            dep = dependency.strip()
            if dep:
                lines.append(f"RUN python -m pip install --no-cache-dir {dep}")
        if lines:
            lines.append("")
        return lines

    def _render_content_copies(self) -> list[str]:
        """Render ``COPY`` instructions for each content path.

        Returns:
            Dockerfile ``COPY`` lines.
        """

        lines: list[str] = []
        for path in self.spec.content.all_paths():
            lines.append(f"COPY {path} /workspace/{path}")
        if lines:
            lines.append("")
        return lines

    def _render_entrypoint(self) -> list[str]:
        """Render the ``CMD`` instruction.

        Returns:
            A single-element list with the ``CMD``
            line.
        """

        entrypoint = self.spec.entrypoint
        if entrypoint.kind == "shell":
            return ['CMD ["/bin/sh"]']
        if entrypoint.kind == "command" and entrypoint.command:
            parts = ", ".join(f'"{part}"' for part in entrypoint.command)
            return [f"CMD [{parts}]"]

        notebook = entrypoint.default_notebook
        command = [
            "jupyter",
            "lab",
            "--ip=0.0.0.0",
            "--allow-root",
            "--no-browser",
        ]
        if notebook:
            command.append(notebook)
        parts = ", ".join(f'"{part}"' for part in command)
        return [f"CMD [{parts}]"]

    def _read_custom_section(self, name: str) -> list[str]:
        """Read a user-supplied Dockerfile snippet.

        Args:
            name: Attribute name on ``DockerfileSections``
                (e.g. ``"before_dependencies"``).

        Returns:
            Lines from the snippet file, or an empty
            list if not configured.
        """

        section = getattr(self.spec.build.custom_sections, name)
        if not section:
            return []

        section_path = self.root / section
        if not section_path.exists():
            return [f"# Missing custom Dockerfile section: {section}", ""]
        return [section_path.read_text().rstrip(), ""]

    @staticmethod
    def _escape_label(value: str) -> str:
        """Escape a string for use in a Dockerfile LABEL."""

        return value.replace("\\", "\\\\").replace('"', '\\"')

    @classmethod
    def _label(
        cls,
        name: str,
        value: str,
    ) -> str:
        """Build an OCI image label instruction."""

        escaped = cls._escape_label(value)
        return f'LABEL org.opencontainers.image.{name}="{escaped}"'
