"""Tests for finding sessions from both Claude Code and Codex CLI directories."""

import tempfile
from pathlib import Path
import time

import pytest

from claude_code_transcripts import find_local_sessions, find_combined_sessions


class TestFindCombinedSessions:
    """Tests for finding sessions from both ~/.claude/projects and ~/.codex/sessions."""

    def test_finds_sessions_from_both_directories(self):
        """Test that sessions from both Claude and Codex directories are found."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)

            # Create mock Claude projects directory
            claude_dir = tmpdir / "claude_projects" / "project-a"
            claude_dir.mkdir(parents=True)
            claude_session = claude_dir / "session1.jsonl"
            claude_session.write_text(
                '{"type": "user", "timestamp": "2025-01-01T10:00:00.000Z", "message": {"role": "user", "content": "Claude session"}}\n'
            )

            # Create mock Codex sessions directory
            codex_dir = tmpdir / "codex_sessions"
            codex_dir.mkdir(parents=True)
            codex_session = codex_dir / "rollout-2025-12-28T10-00-00-abc123.jsonl"
            codex_session.write_text(
                '{"timestamp":"2025-12-28T10:00:00.000Z","type":"session_meta","payload":{"id":"abc123"}}\n'
                '{"timestamp":"2025-12-28T10:00:00.000Z","type":"response_item","payload":{"type":"message","role":"user","content":[{"type":"input_text","text":"Codex session"}]}}\n'
            )

            # Find sessions from both
            results = find_combined_sessions(
                claude_dir=tmpdir / "claude_projects", codex_dir=codex_dir
            )

            # Should find both
            assert len(results) == 2
            paths = [r[0] for r in results]
            assert claude_session in paths
            assert codex_session in paths

    def test_labels_sessions_by_source(self):
        """Test that sessions include source labels (Claude or Codex)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)

            # Create one of each type
            claude_dir = tmpdir / "claude_projects" / "project-a"
            claude_dir.mkdir(parents=True)
            claude_session = claude_dir / "session1.jsonl"
            claude_session.write_text(
                '{"type": "user", "timestamp": "2025-01-01T10:00:00.000Z", "message": {"role": "user", "content": "Test"}}\n'
            )

            codex_dir = tmpdir / "codex_sessions"
            codex_dir.mkdir(parents=True)
            codex_session = codex_dir / "rollout-2025-12-28T10-00-00-abc123.jsonl"
            codex_session.write_text(
                '{"timestamp":"2025-12-28T10:00:00.000Z","type":"session_meta","payload":{"id":"abc123"}}\n'
                '{"timestamp":"2025-12-28T10:00:00.000Z","type":"response_item","payload":{"type":"message","role":"user","content":[{"type":"input_text","text":"Test"}]}}\n'
            )

            results = find_combined_sessions(
                claude_dir=tmpdir / "claude_projects", codex_dir=codex_dir
            )

            # Results should be (Path, summary, source) tuples
            assert len(results) == 2

            claude_result = next(r for r in results if r[0] == claude_session)
            codex_result = next(r for r in results if r[0] == codex_session)

            # Check source labels
            assert claude_result[2] == "Claude"
            assert codex_result[2] == "Codex"

    def test_sorts_combined_by_modification_time(self):
        """Test that all sessions are sorted together by modification time."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)

            # Create older Claude session
            claude_dir = tmpdir / "claude_projects" / "project-a"
            claude_dir.mkdir(parents=True)
            old_claude = claude_dir / "old.jsonl"
            old_claude.write_text(
                '{"type": "user", "timestamp": "2025-01-01T10:00:00.000Z", "message": {"role": "user", "content": "Old"}}\n'
            )

            time.sleep(0.1)

            # Create newer Codex session
            codex_dir = tmpdir / "codex_sessions"
            codex_dir.mkdir(parents=True)
            new_codex = codex_dir / "rollout-2025-12-28T10-00-00-abc123.jsonl"
            new_codex.write_text(
                '{"timestamp":"2025-12-28T10:00:00.000Z","type":"session_meta","payload":{"id":"abc123"}}\n'
                '{"timestamp":"2025-12-28T10:00:00.000Z","type":"response_item","payload":{"type":"message","role":"user","content":[{"type":"input_text","text":"New"}]}}\n'
            )

            results = find_combined_sessions(
                claude_dir=tmpdir / "claude_projects", codex_dir=codex_dir
            )

            # Newer file should be first regardless of source
            assert results[0][0] == new_codex
            assert results[1][0] == old_claude

    def test_respects_limit_across_both_sources(self):
        """Test that limit applies to combined results."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)

            # Create 3 Claude sessions
            claude_dir = tmpdir / "claude_projects" / "project-a"
            claude_dir.mkdir(parents=True)
            for i in range(3):
                f = claude_dir / f"session{i}.jsonl"
                f.write_text(
                    '{"type": "user", "timestamp": "2025-01-01T10:00:00.000Z", "message": {"role": "user", "content": "Test"}}\n'
                )

            # Create 3 Codex sessions
            codex_dir = tmpdir / "codex_sessions"
            codex_dir.mkdir(parents=True)
            for i in range(3):
                f = codex_dir / f"rollout-2025-12-28T10-00-0{i}-test{i}.jsonl"
                f.write_text(
                    f'{{"timestamp":"2025-12-28T10:00:0{i}.000Z","type":"session_meta","payload":{{"id":"test{i}"}}}}\n'
                    f'{{"timestamp":"2025-12-28T10:00:0{i}.000Z","type":"response_item","payload":{{"type":"message","role":"user","content":[{{"type":"input_text","text":"Test"}}]}}}}\n'
                )

            # Request only 4 total
            results = find_combined_sessions(
                claude_dir=tmpdir / "claude_projects", codex_dir=codex_dir, limit=4
            )

            assert len(results) == 4

    def test_handles_missing_directories(self):
        """Test that missing directories don't cause errors."""
        # Both missing
        results = find_combined_sessions(
            claude_dir=Path("/nonexistent/claude"),
            codex_dir=Path("/nonexistent/codex"),
        )
        assert results == []

        # Only Claude exists
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            claude_dir = tmpdir / "claude_projects" / "project-a"
            claude_dir.mkdir(parents=True)
            session = claude_dir / "session1.jsonl"
            session.write_text(
                '{"type": "user", "timestamp": "2025-01-01T10:00:00.000Z", "message": {"role": "user", "content": "Test"}}\n'
            )

            results = find_combined_sessions(
                claude_dir=tmpdir / "claude_projects",
                codex_dir=Path("/nonexistent/codex"),
            )
            assert len(results) == 1
            assert results[0][2] == "Claude"
