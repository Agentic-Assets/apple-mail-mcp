"""Manifest validation check groups for tools/validate_manifests.py.

The check functions historically lived in ``tools/validate_manifests.py``.
They are split here by concern (version, tool counts, install contracts,
codex surface, artifacts, module budget) to keep every module under the
600 LOC budget. ``tools/validate_manifests.py`` orchestrates them in ``main``
and re-exports them for ``tests/infra/test_validate_manifests.py``.
"""
