"""Tests for batch conversion functionality."""

import tempfile
from pathlib import Path

import pytest
from click.testing import CliRunner

from claude_code_transcripts import (
    cli,
    find_all_sessions,
    get_project_display_name,
    generate_batch_html,
)


@pytest.fixture
def mock_projects_dir():
    """Create a mock ~/.claude/projects structure with test sessions."""
    with tempfile.TemporaryDirectory() as tmpdir:
        projects_dir = Path(tmpdir)

        # Create project-a with 2 sessions
        project_a = projects_dir / "-home-user-projects-project-a"
        project_a.mkdir(parents=True)

        session_a1 = project_a / "abc123.jsonl"
        session_a1.write_text(
            '{"type": "user", "timestamp": "2025-01-01T10:00:00.000Z", "message": {"role": "user", "content": "Hello from project A"}}\n'
            '{"type": "assistant", "timestamp": "2025-01-01T10:00:05.000Z", "message": {"role": "assistant", "content": [{"type": "text", "text": "Hi there!"}]}}\n'
        )

        session_a2 = project_a / "def456.jsonl"
        session_a2.write_text(
            '{"type": "user", "timestamp": "2025-01-02T10:00:00.000Z", "message": {"role": "user", "content": "Second session in project A"}}\n'
            '{"type": "assistant", "timestamp": "2025-01-02T10:00:05.000Z", "message": {"role": "assistant", "content": [{"type": "text", "text": "Got it!"}]}}\n'
        )

        # Create an agent file (should be skipped by default)
        agent_a = project_a / "agent-xyz789.jsonl"
        agent_a.write_text(
            '{"type": "user", "timestamp": "2025-01-03T10:00:00.000Z", "message": {"role": "user", "content": "Agent session"}}\n'
        )

        # Create project-b with 1 session
        project_b = projects_dir / "-home-user-projects-project-b"
        project_b.mkdir(parents=True)

        session_b1 = project_b / "ghi789.jsonl"
        session_b1.write_text(
            '{"type": "user", "timestamp": "2025-01-04T10:00:00.000Z", "message": {"role": "user", "content": "Hello from project B"}}\n'
            '{"type": "assistant", "timestamp": "2025-01-04T10:00:05.000Z", "message": {"role": "assistant", "content": [{"type": "text", "text": "Welcome!"}]}}\n'
        )

        # Create empty/warmup session (should be skipped)
        warmup = project_b / "warmup123.jsonl"
        warmup.write_text(
            '{"type": "user", "timestamp": "2025-01-05T10:00:00.000Z", "message": {"role": "user", "content": "warmup"}}\n'
        )

        yield projects_dir


