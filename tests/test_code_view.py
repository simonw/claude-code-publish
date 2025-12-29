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

    def test_extracts_original_file_content_for_edit(self):
        """Test that originalFile from toolUseResult is extracted for Edit operations.

        This enables file reconstruction for remote sessions without local file access.
        """
        original_content = "def add(a, b):\n    return a + b\n"

        loglines = [
            # User prompt
            {
                "type": "user",
                "timestamp": "2025-12-24T10:00:00.000Z",
                "message": {"content": "Edit the file", "role": "user"},
            },
            # Assistant makes an Edit
            {
                "type": "assistant",
                "timestamp": "2025-12-24T10:00:05.000Z",
                "message": {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "toolu_edit_001",
                            "name": "Edit",
                            "input": {
                                "file_path": "/project/math.py",
                                "old_string": "return a + b",
                                "new_string": "return a + b  # sum",
                            },
                        }
                    ],
                },
            },
            # Tool result with originalFile in toolUseResult
            {
                "type": "user",
                "timestamp": "2025-12-24T10:00:10.000Z",
                "toolUseResult": {"originalFile": original_content},
                "message": {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "toolu_edit_001",
                            "content": "File edited successfully",
                            "is_error": False,
                        }
                    ],
                },
            },
        ]

        conversations = [
            {
                "user_text": "Edit the file",
                "timestamp": "2025-12-24T10:00:00.000Z",
                "messages": [
                    (
                        "user",
                        '{"content": "Edit the file", "role": "user"}',
                        "2025-12-24T10:00:00.000Z",
                    ),
                    (
                        "assistant",
                        '{"content": [{"type": "tool_use", "id": "toolu_edit_001", "name": "Edit", "input": {}}], "role": "assistant"}',
                        "2025-12-24T10:00:05.000Z",
                    ),
                    (
                        "user",
                        '{"content": [{"type": "tool_result", "tool_use_id": "toolu_edit_001"}], "role": "user"}',
                        "2025-12-24T10:00:10.000Z",
                    ),
                ],
            }
        ]

        operations = extract_file_operations(loglines, conversations)

        # Should have one Edit operation
        assert len(operations) == 1
        op = operations[0]
        assert op.operation_type == "edit"
        assert op.file_path == "/project/math.py"
        assert op.old_string == "return a + b"
        assert op.new_string == "return a + b  # sum"
        # original_content should be populated from toolUseResult.originalFile
        assert op.original_content == original_content

    def test_original_file_not_set_for_write(self):
        """Test that original_content is not set for Write operations (only Edit)."""
        loglines = [
            {
                "type": "user",
                "timestamp": "2025-12-24T10:00:00.000Z",
                "message": {"content": "Create a file", "role": "user"},
            },
            {
                "type": "assistant",
                "timestamp": "2025-12-24T10:00:05.000Z",
                "message": {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "toolu_write_001",
                            "name": "Write",
                            "input": {
                                "file_path": "/project/new.py",
                                "content": "print('hello')\n",
                            },
                        }
                    ],
                },
            },
            {
                "type": "user",
                "timestamp": "2025-12-24T10:00:10.000Z",
                "message": {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "toolu_write_001",
                            "content": "File written",
                            "is_error": False,
                        }
                    ],
                },
            },
        ]

        conversations = [
            {
                "user_text": "Create a file",
                "timestamp": "2025-12-24T10:00:00.000Z",
                "messages": [
                    (
                        "user",
                        '{"content": "Create a file", "role": "user"}',
                        "2025-12-24T10:00:00.000Z",
                    ),
                    (
                        "assistant",
                        '{"content": [], "role": "assistant"}',
                        "2025-12-24T10:00:05.000Z",
                    ),
                ],
            }
        ]

        operations = extract_file_operations(loglines, conversations)

        assert len(operations) == 1
        op = operations[0]
        assert op.operation_type == "write"
        # Write operations don't use original_content
        assert op.original_content is None


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

        # Check structure - common "/" prefix stripped, src and tests at root
        assert "src" in tree
        assert "tests" in tree
        assert "main.py" in tree["src"]
        assert "utils.py" in tree["src"]
        assert "test_main.py" in tree["tests"]

    def test_empty_file_states(self):
        """Test building tree from empty file states."""
        tree = build_file_tree({})
        assert tree == {}

    def test_single_file(self):
        """Test building tree with single file."""
        file_states = {"/path/to/file.py": FileState(file_path="/path/to/file.py")}
        tree = build_file_tree(file_states)

        # Single file: all parent directories are common prefix, only filename remains
        assert "file.py" in tree
        assert isinstance(tree["file.py"], FileState)

    def test_file_state_is_leaf(self):
        """Test that FileState objects are the leaves of the tree."""
        file_state = FileState(file_path="/src/main.py")
        file_states = {"/src/main.py": file_state}

        tree = build_file_tree(file_states)

        # Single file: common prefix stripped, just the filename at root
        leaf = tree["main.py"]
        assert isinstance(leaf, FileState)
        assert leaf.file_path == "/src/main.py"

    def test_strips_common_prefix(self):
        """Test that common directory prefixes are stripped from the tree."""
        file_states = {
            "/Users/alice/projects/myapp/src/main.py": FileState(
                file_path="/Users/alice/projects/myapp/src/main.py"
            ),
            "/Users/alice/projects/myapp/src/utils.py": FileState(
                file_path="/Users/alice/projects/myapp/src/utils.py"
            ),
            "/Users/alice/projects/myapp/tests/test_main.py": FileState(
                file_path="/Users/alice/projects/myapp/tests/test_main.py"
            ),
        }

        tree = build_file_tree(file_states)

        # Common prefix /Users/alice/projects/myapp should be stripped
        # Tree should start with src and tests at the root
        assert "src" in tree
        assert "tests" in tree
        assert "Users" not in tree
        assert "main.py" in tree["src"]
        assert "utils.py" in tree["src"]
        assert "test_main.py" in tree["tests"]

    def test_strips_common_prefix_single_common_dir(self):
        """Test stripping when all files share exactly one common parent."""
        file_states = {
            "/src/foo.py": FileState(file_path="/src/foo.py"),
            "/src/bar.py": FileState(file_path="/src/bar.py"),
        }

        tree = build_file_tree(file_states)

        # /src is common, so tree should just have the files
        assert "foo.py" in tree
        assert "bar.py" in tree
        assert "src" not in tree

    def test_no_common_prefix_preserved(self):
        """Test that paths with no common prefix are preserved."""
        file_states = {
            "/src/main.py": FileState(file_path="/src/main.py"),
            "/lib/utils.py": FileState(file_path="/lib/utils.py"),
        }

        tree = build_file_tree(file_states)

        # Only "/" is common, so src and lib should be at root
        assert "src" in tree
        assert "lib" in tree


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


