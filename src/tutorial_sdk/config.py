"""Configuration loading helpers."""

from pathlib import Path

from .spec import TutorialSpec


DEFAULT_CONFIG = "tutorial.yml"


def load_config(path: str | Path = DEFAULT_CONFIG) -> TutorialSpec:
    """Load a tutorial specification from disk.

    Args:
        path: Path to the YAML configuration.

    Returns:
        Parsed tutorial specification.
    """

    return TutorialSpec.load(path)