@pytest.fixture
def output_dir():
    """Create a temporary output directory."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


class TestGetProjectDisplayName:
    """Tests for get_project_display_name function."""

    def test_extracts_project_name_from_path(self):
        """Test extracting readable project name from encoded path."""
        assert get_project_display_name("-home-user-projects-myproject") == "myproject"

    def test_handles_nested_paths(self):
        """Test handling nested project paths."""
        assert get_project_display_name("-home-user-code-apps-webapp") == "apps-webapp"

    def test_handles_windows_style_paths(self):
        """Test handling Windows-style encoded paths."""
        assert get_project_display_name("-mnt-c-Users-name-Projects-app") == "app"

    def test_handles_simple_name(self):
        """Test handling already simple names."""
        assert get_project_display_name("simple-project") == "simple-project"


class TestFindAllSessions:
    """Tests for find_all_sessions function."""

    def test_finds_sessions_grouped_by_project(self, mock_projects_dir):
        """Test that sessions are found and grouped by project."""
        result = find_all_sessions(mock_projects_dir)

        # Should have 2 projects
        assert len(result) == 2

        # Check project names are extracted
        project_names = [p["name"] for p in result]
        assert "project-a" in project_names
        assert "project-b" in project_names

    def test_excludes_agent_files_by_default(self, mock_projects_dir):
        """Test that agent-* files are excluded by default."""
        result = find_all_sessions(mock_projects_dir)

        # Find project-a
        project_a = next(p for p in result if p["name"] == "project-a")

        # Should have 2 sessions (not 3, agent excluded)
        assert len(project_a["sessions"]) == 2

        # No session should be an agent file
        for session in project_a["sessions"]:
            assert not session["path"].name.startswith("agent-")

    def test_includes_agent_files_when_requested(self, mock_projects_dir):
        """Test that agent-* files can be included."""
        result = find_all_sessions(mock_projects_dir, include_agents=True)

        # Find project-a
        project_a = next(p for p in result if p["name"] == "project-a")

        # Should have 3 sessions (including agent)
        assert len(project_a["sessions"]) == 3

    def test_excludes_warmup_sessions(self, mock_projects_dir):
        """Test that warmup sessions are excluded."""
        result = find_all_sessions(mock_projects_dir)

        # Find project-b
        project_b = next(p for p in result if p["name"] == "project-b")

        # Should have 1 session (warmup excluded)
        assert len(project_b["sessions"]) == 1

    def test_sessions_sorted_by_date(self, mock_projects_dir):
        """Test that sessions within a project are sorted by modification time."""
        result = find_all_sessions(mock_projects_dir)

        for project in result:
            sessions = project["sessions"]
            if len(sessions) > 1:
                # Check descending order (most recent first)
                for i in range(len(sessions) - 1):
                    assert sessions[i]["mtime"] >= sessions[i + 1]["mtime"]

    def test_returns_empty_for_nonexistent_folder(self):
        """Test handling of non-existent folder."""
        result = find_all_sessions(Path("/nonexistent/path"))
        assert result == []

    def test_session_includes_summary(self, mock_projects_dir):
        """Test that sessions include summary text."""
        result = find_all_sessions(mock_projects_dir)

        project_a = next(p for p in result if p["name"] == "project-a")

        for session in project_a["sessions"]:
            assert "summary" in session
            assert session["summary"] != "(no summary)"


class TestGenerateBatchHtml:
    """Tests for generate_batch_html function."""

    def test_creates_output_directory(self, mock_projects_dir, output_dir):
        """Test that output directory is created."""
        generate_batch_html(mock_projects_dir, output_dir)
        assert output_dir.exists()

    def test_creates_master_index(self, mock_projects_dir, output_dir):
        """Test that master index.html is created."""
        generate_batch_html(mock_projects_dir, output_dir)
        assert (output_dir / "index.html").exists()

    def test_creates_project_directories(self, mock_projects_dir, output_dir):
        """Test that project directories are created."""
        generate_batch_html(mock_projects_dir, output_dir)

        assert (output_dir / "project-a").exists()
        assert (output_dir / "project-b").exists()

    def test_creates_project_indexes(self, mock_projects_dir, output_dir):
        """Test that project index.html files are created."""
        generate_batch_html(mock_projects_dir, output_dir)

        assert (output_dir / "project-a" / "index.html").exists()
        assert (output_dir / "project-b" / "index.html").exists()

    def test_creates_session_directories(self, mock_projects_dir, output_dir):
        """Test that session directories are created with transcripts."""
        generate_batch_html(mock_projects_dir, output_dir)

        # Check project-a has session directories
        project_a_dir = output_dir / "project-a"
        session_dirs = [d for d in project_a_dir.iterdir() if d.is_dir()]
        assert len(session_dirs) == 2

        # Each session directory should have an index.html
        for session_dir in session_dirs:
            assert (session_dir / "index.html").exists()

    def test_master_index_lists_all_projects(self, mock_projects_dir, output_dir):
        """Test that master index lists all projects."""
        generate_batch_html(mock_projects_dir, output_dir)

        index_html = (output_dir / "index.html").read_text()
        assert "project-a" in index_html
        assert "project-b" in index_html

    def test_master_index_shows_session_counts(self, mock_projects_dir, output_dir):
        """Test that master index shows session counts per project."""
        generate_batch_html(mock_projects_dir, output_dir)

        index_html = (output_dir / "index.html").read_text()
        # project-a has 2 sessions, project-b has 1
        assert "2 sessions" in index_html or "2 session" in index_html
        assert "1 session" in index_html

    def test_project_index_lists_sessions(self, mock_projects_dir, output_dir):
        """Test that project index lists all sessions."""
        generate_batch_html(mock_projects_dir, output_dir)

        project_a_index = (output_dir / "project-a" / "index.html").read_text()
        # Should contain links to session directories
        assert "abc123" in project_a_index
        assert "def456" in project_a_index

    def test_returns_statistics(self, mock_projects_dir, output_dir):
        """Test that batch generation returns statistics."""
        stats = generate_batch_html(mock_projects_dir, output_dir)

        assert stats["total_projects"] == 2
        assert stats["total_sessions"] == 3  # 2 + 1
        assert stats["failed_sessions"] == []
        assert "output_dir" in stats

    def test_progress_callback_called(self, mock_projects_dir, output_dir):
        """Test that progress callback is called for each session."""
        progress_calls = []

        def on_progress(project_name, session_name, current, total):
            progress_calls.append((project_name, session_name, current, total))

        generate_batch_html(
            mock_projects_dir, output_dir, progress_callback=on_progress
        )

        # Should be called for each session (3 total)
        assert len(progress_calls) == 3
        # Last call should have current == total
        assert progress_calls[-1][2] == progress_calls[-1][3]

    def test_handles_failed_session_gracefully(self, output_dir):
        """Test that failed session conversion doesn't crash the batch."""
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as tmpdir:
            projects_dir = Path(tmpdir)

            # Create a project with 2 sessions
            project = projects_dir / "-home-user-projects-test"
            project.mkdir(parents=True)

            # Session 1
            session1 = project / "session1.jsonl"
            session1.write_text(
                '{"type": "user", "timestamp": "2025-01-01T10:00:00.000Z", "message": {"role": "user", "content": "Hello from session 1"}}\n'
            )

            # Session 2
            session2 = project / "session2.jsonl"
            session2.write_text(
                '{"type": "user", "timestamp": "2025-01-02T10:00:00.000Z", "message": {"role": "user", "content": "Hello from session 2"}}\n'
            )

            # Patch generate_html to fail on one specific session
            original_generate_html = __import__("claude_code_transcripts").generate_html

            def mock_generate_html(json_path, output_dir, github_repo=None):
                if "session1" in str(json_path):
                    raise RuntimeError("Simulated failure")
                return original_generate_html(json_path, output_dir, github_repo)

            with patch(
                "claude_code_transcripts.generate_html", side_effect=mock_generate_html
            ):
                stats = generate_batch_html(projects_dir, output_dir)

            # Should have processed session2 successfully
            assert stats["total_sessions"] == 1
            # Should have recorded session1 as failed
            assert len(stats["failed_sessions"]) == 1
            assert "session1" in stats["failed_sessions"][0]["session"]
            assert "Simulated failure" in stats["failed_sessions"][0]["error"]


