"""Tests for code viewer functionality."""

import json
from pathlib import Path

import pytest

from claude_code_transcripts import (
    FileOperation,
    FileState,
    CodeViewData,
    extract_file_operations,
    build_file_tree,
    generate_code_view_html,
    PROMPTS_PER_PAGE,
)


@pytest.fixture
def sample_session():
    """Load the sample session fixture."""
    fixture_path = Path(__file__).parent / "sample_session.json"
    with open(fixture_path) as f:
        return json.load(f)


@pytest.fixture
def sample_conversations(sample_session):
    """Build conversations list from sample session (mimics generate_html logic)."""
    from claude_code_transcripts import extract_text_from_content

    loglines = sample_session.get("loglines", [])
    conversations = []
    current_conv = None

    for entry in loglines:
        log_type = entry.get("type")
        timestamp = entry.get("timestamp", "")
        message = entry.get("message", {})

        if not message:
            continue

        message_json = json.dumps(message)
        is_user_prompt = False
        user_text = None

        if log_type == "user":
            content = message.get("content", "")
            text = extract_text_from_content(content)
            if text:
                is_user_prompt = True
                user_text = text

        if is_user_prompt:
            if current_conv:
                conversations.append(current_conv)
            current_conv = {
                "user_text": user_text,
                "timestamp": timestamp,
                "messages": [(log_type, message_json, timestamp)],
            }
        elif current_conv:
            current_conv["messages"].append((log_type, message_json, timestamp))

    if current_conv:
        conversations.append(current_conv)

    return conversations


class TestFileOperationDataclass:
    """Tests for the FileOperation dataclass."""

    def test_create_write_operation(self):
        """Test creating a Write FileOperation."""
        op = FileOperation(
            file_path="/path/to/file.py",
            operation_type="write",
            tool_id="toolu_123",
            timestamp="2025-12-24T10:00:00.000Z",
            page_num=1,
            msg_id="msg-0-1",
            content="print('hello')\n",
        )
        assert op.file_path == "/path/to/file.py"
        assert op.operation_type == "write"
        assert op.content == "print('hello')\n"
        assert op.old_string is None
        assert op.new_string is None

    def test_create_edit_operation(self):
        """Test creating an Edit FileOperation."""
        op = FileOperation(
            file_path="/path/to/file.py",
            operation_type="edit",
            tool_id="toolu_456",
            timestamp="2025-12-24T10:01:00.000Z",
            page_num=1,
            msg_id="msg-0-2",
            old_string="print('hello')",
            new_string="print('world')",
            replace_all=False,
        )
        assert op.file_path == "/path/to/file.py"
        assert op.operation_type == "edit"
        assert op.old_string == "print('hello')"
        assert op.new_string == "print('world')"
        assert op.content is None


