from repoval.patching import drop_test_files, extract_patch, is_test_path, split_by_file, touched_files

DIFF = """diff --git a/pkg/a.py b/pkg/a.py
--- a/pkg/a.py
+++ b/pkg/a.py
@@ -1,3 +1,3 @@
 x = 1
-y = 2
+y = 3
 z = 4
"""
TEST_DIFF = """diff --git a/tests/test_a.py b/tests/test_a.py
--- a/tests/test_a.py
+++ b/tests/test_a.py
@@ -1,2 +1,2 @@
 import a
-assert a
+assert not a
"""


def test_extracts_fenced_diff_and_ignores_surrounding_prose():
    text = f"Sure! Here's the fix:\n\n```diff\n{DIFF}```\n\nThis works because y was wrong."
    assert extract_patch(text) == DIFF


def test_extracts_unlabelled_fence_and_raw_diff():
    assert extract_patch(f"```\n{DIFF}```") == DIFF
    assert extract_patch(DIFF) == DIFF


def test_prose_after_raw_diff_is_cut_even_if_it_looks_like_a_bullet():
    text = DIFF + "\n- this bullet is commentary, not a removed line\n+ so is this\n"
    assert extract_patch(text) == DIFF


def test_hunk_counts_protect_real_lines_that_look_like_headers():
    tricky = DIFF.replace("-y = 2", "--- y = 2").replace("+y = 3", "+++ y = 3")
    assert "--- y = 2" in extract_patch(tricky)


def test_no_diff_returns_none():
    assert extract_patch("I could not find the problem.") is None
    assert extract_patch("") is None
    assert extract_patch("```python\nprint('hi')\n```") is None


def test_picks_the_longest_candidate_when_model_shows_a_snippet_then_the_answer():
    small = "```diff\n--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-a\n+b\n```"
    text = small + "\nActually the full fix is:\n```diff\n" + DIFF + "```"
    assert extract_patch(text) == DIFF


def test_empty_context_lines_stripped_by_the_model_still_count():
    broken = DIFF.replace(" x = 1\n", "\n")
    assert extract_patch(broken) is not None


def test_split_and_touched_files_git_and_plain_styles():
    both = DIFF + TEST_DIFF
    assert touched_files(both) == ["pkg/a.py", "tests/test_a.py"]
    plain = both.replace("diff --git a/pkg/a.py b/pkg/a.py\n", "").replace("diff --git a/tests/test_a.py b/tests/test_a.py\n", "")
    assert touched_files(plain) == ["pkg/a.py", "tests/test_a.py"]


def test_new_file_path_comes_from_plus_side():
    d = "--- /dev/null\n+++ b/NOTES.md\n@@ -0,0 +1 @@\n+hi\n"
    assert touched_files(d) == ["NOTES.md"]


def test_drop_test_files_keeps_only_source():
    clean, dropped = drop_test_files(DIFF + TEST_DIFF)
    assert clean == DIFF and dropped == ["tests/test_a.py"]


def test_is_test_path():
    for p in ["tests/test_x.py", "pkg/tests/helper.py", "test_x.py", "pkg/x_test.py", "conftest.py", "a/testing/b.py"]:
        assert is_test_path(p), p
    for p in ["pkg/latest.py", "pkg/contest.py", "src/attestation.py", "pkg/testimony.py"]:
        assert not is_test_path(p), p
