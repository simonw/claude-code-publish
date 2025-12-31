"""Tests for HTML generation from Claude Code session JSON."""

import json
import tempfile
from pathlib import Path

import pytest
from syrupy.extensions.single_file import SingleFileSnapshotExtension, WriteMode

from claude_code_transcripts import (
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
    inject_gist_preview_js,
    create_gist,
    GIST_PREVIEW_JS,
    parse_session_file,
    get_session_summary,
    find_local_sessions,
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

        index_html = (output_dir / "index.html").read_text(encoding="utf-8")
        assert index_html == snapshot_html

    def test_generates_page_001_html(self, output_dir, snapshot_html):
        """Test page-001.html generation."""
        fixture_path = Path(__file__).parent / "sample_session.json"
        generate_html(fixture_path, output_dir, github_repo="example/project")

        page_html = (output_dir / "page-001.html").read_text(encoding="utf-8")
        assert page_html == snapshot_html

    def test_generates_page_002_html(self, output_dir, snapshot_html):
        """Test page-002.html generation (continuation page)."""
        fixture_path = Path(__file__).parent / "sample_session.json"
        generate_html(fixture_path, output_dir, github_repo="example/project")

        page_html = (output_dir / "page-002.html").read_text(encoding="utf-8")
        assert page_html == snapshot_html

    def test_github_repo_autodetect(self, sample_session):
        """Test GitHub repo auto-detection from git push output."""
        loglines = sample_session["loglines"]
        repo = detect_github_repo(loglines)
        assert repo == "example/project"

    def test_handles_array_content_format(self, tmp_path):
        """Test that user messages with array content format are recognized.

        Claude Code v2.0.76+ uses array content format like:
        {"type": "user", "message": {"content": [{"type": "text", "text": "..."}]}}
        instead of the simpler string format:
        {"type": "user", "message": {"content": "..."}}
        """
        jsonl_file = tmp_path / "session.jsonl"
        jsonl_file.write_text(
            '{"type":"user","message":{"role":"user","content":[{"type":"text","text":"Hello from array format"}]}}\n'
            '{"type":"assistant","message":{"role":"assistant","content":[{"type":"text","text":"Hi there!"}]}}\n'
        )

        output_dir = tmp_path / "output"
        output_dir.mkdir()

        generate_html(jsonl_file, output_dir)

        index_html = (output_dir / "index.html").read_text(encoding="utf-8")
        # Should have 1 prompt, not 0
        assert "1 prompts" in index_html or "1 prompt" in index_html
        assert "0 prompts" not in index_html
        # The page file should exist
        assert (output_dir / "page-001.html").exists()


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

    def test_render_markdown_strips_style_tags(self):
        """Test that <style> tags in markdown content are stripped."""
        # This prevents CSS in transcript content from affecting page styles
        text = "Here is some CSS:\n<style>:root { --bg: red; }</style>"
        result = render_markdown_text(text)
        assert "<style>" not in result
        assert "</style>" not in result

    def test_render_markdown_strips_script_tags(self):
        """Test that <script> tags in markdown content are stripped."""
        # This prevents JS in transcript content from executing
        text = "Here is some code:\n<script>alert('xss')</script>"
        result = render_markdown_text(text)
        assert "<script>" not in result
        assert "</script>" not in result

    def test_render_markdown_strips_form_elements(self):
        """Test that form elements in markdown content are stripped."""
        # This prevents forms/inputs from being rendered
        text = (
            "Here is a form:\n<form><input type='text'><button>Submit</button></form>"
        )
        result = render_markdown_text(text)
        assert "<form>" not in result
        assert "<input" not in result
        assert "<button>" not in result

    def test_render_markdown_allows_safe_tags(self):
        """Test that safe markdown tags are preserved."""
        text = "**bold** and `code` and [link](http://example.com)"
        result = render_markdown_text(text)
        assert "<strong>bold</strong>" in result
        assert "<code>code</code>" in result
        # nh3 adds rel="noopener noreferrer" to links for security
        assert '<a href="http://example.com"' in result
        assert ">link</a>" in result

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

    def test_image_block(self, snapshot_html):
        """Test image block rendering with base64 data URL."""
        # 200x200 black GIF - minimal valid GIF with black pixels
        # Generated with: from PIL import Image; img = Image.new('RGB', (200, 200), (0, 0, 0)); img.save('black.gif')
        import base64
        import io

        # Create a minimal 200x200 black GIF using raw bytes
        # GIF89a header + logical screen descriptor + global color table + image data
        gif_data = (
            b"GIF89a"  # Header
            b"\xc8\x00\xc8\x00"  # Width 200, Height 200
            b"\x80"  # Global color table flag (1 color: 2^(0+1)=2 colors)
            b"\x00"  # Background color index
            b"\x00"  # Pixel aspect ratio
            b"\x00\x00\x00"  # Color 0: black
            b"\x00\x00\x00"  # Color 1: black (padding)
            b","  # Image separator
            b"\x00\x00\x00\x00"  # Left, Top
            b"\xc8\x00\xc8\x00"  # Width 200, Height 200
            b"\x00"  # No local color table
            b"\x08"  # LZW minimum code size
            b"\x02\x04\x01\x00"  # Compressed data (minimal)
            b";"  # GIF trailer
        )
        black_gif_base64 = base64.b64encode(gif_data).decode("ascii")

        block = {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/gif",
                "data": black_gif_base64,
            },
        }
        result = render_content_block(block)
        # The result should contain an img tag with data URL
        assert 'src="data:image/gif;base64,' in result
        assert "max-width: 100%" in result
        assert result == snapshot_html

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
        import claude_code_transcripts

        old_repo = claude_code_transcripts._github_repo
        claude_code_transcripts._github_repo = "example/repo"
        try:
            block = {
                "type": "tool_result",
                "content": "[main abc1234] Add new feature\n 2 files changed, 10 insertions(+)",
                "is_error": False,
            }
            result = render_content_block(block)
            assert result == snapshot_html
        finally:
            claude_code_transcripts._github_repo = old_repo


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


class TestInjectGistPreviewJs:
    """Tests for the inject_gist_preview_js function."""

    def test_injects_js_into_html_files(self, output_dir):
        """Test that JS is injected before </body> tag."""
        # Create test HTML files
        (output_dir / "index.html").write_text(
            "<html><body><h1>Test</h1></body></html>", encoding="utf-8"
        )
        (output_dir / "page-001.html").write_text(
            "<html><body><p>Page 1</p></body></html>", encoding="utf-8"
        )

        inject_gist_preview_js(output_dir)

        index_content = (output_dir / "index.html").read_text(encoding="utf-8")
        page_content = (output_dir / "page-001.html").read_text(encoding="utf-8")

        # Check JS was injected
        assert GIST_PREVIEW_JS in index_content
        assert GIST_PREVIEW_JS in page_content

        # Check JS is before </body>
        assert index_content.endswith("</body></html>")
        assert "<script>" in index_content

    def test_gist_preview_js_handles_fragment_navigation(self):
        """Test that GIST_PREVIEW_JS includes fragment navigation handling.

        When accessing a gisthost URL with a fragment like:
        https://gisthost.github.io/?GIST_ID/page-001.html#msg-2025-12-26T15-30-45-910Z

        The content loads dynamically, so the browser's native fragment
        navigation fails because the element doesn't exist yet. The JS
        should scroll to the fragment element after content loads.
        """
        # The JS should check for fragment in URL
        assert (
            "location.hash" in GIST_PREVIEW_JS
            or "window.location.hash" in GIST_PREVIEW_JS
        )
        # The JS should scroll to the element
        assert "scrollIntoView" in GIST_PREVIEW_JS

    def test_gist_preview_js_supports_both_hosts(self):
        """Test that GIST_PREVIEW_JS supports both gisthost and gistpreview domains."""
        assert "gisthost.github.io" in GIST_PREVIEW_JS
        assert "gistpreview.github.io" in GIST_PREVIEW_JS

    def test_skips_files_without_body(self, output_dir):
        """Test that files without </body> are not modified."""
        original_content = "<html><head><title>Test</title></head></html>"
        (output_dir / "fragment.html").write_text(original_content, encoding="utf-8")

        inject_gist_preview_js(output_dir)

        assert (output_dir / "fragment.html").read_text(
            encoding="utf-8"
        ) == original_content

    def test_handles_empty_directory(self, output_dir):
        """Test that empty directories don't cause errors."""
        inject_gist_preview_js(output_dir)
        # Should complete without error


