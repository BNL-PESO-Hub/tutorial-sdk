from tutorial_sdk.spec import (
    TutorialSpec,
    DependencySpec,
    EntrypointSpec,
    BuildSpec,
    DockerfileSections,
)
from tutorial_sdk.generator.dockerfile import (
    DockerfileGenerator,
)


def test_dockerfile_generator_minimal():
    spec = TutorialSpec(name="minimal")
    generator = DockerfileGenerator(spec)
    rendered = generator.render()

    assert "FROM python:3.11-slim" in rendered
    assert 'LABEL org.opencontainers.image.title="minimal"' in rendered
    assert "ENV PYTHONDONTWRITEBYTECODE=1" in rendered
    assert "WORKDIR /workspace" in rendered
    assert "COPY . /workspace" in rendered
    expected_cmd = (
        'CMD ["jupyter", "lab", "--ip=0.0.0.0", "--allow-root", "--no-browser"]'
    )
    assert expected_cmd in rendered


def test_dockerfile_generator_complex(tmp_path):
    # Setup custom Dockerfile snippet
    before_deps_snippet = tmp_path / "before.Dockerfile"
    before_deps_snippet.write_text("RUN echo 'before dependencies'")

    spec = TutorialSpec(
        name="complex",
        dependencies=DependencySpec(
            apt=["curl", "git"], pip=["numpy", "pandas"], local=["./src/helper"]
        ),
        build=BuildSpec(
            base_image="ubuntu:22.04",
            copy_repo=False,
            custom_sections=DockerfileSections(
                before_dependencies="before.Dockerfile"
            ),
        ),
        entrypoint=EntrypointSpec(kind="shell"),
    )

    generator = DockerfileGenerator(spec, root=tmp_path)
    rendered = generator.render()

    assert "FROM ubuntu:22.04" in rendered
    assert "RUN apt-get update && apt-get install -y" in rendered
    assert "curl" in rendered
    assert "git" in rendered
    assert "RUN echo 'before dependencies'" in rendered
    assert "RUN python -m venv --system-site-packages /opt/venv" in rendered
    assert 'ENV PATH="/opt/venv/bin:$PATH"' in rendered
    assert "python -m pip install --no-cache-dir numpy pandas" in rendered
    assert "python -m pip install --no-cache-dir ./src/helper" in rendered
    assert 'CMD ["/bin/sh"]' in rendered


def test_dockerfile_generator_custom_command():
    spec = TutorialSpec(
        name="command-entrypoint",
        entrypoint=EntrypointSpec(
            kind="command", command=["python", "main.py"]
        ),
    )
    generator = DockerfileGenerator(spec)
    rendered = generator.render()
    assert 'CMD ["python", "main.py"]' in rendered
