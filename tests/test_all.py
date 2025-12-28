"""Tests for batch conversion functionality."""

import tempfile
from pathlib import Path

import pytest
from click.testing import CliRunner

from claude_code_transcripts import (
    cli,
    find_all_sessions,
    find_existing_sessions,
    merge_sessions,
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


@pytest.fixture
def mock_existing_archive():
    """Create a mock existing archive with project index HTML files."""
    with tempfile.TemporaryDirectory() as tmpdir:
        archive_dir = Path(tmpdir)

        # Create project-a with 2 sessions
        project_a = archive_dir / "project-a"
        project_a.mkdir(parents=True)

        # Create project index HTML
        project_a_index = """<!DOCTYPE html>
<html>
<head><title>project-a - Claude Code Archive</title></head>
<body>
<div class="index-item">
    <a href="abc123/index.html">
        <div class="index-item-header">
            <span class="index-item-number">2025-01-01</span>
            <span style="color: var(--text-muted);">15 KB</span>
        </div>
        <div class="index-item-content">
            <p style="margin: 0;">Hello from project A</p>
        </div>
    </a>
</div>
<div class="index-item">
    <a href="def456/index.html">
        <div class="index-item-header">
            <span class="index-item-number">2025-01-02</span>
            <span style="color: var(--text-muted);">20 KB</span>
        </div>
        <div class="index-item-content">
            <p style="margin: 0;">Second session in project A</p>
        </div>
    </a>
</div>
</body>
</html>"""
        (project_a / "index.html").write_text(project_a_index)

        # Create session directories with index.html
        (project_a / "abc123").mkdir()
        (project_a / "abc123" / "index.html").write_text("<html>Session abc123</html>")
        (project_a / "def456").mkdir()
        (project_a / "def456" / "index.html").write_text("<html>Session def456</html>")

        # Create project-b with 1 session
        project_b = archive_dir / "project-b"
        project_b.mkdir(parents=True)

        project_b_index = """<!DOCTYPE html>
<html>
<head><title>project-b - Claude Code Archive</title></head>
<body>
<div class="index-item">
    <a href="ghi789/index.html">
        <div class="index-item-header">
            <span class="index-item-number">2025-01-03</span>
            <span style="color: var(--text-muted);">10 KB</span>
        </div>
        <div class="index-item-content">
            <p style="margin: 0;">Hello from project B</p>
        </div>
    </a>
</div>
</body>
</html>"""
        (project_b / "index.html").write_text(project_b_index)

        (project_b / "ghi789").mkdir()
        (project_b / "ghi789" / "index.html").write_text("<html>Session ghi789</html>")

        # Create master index (not parsed, but should exist)
        (archive_dir / "index.html").write_text("<html>Master index</html>")

        yield archive_dir


class TestFindExistingSessions:
    """Tests for find_existing_sessions function."""

    def test_finds_existing_projects(self, mock_existing_archive):
        """Test that existing projects are discovered."""
        result = find_existing_sessions(mock_existing_archive)

        assert len(result) == 2
        project_names = [p["name"] for p in result]
        assert "project-a" in project_names
        assert "project-b" in project_names

    def test_finds_existing_sessions(self, mock_existing_archive):
        """Test that sessions within projects are discovered."""
        result = find_existing_sessions(mock_existing_archive)

        project_a = next(p for p in result if p["name"] == "project-a")
        assert len(project_a["sessions"]) == 2

        session_names = [s["path"].name for s in project_a["sessions"]]
        assert "abc123" in session_names
        assert "def456" in session_names

    def test_extracts_session_metadata(self, mock_existing_archive):
        """Test that session summary is extracted from HTML."""
        result = find_existing_sessions(mock_existing_archive)

        project_a = next(p for p in result if p["name"] == "project-a")
        session = next(s for s in project_a["sessions"] if s["path"].name == "abc123")

        assert session["summary"] == "Hello from project A"
        assert "mtime" in session
        assert "size" in session

    def test_returns_empty_for_nonexistent_dir(self):
        """Test handling of non-existent output directory."""
        result = find_existing_sessions(Path("/nonexistent/path"))
        assert result == []

    def test_returns_empty_for_empty_dir(self, output_dir):
        """Test handling of empty output directory."""
        result = find_existing_sessions(output_dir)
        assert result == []


class TestMergeSessions:
    """Tests for merge_sessions function."""

    def test_merge_includes_all_source_sessions(self):
        """Test that all source sessions are included in merged result."""
        source = [
            {
                "name": "project-a",
                "path": Path("/src/project-a"),
                "sessions": [
                    {
                        "path": Path("/src/abc.jsonl"),
                        "summary": "Session A",
                        "mtime": 100,
                        "size": 1000,
                    },
                ],
            }
        ]
        existing = []

        result = merge_sessions(source, existing)

        assert len(result) == 1
        assert result[0]["name"] == "project-a"
        assert len(result[0]["sessions"]) == 1

    def test_merge_preserves_orphaned_sessions(self):
        """Test that sessions only in archive are preserved in merged result."""
        source = [
            {
                "name": "project-a",
                "path": Path("/src/project-a"),
                "sessions": [
                    {
                        "path": Path("/src/abc.jsonl"),
                        "summary": "New session",
                        "mtime": 200,
                        "size": 1000,
                    },
                ],
            }
        ]
        existing = [
            {
                "name": "project-a",
                "path": None,
                "sessions": [
                    {
                        "path": Path("/archive/project-a/orphan"),
                        "summary": "Orphan session",
                        "mtime": 100,
                        "size": 500,
                    },
                ],
            }
        ]

        result = merge_sessions(source, existing)

        # Should have 1 project with 2 sessions
        assert len(result) == 1
        project_a = result[0]
        assert len(project_a["sessions"]) == 2

        # Both sessions should be present
        summaries = [s["summary"] for s in project_a["sessions"]]
        assert "New session" in summaries
        assert "Orphan session" in summaries

    def test_merge_combines_projects(self):
        """Test that projects from both sources are combined."""
        source = [
            {
                "name": "project-a",
                "path": Path("/src/project-a"),
                "sessions": [
                    {
                        "path": Path("/src/abc.jsonl"),
                        "summary": "Session A",
                        "mtime": 100,
                        "size": 1000,
                    },
                ],
            }
        ]
        existing = [
            {
                "name": "project-b",
                "path": None,
                "sessions": [
                    {
                        "path": Path("/archive/project-b/xyz"),
                        "summary": "Session B",
                        "mtime": 50,
                        "size": 500,
                    },
                ],
            }
        ]

        result = merge_sessions(source, existing)

        # Should have 2 projects
        assert len(result) == 2
        project_names = [p["name"] for p in result]
        assert "project-a" in project_names
        assert "project-b" in project_names

    def test_merge_source_overwrites_existing_with_same_name(self):
        """Test that source session replaces existing session with same name."""
        source = [
            {
                "name": "project-a",
                "path": Path("/src/project-a"),
                "sessions": [
                    {
                        "path": Path("/src/abc123.jsonl"),
                        "summary": "Updated session",
                        "mtime": 200,
                        "size": 2000,
                    },
                ],
            }
        ]
        existing = [
            {
                "name": "project-a",
                "path": None,
                "sessions": [
                    {
                        "path": Path("/archive/project-a/abc123"),
                        "summary": "Old session",
                        "mtime": 100,
                        "size": 1000,
                    },
                ],
            }
        ]

        result = merge_sessions(source, existing)

        # Should have 1 project with 1 session (source wins)
        assert len(result) == 1
        assert len(result[0]["sessions"]) == 1
        # The source session should be present (identified by stem)
        assert result[0]["sessions"][0]["summary"] == "Updated session"


class TestGenerateBatchHtmlMerge:
    """Tests for generate_batch_html with merge functionality."""

    def test_merge_mode_regenerates_source_sessions(
        self, mock_projects_dir, output_dir
    ):
        """Test that source sessions are regenerated in merge mode."""
        # First run: create initial archive
        generate_batch_html(mock_projects_dir, output_dir)
        assert (output_dir / "project-a" / "abc123" / "index.html").exists()

        # Get initial mtime
        initial_mtime = (
            (output_dir / "project-a" / "abc123" / "index.html").stat().st_mtime
        )

        # Wait a tiny bit to ensure mtime changes
        import time

        time.sleep(0.01)

        # Second run with merge: should regenerate
        generate_batch_html(mock_projects_dir, output_dir, merge=True)

        # File should still exist and be regenerated (mtime updated)
        assert (output_dir / "project-a" / "abc123" / "index.html").exists()
        new_mtime = (output_dir / "project-a" / "abc123" / "index.html").stat().st_mtime
        assert new_mtime >= initial_mtime

    def test_merge_mode_preserves_orphans_in_index(self, output_dir):
        """Test that orphan sessions are preserved in the index when merging."""
        # Create initial archive with a session
        with tempfile.TemporaryDirectory() as tmpdir:
            source1 = Path(tmpdir)
            project = source1 / "-home-user-projects-project-a"
            project.mkdir(parents=True)

            session = project / "original.jsonl"
            session.write_text(
                '{"type": "user", "timestamp": "2025-01-01T10:00:00.000Z", "message": {"role": "user", "content": "Original session"}}\n'
            )

            generate_batch_html(source1, output_dir)

        # Verify original session exists
        assert (output_dir / "project-a" / "original" / "index.html").exists()

        # Create a new source without the original session
        with tempfile.TemporaryDirectory() as tmpdir:
            source2 = Path(tmpdir)
            project = source2 / "-home-user-projects-project-a"
            project.mkdir(parents=True)

            session = project / "new.jsonl"
            session.write_text(
                '{"type": "user", "timestamp": "2025-01-02T10:00:00.000Z", "message": {"role": "user", "content": "New session"}}\n'
            )

            # Merge: should add new session AND preserve orphan in index
            generate_batch_html(source2, output_dir, merge=True)

        # Both sessions should exist
        assert (output_dir / "project-a" / "original" / "index.html").exists()
        assert (output_dir / "project-a" / "new" / "index.html").exists()

        # Project index should list both sessions
        project_index = (output_dir / "project-a" / "index.html").read_text()
        assert "original" in project_index
        assert "new" in project_index

    def test_prefix_in_session_index(self, mock_projects_dir, output_dir):
        """Test that prefix is shown in project index."""
        generate_batch_html(mock_projects_dir, output_dir, prefix="laptop")

        # Check project index contains prefix
        project_index = (output_dir / "project-a" / "index.html").read_text()
        assert "laptop" in project_index


class TestAllMergeCommand:
    """Tests for the all command with --merge and --prefix options."""

    def test_merge_option_exists(self):
        """Test that --merge option is recognized."""
        runner = CliRunner()
        result = runner.invoke(cli, ["all", "--help"])
        assert result.exit_code == 0
        assert "--merge" in result.output or "-m" in result.output

    def test_prefix_option_exists(self):
        """Test that --prefix option is recognized."""
        runner = CliRunner()
        result = runner.invoke(cli, ["all", "--help"])
        assert result.exit_code == 0
        assert "--prefix" in result.output

    def test_merge_preserves_orphan_sessions(self, output_dir):
        """Test that --merge preserves orphan sessions in the index."""
        runner = CliRunner()

        # Create initial archive with a session
        with tempfile.TemporaryDirectory() as tmpdir:
            source1 = Path(tmpdir)
            project = source1 / "-home-user-projects-project-a"
            project.mkdir(parents=True)

            session = project / "original.jsonl"
            session.write_text(
                '{"type": "user", "timestamp": "2025-01-01T10:00:00.000Z", "message": {"role": "user", "content": "Original session"}}\n'
            )

            result = runner.invoke(
                cli,
                ["all", "--source", str(source1), "--output", str(output_dir)],
            )
            assert result.exit_code == 0

        # Verify original session exists
        assert (output_dir / "project-a" / "original" / "index.html").exists()

        # Create a new source without the original session
        with tempfile.TemporaryDirectory() as tmpdir:
            source2 = Path(tmpdir)
            project = source2 / "-home-user-projects-project-a"
            project.mkdir(parents=True)

            session = project / "new.jsonl"
            session.write_text(
                '{"type": "user", "timestamp": "2025-01-02T10:00:00.000Z", "message": {"role": "user", "content": "New session"}}\n'
            )

            # Merge: should add new session AND preserve orphan in index
            result = runner.invoke(
                cli,
                [
                    "all",
                    "--source",
                    str(source2),
                    "--output",
                    str(output_dir),
                    "--merge",
                ],
            )
            assert result.exit_code == 0

        # Both sessions should exist
        assert (output_dir / "project-a" / "original" / "index.html").exists()
        assert (output_dir / "project-a" / "new" / "index.html").exists()

        # Project index should list both sessions
        project_index = (output_dir / "project-a" / "index.html").read_text()
        assert "original" in project_index
        assert "new" in project_index

    def test_prefix_in_cli(self, mock_projects_dir, output_dir):
        """Test that --prefix adds prefix to sessions in index."""
        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "all",
                "--source",
                str(mock_projects_dir),
                "--output",
                str(output_dir),
                "--prefix",
                "workstation",
            ],
        )
        assert result.exit_code == 0

        # Check project index contains prefix
        project_index = (output_dir / "project-a" / "index.html").read_text()
        assert "workstation" in project_index