class TestCreateGist:
    """Tests for the create_gist function."""

    def test_creates_gist_successfully(self, output_dir, monkeypatch):
        """Test successful gist creation."""
        import subprocess
        import click

        # Create test HTML files
        (output_dir / "index.html").write_text(
            "<html><body>Index</body></html>", encoding="utf-8"
        )
        (output_dir / "page-001.html").write_text(
            "<html><body>Page</body></html>", encoding="utf-8"
        )

        # Mock subprocess.run to simulate successful gh gist create
        mock_result = subprocess.CompletedProcess(
            args=["gh", "gist", "create"],
            returncode=0,
            stdout="https://gist.github.com/testuser/abc123def456\n",
            stderr="",
        )

        def mock_run(*args, **kwargs):
            return mock_result

        monkeypatch.setattr(subprocess, "run", mock_run)

        gist_id, gist_url = create_gist(output_dir)

        assert gist_id == "abc123def456"
        assert gist_url == "https://gist.github.com/testuser/abc123def456"

    def test_raises_on_no_html_files(self, output_dir):
        """Test that error is raised when no HTML files exist."""
        import click

        with pytest.raises(click.ClickException) as exc_info:
            create_gist(output_dir)

        assert "No HTML files found" in str(exc_info.value)

    def test_raises_on_gh_cli_error(self, output_dir, monkeypatch):
        """Test that error is raised when gh CLI fails."""
        import subprocess
        import click

        # Create test HTML file
        (output_dir / "index.html").write_text(
            "<html><body>Test</body></html>", encoding="utf-8"
        )

        # Mock subprocess.run to simulate gh error
        def mock_run(*args, **kwargs):
            raise subprocess.CalledProcessError(
                returncode=1,
                cmd=["gh", "gist", "create"],
                stderr="error: Not logged in",
            )

        monkeypatch.setattr(subprocess, "run", mock_run)

        with pytest.raises(click.ClickException) as exc_info:
            create_gist(output_dir)

        assert "Failed to create gist" in str(exc_info.value)

    def test_raises_on_gh_not_found(self, output_dir, monkeypatch):
        """Test that error is raised when gh CLI is not installed."""
        import subprocess
        import click

        # Create test HTML file
        (output_dir / "index.html").write_text(
            "<html><body>Test</body></html>", encoding="utf-8"
        )

        # Mock subprocess.run to simulate gh not found
        def mock_run(*args, **kwargs):
            raise FileNotFoundError()

        monkeypatch.setattr(subprocess, "run", mock_run)

        with pytest.raises(click.ClickException) as exc_info:
            create_gist(output_dir)

        assert "gh CLI not found" in str(exc_info.value)


class TestSessionGistOption:
    """Tests for the session command --gist option."""

    def test_session_gist_creates_gist(self, monkeypatch, tmp_path):
        """Test that session --gist creates a gist."""
        from click.testing import CliRunner
        from claude_code_transcripts import cli
        import subprocess

        # Create sample session file
        fixture_path = Path(__file__).parent / "sample_session.json"

        # Mock subprocess.run for gh gist create
        mock_result = subprocess.CompletedProcess(
            args=["gh", "gist", "create"],
            returncode=0,
            stdout="https://gist.github.com/testuser/abc123\n",
            stderr="",
        )

        def mock_run(*args, **kwargs):
            return mock_result

        monkeypatch.setattr(subprocess, "run", mock_run)

        # Mock tempfile.gettempdir to use our tmp_path
        monkeypatch.setattr(
            "claude_code_transcripts.tempfile.gettempdir", lambda: str(tmp_path)
        )

        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["json", str(fixture_path), "--gist"],
        )

        assert result.exit_code == 0
        assert "Creating GitHub gist" in result.output
        assert "gist.github.com" in result.output
        assert "gisthost.github.io" in result.output

    def test_session_gist_with_output_dir(self, monkeypatch, output_dir):
        """Test that session --gist with -o uses specified directory."""
        from click.testing import CliRunner
        from claude_code_transcripts import cli
        import subprocess

        fixture_path = Path(__file__).parent / "sample_session.json"

        # Mock subprocess.run for gh gist create
        mock_result = subprocess.CompletedProcess(
            args=["gh", "gist", "create"],
            returncode=0,
            stdout="https://gist.github.com/testuser/abc123\n",
            stderr="",
        )

        def mock_run(*args, **kwargs):
            return mock_result

        monkeypatch.setattr(subprocess, "run", mock_run)

        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["json", str(fixture_path), "-o", str(output_dir), "--gist"],
        )

        assert result.exit_code == 0
        assert (output_dir / "index.html").exists()
        # Verify JS was injected (supports both gisthost and gistpreview)
        index_content = (output_dir / "index.html").read_text(encoding="utf-8")
        assert "gisthost.github.io" in index_content


class TestContinuationLongTexts:
    """Tests for long text extraction from continuation conversations."""

    def test_long_text_in_continuation_appears_in_index(self, output_dir):
        """Test that long texts from continuation conversations appear in index.

        This is a regression test for a bug where conversations marked as
        continuations (isCompactSummary=True) were completely skipped when
        building the index, causing their long_texts to be lost.
        """
        # Create a session with:
        # 1. An initial user prompt
        # 2. Some messages
        # 3. A continuation prompt (isCompactSummary=True)
        # 4. An assistant message with a long text summary (>300 chars)
        session_data = {
            "loglines": [
                # Initial user prompt
                {
                    "type": "user",
                    "timestamp": "2025-01-01T10:00:00.000Z",
                    "message": {
                        "content": "Build a Redis JavaScript module",
                        "role": "user",
                    },
                },
                # Some assistant work
                {
                    "type": "assistant",
                    "timestamp": "2025-01-01T10:00:05.000Z",
                    "message": {
                        "role": "assistant",
                        "content": [
                            {"type": "text", "text": "I'll start working on this."}
                        ],
                    },
                },
                # Continuation prompt (context was summarized)
                {
                    "type": "user",
                    "timestamp": "2025-01-01T11:00:00.000Z",
                    "isCompactSummary": True,
                    "message": {
                        "content": "This session is being continued from a previous conversation...",
                        "role": "user",
                    },
                },
                # More assistant work after continuation
                {
                    "type": "assistant",
                    "timestamp": "2025-01-01T11:00:05.000Z",
                    "message": {
                        "role": "assistant",
                        "content": [{"type": "text", "text": "Continuing the work..."}],
                    },
                },
                # Final summary - this is a LONG text (>300 chars) that should appear in index
                {
                    "type": "assistant",
                    "timestamp": "2025-01-01T12:00:00.000Z",
                    "message": {
                        "role": "assistant",
                        "content": [
                            {
                                "type": "text",
                                "text": (
                                    "All tasks completed successfully. Here's a summary of what was built:\n\n"
                                    "## Redis JavaScript Module\n\n"
                                    "A loadable Redis module providing JavaScript scripting via the mquickjs engine.\n\n"
                                    "### Commands Implemented\n"
                                    "- JS.EVAL - Execute JavaScript with KEYS/ARGV arrays\n"
                                    "- JS.LOAD / JS.CALL - Cache and call scripts by SHA1\n"
                                    "- JS.EXISTS / JS.FLUSH - Manage script cache\n\n"
                                    "All 41 tests pass. Changes pushed to branch."
                                ),
                            }
                        ],
                    },
                },
            ]
        }

        # Write the session to a temp file
        session_file = output_dir / "test_session.json"
        session_file.write_text(json.dumps(session_data), encoding="utf-8")

        # Generate HTML
        generate_html(session_file, output_dir)

        # Read the index.html
        index_html = (output_dir / "index.html").read_text(encoding="utf-8")

        # The long text summary should appear in the index
        # This is the bug: currently it doesn't because the continuation
        # conversation is skipped entirely
        assert (
            "All tasks completed successfully" in index_html
        ), "Long text from continuation conversation should appear in index"
        assert "Redis JavaScript Module" in index_html


