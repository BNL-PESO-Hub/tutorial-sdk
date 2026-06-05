"""Devcontainer configuration generation."""

import json

from ..spec import TutorialSpec


class DevcontainerGenerator:
    """Generate a minimal devcontainer configuration."""

    def __init__(self, spec: TutorialSpec) -> None:
        """Create a devcontainer generator.

        Args:
            spec: Parsed tutorial specification.
        """

        self.spec = spec

    def render_dict(self) -> dict[str, object]:
        """Render devcontainer settings.

        Returns:
            Configuration dictionary.
        """

        return {
            "name": self.spec.display_title,
            "build": {
                "dockerfile": self.spec.build.dockerfile,
                "context": "..",
            },
            "workspaceFolder": "/workspace",
            "forwardPorts": [self.spec.runtime.expose_port],
        }

    def render_json(self) -> str:
        """Render devcontainer settings as stable JSON.

        Returns:
            Pretty-printed JSON string.
        """

        return json.dumps(self.render_dict(), indent=2) + "\n"
