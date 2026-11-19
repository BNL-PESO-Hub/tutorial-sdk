import pytest

from tutorial_sdk import ScaffoldError, TutorialProject
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


def test_project_init_default_and_templates(tmp_path):
    minimal_dir = tmp_path / "default-init"
    minimal_proj = TutorialProject.init(minimal_dir)
    assert minimal_proj.spec.name == "default-init"
    assert (minimal_dir / "tutorial.yml").exists()

    workshop_dir = tmp_path / "custom-dir"
    workshop_proj = TutorialProject.init(
        workshop_dir,
        template="workshop",
        name="custom-workshop",
    )
    assert workshop_proj.spec.name == "custom-workshop"
    assert len(workshop_proj.spec.content.notebooks) == 2
    assert len(workshop_proj.spec.content.exercises) == 1


def test_project_init_unknown_template(tmp_path):
    with pytest.raises(ScaffoldError, match="Unknown template: invalid"):
        TutorialProject.init(tmp_path / "bad", template="invalid")