class TestSessionJsonOption:
    """Tests for the session command --json option."""

    def test_session_json_copies_file(self, output_dir):
        """Test that session --json copies the JSON file to output."""
        from click.testing import CliRunner
        from claude_code_transcripts import cli

        fixture_path = Path(__file__).parent / "sample_session.json"

        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["json", str(fixture_path), "-o", str(output_dir), "--json"],
        )

        assert result.exit_code == 0
        json_file = output_dir / "sample_session.json"
        assert json_file.exists()
        assert "JSON:" in result.output
        assert "KB" in result.output

    def test_session_json_preserves_original_name(self, output_dir):
        """Test that --json preserves the original filename."""
        from click.testing import CliRunner
        from claude_code_transcripts import cli

        fixture_path = Path(__file__).parent / "sample_session.json"

        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["json", str(fixture_path), "-o", str(output_dir), "--json"],
        )

        assert result.exit_code == 0
        # Should use original filename, not "session.json"
        assert (output_dir / "sample_session.json").exists()
        assert not (output_dir / "session.json").exists()


class TestImportJsonOption:
    """Tests for the import command --json option."""

    def test_import_json_saves_session_data(self, httpx_mock, output_dir):
        """Test that import --json saves the session JSON."""
        from click.testing import CliRunner
        from claude_code_transcripts import cli

        # Load sample session to mock API response
        fixture_path = Path(__file__).parent / "sample_session.json"
        with open(fixture_path) as f:
            session_data = json.load(f)

        httpx_mock.add_response(
            url="https://api.anthropic.com/v1/session_ingress/session/test-session-id",
            json=session_data,
        )

        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "web",
                "test-session-id",
                "--token",
                "test-token",
                "--org-uuid",
                "test-org",
                "-o",
                str(output_dir),
                "--json",
            ],
        )

        assert result.exit_code == 0
        json_file = output_dir / "test-session-id.json"
        assert json_file.exists()
        assert "JSON:" in result.output
        assert "KB" in result.output

        # Verify JSON content is valid
        with open(json_file) as f:
            saved_data = json.load(f)
        assert saved_data == session_data


class TestImportGistOption:
    """Tests for the import command --gist option."""

    def test_import_gist_creates_gist(self, httpx_mock, monkeypatch, tmp_path):
        """Test that import --gist creates a gist."""
        from click.testing import CliRunner
        from claude_code_transcripts import cli
        import subprocess

        # Load sample session to mock API response
        fixture_path = Path(__file__).parent / "sample_session.json"
        with open(fixture_path) as f:
            session_data = json.load(f)

        httpx_mock.add_response(
            url="https://api.anthropic.com/v1/session_ingress/session/test-session-id",
            json=session_data,
        )

        # Mock subprocess.run for gh gist create
        mock_result = subprocess.CompletedProcess(
            args=["gh", "gist", "create"],
            returncode=0,
            stdout="https://gist.github.com/testuser/def456\n",
            stderr="",
        )

        def mock_run(*args, **kwargs):
            return mock_result

        monkeypatch.setattr(subprocess, "run", mock_run)

        # Mock tempfile.gettempdir
        monkeypatch.setattr(
            "claude_code_transcripts.tempfile.gettempdir", lambda: str(tmp_path)
        )

        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "web",
                "test-session-id",
                "--token",
                "test-token",
                "--org-uuid",
                "test-org",
                "--gist",
            ],
        )

        assert result.exit_code == 0
        assert "Creating GitHub gist" in result.output
        assert "gist.github.com" in result.output
        assert "gisthost.github.io" in result.output


class TestVersionOption:
    """Tests for the --version option."""

    def test_version_long_flag(self):
        """Test that --version shows version info."""
        import importlib.metadata
        from click.testing import CliRunner
        from claude_code_transcripts import cli

        runner = CliRunner()
        result = runner.invoke(cli, ["--version"])

        expected_version = importlib.metadata.version("claude-code-transcripts")
        assert result.exit_code == 0
        assert expected_version in result.output

    def test_version_short_flag(self):
        """Test that -v shows version info."""
        import importlib.metadata
        from click.testing import CliRunner
        from claude_code_transcripts import cli

        runner = CliRunner()
        result = runner.invoke(cli, ["-v"])

        expected_version = importlib.metadata.version("claude-code-transcripts")
        assert result.exit_code == 0
        assert expected_version in result.output


class TestOpenOption:
    """Tests for the --open option."""

    def test_session_open_calls_webbrowser(self, output_dir, monkeypatch):
        """Test that session --open opens the browser."""
        from click.testing import CliRunner
        from claude_code_transcripts import cli

        fixture_path = Path(__file__).parent / "sample_session.json"

        # Track webbrowser.open calls
        opened_urls = []

        def mock_open(url):
            opened_urls.append(url)
            return True

        monkeypatch.setattr("claude_code_transcripts.webbrowser.open", mock_open)

        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["json", str(fixture_path), "-o", str(output_dir), "--open"],
        )

        assert result.exit_code == 0
        assert len(opened_urls) == 1
        assert "index.html" in opened_urls[0]
        assert opened_urls[0].startswith("file://")

    def test_import_open_calls_webbrowser(self, httpx_mock, output_dir, monkeypatch):
        """Test that import --open opens the browser."""
        from click.testing import CliRunner
        from claude_code_transcripts import cli

        # Load sample session to mock API response
        fixture_path = Path(__file__).parent / "sample_session.json"
        with open(fixture_path) as f:
            session_data = json.load(f)

        httpx_mock.add_response(
            url="https://api.anthropic.com/v1/session_ingress/session/test-session-id",
            json=session_data,
        )

        # Track webbrowser.open calls
        opened_urls = []

        def mock_open(url):
            opened_urls.append(url)
            return True

        monkeypatch.setattr("claude_code_transcripts.webbrowser.open", mock_open)

        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "web",
                "test-session-id",
                "--token",
                "test-token",
                "--org-uuid",
                "test-org",
                "-o",
                str(output_dir),
                "--open",
            ],
        )

        assert result.exit_code == 0
        assert len(opened_urls) == 1
        assert "index.html" in opened_urls[0]
        assert opened_urls[0].startswith("file://")


