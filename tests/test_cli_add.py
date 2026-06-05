from tutorial_sdk.cli import main
from tutorial_sdk.spec import TutorialSpec


def test_cli_add_notebook(tmp_path):
    spec = TutorialSpec(name="add-test")
    config = tmp_path / "tutorial.yml"
    spec.write(config)

    code = main(
        [
            "add",
            "notebook",
            "notebooks/new.ipynb",
            "--config",
            str(config),
        ]
    )
    assert code == 0

    reloaded = TutorialSpec.load(config)
    assert "notebooks/new.ipynb" in reloaded.content.notebooks


def test_cli_add_notebook_deduplication(tmp_path):
    spec = TutorialSpec(
        name="dedup-test",
        content={"notebooks": ["notebooks/a.ipynb"]},
    )
    config = tmp_path / "tutorial.yml"
    spec.write(config)

    code = main(
        [
            "add",
            "notebook",
            "notebooks/a.ipynb",
            "--config",
            str(config),
        ]
    )
    assert code == 0

    reloaded = TutorialSpec.load(config)
    assert (
        reloaded.content.notebooks.count(
            "notebooks/a.ipynb",
        )
        == 1
    )


def test_cli_add_dependency(tmp_path):
    spec = TutorialSpec(name="dep-test")
    config = tmp_path / "tutorial.yml"
    spec.write(config)

    code = main(
        [
            "add",
            "dependency",
            "numpy",
            "pandas",
            "--config",
            str(config),
        ]
    )
    assert code == 0

    reloaded = TutorialSpec.load(config)
    assert "numpy" in reloaded.dependencies.pip
    assert "pandas" in reloaded.dependencies.pip


def test_cli_add_apt(tmp_path):
    spec = TutorialSpec(name="apt-test")
    config = tmp_path / "tutorial.yml"
    spec.write(config)

    code = main(
        [
            "add",
            "apt",
            "git",
            "curl",
            "--config",
            str(config),
        ]
    )
    assert code == 0

    reloaded = TutorialSpec.load(config)
    assert "git" in reloaded.dependencies.apt
    assert "curl" in reloaded.dependencies.apt


def test_cli_add_data(tmp_path):
    spec = TutorialSpec(name="data-test")
    config = tmp_path / "tutorial.yml"
    spec.write(config)

    code = main(
        [
            "add",
            "data",
            "data/sample.csv",
            "--config",
            str(config),
        ]
    )
    assert code == 0

    reloaded = TutorialSpec.load(config)
    assert "data/sample.csv" in reloaded.content.data
