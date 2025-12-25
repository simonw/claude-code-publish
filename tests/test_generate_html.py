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
    inject_gist_preview_js,
    create_gist,
    GIST_PREVIEW_JS,
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


class TestListWebCommand:
    """Tests for the list-web command."""

    def test_list_web_displays_sessions(self, httpx_mock):
        """Test that list-web displays sessions from the API."""
        from click.testing import CliRunner
        from claude_code_publish import cli

        # Mock the API response with realistic data
        mock_response = {
            "data": [
                {
                    "id": "session_01ABC123",
                    "title": "Build a CLI tool",
                    "created_at": "2025-12-24T10:30:00Z",
                    "updated_at": "2025-12-24T11:00:00Z",
                    "type": "web",
                    "session_status": "completed",
                    "environment_id": "env_123",
                    "session_context": {},
                },
                {
                    "id": "session_02DEF456",
                    "title": "Fix authentication bug",
                    "created_at": "2025-12-23T14:00:00Z",
                    "updated_at": "2025-12-23T15:30:00Z",
                    "type": "web",
                    "session_status": "completed",
                    "environment_id": "env_123",
                    "session_context": {},
                },
            ],
            "has_more": False,
            "first_id": "session_01ABC123",
            "last_id": "session_02DEF456",
        }

        httpx_mock.add_response(
            url="https://api.anthropic.com/v1/sessions",
            json=mock_response,
        )

        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["list-web", "--token", "test-token", "--org-uuid", "test-org-uuid"],
        )

        assert result.exit_code == 0
        assert "session_01ABC123" in result.output
        assert "session_02DEF456" in result.output
        assert "Build a CLI tool" in result.output
        assert "Fix authentication bug" in result.output
        assert "2025-12-24T10:30:00" in result.output

    def test_list_web_no_sessions(self, httpx_mock):
        """Test list-web when no sessions are found."""
        from click.testing import CliRunner
        from claude_code_publish import cli

        httpx_mock.add_response(
            url="https://api.anthropic.com/v1/sessions",
            json={"data": [], "has_more": False},
        )

        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["list-web", "--token", "test-token", "--org-uuid", "test-org-uuid"],
        )

        assert result.exit_code == 0
        assert "No sessions found" in result.output

    def test_list_web_requires_token_on_non_macos(self, monkeypatch):
        """Test that list-web requires --token on non-macOS platforms."""
        from click.testing import CliRunner
        from claude_code_publish import cli

        # Pretend we're on Linux
        monkeypatch.setattr("claude_code_publish.platform.system", lambda: "Linux")

        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["list-web", "--org-uuid", "test-org-uuid"],
        )

        assert result.exit_code != 0
        assert "must provide --token" in result.output


class TestInjectGistPreviewJs:
    """Tests for the inject_gist_preview_js function."""

    def test_injects_js_into_html_files(self, output_dir):
        """Test that JS is injected before </body> tag."""
        # Create test HTML files
        (output_dir / "index.html").write_text(
            "<html><body><h1>Test</h1></body></html>"
        )
        (output_dir / "page-001.html").write_text(
            "<html><body><p>Page 1</p></body></html>"
        )

        inject_gist_preview_js(output_dir)

        index_content = (output_dir / "index.html").read_text()
        page_content = (output_dir / "page-001.html").read_text()

        # Check JS was injected
        assert GIST_PREVIEW_JS in index_content
        assert GIST_PREVIEW_JS in page_content

        # Check JS is before </body>
        assert index_content.endswith("</body></html>")
        assert "<script>" in index_content

    def test_skips_files_without_body(self, output_dir):
        """Test that files without </body> are not modified."""
        original_content = "<html><head><title>Test</title></head></html>"
        (output_dir / "fragment.html").write_text(original_content)

        inject_gist_preview_js(output_dir)

        assert (output_dir / "fragment.html").read_text() == original_content

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
        (output_dir / "index.html").write_text("<html><body>Index</body></html>")
        (output_dir / "page-001.html").write_text("<html><body>Page</body></html>")

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
        (output_dir / "index.html").write_text("<html><body>Test</body></html>")

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
        (output_dir / "index.html").write_text("<html><body>Test</body></html>")

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
        from claude_code_publish import cli
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
            "claude_code_publish.tempfile.gettempdir", lambda: str(tmp_path)
        )

        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["session", str(fixture_path), "--gist"],
        )

        assert result.exit_code == 0
        assert "Creating GitHub gist" in result.output
        assert "gist.github.com" in result.output
        assert "gistpreview.github.io" in result.output

    def test_session_gist_with_output_dir(self, monkeypatch, output_dir):
        """Test that session --gist with -o uses specified directory."""
        from click.testing import CliRunner
        from claude_code_publish import cli
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
            ["session", str(fixture_path), "-o", str(output_dir), "--gist"],
        )

        assert result.exit_code == 0
        assert (output_dir / "index.html").exists()
        # Verify JS was injected
        index_content = (output_dir / "index.html").read_text()
        assert "gistpreview.github.io" in index_content


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
        session_file.write_text(json.dumps(session_data))

        # Generate HTML
        generate_html(session_file, output_dir)

        # Read the index.html
        index_html = (output_dir / "index.html").read_text()

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
        from claude_code_publish import cli

        fixture_path = Path(__file__).parent / "sample_session.json"

        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["session", str(fixture_path), "-o", str(output_dir), "--json"],
        )

        assert result.exit_code == 0
        json_file = output_dir / "sample_session.json"
        assert json_file.exists()
        assert "JSON:" in result.output
        assert "KB" in result.output

    def test_session_json_preserves_original_name(self, output_dir):
        """Test that --json preserves the original filename."""
        from click.testing import CliRunner
        from claude_code_publish import cli

        fixture_path = Path(__file__).parent / "sample_session.json"

        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["session", str(fixture_path), "-o", str(output_dir), "--json"],
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
        from claude_code_publish import cli

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
                "import",
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
        from claude_code_publish import cli
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
            "claude_code_publish.tempfile.gettempdir", lambda: str(tmp_path)
        )

        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "import",
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
        assert "gistpreview.github.io" in result.output
