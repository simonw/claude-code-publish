"""Tests for Codex CLI format support."""

import tempfile
from pathlib import Path

import pytest

from claude_code_transcripts import parse_session_file, generate_html


class TestCodexCliFormatDetection:
    """Tests for detecting Codex CLI format."""

    def test_detects_codex_format_from_session_meta(self):
        """Test that Codex format is detected from session_meta record type."""
        # Create a minimal Codex CLI JSONL file
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            f.write(
                '{"timestamp":"2025-12-28T12:18:30.533Z","type":"session_meta","payload":{"id":"test-id","timestamp":"2025-12-28T12:18:30.522Z","cwd":"/test","originator":"codex_cli_rs","cli_version":"0.77.0"}}\n'
            )
            f.write(
                '{"timestamp":"2025-12-28T12:18:30.533Z","type":"response_item","payload":{"type":"message","role":"user","content":[{"type":"input_text","text":"Hello world"}]}}\n'
            )
            temp_file = Path(f.name)

        try:
            data = parse_session_file(temp_file)
            # Should have loglines key after parsing
            assert "loglines" in data
            # Should have at least one entry
            assert len(data["loglines"]) >= 1
        finally:
            temp_file.unlink()

    def test_detects_claude_code_format(self):
        """Test that Claude Code format still works."""
        # Create a minimal Claude Code JSONL file
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            f.write(
                '{"type": "user", "timestamp": "2025-01-01T10:00:00.000Z", "message": {"role": "user", "content": "Hello"}}\n'
            )
            f.write(
                '{"type": "assistant", "timestamp": "2025-01-01T10:00:05.000Z", "message": {"role": "assistant", "content": [{"type": "text", "text": "Hi!"}]}}\n'
            )
            temp_file = Path(f.name)

        try:
            data = parse_session_file(temp_file)
            assert "loglines" in data
            assert len(data["loglines"]) == 2
        finally:
            temp_file.unlink()

    def test_detects_codex_format_from_message_record(self):
        """Test that Codex format is detected from message/record_type records."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            f.write(
                '{"id":"test-id","timestamp":"2025-08-31T20:48:31.616Z","instructions":null}\n'
            )
            f.write('{"record_type":"state"}\n')
            f.write(
                '{"type":"message","id":null,"role":"user","content":[{"type":"input_text","text":"Hello old format"}]}\n'
            )
            temp_file = Path(f.name)

        try:
            data = parse_session_file(temp_file)
            loglines = data["loglines"]
            assert len(loglines) == 1
            assert loglines[0]["type"] == "user"
            assert loglines[0]["message"]["content"] == "Hello old format"
        finally:
            temp_file.unlink()


class TestCodexCliMessageParsing:
    """Tests for parsing Codex CLI messages."""

    def test_parses_user_message(self):
        """Test that Codex user messages are converted to Claude Code format."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            f.write(
                '{"timestamp":"2025-12-28T12:18:30.533Z","type":"response_item","payload":{"type":"message","role":"user","content":[{"type":"input_text","text":"Test message"}]}}\n'
            )
            temp_file = Path(f.name)

        try:
            data = parse_session_file(temp_file)
            loglines = data["loglines"]
            assert len(loglines) == 1

            # Check conversion to Claude Code format
            entry = loglines[0]
            assert entry["type"] == "user"
            assert entry["timestamp"] == "2025-12-28T12:18:30.533Z"
            assert "message" in entry
            assert entry["message"]["role"] == "user"
            # Content should be extracted from input_text
            content = entry["message"]["content"]
            assert content == "Test message"
        finally:
            temp_file.unlink()

    def test_parses_assistant_message(self):
        """Test that Codex assistant messages are converted correctly."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            f.write(
                '{"timestamp":"2025-12-28T12:18:40.000Z","type":"response_item","payload":{"type":"message","role":"assistant","content":[{"type":"text","text":"Response text"}]}}\n'
            )
            temp_file = Path(f.name)

        try:
            data = parse_session_file(temp_file)
            loglines = data["loglines"]
            assert len(loglines) == 1

            entry = loglines[0]
            assert entry["type"] == "assistant"
            assert entry["message"]["role"] == "assistant"
            # Content should be in Claude Code format
            assert isinstance(entry["message"]["content"], list)
            assert entry["message"]["content"][0]["type"] == "text"
            assert entry["message"]["content"][0]["text"] == "Response text"
        finally:
            temp_file.unlink()

    def test_skips_non_message_records(self):
        """Test that non-message records (session_meta, turn_context, etc.) are skipped."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            f.write(
                '{"timestamp":"2025-12-28T12:18:30.533Z","type":"session_meta","payload":{"id":"test"}}\n'
            )
            f.write(
                '{"timestamp":"2025-12-28T12:18:30.533Z","type":"turn_context","payload":{}}\n'
            )
            f.write(
                '{"timestamp":"2025-12-28T12:18:30.533Z","type":"event_msg","payload":{}}\n'
            )
            f.write(
                '{"timestamp":"2025-12-28T12:18:30.533Z","type":"response_item","payload":{"type":"message","role":"user","content":[{"type":"input_text","text":"Only this"}]}}\n'
            )
            temp_file = Path(f.name)

        try:
            data = parse_session_file(temp_file)
            loglines = data["loglines"]
            # Should only have the one message
            assert len(loglines) == 1
            assert loglines[0]["message"]["content"] == "Only this"
        finally:
            temp_file.unlink()