class TestParseSessionFile:
    """Tests for parse_session_file which abstracts both JSON and JSONL formats."""

    def test_parses_json_format(self):
        """Test that standard JSON format is parsed correctly."""
        fixture_path = Path(__file__).parent / "sample_session.json"
        result = parse_session_file(fixture_path)

        assert "loglines" in result
        assert len(result["loglines"]) > 0
        # Check first entry
        first = result["loglines"][0]
        assert first["type"] == "user"
        assert "timestamp" in first
        assert "message" in first

    def test_parses_jsonl_format(self):
        """Test that JSONL format is parsed and converted to standard format."""
        fixture_path = Path(__file__).parent / "sample_session.jsonl"
        result = parse_session_file(fixture_path)

        assert "loglines" in result
        assert len(result["loglines"]) > 0
        # Check structure matches JSON format
        for entry in result["loglines"]:
            assert "type" in entry
            # Skip summary entries which don't have message
            if entry["type"] in ("user", "assistant"):
                assert "timestamp" in entry
                assert "message" in entry

    def test_jsonl_skips_non_message_entries(self):
        """Test that summary and file-history-snapshot entries are skipped."""
        fixture_path = Path(__file__).parent / "sample_session.jsonl"
        result = parse_session_file(fixture_path)

        # None of the loglines should be summary or file-history-snapshot
        for entry in result["loglines"]:
            assert entry["type"] in ("user", "assistant")

    def test_jsonl_preserves_message_content(self):
        """Test that message content is preserved correctly."""
        fixture_path = Path(__file__).parent / "sample_session.jsonl"
        result = parse_session_file(fixture_path)

        # Find the first user message
        user_msg = next(e for e in result["loglines"] if e["type"] == "user")
        assert user_msg["message"]["content"] == "Create a hello world function"

    def test_jsonl_generates_html(self, output_dir, snapshot_html):
        """Test that JSONL files can be converted to HTML."""
        fixture_path = Path(__file__).parent / "sample_session.jsonl"
        generate_html(fixture_path, output_dir)

        index_html = (output_dir / "index.html").read_text(encoding="utf-8")
        assert "hello world" in index_html.lower()
        assert index_html == snapshot_html

    def test_jsonl_preserves_tool_use_result(self, tmp_path):
        """Test that toolUseResult field is preserved in parsed entries.

        This is needed for originalFile content used in remote session code reconstruction.
        """
        # Create a JSONL file with toolUseResult
        jsonl_content = """{"type":"user","timestamp":"2025-01-01T10:00:00Z","message":{"role":"user","content":"Edit the file"}}
{"type":"assistant","timestamp":"2025-01-01T10:00:05Z","message":{"role":"assistant","content":[{"type":"tool_use","id":"toolu_001","name":"Edit","input":{"file_path":"/test.py","old_string":"old","new_string":"new"}}]}}
{"type":"user","timestamp":"2025-01-01T10:00:10Z","toolUseResult":{"originalFile":"original content here","filePath":"/test.py"},"message":{"role":"user","content":[{"type":"tool_result","tool_use_id":"toolu_001","content":"File edited"}]}}"""

        jsonl_file = tmp_path / "test.jsonl"
        jsonl_file.write_text(jsonl_content)

        result = parse_session_file(jsonl_file)

        # Find the tool result entry (last user message)
        tool_result_entry = [
            e
            for e in result["loglines"]
            if e["type"] == "user" and "tool_result" in str(e)
        ][-1]

        # toolUseResult should be preserved
        assert "toolUseResult" in tool_result_entry
        assert (
            tool_result_entry["toolUseResult"]["originalFile"]
            == "original content here"
        )
        assert tool_result_entry["toolUseResult"]["filePath"] == "/test.py"

    def test_jsonl_preserves_is_meta(self, tmp_path):
        """Test that isMeta field is preserved in parsed entries.

        Skill expansion messages have isMeta=True and should be treated as
        continuations for prompt numbering.
        """
        jsonl_content = """{"type":"user","timestamp":"2025-01-01T10:00:00Z","message":{"role":"user","content":"Use the test skill"}}
{"type":"assistant","timestamp":"2025-01-01T10:00:05Z","message":{"role":"assistant","content":[{"type":"text","text":"Invoking skill..."}]}}
{"type":"user","timestamp":"2025-01-01T10:00:10Z","isMeta":true,"message":{"role":"user","content":[{"type":"text","text":"Base directory for this skill: /path/to/skill"}]}}
{"type":"assistant","timestamp":"2025-01-01T10:00:15Z","message":{"role":"assistant","content":[{"type":"text","text":"Working on it..."}]}}"""

        jsonl_file = tmp_path / "test.jsonl"
        jsonl_file.write_text(jsonl_content)

        result = parse_session_file(jsonl_file)

        # Find the skill expansion entry (isMeta=True)
        meta_entry = [e for e in result["loglines"] if e.get("isMeta")]
        assert len(meta_entry) == 1
        assert meta_entry[0]["isMeta"] is True
        assert "Base directory for this skill" in str(meta_entry[0]["message"])


class TestGetSessionSummary:
    """Tests for get_session_summary which extracts summary from session files."""

    def test_gets_summary_from_jsonl(self):
        """Test extracting summary from JSONL file."""
        fixture_path = Path(__file__).parent / "sample_session.jsonl"
        summary = get_session_summary(fixture_path)
        assert summary == "Test session for JSONL parsing"

    def test_gets_first_user_message_if_no_summary(self, tmp_path):
        """Test falling back to first user message when no summary entry."""
        jsonl_file = tmp_path / "test.jsonl"
        jsonl_file.write_text(
            '{"type":"user","timestamp":"2025-01-01T00:00:00Z","message":{"role":"user","content":"Hello world test"}}\n'
        )
        summary = get_session_summary(jsonl_file)
        assert summary == "Hello world test"

    def test_returns_no_summary_for_empty_file(self, tmp_path):
        """Test handling empty or invalid files."""
        jsonl_file = tmp_path / "empty.jsonl"
        jsonl_file.write_text("", encoding="utf-8")
        summary = get_session_summary(jsonl_file)
        assert summary == "(no summary)"

    def test_truncates_long_summaries(self, tmp_path):
        """Test that long summaries are truncated."""
        jsonl_file = tmp_path / "long.jsonl"
        long_text = "x" * 300
        jsonl_file.write_text(f'{{"type":"summary","summary":"{long_text}"}}\n')
        summary = get_session_summary(jsonl_file, max_length=100)
        assert len(summary) <= 100
        assert summary.endswith("...")


