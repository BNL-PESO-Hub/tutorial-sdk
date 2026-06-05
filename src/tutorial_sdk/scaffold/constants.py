"""Scaffold constants, mappings, and compiled patterns."""

import re


# ---- Default Python version ----
from ..spec import _PYTHON_VERSION_STR

_PYTHON_VERSION: tuple[int, int] = tuple(
    int(x) for x in _PYTHON_VERSION_STR.split(".")
)


# ---- Module-name → PyPI-package mapping ----
# Maps common import names that differ from their
# PyPI distribution name.

_MODULE_TO_PACKAGE: dict[str, str] = {
    "cv2": "opencv-python",
    "PIL": "Pillow",
    "sklearn": "scikit-learn",
    "skimage": "scikit-image",
    "yaml": "PyYAML",
    "bs4": "beautifulsoup4",
    "attr": "attrs",
    "gi": "PyGObject",
    "wx": "wxPython",
    "serial": "pyserial",
    "usb": "pyusb",
    "Crypto": "pycryptodome",
    "dateutil": "python-dateutil",
    "dotenv": "python-dotenv",
    "google.cloud": "google-cloud",
    "google.auth": "google-auth",
    "google.protobuf": "protobuf",
    "google.generativeai": "google-generativeai",
    "tensorflow": "tensorflow",
    "tf": "tensorflow",
    "torch": "torch",
    "torchvision": "torchvision",
    "torchaudio": "torchaudio",
    "transformers": "transformers",
    "datasets": "datasets",
    "lightning": "pytorch-lightning",
    "wandb": "wandb",
    "mlflow": "mlflow",
    "Bio": "biopython",
    "lxml": "lxml",
    "zmq": "pyzmq",
    "jwt": "PyJWT",
    "dns": "dnspython",
    "magic": "python-magic",
    "pptx": "python-pptx",
    "docx": "python-docx",
    "OpenSSL": "pyOpenSSL",
    "nacl": "PyNaCl",
    "socks": "PySocks",
    "ruamel": "ruamel.yaml",
    "fitz": "PyMuPDF",
}

# Data file extensions for content.data discovery.
_DATA_EXTENSIONS: set[str] = {
    ".csv",
    ".tsv",
    ".json",
    ".jsonl",
    ".parquet",
    ".h5",
    ".hdf5",
    ".pkl",
    ".pickle",
    ".npy",
    ".npz",
    ".txt",
    ".dat",
    ".xlsx",
    ".xls",
    ".feather",
    ".arrow",
}

# Doc file extensions.
_DOC_EXTENSIONS: set[str] = {".md", ".rst", ".txt"}

# Regex for extracting packages from pip/conda/uv
# install commands inside notebook magic or shell lines.
_INSTALL_RE = re.compile(
    r"^[%!]\s*(?:pip|pip3|uv pip|conda)\s+install\s+"
    r"(.+)",
    re.MULTILINE,
)

# Pattern for individual package specs (strips flags).
_PKG_TOKEN_RE = re.compile(
    r"^([A-Za-z0-9][A-Za-z0-9._-]*"
    r"(?:\[.*?\])?(?:[><!=~]+.*)?)$"
)

# Helpers for stripping version specifiers / extras.
_VERSION_SPEC_RE = re.compile(r"[><!=~;@].*")
_EXTRAS_RE = re.compile(r"\[.*?\]")


# ---- Scaffold template configurations ----

_TEMPLATE_CONFIGS: dict[str, dict[str, object]] = {
    "minimal": {
        "description": "A minimal tutorial project.",
        "notebooks": ["notebooks/01-introduction.ipynb"],
        "pip": ["jupyterlab", "ipykernel"],
        "dirs": ["notebooks"],
    },
    "notebook-tutorial": {
        "description": ("An interactive notebook-based tutorial."),
        "notebooks": [
            "notebooks/01-introduction.ipynb",
            "notebooks/02-exercises.ipynb",
        ],
        "pip": ["jupyterlab", "ipykernel"],
        "dirs": ["notebooks", "data"],
    },
    "workshop": {
        "description": (
            "A multi-session workshop with exercises and solutions."
        ),
        "notebooks": [
            "notebooks/01-introduction.ipynb",
            "notebooks/02-hands-on.ipynb",
        ],
        "exercises": [
            "notebooks/exercises/01-exercise.ipynb",
        ],
        "solutions": [
            "notebooks/solutions/01-solution.ipynb",
        ],
        "pip": ["jupyterlab", "ipykernel", "numpy"],
        "dirs": [
            "notebooks",
            "notebooks/exercises",
            "notebooks/solutions",
            "data",
            "src",
        ],
    },
    "lab-exercise": {
        "description": (
            "A focused lab exercise with a starter and solution notebook."
        ),
        "notebooks": [
            "notebooks/01-lab.ipynb",
        ],
        "exercises": [
            "notebooks/exercises/01-lab-starter.ipynb",
        ],
        "solutions": [
            "notebooks/solutions/01-lab-solution.ipynb",
        ],
        "pip": ["jupyterlab", "ipykernel"],
        "dirs": [
            "notebooks",
            "notebooks/exercises",
            "notebooks/solutions",
        ],
        "validation": {"require_clean_execution": True},
    },
    "demo": {
        "description": ("A lightweight demonstration notebook."),
        "notebooks": [
            "notebooks/demo.ipynb",
        ],
        "pip": ["jupyterlab", "ipykernel"],
        "dirs": ["notebooks"],
        "build": {"preexecute_notebooks": True},
    },
}

# Minimal valid .ipynb JSON string (single empty code cell).
_EMPTY_NOTEBOOK: str = (
    "{\n"
    '  "cells": [\n'
    "    {\n"
    '      "cell_type": "code",\n'
    '      "execution_count": null,\n'
    '      "metadata": {},\n'
    '      "outputs": [],\n'
    '      "source": []\n'
    "    }\n"
    "  ],\n"
    '  "metadata": {},\n'
    '  "nbformat": 4,\n'
    '  "nbformat_minor": 5\n'
    "}\n"
)
