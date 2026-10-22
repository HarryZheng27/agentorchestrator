"""Tests for WorkspaceManager file operations, safety checks, and commands."""

import pytest
from pathlib import Path
from agent_orchestrator.workspace import WorkspaceManager


@pytest.fixture
def temp_workspace(tmp_path: Path) -> WorkspaceManager:
    return WorkspaceManager(project_dir=tmp_path)


def test_write_and_read_file(temp_workspace: WorkspaceManager):
    temp_workspace.write_file("src/main.py", "print('hello world')\n")
    content = temp_workspace.read_file("src/main.py")
    assert "print('hello world')" in content


def test_read_file_line_slices(temp_workspace: WorkspaceManager):
    lines = "line1\nline2\nline3\nline4\nline5\n"
    temp_workspace.write_file("test.txt", lines)
    slice_content = temp_workspace.read_file("test.txt", start_line=2, end_line=4)
    assert slice_content == "line2\nline3\nline4\n"


def test_patch_file(temp_workspace: WorkspaceManager):
    code = "def get_value():\n    return 41\n"
    temp_workspace.write_file("calc.py", code)
    res = temp_workspace.patch_file("calc.py", "return 41", "return 42")
    assert "Successfully patched" in res

    updated = temp_workspace.read_file("calc.py")
    assert "return 42" in updated


def test_patch_file_duplicate_error(temp_workspace: WorkspaceManager):
    code = "foo\nfoo\n"
    temp_workspace.write_file("dup.txt", code)
    with pytest.raises(ValueError, match="occurs 2 times"):
        temp_workspace.patch_file("dup.txt", "foo", "bar")


def test_path_traversal_prevention(temp_workspace: WorkspaceManager):
    with pytest.raises(PermissionError):
        temp_workspace.resolve_path("../outside.txt")


def test_list_and_search_files(temp_workspace: WorkspaceManager):
    temp_workspace.write_file("app/auth.py", "def authenticate_user(): pass\n")
    temp_workspace.write_file("app/db.py", "def connect_db(): pass\n")

    files = temp_workspace.list_files()
    assert "app/auth.py" in files
    assert "app/db.py" in files

    search_hits = temp_workspace.search_code("authenticate_user")
    assert len(search_hits) == 1
    assert search_hits[0]["file"] == "app/auth.py"


def test_run_command_in_workspace(temp_workspace: WorkspaceManager):
    denied = temp_workspace.run_command("python -c \"print('command test')\"")
    assert denied["success"] is False
    assert "disabled" in denied["stderr"]

    trusted_workspace = WorkspaceManager(
        project_dir=temp_workspace.project_dir, allow_shell_commands=True
    )
    res = trusted_workspace.run_command("python -c \"print('command test')\"")
    assert res["success"] is True
    assert "command test" in res["stdout"]


def test_list_and_search_reject_external_symlink(tmp_path: Path):
    project = tmp_path / "project"
    project.mkdir()
    outside = tmp_path / "outside.py"
    outside.write_text("external_secret_marker\n", encoding="utf-8")
    try:
        (project / "leak.py").symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("File symlinks are unavailable on this platform")

    workspace = WorkspaceManager(project_dir=project)
    assert "leak.py" not in workspace.list_files(pattern="*.py")
    assert workspace.search_code("external_secret_marker", pattern="*.py") == []


def test_list_reports_canonical_in_workspace_symlink_path(tmp_path: Path):
    workspace = WorkspaceManager(project_dir=tmp_path)
    workspace.write_file("actual.py", "safe_marker\n")
    try:
        (tmp_path / "alias.py").symlink_to(tmp_path / "actual.py")
    except (OSError, NotImplementedError):
        pytest.skip("File symlinks are unavailable on this platform")

    assert workspace.list_files(pattern="*.py") == ["actual.py"]
    assert workspace.search_code("safe_marker", pattern="*.py")[0]["file"] == "actual.py"