class TestAllCommand:
    """Tests for the all CLI command."""

    def test_all_command_exists(self):
        """Test that all command is registered."""
        runner = CliRunner()
        result = runner.invoke(cli, ["all", "--help"])
        assert result.exit_code == 0
        assert "all" in result.output.lower() or "convert" in result.output.lower()

    def test_all_dry_run(self, mock_projects_dir, output_dir):
        """Test dry-run mode shows what would be converted."""
        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "all",
                "--source",
                str(mock_projects_dir),
                "--output",
                str(output_dir),
                "--dry-run",
            ],
        )

        assert result.exit_code == 0
        assert "project-a" in result.output
        assert "project-b" in result.output
        # Dry run should not create files
        assert not (output_dir / "index.html").exists()

    def test_all_creates_archive(self, mock_projects_dir, output_dir):
        """Test all command creates full archive."""
        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "all",
                "--source",
                str(mock_projects_dir),
                "--output",
                str(output_dir),
            ],
        )

        assert result.exit_code == 0
        assert (output_dir / "index.html").exists()

    def test_all_include_agents_flag(self, mock_projects_dir, output_dir):
        """Test --include-agents flag includes agent sessions."""
        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "all",
                "--source",
                str(mock_projects_dir),
                "--output",
                str(output_dir),
                "--include-agents",
            ],
        )

        assert result.exit_code == 0
        # Should have agent directory in project-a
        project_a_dir = output_dir / "project-a"
        session_dirs = [d for d in project_a_dir.iterdir() if d.is_dir()]
        assert len(session_dirs) == 3  # 2 regular + 1 agent

    def test_all_quiet_flag(self, mock_projects_dir, output_dir):
        """Test --quiet flag suppresses non-error output."""
        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "all",
                "--source",
                str(mock_projects_dir),
                "--output",
                str(output_dir),
                "--quiet",
            ],
        )

        assert result.exit_code == 0
        # Should create the archive
        assert (output_dir / "index.html").exists()
        # Output should be minimal (no progress messages)
        assert "Scanning" not in result.output
        assert "Processed" not in result.output
        assert "Generating" not in result.output

    def test_all_quiet_with_dry_run(self, mock_projects_dir, output_dir):
        """Test --quiet flag works with --dry-run."""
        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "all",
                "--source",
                str(mock_projects_dir),
                "--output",
                str(output_dir),
                "--dry-run",
                "--quiet",
            ],
        )

        assert result.exit_code == 0
        # Dry run with quiet should produce no output
        assert "Dry run" not in result.output
        assert "project-a" not in result.output
        # Should not create any files
        assert not (output_dir / "index.html").exists()