class TestFindLocalSessions:
    """Tests for find_local_sessions which discovers local JSONL files."""

    def test_finds_jsonl_files(self, tmp_path):
        """Test finding JSONL files in projects directory."""
        # Create mock .claude/projects structure
        projects_dir = tmp_path / ".claude" / "projects" / "test-project"
        projects_dir.mkdir(parents=True)

        # Create a session file
        session_file = projects_dir / "session-123.jsonl"
        session_file.write_text(
            '{"type":"summary","summary":"Test session"}\n'
            '{"type":"user","timestamp":"2025-01-01T00:00:00Z","message":{"role":"user","content":"Hello"}}\n'
        )

        results = find_local_sessions(tmp_path / ".claude" / "projects", limit=10)
        assert len(results) == 1
        assert results[0][0] == session_file
        assert results[0][1] == "Test session"

    def test_excludes_agent_files(self, tmp_path):
        """Test that agent- prefixed files are excluded."""
        projects_dir = tmp_path / ".claude" / "projects" / "test-project"
        projects_dir.mkdir(parents=True)

        # Create agent file (should be excluded)
        agent_file = projects_dir / "agent-123.jsonl"
        agent_file.write_text('{"type":"user","message":{"content":"test"}}\n')

        # Create regular file (should be included)
        session_file = projects_dir / "session-123.jsonl"
        session_file.write_text(
            '{"type":"summary","summary":"Real session"}\n'
            '{"type":"user","timestamp":"2025-01-01T00:00:00Z","message":{"role":"user","content":"Hello"}}\n'
        )

        results = find_local_sessions(tmp_path / ".claude" / "projects", limit=10)
        assert len(results) == 1
        assert "agent-" not in results[0][0].name

    def test_excludes_warmup_sessions(self, tmp_path):
        """Test that warmup sessions are excluded."""
        projects_dir = tmp_path / ".claude" / "projects" / "test-project"
        projects_dir.mkdir(parents=True)

        # Create warmup file (should be excluded)
        warmup_file = projects_dir / "warmup-session.jsonl"
        warmup_file.write_text('{"type":"summary","summary":"warmup"}\n')

        # Create regular file
        session_file = projects_dir / "session-123.jsonl"
        session_file.write_text(
            '{"type":"summary","summary":"Real session"}\n'
            '{"type":"user","timestamp":"2025-01-01T00:00:00Z","message":{"role":"user","content":"Hello"}}\n'
        )

        results = find_local_sessions(tmp_path / ".claude" / "projects", limit=10)
        assert len(results) == 1
        assert results[0][1] == "Real session"

    def test_sorts_by_modification_time(self, tmp_path):
        """Test that results are sorted by modification time, newest first."""
        import time

        projects_dir = tmp_path / ".claude" / "projects" / "test-project"
        projects_dir.mkdir(parents=True)

        # Create files with different mtimes
        file1 = projects_dir / "older.jsonl"
        file1.write_text(
            '{"type":"summary","summary":"Older"}\n{"type":"user","timestamp":"2025-01-01T00:00:00Z","message":{"role":"user","content":"test"}}\n'
        )

        time.sleep(0.1)  # Ensure different mtime

        file2 = projects_dir / "newer.jsonl"
        file2.write_text(
            '{"type":"summary","summary":"Newer"}\n{"type":"user","timestamp":"2025-01-01T00:00:00Z","message":{"role":"user","content":"test"}}\n'
        )

        results = find_local_sessions(tmp_path / ".claude" / "projects", limit=10)
        assert len(results) == 2
        assert results[0][1] == "Newer"  # Most recent first
        assert results[1][1] == "Older"

    def test_respects_limit(self, tmp_path):
        """Test that limit parameter is respected."""
        projects_dir = tmp_path / ".claude" / "projects" / "test-project"
        projects_dir.mkdir(parents=True)

        # Create 5 files
        for i in range(5):
            f = projects_dir / f"session-{i}.jsonl"
            f.write_text(
                f'{{"type":"summary","summary":"Session {i}"}}\n{{"type":"user","timestamp":"2025-01-01T00:00:00Z","message":{{"role":"user","content":"test"}}}}\n'
            )

        results = find_local_sessions(tmp_path / ".claude" / "projects", limit=3)
        assert len(results) == 3


class TestLocalSessionCLI:
    """Tests for CLI behavior with local sessions."""

    def test_local_shows_sessions_and_converts(self, tmp_path, monkeypatch):
        """Test that 'local' command shows sessions and converts selected one."""
        from click.testing import CliRunner
        from claude_code_transcripts import cli
        import questionary

        # Create mock .claude/projects structure
        projects_dir = tmp_path / ".claude" / "projects" / "test-project"
        projects_dir.mkdir(parents=True)

        session_file = projects_dir / "session-123.jsonl"
        session_file.write_text(
            '{"type":"summary","summary":"Test local session"}\n'
            '{"type":"user","timestamp":"2025-01-01T00:00:00Z","message":{"role":"user","content":"Hello"}}\n'
        )

        # Mock Path.home() to return our tmp_path
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        # Mock questionary.select to return the session file
        class MockSelect:
            def __init__(self, *args, **kwargs):
                pass

            def ask(self):
                return session_file

        monkeypatch.setattr(questionary, "select", MockSelect)

        runner = CliRunner()
        result = runner.invoke(cli, ["local"])

        assert result.exit_code == 0
        assert "Loading local sessions" in result.output
        assert "Generated" in result.output

    def test_no_args_runs_local_command(self, tmp_path, monkeypatch):
        """Test that running with no arguments runs local command."""
        from click.testing import CliRunner
        from claude_code_transcripts import cli
        import questionary

        # Create mock .claude/projects structure
        projects_dir = tmp_path / ".claude" / "projects" / "test-project"
        projects_dir.mkdir(parents=True)

        session_file = projects_dir / "session-123.jsonl"
        session_file.write_text(
            '{"type":"summary","summary":"Test default session"}\n'
            '{"type":"user","timestamp":"2025-01-01T00:00:00Z","message":{"role":"user","content":"Hello"}}\n'
        )

        # Mock Path.home() to return our tmp_path
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        # Mock questionary.select to return the session file
        class MockSelect:
            def __init__(self, *args, **kwargs):
                pass

            def ask(self):
                return session_file

        monkeypatch.setattr(questionary, "select", MockSelect)

        runner = CliRunner()
        result = runner.invoke(cli, [])

        assert result.exit_code == 0
        assert "Loading local sessions" in result.output

    def test_local_handles_cancelled_selection(self, tmp_path, monkeypatch):
        """Test that local command handles cancelled selection gracefully."""
        from click.testing import CliRunner
        from claude_code_transcripts import cli
        import questionary

        # Create mock .claude/projects structure
        projects_dir = tmp_path / ".claude" / "projects" / "test-project"
        projects_dir.mkdir(parents=True)

        session_file = projects_dir / "session-123.jsonl"
        session_file.write_text(
            '{"type":"summary","summary":"Test session"}\n'
            '{"type":"user","timestamp":"2025-01-01T00:00:00Z","message":{"role":"user","content":"Hello"}}\n'
        )

        # Mock Path.home() to return our tmp_path
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        # Mock questionary.select to return None (cancelled)
        class MockSelect:
            def __init__(self, *args, **kwargs):
                pass

            def ask(self):
                return None

        monkeypatch.setattr(questionary, "select", MockSelect)

        runner = CliRunner()
        result = runner.invoke(cli, ["local"])

        assert result.exit_code == 0
        assert "No session selected" in result.output


