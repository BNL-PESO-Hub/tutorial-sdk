"""Tutorial manifest generation."""

import json
from pathlib import Path

from ..spec import TutorialSpec


class ManifestGenerator:
    """Generate machine-readable tutorial manifests."""

    def __init__(self, spec: TutorialSpec) -> None:
        """Create a manifest generator.

        Args:
            spec: Parsed tutorial specification.
        """

        self.spec = spec

    def render_dict(self, image: str | None = None) -> dict[str, object]:
        """Render the manifest as a dictionary.

        Args:
            image: Override image tag.

        Returns:
            Manifest payload dictionary.
        """

        return self.spec.to_manifest_dict(image=image)

    def render_json(self, image: str | None = None) -> str:
        """Render the manifest as stable JSON.

        Args:
            image: Override image tag.

        Returns:
            Pretty-printed JSON string.
        """

        return json.dumps(self.render_dict(image=image), indent=2) + "\n"

    def write(self, path: str | Path, image: str | None = None) -> None:
        """Write the manifest to disk.

        Args:
            path: Destination file path.
            image: Override image tag.
        """

        Path(path).write_text(self.render_json(image=image))
