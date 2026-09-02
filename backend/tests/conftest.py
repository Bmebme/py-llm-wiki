"""Test isolation: point the settings data dir at a temp location BEFORE
backend.config is imported (it reads the env at import time)."""

import os
import tempfile

os.environ.setdefault(
    "PY_LLM_WIKI_DATA_DIR", tempfile.mkdtemp(prefix="py-llm-wiki-test-")
)