class TestGitBlameAttribution:
    """Tests for git-based blame attribution."""

    def test_write_operation_attributes_all_lines(self):
        """Test that Write operations attribute all lines to the operation."""
        from claude_code_transcripts import (
            build_file_history_repo,
            get_file_blame_ranges,
            FileOperation,
        )
        import shutil

        write_op = FileOperation(
            file_path="/project/test.py",
            operation_type="write",
            tool_id="toolu_001",
            timestamp="2025-12-24T10:00:00.000Z",
            page_num=1,
            msg_id="msg-001",
            content="line1\nline2\nline3\n",
        )

        repo, temp_dir, path_mapping = build_file_history_repo([write_op])
        try:
            rel_path = path_mapping[write_op.file_path]
            blame_ranges = get_file_blame_ranges(repo, rel_path)

            # All lines should be attributed to the write operation
            assert len(blame_ranges) == 1
            assert blame_ranges[0].start_line == 1
            assert blame_ranges[0].end_line == 3
            assert blame_ranges[0].msg_id == "msg-001"
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_edit_only_attributes_changed_lines(self, tmp_path):
        """Test that Edit operations only attribute changed lines, not context."""
        from claude_code_transcripts import (
            build_file_history_repo,
            get_file_blame_ranges,
            FileOperation,
        )
        import shutil

        # Create a file on disk to simulate pre-existing content
        test_file = tmp_path / "existing.py"
        test_file.write_text("line1\nline2\nline3\nline4\nline5\n")

        edit_op = FileOperation(
            file_path=str(test_file),
            operation_type="edit",
            tool_id="toolu_001",
            timestamp="2025-12-24T10:00:00.000Z",
            page_num=1,
            msg_id="msg-001",
            old_string="line3",
            new_string="MODIFIED",
        )

        repo, temp_dir, path_mapping = build_file_history_repo([edit_op])
        try:
            rel_path = path_mapping[edit_op.file_path]
            blame_ranges = get_file_blame_ranges(repo, rel_path)

            # Should have multiple ranges: pre-edit lines and edited line
            # Find the range with msg_id (the edit)
            edit_ranges = [r for r in blame_ranges if r.msg_id == "msg-001"]
            pre_ranges = [r for r in blame_ranges if not r.msg_id]

            # The edit should only cover the changed line
            assert len(edit_ranges) == 1
            assert edit_ranges[0].start_line == edit_ranges[0].end_line  # Single line

            # Pre-existing lines should have no msg_id
            assert len(pre_ranges) >= 1
            total_pre_lines = sum(r.end_line - r.start_line + 1 for r in pre_ranges)
            assert total_pre_lines == 4  # lines 1,2,4,5 unchanged
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_multiple_edits_track_separately(self):
        """Test that multiple edits to the same file are tracked separately."""
        from claude_code_transcripts import (
            build_file_history_repo,
            get_file_blame_ranges,
            FileOperation,
        )
        import shutil

        write_op = FileOperation(
            file_path="/project/test.py",
            operation_type="write",
            tool_id="toolu_001",
            timestamp="2025-12-24T10:00:00.000Z",
            page_num=1,
            msg_id="msg-001",
            content="aaa\nbbb\nccc\n",
        )

        edit1 = FileOperation(
            file_path="/project/test.py",
            operation_type="edit",
            tool_id="toolu_002",
            timestamp="2025-12-24T10:01:00.000Z",
            page_num=1,
            msg_id="msg-002",
            old_string="aaa",
            new_string="AAA",
        )

        edit2 = FileOperation(
            file_path="/project/test.py",
            operation_type="edit",
            tool_id="toolu_003",
            timestamp="2025-12-24T10:02:00.000Z",
            page_num=1,
            msg_id="msg-003",
            old_string="ccc",
            new_string="CCC",
        )

        repo, temp_dir, path_mapping = build_file_history_repo([write_op, edit1, edit2])
        try:
            rel_path = path_mapping[write_op.file_path]
            blame_ranges = get_file_blame_ranges(repo, rel_path)

            # Collect msg_ids from all ranges
            msg_ids = set(r.msg_id for r in blame_ranges if r.msg_id)

            # Should have at least edit1 and edit2 tracked
            assert "msg-002" in msg_ids  # First edit
            assert "msg-003" in msg_ids  # Second edit
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_resyncs_from_original_content_when_edit_fails_to_match(self):
        """Test that edits resync from original_content when old_string doesn't match."""
        from claude_code_transcripts import (
            build_file_history_repo,
            get_file_content_from_repo,
            FileOperation,
        )
        import shutil

        # First write creates file with content A
        write_op = FileOperation(
            file_path="/project/test.py",
            operation_type="write",
            tool_id="toolu_001",
            timestamp="2025-12-24T10:00:00.000Z",
            page_num=1,
            msg_id="msg-001",
            content="line1\nMARKER\nline3\n",
        )

        # Edit that expects different content (simulates divergence)
        # old_string="MARKER" won't match if our reconstruction has "WRONG"
        # But original_content shows the real state had "MARKER"
        edit_op = FileOperation(
            file_path="/project/test.py",
            operation_type="edit",
            tool_id="toolu_002",
            timestamp="2025-12-24T10:01:00.000Z",
            page_num=1,
            msg_id="msg-002",
            old_string="MARKER",
            new_string="REPLACED",
            original_content="line1\nMARKER\nline3\n",  # Real state before edit
        )

        # Simulate a scenario where our reconstruction diverged
        # by using a write that puts wrong content, then the edit should resync
        wrong_write = FileOperation(
            file_path="/project/test.py",
            operation_type="write",
            tool_id="toolu_000",
            timestamp="2025-12-24T09:59:00.000Z",  # Earlier than other ops
            page_num=1,
            msg_id="msg-000",
            content="line1\nWRONG\nline3\n",  # Wrong content - MARKER not present
        )

        # Apply: wrong_write, then edit_op (which should resync from original_content)
        repo, temp_dir, path_mapping = build_file_history_repo([wrong_write, edit_op])
        try:
            rel_path = path_mapping[edit_op.file_path]
            content = get_file_content_from_repo(repo, rel_path)

            # The edit should have resynced and replaced MARKER with REPLACED
            assert "REPLACED" in content
            assert "MARKER" not in content
            assert "WRONG" not in content  # The wrong content should be gone
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)


