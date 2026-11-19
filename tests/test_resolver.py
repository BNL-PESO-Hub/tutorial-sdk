from tutorial_sdk.spec import TutorialSpec, ContentSpec
from tutorial_sdk.resolver import (
    TutorialResolver,
    ResolutionResult,
)


def test_resolver_all_present(tmp_path):
    (tmp_path / "notebooks").mkdir()
    nb = tmp_path / "notebooks/01-intro.ipynb"
    nb.write_text("{}")
    readme = tmp_path / "README.md"
    readme.write_text("# Hello")

    spec = TutorialSpec(
        name="resolve-test",
        content=ContentSpec(
            notebooks=["notebooks/01-intro.ipynb"],
            docs=["README.md"],
        ),
    )
    resolver = TutorialResolver(spec, tmp_path)
    result = resolver.resolve()

    assert isinstance(result, ResolutionResult)
    assert len(result.missing_paths) == 0
    assert len(result.content_paths) == 2


def test_resolver_missing_files(tmp_path):
    spec = TutorialSpec(
        name="miss-test",
        content=ContentSpec(
            notebooks=["notebooks/missing.ipynb"],
        ),
    )
    resolver = TutorialResolver(spec, tmp_path)
    result = resolver.resolve()

    assert len(result.missing_paths) == 1
    assert "missing.ipynb" in str(result.missing_paths[0])
    assert result.missing_relative_paths == ("notebooks/missing.ipynb",)


def test_resolver_missing_non_subpath(tmp_path):
    spec = TutorialSpec(
        name="abs-miss-test",
        content=ContentSpec(
            notebooks=["/nonexistent/abs_missing.ipynb"],
        ),
    )
    result = TutorialResolver(spec, tmp_path).resolve()
    assert result.missing_relative_paths == ("/nonexistent/abs_missing.ipynb",)


def test_resolver_image_default():
    spec = TutorialSpec(name="img-test")
    resolver = TutorialResolver(spec, ".")
    result = resolver.resolve()
    assert result.image == "img-test:latest"


def test_resolver_image_from_spec():
    spec = TutorialSpec(
        name="custom-img",
        build={"image": "ghcr.io/org/custom:v1"},
    )
    resolver = TutorialResolver(spec, ".")
    result = resolver.resolve()
    assert result.image == "ghcr.io/org/custom:v1"