class TestOutputAutoOption:
    """Tests for the -a/--output-auto flag."""

    def test_json_output_auto_creates_subdirectory(self, tmp_path):
        """Test that json -a creates output subdirectory named after file stem."""
        from click.testing import CliRunner
        from claude_code_transcripts import cli

        fixture_path = Path(__file__).parent / "sample_session.json"

        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["json", str(fixture_path), "-a", "-o", str(tmp_path)],
        )

        assert result.exit_code == 0
        # Output should be in tmp_path/sample_session/
        expected_dir = tmp_path / "sample_session"
        assert expected_dir.exists()
        assert (expected_dir / "index.html").exists()

    def test_json_output_auto_uses_cwd_when_no_output(self, tmp_path, monkeypatch):
        """Test that json -a uses current directory when -o not specified."""
        from click.testing import CliRunner
        from claude_code_transcripts import cli
        import os

        fixture_path = Path(__file__).parent / "sample_session.json"

        # Change to tmp_path
        monkeypatch.chdir(tmp_path)

        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["json", str(fixture_path), "-a"],
        )

        assert result.exit_code == 0
        # Output should be in ./sample_session/
        expected_dir = tmp_path / "sample_session"
        assert expected_dir.exists()
        assert (expected_dir / "index.html").exists()

    def test_json_output_auto_no_browser_open(self, tmp_path, monkeypatch):
        """Test that json -a does not auto-open browser."""
        from click.testing import CliRunner
        from claude_code_transcripts import cli

        fixture_path = Path(__file__).parent / "sample_session.json"

        # Track webbrowser.open calls
        opened_urls = []

        def mock_open(url):
            opened_urls.append(url)
            return True

        monkeypatch.setattr("claude_code_transcripts.webbrowser.open", mock_open)

        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["json", str(fixture_path), "-a", "-o", str(tmp_path)],
        )

        assert result.exit_code == 0
        assert len(opened_urls) == 0  # No browser opened

    def test_local_output_auto_creates_subdirectory(self, tmp_path, monkeypatch):
        """Test that local -a creates output subdirectory named after file stem."""
        from click.testing import CliRunner
        from claude_code_transcripts import cli
        import questionary

        # Create mock .claude/projects structure
        projects_dir = tmp_path / ".claude" / "projects" / "test-project"
        projects_dir.mkdir(parents=True)

        session_file = projects_dir / "my-session-file.jsonl"
        session_file.write_text(
            '{"type":"summary","summary":"Test local session"}\n'
            '{"type":"user","timestamp":"2025-01-01T00:00:00Z","message":{"role":"user","content":"Hello"}}\n'
        )

        output_parent = tmp_path / "output"
        output_parent.mkdir()

        # Mock Path.home() to return our tmp_path
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        # Mock questionary.select to return the session file
        class MockSelect:
            def __init__(self, *args, **kwargs):
                pass

            def ask(self):
                return session_file

        monkeypatch.setattr(questionary, "select", MockSelect)

        runner = CliRunner()
        result = runner.invoke(cli, ["local", "-a", "-o", str(output_parent)])

        assert result.exit_code == 0
        # Output should be in output_parent/my-session-file/
        expected_dir = output_parent / "my-session-file"
        assert expected_dir.exists()
        assert (expected_dir / "index.html").exists()

    def test_web_output_auto_creates_subdirectory(self, httpx_mock, tmp_path):
        """Test that web -a creates output subdirectory named after session ID."""
        from click.testing import CliRunner
        from claude_code_transcripts import cli

        # Load sample session to mock API response
        fixture_path = Path(__file__).parent / "sample_session.json"
        with open(fixture_path) as f:
            session_data = json.load(f)

        httpx_mock.add_response(
            url="https://api.anthropic.com/v1/session_ingress/session/my-web-session-id",
            json=session_data,
        )

        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "web",
                "my-web-session-id",
                "--token",
                "test-token",
                "--org-uuid",
                "test-org",
                "-a",
                "-o",
                str(tmp_path),
            ],
        )

        assert result.exit_code == 0
        # Output should be in tmp_path/my-web-session-id/
        expected_dir = tmp_path / "my-web-session-id"
        assert expected_dir.exists()
        assert (expected_dir / "index.html").exists()

    def test_output_auto_with_jsonl_uses_stem(self, tmp_path, monkeypatch):
        """Test that -a with JSONL file uses file stem (without .jsonl extension)."""
        from click.testing import CliRunner
        from claude_code_transcripts import cli

        # Create a JSONL file
        fixture_path = Path(__file__).parent / "sample_session.jsonl"

        monkeypatch.chdir(tmp_path)

        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["json", str(fixture_path), "-a"],
        )

        assert result.exit_code == 0
        # Output should be in ./sample_session/ (not ./sample_session.jsonl/)
        expected_dir = tmp_path / "sample_session"
        assert expected_dir.exists()
        assert (expected_dir / "index.html").exists()


class TestTwoGistStrategy:
    """Tests for the two-gist strategy when files are too large."""

    def test_single_gist_when_files_small(self, output_dir, monkeypatch):
        """Test that small files use single gist strategy (one gist, not two)."""
        import subprocess

        # Create small test HTML files (under 1MB total)
        (output_dir / "index.html").write_text(
            "<html><head></head><body>Index</body></html>", encoding="utf-8"
        )
        (output_dir / "page-001.html").write_text(
            "<html><head></head><body>Page</body></html>", encoding="utf-8"
        )

        # Track subprocess calls
        subprocess_calls = []

        def mock_run(cmd, *args, **kwargs):
            subprocess_calls.append(cmd)
            return subprocess.CompletedProcess(
                args=cmd,
                returncode=0,
                stdout="https://gist.github.com/testuser/abc123def456\n",
                stderr="",
            )

        monkeypatch.setattr(subprocess, "run", mock_run)

        gist_id, gist_url = create_gist(output_dir)

        # Should call gh gist create once with all files
        assert len(subprocess_calls) == 1
        assert subprocess_calls[0][0:3] == ["gh", "gist", "create"]
        assert gist_id == "abc123def456"

    def test_two_gist_when_files_large(self, output_dir, monkeypatch):
        """Test that large files use two-gist strategy."""
        import subprocess

        # Create test HTML files with a large code-data.json (over 1MB)
        (output_dir / "index.html").write_text(
            "<html><body>Index</body></html>", encoding="utf-8"
        )
        (output_dir / "code.html").write_text(
            "<html><body>Code</body></html>", encoding="utf-8"
        )
        # Create large code-data.json (1.5MB)
        large_data = "x" * (1500 * 1024)  # 1.5MB
        (output_dir / "code-data.json").write_text(large_data, encoding="utf-8")

        # Track subprocess calls
        subprocess_calls = []
        gist_counter = [0]  # Use list to allow mutation in closure

        def mock_run(cmd, *args, **kwargs):
            subprocess_calls.append(cmd)
            gist_counter[0] += 1
            # Return different gist IDs for each call
            gist_id = f"gist{gist_counter[0]:03d}"
            return subprocess.CompletedProcess(
                args=cmd,
                returncode=0,
                stdout=f"https://gist.github.com/testuser/{gist_id}\n",
                stderr="",
            )

        monkeypatch.setattr(subprocess, "run", mock_run)

        gist_id, gist_url = create_gist(output_dir)

        # Should call gh gist create twice (data gist + main gist with all HTML files)
        assert len(subprocess_calls) == 2
        # First call should be for data gist (code-data.json)
        first_cmd = subprocess_calls[0]
        assert subprocess_calls[0][0:3] == ["gh", "gist", "create"]
        assert "code-data.json" in " ".join(str(x) for x in first_cmd)
        # Second call should create main gist with all HTML files
        second_cmd = subprocess_calls[1]
        assert subprocess_calls[1][0:3] == ["gh", "gist", "create"]
        assert "code-data.json" not in " ".join(str(x) for x in second_cmd)

    def test_data_gist_id_injected_into_html(self, output_dir, monkeypatch):
        """Test that data gist ID is injected into HTML when using two-gist strategy."""
        import subprocess

        # Create test HTML files with large code-data.json
        # Note: inject_gist_preview_js looks for <head> tag to inject DATA_GIST_ID
        (output_dir / "index.html").write_text(
            "<html><head></head><body>Index</body></html>", encoding="utf-8"
        )
        (output_dir / "code.html").write_text(
            "<html><head></head><body>Code</body></html>", encoding="utf-8"
        )
        # Large code-data.json to trigger two-gist strategy
        large_data = "x" * (1500 * 1024)
        (output_dir / "code-data.json").write_text(large_data, encoding="utf-8")

        gist_counter = [0]

        def mock_run(cmd, *args, **kwargs):
            gist_counter[0] += 1
            # Data gist gets ID "datagist001", main gist gets "maingist002"
            if gist_counter[0] == 1:
                gist_id = "datagist001"
            else:
                gist_id = "maingist002"
            return subprocess.CompletedProcess(
                args=cmd,
                returncode=0,
                stdout=f"https://gist.github.com/testuser/{gist_id}\n",
                stderr="",
            )

        monkeypatch.setattr(subprocess, "run", mock_run)

        # create_gist handles inject_gist_preview_js internally
        gist_id, gist_url = create_gist(output_dir)

        # The main gist ID should be returned
        assert gist_id == "maingist002"

        # The code.html should have the data gist ID injected
        code_html = (output_dir / "code.html").read_text(encoding="utf-8")
        assert "datagist001" in code_html
        assert 'window.DATA_GIST_ID = "datagist001"' in code_html

    def test_size_threshold_configurable(self, output_dir, monkeypatch):
        """Test that the size threshold for two-gist strategy can be configured."""
        import subprocess

        # Create files just under default threshold
        (output_dir / "index.html").write_text(
            "<html><body>Index</body></html>", encoding="utf-8"
        )
        # ~900KB code-data.json (under 1MB default threshold)
        medium_data = "x" * (900 * 1024)
        (output_dir / "code-data.json").write_text(medium_data, encoding="utf-8")

        subprocess_calls = []

        def mock_run(cmd, *args, **kwargs):
            subprocess_calls.append(cmd)
            return subprocess.CompletedProcess(
                args=cmd,
                returncode=0,
                stdout="https://gist.github.com/testuser/abc123\n",
                stderr="",
            )

        monkeypatch.setattr(subprocess, "run", mock_run)

        # With default threshold (1MB), should use single gist (create with all files)
        gist_id, gist_url = create_gist(output_dir)
        assert len(subprocess_calls) == 1
        assert subprocess_calls[0][0:3] == ["gh", "gist", "create"]


