"""Detection of Dockerfiles that need BuildKit.

``DockerSdkBackend`` builds through the Engine API's classic builder
and hands Dockerfiles using BuildKit-only features to the Docker CLI.
The detector is pure text processing, so it is tested on a base
install (importing ``docker_sdk`` does not import docker-py).
"""

import pytest

from tutorial_sdk.backend.docker_sdk import requires_buildkit
from tutorial_sdk.generator import DockerfileGenerator
from tutorial_sdk.spec import TutorialSpec


@pytest.mark.parametrize(
    "text",
    [
        "# syntax=docker/dockerfile:1\nFROM base\n",
        "#syntax = docker/dockerfile:1.7\nFROM base\n",
        "# escape=`\n# check=skip=all\nFROM base\n",
        "FROM base\nRUN --mount=type=cache,target=/root/.cache pip install x\n",
        "FROM base\nRUN --network=none make\n",
        "FROM base\nRUN --mount=type=secret,id=tok cat /run/secrets/tok\n",
        "FROM base\nCOPY --link src /dst\n",
        "FROM base\nCOPY --chmod=755 run.sh /run.sh\n",
        "FROM base\nCOPY --chown=1:1 --parents a/b /dst\n",
        "FROM base\nADD --checksum=sha256:00 https://x/y /y\n",
        "FROM base\nRUN \\\n    --mount=type=ssh git clone x\n",
        (
            "FROM base\nRUN \\\n    # cache pip wheels\n"
            "    --mount=type=cache,target=/root/.cache \\\n    pip install x\n"
        ),
        (
            "FROM base\n# trailing backslash \\\n"
            "RUN --mount=type=cache,target=/root/.cache pip install x\n"
        ),
        "FROM base\nRUN <<EOF\necho hi\nEOF\n",
        "FROM base\nCOPY <<-'END' /etc/conf\nx\nEND\n",
        "FROM --platform=$BUILDPLATFORM base\n",
        "FROM base\nARG TARGETARCH\nRUN echo ${TARGETARCH}\n",
    ],
    ids=[
        "syntax",
        "syntax-spaced",
        "check-after-escape",
        "run-mount",
        "run-network",
        "run-secret",
        "copy-link",
        "copy-chmod",
        "copy-parents",
        "add-checksum",
        "continued-flag",
        "continued-with-comment",
        "comment-trailing-backslash",
        "run-heredoc",
        "copy-heredoc",
        "buildplatform",
        "targetarch",
    ],
)
def test_buildkit_features_are_detected(text):
    assert requires_buildkit(text) is True


@pytest.mark.parametrize(
    "text",
    [
        "FROM base\nRUN pip install x\n",
        "FROM base\nCOPY --chown=1000:1000 . /workspace\n",
        "FROM base\nRUN echo --mount=not-a-flag\n",
        "FROM base\n# syntax=docker/dockerfile:1\n",
        "FROM base\n# RUN --mount=type=cache true\n",
        "FROM base\nARG PLATFORM\nRUN echo $PLATFORM\n",
        "",
    ],
    ids=[
        "plain",
        "copy-chown",
        "flag-like-argument",
        "late-directive-is-a-comment",
        "commented-out",
        "user-arg",
        "empty",
    ],
)
def test_classic_dockerfiles_are_not_flagged(text):
    assert requires_buildkit(text) is False


def test_generated_dockerfiles_build_natively():
    spec = TutorialSpec.model_validate(
        {
            "name": "demo",
            "dependencies": {
                "apt": ["git"],
                "pip": ["numpy"],
                "conda": ["scipy"],
            },
            "content": {"notebooks": ["nb/a.ipynb"], "data": ["data"]},
            "build": {"export_devcontainer": True},
        }
    )
    assert requires_buildkit(DockerfileGenerator(spec, ".").render()) is False
