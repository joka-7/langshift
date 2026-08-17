"""
Shared fixtures.

Provider doubles live in tests/helpers.py; sample repositories live here.
The ts_repo fixture was previously duplicated between test_integration.py and
test_webui.py.
"""

from __future__ import annotations

import pytest


@pytest.fixture
def ts_repo(tmp_path):
    """A minimal single-file TypeScript repo the offline transformer can handle."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "main.ts").write_text(
        "function add(a: number, b: number): number {\n  return a + b;\n}\n"
        "console.log(add(2, 3));\n"
    )
    return repo


@pytest.fixture
def ts_repo_with_tests(tmp_path):
    """
    A TypeScript repo containing both source and test files.

    The suite previously had no fixture with test files in it at all, which left
    agent._is_test_file, the source/test split, and the TEST_FRAMEWORK_MAP
    prompt injection completely uncovered.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "math.ts").write_text(
        "export function add(a: number, b: number): number {\n  return a + b;\n}\n"
    )
    (repo / "math.test.ts").write_text(
        "import { add } from './math';\n"
        "test('adds', () => {\n  expect(add(2, 3)).toBe(5);\n});\n"
    )
    (repo / "util.spec.ts").write_text(
        "describe('util', () => {\n  it('works', () => {});\n});\n"
    )
    return repo
