"""Typed tutorial specification models."""

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .errors import ConfigError

_PYTHON_VERSION_STR: str = "3.11"


class AuthorSpec(BaseModel):
    """Tutorial author metadata."""

    model_config = ConfigDict(extra="forbid")

    name: str
    email: str | None = None


class RuntimeSpec(BaseModel):
    """Runtime configuration for a tutorial environment."""

    model_config = ConfigDict(extra="forbid")

    language: str = "python"
    python: str = _PYTHON_VERSION_STR
    kernel: str = "python3"
    jupyterlab: bool = True
    expose_port: int = 8888


class DependencySpec(BaseModel):
    """Package dependencies declared by package manager."""

    model_config = ConfigDict(extra="forbid")

    apt: list[str] = Field(default_factory=list)
    pip: list[str] = Field(default_factory=list)
    conda: list[str] = Field(default_factory=list)
    local: list[str] = Field(default_factory=list)


class ContentSpec(BaseModel):
    """Tutorial content assets copied into the image."""

    model_config = ConfigDict(extra="forbid")

    notebooks: list[str] = Field(default_factory=list)
    scripts: list[str] = Field(default_factory=list)
    data: list[str] = Field(default_factory=list)
    docs: list[str] = Field(default_factory=list)
    exercises: list[str] = Field(default_factory=list)
    solutions: list[str] = Field(default_factory=list)

    def all_paths(self) -> list[str]:
        """Return every declared content path in stable order."""

        return [
            *self.notebooks,
            *self.scripts,
            *self.data,
            *self.docs,
            *self.exercises,
            *self.solutions,
        ]


class DockerfileSections(BaseModel):
    """Optional user-managed Dockerfile snippets."""

    model_config = ConfigDict(extra="forbid")

    before_dependencies: str | None = None
    after_dependencies: str | None = None
    before_entrypoint: str | None = None


class BuildSpec(BaseModel):
    """Build artifact and container image configuration."""

    model_config = ConfigDict(extra="forbid")

    dockerfile: str = "Dockerfile"
    image: str | None = None
    base_image: str = f"python:{_PYTHON_VERSION_STR}-slim"
    copy_repo: bool = True
    preexecute_notebooks: bool = False
    export_devcontainer: bool = False
    export_manifest: bool = True
    cache: bool = True
    custom_sections: DockerfileSections = Field(
        default_factory=DockerfileSections
    )


class ValidationSpec(BaseModel):
    """Validation checks enabled for a tutorial."""

    model_config = ConfigDict(extra="forbid")

    execute_notebooks: bool = False
    check_imports: bool = True
    check_links: bool = True
    require_clean_execution: bool = True


class EntrypointSpec(BaseModel):
    """Default runtime entrypoint."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["jupyterlab", "shell", "command"] = "jupyterlab"
    default_notebook: str | None = None
    command: list[str] | None = None


class TutorialSpec(BaseModel):
    """Source-of-truth tutorial specification."""

    model_config = ConfigDict(extra="forbid")

    name: str
    version: str = "0.1.0"
    title: str | None = None
    description: str = ""
    authors: list[AuthorSpec] = Field(default_factory=list)
    license: str | None = None
    runtime: RuntimeSpec = Field(default_factory=RuntimeSpec)
    dependencies: DependencySpec = Field(default_factory=DependencySpec)
    content: ContentSpec = Field(default_factory=ContentSpec)
    build: BuildSpec = Field(default_factory=BuildSpec)
    validation: ValidationSpec = Field(default_factory=ValidationSpec)
    entrypoint: EntrypointSpec = Field(default_factory=EntrypointSpec)

    @field_validator("name")
    @classmethod
    def validate_name(
        cls,
        value: str,
    ) -> str:
        """Validate the spec name used for files and image tags."""

        cleaned = value.strip()
        if not cleaned:
            raise ValueError("name must not be empty")
        return cleaned

    @property
    def display_title(self) -> str:
        """Return the title exposed in generated metadata."""

        return self.title or self.name

    @classmethod
    def load(cls, path: str | Path) -> "TutorialSpec":
        """Load a tutorial specification from YAML.

        Args:
            path: Path to a tutorial YAML file.

        Returns:
            Parsed tutorial specification.

        Raises:
            ConfigError: If the file cannot be read or validated.
        """

        spec_path = Path(path)
        try:
            raw = yaml.safe_load(spec_path.read_text()) or {}
        except OSError as exc:
            raise ConfigError(f"Unable to read {spec_path}: {exc}") from exc
        except yaml.YAMLError as exc:
            raise ConfigError(f"Unable to parse {spec_path}: {exc}") from exc

        try:
            return cls.model_validate(raw)
        except ValueError as exc:
            raise ConfigError(str(exc)) from exc

    def write(self, path: str | Path) -> None:
        """Write this specification as YAML.

        Args:
            path: Destination file path.
        """

        spec_path = Path(path)
        data = self.model_dump(mode="json", exclude_none=True)
        spec_path.write_text(yaml.safe_dump(data, sort_keys=False))

    def to_manifest_dict(
        self,
        image: str | None = None,
    ) -> dict[str, Any]:
        """Return the reproducibility manifest payload.

        Args:
            image: Override image tag.  Falls back to
                the build spec's configured image.

        Returns:
            Dictionary suitable for JSON serialisation.
        """

        return {
            "name": self.name,
            "version": self.version,
            "title": self.display_title,
            "description": self.description,
            "base_image": self.build.base_image,
            "image": image or self.build.image,
            "runtime": self.runtime.model_dump(mode="json"),
            "content": self.content.model_dump(mode="json"),
            "dependencies": self.dependencies.model_dump(mode="json"),
            "entrypoint": self.entrypoint.model_dump(mode="json"),
        }