class TestSearchFeature:
    """Tests for the search feature on index.html pages."""

    def test_search_box_in_index_html(self, output_dir):
        """Test that search box is present in index.html."""
        fixture_path = Path(__file__).parent / "sample_session.json"
        generate_html(fixture_path, output_dir, github_repo="example/project")

        index_html = (output_dir / "index.html").read_text(encoding="utf-8")

        # Search box should be present with id="search-box"
        assert 'id="search-box"' in index_html
        # Search input should be present
        assert 'id="search-input"' in index_html
        # Search button should be present
        assert 'id="search-btn"' in index_html

    def test_search_modal_in_index_html(self, output_dir):
        """Test that search modal dialog is present in index.html."""
        fixture_path = Path(__file__).parent / "sample_session.json"
        generate_html(fixture_path, output_dir, github_repo="example/project")

        index_html = (output_dir / "index.html").read_text(encoding="utf-8")

        # Search modal should be present
        assert 'id="search-modal"' in index_html
        # Results container should be present
        assert 'id="search-results"' in index_html

    def test_search_javascript_present(self, output_dir):
        """Test that search JavaScript functionality is present."""
        fixture_path = Path(__file__).parent / "sample_session.json"
        generate_html(fixture_path, output_dir, github_repo="example/project")

        # For small sessions, JavaScript is inlined in HTML
        index_html = (output_dir / "index.html").read_text(encoding="utf-8")

        # JavaScript should handle DOMParser for parsing fetched pages
        assert "DOMParser" in index_html
        # JavaScript should handle fetch for getting pages
        assert "fetch(" in index_html
        # JavaScript should handle #search= URL fragment
        assert "#search=" in index_html or "search=" in index_html

    def test_search_css_present(self, output_dir):
        """Test that search CSS styles are present."""
        fixture_path = Path(__file__).parent / "sample_session.json"
        generate_html(fixture_path, output_dir, github_repo="example/project")

        # For small sessions, CSS is inlined in HTML
        index_html = (output_dir / "index.html").read_text(encoding="utf-8")

        # CSS should style the search box
        assert "#search-box" in index_html
        # CSS should style the search modal
        assert "#search-modal" in index_html

    def test_search_box_hidden_by_default_in_css(self, output_dir):
        """Test that search box is hidden by default (for progressive enhancement)."""
        fixture_path = Path(__file__).parent / "sample_session.json"
        generate_html(fixture_path, output_dir, github_repo="example/project")

        # For small sessions, CSS is inlined in HTML
        index_html = (output_dir / "index.html").read_text(encoding="utf-8")

        # Search box should be hidden by default in CSS
        # JavaScript will show it when loaded
        assert "#search-box" in index_html
        assert "display: none" in index_html

    def test_search_total_pages_available(self, output_dir):
        """Test that total_pages is available to JavaScript for fetching."""
        fixture_path = Path(__file__).parent / "sample_session.json"
        generate_html(fixture_path, output_dir, github_repo="example/project")

        index_html = (output_dir / "index.html").read_text(encoding="utf-8")

        # Total pages should be embedded for JS to know how many pages to fetch
        assert "TOTAL_PAGES" in index_html


