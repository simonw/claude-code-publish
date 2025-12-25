"""Tests for HTML generation from Claude Code session JSON."""

import json
import tempfile
from pathlib import Path

import pytest
from syrupy.extensions.single_file import SingleFileSnapshotExtension, WriteMode

from claude_code_publish import (
    generate_html,
    detect_github_repo,
    render_markdown_text,
    format_json,
    is_json_like,
    render_todo_write,
    render_write_tool,
    render_edit_tool,
    render_bash_tool,
    render_content_block,
    analyze_conversation,
    format_tool_stats,
    is_tool_result_message,
)


class HTMLSnapshotExtension(SingleFileSnapshotExtension):
    """Snapshot extension that saves HTML files."""

    _write_mode = WriteMode.TEXT
    file_extension = "html"


@pytest.fixture
def snapshot_html(snapshot):
    """Fixture for HTML file snapshots."""
    return snapshot.use_extension(HTMLSnapshotExtension)


@pytest.fixture
def sample_session():
    """Load the sample session fixture."""
    fixture_path = Path(__file__).parent / "sample_session.json"
    with open(fixture_path) as f:
        return json.load(f)


@pytest.fixture
def output_dir():
    """Create a temporary output directory."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


class TestGenerateHtml:
    """Tests for the main generate_html function."""

    def test_generates_index_html(self, output_dir, snapshot_html):
        """Test index.html generation."""
        fixture_path = Path(__file__).parent / "sample_session.json"
        generate_html(fixture_path, output_dir, github_repo="example/project")

        index_html = (output_dir / "index.html").read_text()
        assert index_html == snapshot_html

    def test_generates_page_001_html(self, output_dir, snapshot_html):
        """Test page-001.html generation."""
        fixture_path = Path(__file__).parent / "sample_session.json"
        generate_html(fixture_path, output_dir, github_repo="example/project")

        page_html = (output_dir / "page-001.html").read_text()
        assert page_html == snapshot_html

    def test_generates_page_002_html(self, output_dir, snapshot_html):
        """Test page-002.html generation (continuation page)."""
        fixture_path = Path(__file__).parent / "sample_session.json"
        generate_html(fixture_path, output_dir, github_repo="example/project")

        page_html = (output_dir / "page-002.html").read_text()
        assert page_html == snapshot_html

    def test_github_repo_autodetect(self, sample_session):
        """Test GitHub repo auto-detection from git push output."""
        loglines = sample_session["loglines"]
        repo = detect_github_repo(loglines)
        assert repo == "example/project"


class TestRenderFunctions:
    """Tests for individual render functions."""

    def test_render_markdown_text(self, snapshot_html):
        """Test markdown rendering."""
        result = render_markdown_text("**bold** and `code`\n\n- item 1\n- item 2")
        assert result == snapshot_html

    def test_render_markdown_text_empty(self):
        """Test markdown rendering with empty input."""
        assert render_markdown_text("") == ""
        assert render_markdown_text(None) == ""

    def test_format_json(self, snapshot_html):
        """Test JSON formatting."""
        result = format_json({"key": "value", "number": 42, "nested": {"a": 1}})
        assert result == snapshot_html

    def test_is_json_like(self):
        """Test JSON-like string detection."""
        assert is_json_like('{"key": "value"}')
        assert is_json_like("[1, 2, 3]")
        assert not is_json_like("plain text")
        assert not is_json_like("")
        assert not is_json_like(None)

    def test_render_todo_write(self, snapshot_html):
        """Test TodoWrite rendering."""
        tool_input = {
            "todos": [
                {"content": "First task", "status": "completed", "activeForm": "First"},
                {
                    "content": "Second task",
                    "status": "in_progress",
                    "activeForm": "Second",
                },
                {"content": "Third task", "status": "pending", "activeForm": "Third"},
            ]
        }
        result = render_todo_write(tool_input, "tool-123")
        assert result == snapshot_html

    def test_render_todo_write_empty(self):
        """Test TodoWrite with no todos."""
        result = render_todo_write({"todos": []}, "tool-123")
        assert result == ""

    def test_render_write_tool(self, snapshot_html):
        """Test Write tool rendering."""
        tool_input = {
            "file_path": "/project/src/main.py",
            "content": "def hello():\n    print('hello world')\n",
        }
        result = render_write_tool(tool_input, "tool-123")
        assert result == snapshot_html

    def test_render_edit_tool(self, snapshot_html):
        """Test Edit tool rendering."""
        tool_input = {
            "file_path": "/project/file.py",
            "old_string": "old code here",
            "new_string": "new code here",
        }
        result = render_edit_tool(tool_input, "tool-123")
        assert result == snapshot_html

    def test_render_edit_tool_replace_all(self, snapshot_html):
        """Test Edit tool with replace_all flag."""
        tool_input = {
            "file_path": "/project/file.py",
            "old_string": "old",
            "new_string": "new",
            "replace_all": True,
        }
        result = render_edit_tool(tool_input, "tool-123")
        assert result == snapshot_html

    def test_render_bash_tool(self, snapshot_html):
        """Test Bash tool rendering."""
        tool_input = {
            "command": "pytest tests/ -v",
            "description": "Run tests with verbose output",
        }
        result = render_bash_tool(tool_input, "tool-123")
        assert result == snapshot_html


class TestRenderContentBlock:
    """Tests for render_content_block function."""

    def test_thinking_block(self, snapshot_html):
        """Test thinking block rendering."""
        block = {
            "type": "thinking",
            "thinking": "Let me think about this...\n\n1. First consideration\n2. Second point",
        }
        result = render_content_block(block)
        assert result == snapshot_html

    def test_text_block(self, snapshot_html):
        """Test text block rendering."""
        block = {"type": "text", "text": "Here is my response with **markdown**."}
        result = render_content_block(block)
        assert result == snapshot_html

    def test_tool_result_block(self, snapshot_html):
        """Test tool result rendering."""
        block = {
            "type": "tool_result",
            "content": "Command completed successfully\nOutput line 1\nOutput line 2",
            "is_error": False,
        }
        result = render_content_block(block)
        assert result == snapshot_html

    def test_tool_result_error(self, snapshot_html):
        """Test tool result error rendering."""
        block = {
            "type": "tool_result",
            "content": "Error: file not found\nTraceback follows...",
            "is_error": True,
        }
        result = render_content_block(block)
        assert result == snapshot_html

    def test_tool_result_with_commit(self, snapshot_html):
        """Test tool result with git commit output."""
        # Need to set the global _github_repo for commit link rendering
        import claude_code_publish

        old_repo = claude_code_publish._github_repo
        claude_code_publish._github_repo = "example/repo"
        try:
            block = {
                "type": "tool_result",
                "content": "[main abc1234] Add new feature\n 2 files changed, 10 insertions(+)",
                "is_error": False,
            }
            result = render_content_block(block)
            assert result == snapshot_html
        finally:
            claude_code_publish._github_repo = old_repo


class TestAnalyzeConversation:
    """Tests for conversation analysis."""

    def test_counts_tools(self):
        """Test that tool usage is counted."""
        messages = [
            (
                "assistant",
                json.dumps(
                    {
                        "content": [
                            {
                                "type": "tool_use",
                                "name": "Bash",
                                "id": "1",
                                "input": {},
                            },
                            {
                                "type": "tool_use",
                                "name": "Bash",
                                "id": "2",
                                "input": {},
                            },
                            {
                                "type": "tool_use",
                                "name": "Write",
                                "id": "3",
                                "input": {},
                            },
                        ]
                    }
                ),
                "2025-01-01T00:00:00Z",
            ),
        ]
        result = analyze_conversation(messages)
        assert result["tool_counts"]["Bash"] == 2
        assert result["tool_counts"]["Write"] == 1

    def test_extracts_commits(self):
        """Test that git commits are extracted."""
        messages = [
            (
                "user",
                json.dumps(
                    {
                        "content": [
                            {
                                "type": "tool_result",
                                "content": "[main abc1234] Add new feature\n 1 file changed",
                            }
                        ]
                    }
                ),
                "2025-01-01T00:00:00Z",
            ),
        ]
        result = analyze_conversation(messages)
        assert len(result["commits"]) == 1
        assert result["commits"][0][0] == "abc1234"
        assert "Add new feature" in result["commits"][0][1]


class TestFormatToolStats:
    """Tests for tool stats formatting."""

    def test_formats_counts(self):
        """Test tool count formatting."""
        counts = {"Bash": 5, "Read": 3, "Write": 1}
        result = format_tool_stats(counts)
        assert "5 bash" in result
        assert "3 read" in result
        assert "1 write" in result

    def test_empty_counts(self):
        """Test empty tool counts."""
        assert format_tool_stats({}) == ""


class TestIsToolResultMessage:
    """Tests for tool result message detection."""

    def test_detects_tool_result_only(self):
        """Test detection of tool-result-only messages."""
        message = {"content": [{"type": "tool_result", "content": "result"}]}
        assert is_tool_result_message(message) is True

    def test_rejects_mixed_content(self):
        """Test rejection of mixed content messages."""
        message = {
            "content": [
                {"type": "text", "text": "hello"},
                {"type": "tool_result", "content": "result"},
            ]
        }
        assert is_tool_result_message(message) is False

    def test_rejects_empty(self):
        """Test rejection of empty content."""
        assert is_tool_result_message({"content": []}) is False
        assert is_tool_result_message({"content": "string"}) is False
