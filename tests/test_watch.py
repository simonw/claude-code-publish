"""Tests for watch mode functionality."""

import tempfile
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from claude_code_transcripts import TranscriptWatcher, generate_batch_html


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

        # Create project-b with 1 session
        project_b = projects_dir / "-home-user-projects-project-b"
        project_b.mkdir(parents=True)

        session_b1 = project_b / "ghi789.jsonl"
        session_b1.write_text(
            '{"type": "user", "timestamp": "2025-01-04T10:00:00.000Z", "message": {"role": "user", "content": "Hello from project B"}}\n'
            '{"type": "assistant", "timestamp": "2025-01-04T10:00:05.000Z", "message": {"role": "assistant", "content": [{"type": "text", "text": "Welcome!"}]}}\n'
        )

        yield projects_dir


@pytest.fixture
def output_dir():
    """Create a temporary output directory."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


class TestTranscriptWatcher:
    """Tests for TranscriptWatcher class."""

    def test_tracks_changed_files(self, output_dir):
        """Test that TranscriptWatcher tracks which files have changed."""
        watcher = TranscriptWatcher(output_dir, debounce_seconds=0.1)

        # Simulate a file change event
        mock_event = MagicMock()
        mock_event.is_directory = False
        mock_event.src_path = "/home/user/.claude/projects/test/session1.jsonl"

        watcher.on_any_event(mock_event)

        # Should have tracked the changed file
        assert hasattr(watcher, "changed_files")
        assert len(watcher.changed_files) > 0
        assert Path(mock_event.src_path) in watcher.changed_files

    def test_tracks_multiple_changed_files(self, output_dir):
        """Test that multiple file changes are tracked."""
        watcher = TranscriptWatcher(output_dir, debounce_seconds=0.1)

        # Simulate multiple file change events
        paths = [
            "/home/user/.claude/projects/test/session1.jsonl",
            "/home/user/.claude/projects/test/session2.jsonl",
            "/home/user/.claude/projects/other/session3.jsonl",
        ]

        for path in paths:
            mock_event = MagicMock()
            mock_event.is_directory = False
            mock_event.src_path = path
            watcher.on_any_event(mock_event)

        assert len(watcher.changed_files) == 3

    def test_clears_changed_files_after_callback(self, output_dir):
        """Test that changed files are cleared after callback is invoked."""
        watcher = TranscriptWatcher(output_dir, debounce_seconds=0)
        callback_args = []

        def callback(changed_paths):
            callback_args.append(changed_paths.copy())

        watcher.generation_callback = callback

        # Simulate a file change
        mock_event = MagicMock()
        mock_event.is_directory = False
        mock_event.src_path = "/home/user/.claude/projects/test/session1.jsonl"

        watcher.on_any_event(mock_event)
        watcher.check_and_update()

        # Callback should have received the changed files
        assert len(callback_args) == 1
        assert len(callback_args[0]) == 1

        # Changed files should be cleared
        assert len(watcher.changed_files) == 0

    def test_passes_changed_files_to_callback(self, output_dir):
        """Test that callback receives set of changed files."""
        watcher = TranscriptWatcher(output_dir, debounce_seconds=0)
        received_changes = []

        def callback(changed_paths):
            received_changes.extend(changed_paths)

        watcher.generation_callback = callback

        # Simulate file changes
        mock_event = MagicMock()
        mock_event.is_directory = False
        mock_event.src_path = "/home/user/.claude/projects/test/session1.jsonl"

        watcher.on_any_event(mock_event)
        watcher.check_and_update()

        assert len(received_changes) == 1
        assert (
            Path("/home/user/.claude/projects/test/session1.jsonl") in received_changes
        )


class TestIncrementalGeneration:
    """Tests for incremental HTML generation."""

    def test_incremental_update_only_regenerates_changed_session(
        self, mock_projects_dir, output_dir
    ):
        """Test that incremental update only regenerates the changed session."""
        # Do initial full generation
        generate_batch_html(mock_projects_dir, output_dir)

        # Record initial modification times
        session_a1_html = output_dir / "project-a" / "abc123" / "index.html"
        session_a2_html = output_dir / "project-a" / "def456" / "index.html"
        session_b_html = output_dir / "project-b" / "ghi789" / "index.html"

        initial_a1_mtime = session_a1_html.stat().st_mtime
        initial_a2_mtime = session_a2_html.stat().st_mtime
        initial_b_mtime = session_b_html.stat().st_mtime

        # Wait a bit to ensure mtime differences are detectable
        time.sleep(0.1)

        # Import the incremental update function
        from claude_code_transcripts import generate_incremental_html

        # Simulate only session a1 changing
        changed_files = {
            mock_projects_dir / "-home-user-projects-project-a" / "abc123.jsonl"
        }

        # Do incremental update
        stats = generate_incremental_html(
            mock_projects_dir, output_dir, changed_files, include_agents=False
        )

        # Only the changed session should have been regenerated
        assert stats["sessions_regenerated"] == 1

        # session_a1 should have new mtime
        assert session_a1_html.stat().st_mtime > initial_a1_mtime

        # session_a2 and session_b should have same mtime (not regenerated)
        assert session_a2_html.stat().st_mtime == initial_a2_mtime
        assert session_b_html.stat().st_mtime == initial_b_mtime

    def test_incremental_update_updates_affected_project_index(
        self, mock_projects_dir, output_dir
    ):
        """Test that project index is updated when a session in it changes."""
        # Do initial full generation
        generate_batch_html(mock_projects_dir, output_dir)

        project_a_index = output_dir / "project-a" / "index.html"
        project_b_index = output_dir / "project-b" / "index.html"

        initial_a_index_mtime = project_a_index.stat().st_mtime
        initial_b_index_mtime = project_b_index.stat().st_mtime

        time.sleep(0.1)

        from claude_code_transcripts import generate_incremental_html

        # Simulate session in project-a changing
        changed_files = {
            mock_projects_dir / "-home-user-projects-project-a" / "abc123.jsonl"
        }

        generate_incremental_html(
            mock_projects_dir, output_dir, changed_files, include_agents=False
        )

        # project-a index should be updated
        assert project_a_index.stat().st_mtime > initial_a_index_mtime

        # project-b index should NOT be updated
        assert project_b_index.stat().st_mtime == initial_b_index_mtime

    def test_incremental_update_updates_master_index(
        self, mock_projects_dir, output_dir
    ):
        """Test that master index is updated on incremental changes."""
        # Do initial full generation
        generate_batch_html(mock_projects_dir, output_dir)

        master_index = output_dir / "index.html"
        initial_master_mtime = master_index.stat().st_mtime

        time.sleep(0.1)

        from claude_code_transcripts import generate_incremental_html

        changed_files = {
            mock_projects_dir / "-home-user-projects-project-a" / "abc123.jsonl"
        }

        generate_incremental_html(
            mock_projects_dir, output_dir, changed_files, include_agents=False
        )

        # Master index should be updated
        assert master_index.stat().st_mtime > initial_master_mtime

    def test_incremental_update_handles_new_session(
        self, mock_projects_dir, output_dir
    ):
        """Test that incremental update can handle a newly created session."""
        # Do initial full generation
        generate_batch_html(mock_projects_dir, output_dir)

        # Create a new session file
        new_session = (
            mock_projects_dir / "-home-user-projects-project-a" / "newfile.jsonl"
        )
        new_session.write_text(
            '{"type": "user", "timestamp": "2025-01-10T10:00:00.000Z", "message": {"role": "user", "content": "New session content"}}\n'
            '{"type": "assistant", "timestamp": "2025-01-10T10:00:05.000Z", "message": {"role": "assistant", "content": [{"type": "text", "text": "New response!"}]}}\n'
        )

        from claude_code_transcripts import generate_incremental_html

        changed_files = {new_session}

        stats = generate_incremental_html(
            mock_projects_dir, output_dir, changed_files, include_agents=False
        )

        # New session should have been generated
        new_session_html = output_dir / "project-a" / "newfile" / "index.html"
        assert new_session_html.exists()
        assert stats["sessions_regenerated"] == 1

    def test_incremental_update_handles_multiple_changed_sessions(
        self, mock_projects_dir, output_dir
    ):
        """Test that multiple changed sessions are all regenerated."""
        generate_batch_html(mock_projects_dir, output_dir)

        session_a1_html = output_dir / "project-a" / "abc123" / "index.html"
        session_b_html = output_dir / "project-b" / "ghi789" / "index.html"

        initial_a1_mtime = session_a1_html.stat().st_mtime
        initial_b_mtime = session_b_html.stat().st_mtime

        time.sleep(0.1)

        from claude_code_transcripts import generate_incremental_html

        # Both sessions changed
        changed_files = {
            mock_projects_dir / "-home-user-projects-project-a" / "abc123.jsonl",
            mock_projects_dir / "-home-user-projects-project-b" / "ghi789.jsonl",
        }

        stats = generate_incremental_html(
            mock_projects_dir, output_dir, changed_files, include_agents=False
        )

        assert stats["sessions_regenerated"] == 2
        assert session_a1_html.stat().st_mtime > initial_a1_mtime
        assert session_b_html.stat().st_mtime > initial_b_mtime