class TestCodexCliToolCalls:
    """Tests for parsing Codex CLI tool calls."""

    def test_parses_function_call(self):
        """Test that Codex function_call is converted to tool_use."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            # Add a function call
            f.write(
                '{"timestamp":"2025-12-28T12:18:40.000Z","type":"response_item","payload":{"type":"function_call","name":"shell_command","arguments":"{\\"command\\":\\"ls -la\\"}","call_id":"call_123"}}\n'
            )
            temp_file = Path(f.name)

        try:
            data = parse_session_file(temp_file)
            loglines = data["loglines"]
            assert len(loglines) == 1

            entry = loglines[0]
            assert entry["type"] == "assistant"
            assert isinstance(entry["message"]["content"], list)

            # Check tool_use block
            tool_use = entry["message"]["content"][0]
            assert tool_use["type"] == "tool_use"
            assert tool_use["name"] == "Bash"  # shell_command -> Bash
            assert "input" in tool_use
            assert tool_use["input"]["command"] == "ls -la"
        finally:
            temp_file.unlink()

    def test_parses_function_call_output_old_format(self):
        """Test that Codex function_call_output converts to tool_result."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            f.write(
                '{"id":"test-id","timestamp":"2025-08-31T20:48:31.616Z","instructions":null}\n'
            )
            f.write('{"record_type":"state"}\n')
            f.write(
                '{"type":"function_call","id":"fc_123","name":"shell_command","arguments":"{\\"command\\":\\"ls -la\\"}","call_id":"call_123"}\n'
            )
            f.write(
                '{"type":"function_call_output","call_id":"call_123","output":"OK"}\n'
            )
            temp_file = Path(f.name)

        try:
            data = parse_session_file(temp_file)
            loglines = data["loglines"]
            assert len(loglines) == 2

            tool_use = loglines[0]["message"]["content"][0]
            assert tool_use["type"] == "tool_use"
            assert tool_use["name"] == "Bash"
            assert tool_use["id"] == "call_123"

            tool_result = loglines[1]["message"]["content"][0]
            assert tool_result["type"] == "tool_result"
            assert tool_result["tool_use_id"] == "call_123"
            assert tool_result["content"] == "OK"
        finally:
            temp_file.unlink()


class TestCodexCliHtmlGeneration:
    """Integration test for generating HTML from Codex CLI files."""

    def test_generates_html_from_codex_file(self):
        """Test that HTML can be generated from a Codex CLI session."""
        # Use the sample codex session file
        sample_file = Path(__file__).parent / "sample_codex_session.jsonl"
        if not sample_file.exists():
            pytest.skip("sample_codex_session.jsonl not found")

        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir)

            # Should not raise an exception
            generate_html(sample_file, output_dir)

            # Check that HTML was generated
            assert (output_dir / "index.html").exists()
            # Should have at least one page
            pages = list(output_dir.glob("page-*.html"))
            assert len(pages) >= 1
