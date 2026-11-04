"""Docker image builder adapter."""

from dataclasses import dataclass
from pathlib import Path

from ..backend import ContainerBackend, LogCallback, get_backend
from ..errors import BackendError, BuildError
from ..spec import TutorialSpec


@dataclass(frozen=True)
class BuildResult:
    """Result of a container build attempt."""

    image: str
    dockerfile: Path
    pushed: bool = False
    digest: str | None = None
    status: str = "success"


class DockerBuilder:
    """Build tutorial images with Docker."""

    def __init__(
        self,
        spec: TutorialSpec,
        root: str | Path = ".",
        backend: ContainerBackend | str | None = None,
    ) -> None:
        """Create a Docker builder.

        Args:
            spec: Parsed tutorial specification.
            root: Tutorial project root directory.
            backend: Container backend instance, or a backend name
                (``"auto"``, ``"docker-cli"``, ``"docker-sdk"``)
                taking precedence over ``TUTORIAL_SDK_BACKEND`` and
                ``build.backend``.  Defaults to that selection
                (see :func:`tutorial_sdk.backend.get_backend`).
        """

        self.spec = spec
        self.root = Path(root)
        self.backend = backend

    def build(
        self,
        image: str | None = None,
        no_cache: bool | None = None,
        platform: str | None = None,
        log_callback: LogCallback | None = None,
    ) -> BuildResult:
        """Build the tutorial image.

        Args:
            image: Override image tag.
            no_cache: If ``True``, disable Docker layer
                caching.
            platform: Target platform (e.g.
                ``linux/amd64``).
            log_callback: Optional sink receiving each build output
                line as it is produced.  Without it the build is
                silent and a failure's ``BuildError`` message carries
                the build output; with it the output has already been
                shown and the message is short.

        Returns:
            A ``BuildResult`` on success.

        Raises:
            BuildError: If the build fails or no container
                backend is available.
            ConfigError: If the selected backend name is
                unknown.
        """

        image = image or self.spec.build.image or f"{self.spec.name}:latest"
        dockerfile = self.root / self.spec.build.dockerfile

        if no_cache is None:
            cache_enabled = self.spec.build.cache
        else:
            cache_enabled = not no_cache

        try:
            backend = get_backend(
                self.backend, fallback_name=self.spec.build.backend
            )
            result = backend.build_image(
                context=self.root,
                dockerfile=dockerfile,
                tag=image,
                no_cache=not cache_enabled,
                platform=platform,
                log_callback=log_callback,
            )
        except BackendError as exc:
            # Covers ImageBuildFailed and BackendUnavailableError.
            raise BuildError(str(exc)) from exc

        return BuildResult(
            image=result.image,
            dockerfile=dockerfile,
            digest=result.image_id,
        )
