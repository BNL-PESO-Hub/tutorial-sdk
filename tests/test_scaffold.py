from tutorial_sdk.cli import main
from tutorial_sdk.spec import TutorialSpec
from tutorial_sdk.scaffold.templates import (
    ProjectScaffolder,
)


def test_scaffold_minimal(tmp_path):
    target = tmp_path / "minimal-proj"
    scaffolder = ProjectScaffolder()
    scaffolder.scaffold(
        "minimal",
        target,
        "my-minimal",
    )

    assert (target / "tutorial.yml").exists()
    assert (target / "notebooks").is_dir()
    spec = TutorialSpec.load(target / "tutorial.yml")
    assert spec.name == "my-minimal"
    assert len(spec.content.notebooks) == 1
    assert spec.content.exercises == []
    assert spec.content.solutions == []


def test_scaffold_workshop(tmp_path):
    target = tmp_path / "workshop-proj"
    scaffolder = ProjectScaffolder()
    scaffolder.scaffold(
        "workshop",
        target,
        "my-workshop",
    )

    assert (target / "tutorial.yml").exists()
    spec = TutorialSpec.load(target / "tutorial.yml")
    assert spec.name == "my-workshop"
    assert len(spec.content.notebooks) == 2
    assert len(spec.content.exercises) == 1
    assert len(spec.content.solutions) == 1
    assert "numpy" in spec.dependencies.pip

    # Verify exercise/solution dirs were created
    assert (target / "notebooks" / "exercises").is_dir()
    assert (target / "notebooks" / "solutions").is_dir()


def test_scaffold_lab_exercise(tmp_path):
    target = tmp_path / "lab-proj"
    scaffolder = ProjectScaffolder()
    scaffolder.scaffold(
        "lab-exercise",
        target,
        "my-lab",
    )

    spec = TutorialSpec.load(target / "tutorial.yml")
    assert spec.name == "my-lab"
    assert len(spec.content.exercises) == 1
    assert len(spec.content.solutions) == 1
    assert spec.validation.require_clean_execution


def test_scaffold_demo(tmp_path):
    target = tmp_path / "demo-proj"
    scaffolder = ProjectScaffolder()
    scaffolder.scaffold(
        "demo",
        target,
        "my-demo",
    )

    spec = TutorialSpec.load(target / "tutorial.yml")
    assert spec.name == "my-demo"
    assert len(spec.content.notebooks) == 1
    assert "demo.ipynb" in spec.content.notebooks[0]
    assert spec.build.preexecute_notebooks


def test_scaffold_notebook_tutorial(tmp_path):
    target = tmp_path / "nb-proj"
    scaffolder = ProjectScaffolder()
    scaffolder.scaffold(
        "notebook-tutorial",
        target,
        "my-nb",
    )

    spec = TutorialSpec.load(target / "tutorial.yml")
    assert spec.name == "my-nb"
    assert len(spec.content.notebooks) == 2
    assert (target / "data").is_dir()


def test_scaffold_cli_integration(tmp_path):
    target = tmp_path / "cli-scaffold"
    code = main(
        [
            "scaffold",
            "workshop",
            "--name",
            "ws-test",
            "--path",
            str(target),
        ]
    )
    assert code == 0
    assert (target / "tutorial.yml").exists()