class TestExtractFileOperations:
    """Tests for the extract_file_operations function."""

    def test_extracts_write_operations(self, sample_session, sample_conversations):
        """Test that Write tool calls are extracted."""
        loglines = sample_session.get("loglines", [])
        operations = extract_file_operations(loglines, sample_conversations)

        write_ops = [op for op in operations if op.operation_type == "write"]
        assert len(write_ops) >= 1

        # Check first write operation
        first_write = write_ops[0]
        assert first_write.file_path == "/project/math_utils.py"
        assert "def add" in first_write.content
        assert first_write.tool_id == "toolu_write_001"

    def test_extracts_edit_operations(self, sample_session, sample_conversations):
        """Test that Edit tool calls are extracted."""
        loglines = sample_session.get("loglines", [])
        operations = extract_file_operations(loglines, sample_conversations)

        edit_ops = [op for op in operations if op.operation_type == "edit"]
        assert len(edit_ops) >= 1

        # Check an edit operation
        first_edit = edit_ops[0]
        assert first_edit.file_path == "/project/math_utils.py"
        assert first_edit.old_string is not None
        assert first_edit.new_string is not None

    def test_operations_sorted_by_timestamp(self, sample_session, sample_conversations):
        """Test that operations are returned in chronological order."""
        loglines = sample_session.get("loglines", [])
        operations = extract_file_operations(loglines, sample_conversations)

        # Check timestamps are in order
        for i in range(len(operations) - 1):
            assert operations[i].timestamp <= operations[i + 1].timestamp

    def test_operations_have_page_numbers(self, sample_session, sample_conversations):
        """Test that operations have valid page numbers."""
        loglines = sample_session.get("loglines", [])
        operations = extract_file_operations(loglines, sample_conversations)

        for op in operations:
            assert op.page_num >= 1
            # Page number should be within reasonable bounds
            max_page = (len(sample_conversations) // PROMPTS_PER_PAGE) + 1
            assert op.page_num <= max_page

    def test_operations_have_message_ids(self, sample_session, sample_conversations):
        """Test that operations have message IDs for linking."""
        loglines = sample_session.get("loglines", [])
        operations = extract_file_operations(loglines, sample_conversations)

        for op in operations:
            assert op.msg_id.startswith("msg-")

    def test_handles_multiple_files(self, sample_session, sample_conversations):
        """Test that multiple files are tracked correctly."""
        loglines = sample_session.get("loglines", [])
        operations = extract_file_operations(loglines, sample_conversations)

        # Get unique file paths
        file_paths = set(op.file_path for op in operations)
        # Sample session should have at least 1 file
        assert len(file_paths) >= 1

    def test_empty_loglines(self, sample_conversations):
        """Test handling of empty loglines."""
        operations = extract_file_operations([], sample_conversations)
        assert operations == []

    def test_no_tool_calls(self):
        """Test handling of session with no Write/Edit operations."""
        loglines = [
            {
                "type": "user",
                "timestamp": "2025-12-24T10:00:00.000Z",
                "message": {"content": "Hello", "role": "user"},
            },
            {
                "type": "assistant",
                "timestamp": "2025-12-24T10:00:05.000Z",
                "message": {
                    "content": [{"type": "text", "text": "Hi!"}],
                    "role": "assistant",
                },
            },
        ]
        conversations = [
            {
                "user_text": "Hello",
                "timestamp": "2025-12-24T10:00:00.000Z",
                "messages": [
                    (
                        "user",
                        '{"content": "Hello", "role": "user"}',
                        "2025-12-24T10:00:00.000Z",
                    ),
                    (
                        "assistant",
                        '{"content": [{"type": "text", "text": "Hi!"}], "role": "assistant"}',
                        "2025-12-24T10:00:05.000Z",
                    ),
                ],
            }
        ]
        operations = extract_file_operations(loglines, conversations)
        assert operations == []


class TestBuildFileTree:
    """Tests for the build_file_tree function."""

    def test_builds_simple_tree(self):
        """Test building a file tree from flat file paths."""
        file_states = {
            "/src/main.py": FileState(file_path="/src/main.py"),
            "/src/utils.py": FileState(file_path="/src/utils.py"),
            "/tests/test_main.py": FileState(file_path="/tests/test_main.py"),
        }

        tree = build_file_tree(file_states)

        # Check structure - should have /src and /tests at root level
        assert "/" in tree
        root = tree["/"]
        assert "src" in root
        assert "tests" in root
        assert "main.py" in root["src"]
        assert "utils.py" in root["src"]
        assert "test_main.py" in root["tests"]

    def test_empty_file_states(self):
        """Test building tree from empty file states."""
        tree = build_file_tree({})
        assert tree == {}

    def test_single_file(self):
        """Test building tree with single file."""
        file_states = {"/path/to/file.py": FileState(file_path="/path/to/file.py")}
        tree = build_file_tree(file_states)

        assert "/" in tree
        current = tree["/"]
        assert "path" in current
        assert "to" in current["path"]
        assert "file.py" in current["path"]["to"]

    def test_file_state_is_leaf(self):
        """Test that FileState objects are the leaves of the tree."""
        file_state = FileState(file_path="/src/main.py")
        file_states = {"/src/main.py": file_state}

        tree = build_file_tree(file_states)

        # Navigate to the leaf
        leaf = tree["/"]["src"]["main.py"]
        assert isinstance(leaf, FileState)
        assert leaf.file_path == "/src/main.py"


class TestCodeViewDataDataclass:
    """Tests for the CodeViewData dataclass."""

    def test_create_empty(self):
        """Test creating empty CodeViewData."""
        data = CodeViewData()
        assert data.files == {}
        assert data.file_tree == {}
        assert data.mode == "diff_only"
        assert data.repo_path is None

    def test_create_with_data(self):
        """Test creating CodeViewData with data."""
        file_state = FileState(file_path="/src/main.py")
        data = CodeViewData(
            files={"/src/main.py": file_state},
            file_tree={"/": {"src": {"main.py": file_state}}},
            mode="full",
            repo_path="/path/to/repo",
            session_cwd="/path/to/project",
        )
        assert len(data.files) == 1
        assert data.mode == "full"
        assert data.repo_path == "/path/to/repo"


class TestReconstructFileWithBlame:
    """Tests for the reconstruct_file_with_blame function."""

    def test_write_operation_replaces_all_content(self):
        """Test that a Write operation replaces all content."""
        from claude_code_transcripts import reconstruct_file_with_blame, FileOperation

        op = FileOperation(
            file_path="/test.py",
            operation_type="write",
            tool_id="toolu_001",
            timestamp="2025-12-24T10:00:00.000Z",
            page_num=1,
            msg_id="msg-0-1",
            content="line1\nline2\n",
        )

        final_content, blame_lines = reconstruct_file_with_blame(None, [op])

        assert final_content == "line1\nline2\n"
        assert len(blame_lines) == 2
        assert blame_lines[0][0] == "line1"
        assert blame_lines[0][1] == op
        assert blame_lines[1][0] == "line2"
        assert blame_lines[1][1] == op

    def test_edit_operation_modifies_content(self):
        """Test that an Edit operation modifies specific content."""
        from claude_code_transcripts import reconstruct_file_with_blame, FileOperation

        # Start with content from a Write
        write_op = FileOperation(
            file_path="/test.py",
            operation_type="write",
            tool_id="toolu_001",
            timestamp="2025-12-24T10:00:00.000Z",
            page_num=1,
            msg_id="msg-0-1",
            content="line1\nline2\nline3\n",
        )

        edit_op = FileOperation(
            file_path="/test.py",
            operation_type="edit",
            tool_id="toolu_002",
            timestamp="2025-12-24T10:01:00.000Z",
            page_num=1,
            msg_id="msg-0-2",
            old_string="line2",
            new_string="modified_line2",
        )

        final_content, blame_lines = reconstruct_file_with_blame(
            None, [write_op, edit_op]
        )

        assert final_content == "line1\nmodified_line2\nline3\n"
        assert len(blame_lines) == 3
        assert blame_lines[0][1] == write_op  # line1 still from write
        assert blame_lines[1][1] == edit_op  # modified_line2 from edit
        assert blame_lines[2][1] == edit_op  # line3 also from edit (after old_string)

    def test_initial_content_attributed_to_none(self):
        """Test that initial content lines are attributed to None."""
        from claude_code_transcripts import reconstruct_file_with_blame, FileOperation

        initial_content = "existing1\nexisting2\n"

        edit_op = FileOperation(
            file_path="/test.py",
            operation_type="edit",
            tool_id="toolu_001",
            timestamp="2025-12-24T10:00:00.000Z",
            page_num=1,
            msg_id="msg-0-1",
            old_string="existing2",
            new_string="modified",
        )

        final_content, blame_lines = reconstruct_file_with_blame(
            initial_content, [edit_op]
        )

        assert final_content == "existing1\nmodified\n"
        assert blame_lines[0][1] is None  # existing1 is pre-session
        assert blame_lines[1][1] == edit_op  # modified is from edit

    def test_no_operations_returns_initial(self):
        """Test that no operations returns initial content unchanged."""
        from claude_code_transcripts import reconstruct_file_with_blame

        initial_content = "line1\nline2\n"

        final_content, blame_lines = reconstruct_file_with_blame(initial_content, [])

        assert final_content == "line1\nline2\n"
        assert len(blame_lines) == 2
        assert blame_lines[0][1] is None  # All attributed to None (pre-session)
        assert blame_lines[1][1] is None

    def test_multiline_edit(self):
        """Test edit operation that adds multiple lines."""
        from claude_code_transcripts import reconstruct_file_with_blame, FileOperation

        write_op = FileOperation(
            file_path="/test.py",
            operation_type="write",
            tool_id="toolu_001",
            timestamp="2025-12-24T10:00:00.000Z",
            page_num=1,
            msg_id="msg-0-1",
            content="def foo():\n    pass\n",
        )

        edit_op = FileOperation(
            file_path="/test.py",
            operation_type="edit",
            tool_id="toolu_002",
            timestamp="2025-12-24T10:01:00.000Z",
            page_num=1,
            msg_id="msg-0-2",
            old_string="    pass",
            new_string="    x = 1\n    y = 2\n    return x + y",
        )

        final_content, blame_lines = reconstruct_file_with_blame(
            None, [write_op, edit_op]
        )

        assert "x = 1" in final_content
        assert "y = 2" in final_content
        assert "return x + y" in final_content


class TestGenerateCodeViewHtml:
    """Tests for generate_code_view_html function."""

    def test_escapes_script_tags_in_json(self, tmp_path):
        """Test that </script> and <!-- are escaped to prevent HTML injection."""
        # Content with dangerous HTML sequences
        content = 'console.log("</script>"); // <!-- comment'

        fs = FileState("/test/path.js")
        fs.operations = [
            FileOperation(
                file_path="/test/path.js",
                operation_type="write",
                tool_id="t1",
                timestamp="2024-01-01T10:00:00Z",
                page_num=1,
                msg_id="msg-001",
                content=content,
            )
        ]
        fs.final_content = content
        fs.blame_lines = [(content, fs.operations[0])]
        fs.diff_only = False

        file_states = {"/test/path.js": fs}

        generate_code_view_html(tmp_path, file_states)

        html = (tmp_path / "code.html").read_text()

        # Find the JSON part - between const fileData and the first semicolon
        start = html.find("const fileData")
        end = html.find("</script>", start)  # Should find the closing tag, not content

        json_section = html[start:end]

        # Check that </script> is escaped in the JSON
        assert "<\\/script>" in json_section
        # Check that <!-- is escaped
        assert "<\\!--" in json_section
        # Make sure the literal strings don't appear unescaped
        assert "</script>" not in json_section
        assert "<!--" not in json_section