class TestAllCommandNewUi:
    """Tests for the --new-ui flag on the all command."""

    def test_all_command_has_new_ui_flag(self):
        """Test that all command accepts --new-ui flag."""
        runner = CliRunner()
        result = runner.invoke(cli, ["all", "--help"])
        assert result.exit_code == 0
        assert "--new-ui" in result.output

    def test_all_new_ui_generates_unified_html(self, mock_projects_dir, output_dir):
        """Test that --new-ui generates unified.html for each session."""
        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "all",
                "--source",
                str(mock_projects_dir),
                "--output",
                str(output_dir),
                "--new-ui",
            ],
        )

        assert result.exit_code == 0
        # Check that master index exists
        assert (output_dir / "index.html").exists()
        # Check that sessions have unified.html instead of index.html
        project_a_dir = output_dir / "project-a"
        session_dirs = [d for d in project_a_dir.iterdir() if d.is_dir()]
        assert len(session_dirs) > 0
        # At least one session should have unified.html
        for session_dir in session_dirs:
            assert (session_dir / "unified.html").exists()

    def test_generate_batch_html_new_ui_parameter(self, mock_projects_dir, output_dir):
        """Test that generate_batch_html accepts and uses new_ui parameter."""
        stats = generate_batch_html(mock_projects_dir, output_dir, new_ui=True)

        # Should succeed
        assert stats["total_sessions"] >= 1

        # Sessions should have unified.html
        for project_dir in output_dir.iterdir():
            if project_dir.is_dir() and project_dir.name != "index.html":
                for session_dir in project_dir.iterdir():
                    if session_dir.is_dir():
                        assert (session_dir / "unified.html").exists()


class TestJsonCommandWithUrl:
    """Tests for the json command with URL support."""

    def test_json_command_accepts_url(self, output_dir):
        """Test that json command can accept a URL starting with http:// or https://."""
        from unittest.mock import patch, MagicMock

        # Sample JSONL content
        jsonl_content = (
            '{"type": "user", "timestamp": "2025-01-01T10:00:00.000Z", "message": {"role": "user", "content": "Hello from URL"}}\n'
            '{"type": "assistant", "timestamp": "2025-01-01T10:00:05.000Z", "message": {"role": "assistant", "content": [{"type": "text", "text": "Hi there!"}]}}\n'
        )

        # Mock the httpx.get response
        mock_response = MagicMock()
        mock_response.text = jsonl_content
        mock_response.raise_for_status = MagicMock()

        runner = CliRunner()
        with patch(
            "claude_code_transcripts.httpx.get", return_value=mock_response
        ) as mock_get:
            result = runner.invoke(
                cli,
                [
                    "json",
                    "https://example.com/session.jsonl",
                    "-o",
                    str(output_dir),
                ],
            )

        # Check that the URL was fetched
        mock_get.assert_called_once()
        call_url = mock_get.call_args[0][0]
        assert call_url == "https://example.com/session.jsonl"

        # Check that HTML was generated
        assert result.exit_code == 0
        assert (output_dir / "index.html").exists()

    def test_json_command_accepts_http_url(self, output_dir):
        """Test that json command can accept http:// URLs."""
        from unittest.mock import patch, MagicMock

        jsonl_content = '{"type": "user", "timestamp": "2025-01-01T10:00:00.000Z", "message": {"role": "user", "content": "Hello"}}\n'

        mock_response = MagicMock()
        mock_response.text = jsonl_content
        mock_response.raise_for_status = MagicMock()

        runner = CliRunner()
        with patch(
            "claude_code_transcripts.httpx.get", return_value=mock_response
        ) as mock_get:
            result = runner.invoke(
                cli,
                [
                    "json",
                    "http://example.com/session.jsonl",
                    "-o",
                    str(output_dir),
                ],
            )

        mock_get.assert_called_once()
        assert result.exit_code == 0

    def test_json_command_url_fetch_error(self, output_dir):
        """Test that json command handles URL fetch errors gracefully."""
        from unittest.mock import patch
        import httpx

        runner = CliRunner()
        with patch(
            "claude_code_transcripts.httpx.get",
            side_effect=httpx.RequestError("Network error"),
        ):
            result = runner.invoke(
                cli,
                [
                    "json",
                    "https://example.com/session.jsonl",
                    "-o",
                    str(output_dir),
                ],
            )

        assert result.exit_code != 0
        assert "error" in result.output.lower() or "Error" in result.output

    def test_json_command_still_works_with_local_file(self, output_dir):
        """Test that json command still works with local file paths."""
        # Create a temp JSONL file
        jsonl_file = output_dir / "test.jsonl"
        jsonl_file.write_text(
            '{"type": "user", "timestamp": "2025-01-01T10:00:00.000Z", "message": {"role": "user", "content": "Hello local"}}\n'
            '{"type": "assistant", "timestamp": "2025-01-01T10:00:05.000Z", "message": {"role": "assistant", "content": [{"type": "text", "text": "Hi!"}]}}\n'
        )

        html_output = output_dir / "html_output"

        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "json",
                str(jsonl_file),
                "-o",
                str(html_output),
            ],
        )

        assert result.exit_code == 0
        assert (html_output / "index.html").exists()