class TestPageDataJson:
    """Tests for page-data.json generation for gist two-gist strategy."""

    def test_generates_page_data_files_for_large_sessions(self, tmp_path):
        """Test that page-data-NNN.json files are generated for large sessions."""
        from claude_code_transcripts import PAGE_DATA_SIZE_THRESHOLD

        # Create a session with enough content to exceed threshold
        # Generate many conversations to make pages large
        loglines = []
        for i in range(50):  # Many conversations
            loglines.append(
                {
                    "type": "user",
                    "timestamp": f"2025-01-01T{i:02d}:00:00.000Z",
                    "message": {"role": "user", "content": f"Task {i}: " + "x" * 5000},
                }
            )
            loglines.append(
                {
                    "type": "assistant",
                    "timestamp": f"2025-01-01T{i:02d}:00:05.000Z",
                    "message": {
                        "role": "assistant",
                        "content": [{"type": "text", "text": "Response " + "y" * 5000}],
                    },
                }
            )

        session_file = tmp_path / "large_session.json"
        session_file.write_text(json.dumps({"loglines": loglines}), encoding="utf-8")

        output_dir = tmp_path / "output"
        output_dir.mkdir()

        generate_html(session_file, output_dir)

        # Should generate individual page-data-NNN.json files for large sessions
        page_data_files = list(output_dir.glob("page-data-*.json"))
        assert len(page_data_files) > 0, "page-data-NNN.json files should be generated"

        # Verify first page data file
        page_data_001 = output_dir / "page-data-001.json"
        assert page_data_001.exists(), "page-data-001.json should exist"

        # The file contains the HTML string directly (not a dict)
        page_data = json.loads(page_data_001.read_text(encoding="utf-8"))
        assert "<div" in page_data, "Page 1 should contain HTML"

    def test_no_page_data_files_for_small_sessions(self, output_dir):
        """Test that page-data-NNN.json files are NOT generated for small sessions."""
        fixture_path = Path(__file__).parent / "sample_session.json"
        generate_html(fixture_path, output_dir)

        # Small sessions should not have any page-data files
        page_data_files = list(output_dir.glob("page-data-*.json"))
        assert (
            len(page_data_files) == 0
        ), "page-data files should not be generated for small sessions"

    def test_page_data_files_collected_for_gist(self, tmp_path, monkeypatch):
        """Test that page-data-*.json files are collected for gist upload."""
        import subprocess

        # Create a large session to generate page-data files
        loglines = []
        for i in range(50):
            loglines.append(
                {
                    "type": "user",
                    "timestamp": f"2025-01-01T{i:02d}:00:00.000Z",
                    "message": {"role": "user", "content": f"Task {i}: " + "x" * 5000},
                }
            )
            loglines.append(
                {
                    "type": "assistant",
                    "timestamp": f"2025-01-01T{i:02d}:00:05.000Z",
                    "message": {
                        "role": "assistant",
                        "content": [{"type": "text", "text": "Response " + "y" * 5000}],
                    },
                }
            )

        session_file = tmp_path / "large_session.json"
        session_file.write_text(json.dumps({"loglines": loglines}), encoding="utf-8")

        output_dir = tmp_path / "output"
        output_dir.mkdir()

        generate_html(session_file, output_dir)

        # Make page-data files large enough to trigger two-gist
        for f in output_dir.glob("page-data-*.json"):
            f.write_text(json.dumps("x" * (200 * 1024)), encoding="utf-8")

        subprocess_calls = []
        gist_counter = [0]

        def mock_run(cmd, *args, **kwargs):
            subprocess_calls.append(cmd)
            gist_counter[0] += 1
            gist_id = f"gist{gist_counter[0]:03d}"
            return subprocess.CompletedProcess(
                args=cmd,
                returncode=0,
                stdout=f"https://gist.github.com/testuser/{gist_id}\n",
                stderr="",
            )

        monkeypatch.setattr(subprocess, "run", mock_run)

        create_gist(output_dir)

        # First call should include page-data files in the data gist
        first_cmd = " ".join(str(x) for x in subprocess_calls[0])
        assert "page-data-" in first_cmd, "page-data files should be in data gist"

    def test_page_html_has_page_num_for_large_sessions(self, tmp_path):
        """Test that page HTML has page number when page-data.json is generated."""
        # Create a large session
        loglines = []
        for i in range(50):
            loglines.append(
                {
                    "type": "user",
                    "timestamp": f"2025-01-01T{i:02d}:00:00.000Z",
                    "message": {"role": "user", "content": f"Task {i}: " + "x" * 5000},
                }
            )
            loglines.append(
                {
                    "type": "assistant",
                    "timestamp": f"2025-01-01T{i:02d}:00:05.000Z",
                    "message": {
                        "role": "assistant",
                        "content": [{"type": "text", "text": "Response " + "y" * 5000}],
                    },
                }
            )

        session_file = tmp_path / "large_session.json"
        session_file.write_text(json.dumps({"loglines": loglines}), encoding="utf-8")

        output_dir = tmp_path / "output"
        output_dir.mkdir()

        generate_html(session_file, output_dir)

        page_html = (output_dir / "page-001.html").read_text(encoding="utf-8")

        # Should have page number embedded for fetching
        assert "window.PAGE_NUM" in page_html

    def test_generates_index_data_json_for_large_sessions(self, tmp_path):
        """Test that index-data.json is generated for large sessions."""
        # Create a large session
        loglines = []
        for i in range(50):
            loglines.append(
                {
                    "type": "user",
                    "timestamp": f"2025-01-01T{i:02d}:00:00.000Z",
                    "message": {"role": "user", "content": f"Task {i}: " + "x" * 5000},
                }
            )
            loglines.append(
                {
                    "type": "assistant",
                    "timestamp": f"2025-01-01T{i:02d}:00:05.000Z",
                    "message": {
                        "role": "assistant",
                        "content": [{"type": "text", "text": "Response " + "y" * 5000}],
                    },
                }
            )

        session_file = tmp_path / "large_session.json"
        session_file.write_text(json.dumps({"loglines": loglines}), encoding="utf-8")

        output_dir = tmp_path / "output"
        output_dir.mkdir()

        generate_html(session_file, output_dir)

        # Should generate index-data.json for large sessions
        index_data_file = output_dir / "index-data.json"
        assert index_data_file.exists(), "index-data.json should exist"

        # The file contains the HTML string directly
        index_data = json.loads(index_data_file.read_text(encoding="utf-8"))
        assert "<div" in index_data, "index-data.json should contain HTML"

        # index.html should still include content for local viewing
        # (loader JS is only injected during gist upload by inject_gist_preview_js)
        index_html = (output_dir / "index.html").read_text(encoding="utf-8")
        assert (
            'id="index-items"' in index_html
        ), "index.html should have index-items container"

    def test_no_index_data_json_for_small_sessions(self, output_dir):
        """Test that index-data.json is NOT generated for small sessions."""
        fixture_path = Path(__file__).parent / "sample_session.json"
        generate_html(fixture_path, output_dir)

        # Small sessions should not have index-data.json
        index_data_file = output_dir / "index-data.json"
        assert (
            not index_data_file.exists()
        ), "index-data.json should not exist for small sessions"

    def test_large_session_html_includes_content_for_local_viewing(self, tmp_path):
        """Test that large session HTML includes content when not uploading to gist.

        Even when page-data-XXX.json files are generated, the HTML pages should
        contain the actual content for local viewing (file:// or local http server).
        The JSON files are only needed when uploading to gist where file size matters.
        """
        # Create a large session that triggers page-data JSON generation
        loglines = []
        for i in range(50):
            loglines.append(
                {
                    "type": "user",
                    "timestamp": f"2025-01-01T{i:02d}:00:00.000Z",
                    "message": {"role": "user", "content": f"Task {i}: " + "x" * 5000},
                }
            )
            loglines.append(
                {
                    "type": "assistant",
                    "timestamp": f"2025-01-01T{i:02d}:00:05.000Z",
                    "message": {
                        "role": "assistant",
                        "content": [{"type": "text", "text": "Response " + "y" * 5000}],
                    },
                }
            )

        session_file = tmp_path / "large_session.json"
        session_file.write_text(json.dumps({"loglines": loglines}), encoding="utf-8")

        output_dir = tmp_path / "output"
        output_dir.mkdir()

        generate_html(session_file, output_dir)

        # Verify page-data JSON files were generated (confirms session is "large")
        page_data_files = list(output_dir.glob("page-data-*.json"))
        assert (
            len(page_data_files) > 0
        ), "page-data files should be generated for large sessions"

        # But the HTML pages should STILL include content for local viewing
        page_html = (output_dir / "page-001.html").read_text(encoding="utf-8")
        # The page should have actual message content, not just empty containers
        assert (
            "Task 0:" in page_html or "xxxxx" in page_html
        ), "page-001.html should include message content for local viewing"

        # Index should also include content
        index_html = (output_dir / "index.html").read_text(encoding="utf-8")
        # The index should have items, not just empty #index-items div
        assert (
            'class="index-item"' in index_html or "Task " in index_html
        ), "index.html should include index items for local viewing"