class TestGenerateCodeViewHtml:
    """Tests for generate_code_view_html function."""

    def test_generates_separate_data_file(self, tmp_path):
        """Test that code-data.json is generated with file content."""
        import json

        content = 'console.log("</script>"); // end'

        operations = [
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

        generate_code_view_html(tmp_path, operations)

        html = (tmp_path / "code.html").read_text()
        assert "</script>" in html  # Has script tag

        # Local version has embedded data for file:// access
        assert (
            "window.CODE_DATA" in html
        ), "Embedded data should be present for local use"
        # Script tags in content should be escaped
        assert r"<\/script>" in html, "Script tags should be escaped in embedded JSON"

        # code-data.json should also exist for gist version fetching
        data_file = tmp_path / "code-data.json"
        assert data_file.exists()
        data = json.loads(data_file.read_text())
        assert "fileData" in data
        assert "messagesData" in data
        # The content should be preserved correctly in JSON
        assert data["fileData"]["/test/path.js"]["content"] == content


class TestBuildMsgToUserHtml:
    """Tests for build_msg_to_user_html function."""

    def test_includes_assistant_context(self):
        """Test that assistant text before tool_use is included in tooltip."""
        from claude_code_transcripts import build_msg_to_user_html

        conversations = [
            {
                "user_text": "Create a file",
                "timestamp": "2025-01-01T10:00:00Z",
                "messages": [
                    (
                        "user",
                        '{"content": "Create a file", "role": "user"}',
                        "2025-01-01T10:00:00Z",
                    ),
                    (
                        "assistant",
                        json.dumps(
                            {
                                "content": [
                                    {
                                        "type": "text",
                                        "text": "I'll create that file for you.",
                                    },
                                    {
                                        "type": "tool_use",
                                        "id": "toolu_001",
                                        "name": "Write",
                                        "input": {
                                            "file_path": "/test.py",
                                            "content": "# test",
                                        },
                                    },
                                ],
                                "role": "assistant",
                            }
                        ),
                        "2025-01-01T10:00:05Z",
                    ),
                ],
            }
        ]

        result, context_ids = build_msg_to_user_html(conversations)

        # Should have entry for the assistant message with tool_use
        assert "msg-2025-01-01T10-00-05Z" in result
        html = result["msg-2025-01-01T10-00-05Z"]

        # Should contain user prompt
        assert "Create a file" in html
        # Should contain assistant context
        assert "Assistant context" in html

        # Should have context_msg_id mapping
        assert "msg-2025-01-01T10-00-05Z" in context_ids
        assert "create that file for you" in html

    def test_includes_thinking_block(self):
        """Test that thinking blocks are included in tooltip."""
        from claude_code_transcripts import build_msg_to_user_html

        conversations = [
            {
                "user_text": "Create a file",
                "timestamp": "2025-01-01T10:00:00Z",
                "messages": [
                    (
                        "user",
                        '{"content": "Create a file", "role": "user"}',
                        "2025-01-01T10:00:00Z",
                    ),
                    (
                        "assistant",
                        json.dumps(
                            {
                                "content": [
                                    {
                                        "type": "thinking",
                                        "thinking": "Let me think about this...",
                                    },
                                    {"type": "text", "text": "I'll create that file."},
                                    {
                                        "type": "tool_use",
                                        "id": "toolu_001",
                                        "name": "Write",
                                        "input": {
                                            "file_path": "/test.py",
                                            "content": "# test",
                                        },
                                    },
                                ],
                                "role": "assistant",
                            }
                        ),
                        "2025-01-01T10:00:05Z",
                    ),
                ],
            }
        ]

        result, context_ids = build_msg_to_user_html(conversations)

        html = result["msg-2025-01-01T10-00-05Z"]

        # Should contain thinking block with proper styling inside assistant context
        assert 'class="context-thinking"' in html
        assert "Thinking:" in html
        assert "Let me think about this" in html
        # Should be inside the assistant context section
        assert 'class="tooltip-assistant"' in html

    def test_thinking_persists_across_messages(self):
        """Test that thinking from a previous message is captured for tool calls."""
        from claude_code_transcripts import build_msg_to_user_html

        conversations = [
            {
                "user_text": "Create a file",
                "timestamp": "2025-01-01T10:00:00Z",
                "messages": [
                    (
                        "user",
                        '{"content": "Create a file", "role": "user"}',
                        "2025-01-01T10:00:00Z",
                    ),
                    # First assistant message with thinking and text
                    (
                        "assistant",
                        json.dumps(
                            {
                                "content": [
                                    {
                                        "type": "thinking",
                                        "thinking": "I need to plan this carefully.",
                                    },
                                    {
                                        "type": "text",
                                        "text": "Let me create that file.",
                                    },
                                ],
                                "role": "assistant",
                            }
                        ),
                        "2025-01-01T10:00:05Z",
                    ),
                    # Second assistant message with just tool_use (no thinking in this message)
                    (
                        "assistant",
                        json.dumps(
                            {
                                "content": [
                                    {
                                        "type": "tool_use",
                                        "id": "toolu_001",
                                        "name": "Write",
                                        "input": {
                                            "file_path": "/test.py",
                                            "content": "# test",
                                        },
                                    },
                                ],
                                "role": "assistant",
                            }
                        ),
                        "2025-01-01T10:00:10Z",
                    ),
                ],
            }
        ]

        result, context_ids = build_msg_to_user_html(conversations)

        # The tool_use message should have the thinking from the previous message
        html = result["msg-2025-01-01T10-00-10Z"]

        # Should contain thinking block (persisted from previous message) inside assistant context
        assert 'class="context-thinking"' in html
        assert "plan this carefully" in html
        # Should also have assistant text
        assert "create that file" in html
        # Both should be inside the assistant context section
        assert 'class="tooltip-assistant"' in html

    def test_preserves_block_order_thinking_first(self):
        """Test that blocks are rendered in original order (thinking before text)."""
        from claude_code_transcripts import build_msg_to_user_html

        conversations = [
            {
                "user_text": "Create a file",
                "timestamp": "2025-01-01T10:00:00Z",
                "messages": [
                    (
                        "user",
                        '{"content": "Create a file", "role": "user"}',
                        "2025-01-01T10:00:00Z",
                    ),
                    (
                        "assistant",
                        json.dumps(
                            {
                                "content": [
                                    # Thinking comes FIRST
                                    {
                                        "type": "thinking",
                                        "thinking": "THINKING_MARKER_FIRST",
                                    },
                                    # Then text
                                    {"type": "text", "text": "TEXT_MARKER_SECOND"},
                                    {
                                        "type": "tool_use",
                                        "id": "toolu_001",
                                        "name": "Write",
                                        "input": {
                                            "file_path": "/test.py",
                                            "content": "# test",
                                        },
                                    },
                                ],
                                "role": "assistant",
                            }
                        ),
                        "2025-01-01T10:00:05Z",
                    ),
                ],
            }
        ]

        result, context_ids = build_msg_to_user_html(conversations)
        html = result["msg-2025-01-01T10-00-05Z"]

        # Thinking should appear before text in the HTML
        thinking_pos = html.find("THINKING_MARKER_FIRST")
        text_pos = html.find("TEXT_MARKER_SECOND")

        assert thinking_pos != -1, "Thinking marker not found"
        assert text_pos != -1, "Text marker not found"
        assert thinking_pos < text_pos, "Thinking should come before text"

    def test_preserves_block_order_text_first(self):
        """Test that blocks are rendered in original order (text before thinking)."""
        from claude_code_transcripts import build_msg_to_user_html

        conversations = [
            {
                "user_text": "Create a file",
                "timestamp": "2025-01-01T10:00:00Z",
                "messages": [
                    (
                        "user",
                        '{"content": "Create a file", "role": "user"}',
                        "2025-01-01T10:00:00Z",
                    ),
                    (
                        "assistant",
                        json.dumps(
                            {
                                "content": [
                                    # Text comes FIRST
                                    {"type": "text", "text": "TEXT_MARKER_FIRST"},
                                    # Then thinking
                                    {
                                        "type": "thinking",
                                        "thinking": "THINKING_MARKER_SECOND",
                                    },
                                    {
                                        "type": "tool_use",
                                        "id": "toolu_001",
                                        "name": "Write",
                                        "input": {
                                            "file_path": "/test.py",
                                            "content": "# test",
                                        },
                                    },
                                ],
                                "role": "assistant",
                            }
                        ),
                        "2025-01-01T10:00:05Z",
                    ),
                ],
            }
        ]

        result, context_ids = build_msg_to_user_html(conversations)
        html = result["msg-2025-01-01T10-00-05Z"]

        # Text should appear before thinking in the HTML
        text_pos = html.find("TEXT_MARKER_FIRST")
        thinking_pos = html.find("THINKING_MARKER_SECOND")

        assert text_pos != -1, "Text marker not found"
        assert thinking_pos != -1, "Thinking marker not found"
        assert text_pos < thinking_pos, "Text should come before thinking"

    def test_accumulates_blocks_across_messages(self):
        """Test that thinking and text from separate messages are both included."""
        from claude_code_transcripts import build_msg_to_user_html

        conversations = [
            {
                "user_text": "Create a file",
                "timestamp": "2025-01-01T10:00:00Z",
                "messages": [
                    (
                        "user",
                        '{"content": "Create a file", "role": "user"}',
                        "2025-01-01T10:00:00Z",
                    ),
                    # First message has only thinking (extended thinking scenario)
                    (
                        "assistant",
                        json.dumps(
                            {
                                "content": [
                                    {
                                        "type": "thinking",
                                        "thinking": "THINKING_FROM_FIRST_MESSAGE",
                                    },
                                ],
                                "role": "assistant",
                            }
                        ),
                        "2025-01-01T10:00:02Z",
                    ),
                    # Second message has text + tool_use
                    (
                        "assistant",
                        json.dumps(
                            {
                                "content": [
                                    {
                                        "type": "text",
                                        "text": "TEXT_FROM_SECOND_MESSAGE",
                                    },
                                    {
                                        "type": "tool_use",
                                        "id": "toolu_001",
                                        "name": "Write",
                                        "input": {
                                            "file_path": "/test.py",
                                            "content": "# test",
                                        },
                                    },
                                ],
                                "role": "assistant",
                            }
                        ),
                        "2025-01-01T10:00:05Z",
                    ),
                ],
            }
        ]

        result, context_ids = build_msg_to_user_html(conversations)
        html = result["msg-2025-01-01T10-00-05Z"]

        # Both thinking and text should be present
        assert (
            "THINKING_FROM_FIRST_MESSAGE" in html
        ), "Thinking from first message not found"
        assert "TEXT_FROM_SECOND_MESSAGE" in html, "Text from second message not found"

        # And thinking should come before text (since it was in the earlier message)
        thinking_pos = html.find("THINKING_FROM_FIRST_MESSAGE")
        text_pos = html.find("TEXT_FROM_SECOND_MESSAGE")
        assert thinking_pos < text_pos, "Thinking should come before text"

    def test_only_keeps_most_recent_of_each_block_type(self):
        """Test that only the most recent thinking and text blocks are shown."""
        from claude_code_transcripts import build_msg_to_user_html

        conversations = [
            {
                "user_text": "Create a file",
                "timestamp": "2025-01-01T10:00:00Z",
                "messages": [
                    (
                        "user",
                        '{"content": "Create a file", "role": "user"}',
                        "2025-01-01T10:00:00Z",
                    ),
                    # First thinking block
                    (
                        "assistant",
                        json.dumps(
                            {
                                "content": [
                                    {"type": "thinking", "thinking": "OLD_THINKING"},
                                ],
                                "role": "assistant",
                            }
                        ),
                        "2025-01-01T10:00:01Z",
                    ),
                    # First text block
                    (
                        "assistant",
                        json.dumps(
                            {
                                "content": [
                                    {"type": "text", "text": "OLD_TEXT"},
                                ],
                                "role": "assistant",
                            }
                        ),
                        "2025-01-01T10:00:02Z",
                    ),
                    # Second (newer) thinking block
                    (
                        "assistant",
                        json.dumps(
                            {
                                "content": [
                                    {"type": "thinking", "thinking": "NEW_THINKING"},
                                ],
                                "role": "assistant",
                            }
                        ),
                        "2025-01-01T10:00:03Z",
                    ),
                    # Second (newer) text block + tool_use
                    (
                        "assistant",
                        json.dumps(
                            {
                                "content": [
                                    {"type": "text", "text": "NEW_TEXT"},
                                    {
                                        "type": "tool_use",
                                        "id": "toolu_001",
                                        "name": "Write",
                                        "input": {
                                            "file_path": "/test.py",
                                            "content": "# test",
                                        },
                                    },
                                ],
                                "role": "assistant",
                            }
                        ),
                        "2025-01-01T10:00:05Z",
                    ),
                ],
            }
        ]

        result, context_ids = build_msg_to_user_html(conversations)
        html = result["msg-2025-01-01T10-00-05Z"]

        # Only the NEW (most recent) blocks should be present
        assert "NEW_THINKING" in html, "New thinking not found"
        assert "NEW_TEXT" in html, "New text not found"

        # The OLD blocks should NOT be present
        assert "OLD_THINKING" not in html, "Old thinking should not be present"
        assert "OLD_TEXT" not in html, "Old text should not be present"

    def test_context_msg_id_uses_most_recent_block_message(self):
        """Test that context_msg_id is set to the message containing the most recent block."""
        from claude_code_transcripts import build_msg_to_user_html

        conversations = [
            {
                "user_text": "Create a file",
                "timestamp": "2025-01-01T10:00:00Z",
                "messages": [
                    (
                        "user",
                        '{"content": "Create a file", "role": "user"}',
                        "2025-01-01T10:00:00Z",
                    ),
                    # First message has thinking
                    (
                        "assistant",
                        json.dumps(
                            {
                                "content": [
                                    {"type": "thinking", "thinking": "Thinking..."},
                                ],
                                "role": "assistant",
                            }
                        ),
                        "2025-01-01T10:00:02Z",
                    ),
                    # Second message has text (more recent)
                    (
                        "assistant",
                        json.dumps(
                            {
                                "content": [
                                    {"type": "text", "text": "Creating file..."},
                                ],
                                "role": "assistant",
                            }
                        ),
                        "2025-01-01T10:00:03Z",
                    ),
                    # Third message has tool_use
                    (
                        "assistant",
                        json.dumps(
                            {
                                "content": [
                                    {
                                        "type": "tool_use",
                                        "id": "toolu_001",
                                        "name": "Write",
                                        "input": {
                                            "file_path": "/test.py",
                                            "content": "# test",
                                        },
                                    },
                                ],
                                "role": "assistant",
                            }
                        ),
                        "2025-01-01T10:00:05Z",
                    ),
                ],
            }
        ]

        result, context_ids = build_msg_to_user_html(conversations)

        # The context_msg_id should be the message with the text (most recent block)
        tool_msg_id = "msg-2025-01-01T10-00-05Z"
        text_msg_id = "msg-2025-01-01T10-00-03Z"
        assert tool_msg_id in context_ids
        assert context_ids[tool_msg_id] == text_msg_id

    def test_truncates_long_text(self):
        """Test that long assistant text is truncated."""
        from claude_code_transcripts import build_msg_to_user_html

        long_text = "x" * 1000  # Much longer than 500 char limit

        conversations = [
            {
                "user_text": "Create a file",
                "timestamp": "2025-01-01T10:00:00Z",
                "messages": [
                    (
                        "user",
                        '{"content": "Create a file", "role": "user"}',
                        "2025-01-01T10:00:00Z",
                    ),
                    (
                        "assistant",
                        json.dumps(
                            {
                                "content": [
                                    {"type": "text", "text": long_text},
                                    {
                                        "type": "tool_use",
                                        "id": "toolu_001",
                                        "name": "Write",
                                        "input": {
                                            "file_path": "/test.py",
                                            "content": "# test",
                                        },
                                    },
                                ],
                                "role": "assistant",
                            }
                        ),
                        "2025-01-01T10:00:05Z",
                    ),
                ],
            }
        ]

        result, context_ids = build_msg_to_user_html(conversations)
        html = result["msg-2025-01-01T10-00-05Z"]

        # Should contain ellipsis indicating truncation
        assert "..." in html
        # Should not contain the full 1000 char string
        assert long_text not in html

    def test_first_tool_use_with_no_preceding_context(self):
        """Test first tool_use only shows user prompt when no assistant context exists."""
        from claude_code_transcripts import build_msg_to_user_html

        # First (and only) assistant message has only tool_use, no text/thinking
        conversations = [
            {
                "user_text": "Create a file",
                "timestamp": "2025-01-01T10:00:00Z",
                "messages": [
                    (
                        "assistant",
                        json.dumps(
                            {
                                "content": [
                                    {
                                        "type": "tool_use",
                                        "id": "toolu_001",
                                        "name": "Write",
                                        "input": {
                                            "file_path": "/test.py",
                                            "content": "# test",
                                        },
                                    },
                                ],
                                "role": "assistant",
                            }
                        ),
                        "2025-01-01T10:00:05Z",
                    ),
                ],
            }
        ]

        result, context_ids = build_msg_to_user_html(conversations)
        html = result["msg-2025-01-01T10-00-05Z"]

        # Should still have user prompt
        assert "Create a file" in html
        # Should NOT have assistant context since there's none
        assert "Assistant context" not in html
        assert "Thinking" not in html

    def test_text_after_tool_use_in_same_message(self):
        """Test text that appears after tool_use in same message is still captured."""
        from claude_code_transcripts import build_msg_to_user_html

        # Content order: tool_use THEN text (Claude sometimes comments after acting)
        conversations = [
            {
                "user_text": "Do something",
                "timestamp": "2025-01-01T10:00:00Z",
                "messages": [
                    (
                        "assistant",
                        json.dumps(
                            {
                                "content": [
                                    {
                                        "type": "tool_use",
                                        "id": "toolu_001",
                                        "name": "Read",
                                        "input": {"file_path": "/test.py"},
                                    },
                                    {
                                        "type": "text",
                                        "text": "Now I can see the differences...",
                                    },
                                ],
                                "role": "assistant",
                            }
                        ),
                        "2025-01-01T10:00:05Z",
                    ),
                ],
            }
        ]

        result, context_ids = build_msg_to_user_html(conversations)
        html = result["msg-2025-01-01T10-00-05Z"]

        # Should contain the text that came after tool_use
        assert "Now I can see the differences" in html
        assert "Assistant context" in html

    def test_text_in_later_message_not_included(self):
        """Test that text from a later message is NOT included (by design).

        When tool_use happens first and text comes in a subsequent message,
        the tooltip only shows context that preceded the tool_use.
        """
        from claude_code_transcripts import build_msg_to_user_html

        # First message: tool_use only
        # Second message: text (comes after, so not included for first tool)
        conversations = [
            {
                "user_text": "Do something",
                "timestamp": "2025-01-01T10:00:00Z",
                "messages": [
                    (
                        "assistant",
                        json.dumps(
                            {
                                "content": [
                                    {
                                        "type": "tool_use",
                                        "id": "toolu_001",
                                        "name": "Read",
                                        "input": {"file_path": "/test.py"},
                                    },
                                ],
                                "role": "assistant",
                            }
                        ),
                        "2025-01-01T10:00:05Z",
                    ),
                    (
                        "assistant",
                        json.dumps(
                            {
                                "content": [
                                    {
                                        "type": "text",
                                        "text": "Now I can see the differences...",
                                    },
                                ],
                                "role": "assistant",
                            }
                        ),
                        "2025-01-01T10:00:10Z",
                    ),
                ],
            }
        ]

        result, context_ids = build_msg_to_user_html(conversations)
        html = result["msg-2025-01-01T10-00-05Z"]

        # Text from later message should NOT be included
        assert "Now I can see the differences" not in html
        # But user prompt should still be there
        assert "Do something" in html

    def test_strips_code_blocks_from_tooltip(self):
        """Test that code blocks are stripped from tooltip content to avoid HTML injection."""
        from claude_code_transcripts import build_msg_to_user_html

        # Content with code block containing HTML that could cause issues
        text_with_code = """Let me analyze this:

```html
<div id="prompts-modal" class="prompts-modal">
  <button type="button" class="btn">Close</button>
</div>
```

The implementation looks correct."""

        conversations = [
            {
                "user_text": "Do something",
                "timestamp": "2025-01-01T10:00:00Z",
                "messages": [
                    (
                        "assistant",
                        json.dumps(
                            {
                                "content": [
                                    {"type": "text", "text": text_with_code},
                                    {
                                        "type": "tool_use",
                                        "id": "toolu_001",
                                        "name": "Read",
                                        "input": {"file_path": "/test.py"},
                                    },
                                ],
                                "role": "assistant",
                            }
                        ),
                        "2025-01-01T10:00:05Z",
                    ),
                ],
            }
        ]

        result, context_ids = build_msg_to_user_html(conversations)
        html = result["msg-2025-01-01T10-00-05Z"]

        # Code block should be replaced with placeholder, not rendered as HTML
        assert "<div id=" not in html
        assert "<button" not in html
        # But the surrounding text should still be there
        assert "Let me analyze this" in html
        assert "implementation looks correct" in html
        # Code block should be replaced with placeholder
        assert "[code block]" in html
        # Should show truncation indicator since code block was stripped
        assert "(truncated)" in html
