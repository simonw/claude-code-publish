"""Code viewer functionality for Claude Code transcripts.

This module handles the three-pane code viewer with git-based blame annotations.
"""

import html
import json
import os
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Tuple, Dict, Any, Set

from git import Repo
from git.exc import InvalidGitRepositoryError


# ============================================================================
# Helper Functions
# ============================================================================


def group_operations_by_file(
    operations: List["FileOperation"],
) -> Dict[str, List["FileOperation"]]:
    """Group operations by file path and sort each group by timestamp.

    Args:
        operations: List of FileOperation objects.

    Returns:
        Dict mapping file paths to lists of FileOperation objects, sorted by timestamp.
    """
    file_ops: Dict[str, List["FileOperation"]] = {}
    for op in operations:
        if op.file_path not in file_ops:
            file_ops[op.file_path] = []
        file_ops[op.file_path].append(op)

    # Sort each file's operations by timestamp
    for ops in file_ops.values():
        ops.sort(key=lambda o: o.timestamp)

    return file_ops


def read_blob_content(tree, file_path: str) -> Optional[str]:
    """Read file content from a git tree/commit as string.

    Args:
        tree: Git tree object (e.g., commit.tree).
        file_path: Relative path to the file within the repo.

    Returns:
        File content as string, or None if not found.
    """
    try:
        blob = tree / file_path
        return blob.data_stream.read().decode("utf-8")
    except (KeyError, TypeError, ValueError):
        return None


def read_blob_bytes(tree, file_path: str) -> Optional[bytes]:
    """Read file content from a git tree/commit as bytes.

    Args:
        tree: Git tree object (e.g., commit.tree).
        file_path: Relative path to the file within the repo.

    Returns:
        File content as bytes, or None if not found.
    """
    try:
        blob = tree / file_path
        return blob.data_stream.read()
    except (KeyError, TypeError, ValueError):
        return None


def parse_iso_timestamp(timestamp: str) -> Optional[datetime]:
    """Parse ISO timestamp string to datetime with UTC timezone.

    Handles 'Z' suffix by converting to '+00:00' format.

    Args:
        timestamp: ISO format timestamp (e.g., "2025-12-27T16:12:36.904Z").

    Returns:
        datetime object, or None on parse failure.
    """
    try:
        ts = timestamp.replace("Z", "+00:00")
        return datetime.fromisoformat(ts)
    except ValueError:
        return None


# ============================================================================
# Constants
# ============================================================================

# Operation types for file operations
OP_WRITE = "write"
OP_EDIT = "edit"
OP_DELETE = "delete"

# File status for tree display
STATUS_ADDED = "added"
STATUS_MODIFIED = "modified"

# Regex patterns for rm commands
# Matches: rm, rm -f, rm -r, rm -rf, rm -fr, etc.
RM_COMMAND_PATTERN = re.compile(r"^\s*rm\s+(?:-[rfivI]+\s+)*(.+)$")


# ============================================================================
# Data Structures
# ============================================================================


@dataclass
class FileOperation:
    """Represents a single Write or Edit operation on a file."""

    file_path: str
    operation_type: str  # "write" or "edit"
    tool_id: str  # tool_use.id for linking
    timestamp: str
    page_num: int  # which page this operation appears on
    msg_id: str  # anchor ID in the HTML page

    # For Write operations
    content: Optional[str] = None

    # For Edit operations
    old_string: Optional[str] = None
    new_string: Optional[str] = None
    replace_all: bool = False

    # Original file content from tool result (for Edit operations)
    # This allows reconstruction without local file access
    original_content: Optional[str] = None


@dataclass
class FileState:
    """Represents the reconstructed state of a file with blame annotations."""

    file_path: str
    operations: List[FileOperation] = field(default_factory=list)

    # If we have a git repo, we can reconstruct full content
    initial_content: Optional[str] = None  # From git or first Write
    final_content: Optional[str] = None  # Reconstructed content

    # Blame data: list of (line_text, FileOperation or None)
    # None means the line came from initial_content (pre-session)
    blame_lines: List[Tuple[str, Optional[FileOperation]]] = field(default_factory=list)

    # For diff-only mode when no repo is available
    diff_only: bool = False

    # File status: "added" (first op is Write), "modified" (first op is Edit)
    status: str = "modified"


@dataclass
class CodeViewData:
    """All data needed to render the code viewer."""

    files: Dict[str, FileState] = field(default_factory=dict)  # file_path -> FileState
    file_tree: Dict[str, Any] = field(default_factory=dict)  # Nested dict for file tree
    mode: str = "diff_only"  # "full" or "diff_only"
    repo_path: Optional[str] = None
    session_cwd: Optional[str] = None


@dataclass
class BlameRange:
    """A range of consecutive lines from the same operation."""

    start_line: int  # 1-indexed
    end_line: int  # 1-indexed, inclusive
    tool_id: Optional[str]
    page_num: int
    msg_id: str
    operation_type: str  # "write" or "edit"
    timestamp: str


# ============================================================================
# Code Viewer Functions
# ============================================================================


def extract_deleted_paths_from_bash(command: str) -> List[str]:
    """Extract file paths deleted by an rm command.

    Handles various rm forms:
    - rm file.py
    - rm -f file.py
    - rm -rf /path/to/dir
    - rm "file with spaces.py"
    - rm 'file.py'

    Args:
        command: The bash command string.

    Returns:
        List of file paths that would be deleted by this command.
    """
    paths = []

    # Check if this is an rm command
    match = RM_COMMAND_PATTERN.match(command)
    if not match:
        return paths

    # Get the path arguments part
    args_str = match.group(1).strip()

    # Parse paths - handle quoted and unquoted paths
    # Simple approach: split on spaces but respect quotes
    current_path = ""
    in_quotes = None
    i = 0

    while i < len(args_str):
        char = args_str[i]

        if in_quotes:
            if char == in_quotes:
                # End of quoted string
                if current_path:
                    paths.append(current_path)
                    current_path = ""
                in_quotes = None
            else:
                current_path += char
        elif char in ('"', "'"):
            # Start of quoted string
            in_quotes = char
        elif char == " ":
            # Space outside quotes - end of path
            if current_path:
                paths.append(current_path)
                current_path = ""
        else:
            current_path += char

        i += 1

    # Don't forget the last path if not quoted
    if current_path:
        paths.append(current_path)

    return paths


def extract_file_operations(
    loglines: List[Dict],
    conversations: List[Dict],
    prompts_per_page: int = 5,
) -> List[FileOperation]:
    """Extract all Write, Edit, and Delete operations from session loglines.

    Delete operations are extracted from Bash rm commands. Files that are
    ultimately deleted will be filtered out when the operations are replayed
    in the git repo (deleted files won't exist in the final state).

    Args:
        loglines: List of parsed logline entries from the session.
        conversations: List of conversation dicts with page mapping info.
        prompts_per_page: Number of prompts per page for pagination.

    Returns:
        List of FileOperation objects sorted by timestamp.
    """
    operations = []

    # Build a mapping from message content to page number and message ID
    # We need to track which page each operation appears on
    msg_to_page = {}
    for conv_idx, conv in enumerate(conversations):
        page_num = (conv_idx // prompts_per_page) + 1
        for msg_idx, (log_type, message_json, timestamp) in enumerate(
            conv.get("messages", [])
        ):
            # Generate a unique ID matching the HTML message IDs
            msg_id = f"msg-{timestamp.replace(':', '-').replace('.', '-')}"
            # Store timestamp -> (page_num, msg_id) mapping
            msg_to_page[timestamp] = (page_num, msg_id)

    # First pass: collect originalFile content from tool results
    # These are stored in the toolUseResult field of user messages
    tool_id_to_original = {}
    for entry in loglines:
        tool_use_result = entry.get("toolUseResult", {})
        if tool_use_result and "originalFile" in tool_use_result:
            # Find the matching tool_use_id from the message content
            message = entry.get("message", {})
            content = message.get("content", [])
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_result":
                        tool_use_id = block.get("tool_use_id", "")
                        if tool_use_id:
                            tool_id_to_original[tool_use_id] = tool_use_result.get(
                                "originalFile"
                            )

    for entry in loglines:
        timestamp = entry.get("timestamp", "")
        message = entry.get("message", {})
        content = message.get("content", [])

        if not isinstance(content, list):
            continue

        for block in content:
            if not isinstance(block, dict):
                continue

            if block.get("type") != "tool_use":
                continue

            tool_name = block.get("name", "")
            tool_id = block.get("id", "")
            tool_input = block.get("input", {})

            # Get page and message ID from our mapping
            fallback_msg_id = f"msg-{timestamp.replace(':', '-').replace('.', '-')}"
            page_num, msg_id = msg_to_page.get(timestamp, (1, fallback_msg_id))

            if tool_name == "Write":
                file_path = tool_input.get("file_path", "")
                file_content = tool_input.get("content", "")

                if file_path:
                    operations.append(
                        FileOperation(
                            file_path=file_path,
                            operation_type=OP_WRITE,
                            tool_id=tool_id,
                            timestamp=timestamp,
                            page_num=page_num,
                            msg_id=msg_id,
                            content=file_content,
                        )
                    )

            elif tool_name == "Edit":
                file_path = tool_input.get("file_path", "")
                old_string = tool_input.get("old_string", "")
                new_string = tool_input.get("new_string", "")
                replace_all = tool_input.get("replace_all", False)

                if file_path and old_string is not None and new_string is not None:
                    # Get original file content if available from tool result
                    original_content = tool_id_to_original.get(tool_id)

                    operations.append(
                        FileOperation(
                            file_path=file_path,
                            operation_type=OP_EDIT,
                            tool_id=tool_id,
                            timestamp=timestamp,
                            page_num=page_num,
                            msg_id=msg_id,
                            old_string=old_string,
                            new_string=new_string,
                            replace_all=replace_all,
                            original_content=original_content,
                        )
                    )

            elif tool_name == "Bash":
                # Extract delete operations from rm commands
                command = tool_input.get("command", "")
                deleted_paths = extract_deleted_paths_from_bash(command)
                is_recursive = "-r" in command

                for path in deleted_paths:
                    operations.append(
                        FileOperation(
                            file_path=path,
                            operation_type=OP_DELETE,
                            tool_id=tool_id,
                            timestamp=timestamp,
                            page_num=page_num,
                            msg_id=msg_id,
                            # Store whether this is a recursive delete (directory)
                            # We reuse replace_all field for this purpose
                            replace_all=is_recursive,
                        )
                    )

    # Sort by timestamp
    operations.sort(key=lambda op: op.timestamp)

    return operations


def filter_deleted_files(operations: List[FileOperation]) -> List[FileOperation]:
    """Filter out operations for files that no longer exist on disk.

    This is used with the --exclude-deleted-files flag to filter out files
    that were modified during the session but have since been deleted
    (outside of the session or by commands we didn't detect).

    Only checks absolute paths - relative paths are left as-is since we can't
    reliably determine where they are.

    Args:
        operations: List of FileOperation objects.

    Returns:
        Filtered list excluding operations for files that don't exist.
    """
    if not operations:
        return operations

    # Get unique file paths from Write/Edit operations (not Delete)
    file_paths = set(
        op.file_path for op in operations if op.operation_type in (OP_WRITE, OP_EDIT)
    )

    # Check which files exist (only for absolute paths)
    missing_files: Set[str] = set()
    for file_path in file_paths:
        if os.path.isabs(file_path) and not os.path.exists(file_path):
            missing_files.add(file_path)

    if not missing_files:
        return operations

    # Filter out operations for missing files
    return [op for op in operations if op.file_path not in missing_files]


def normalize_file_paths(operations: List[FileOperation]) -> Tuple[str, Dict[str, str]]:
    """Find common prefix in file paths and create normalized relative paths.

    Args:
        operations: List of FileOperation objects.

    Returns:
        Tuple of (common_prefix, path_mapping) where path_mapping maps
        original absolute paths to normalized relative paths.
    """
    if not operations:
        return "", {}

    # Get all unique file paths
    file_paths = list(set(op.file_path for op in operations))

    if len(file_paths) == 1:
        # Single file - use its parent as prefix
        path = Path(file_paths[0])
        prefix = str(path.parent)
        return prefix, {file_paths[0]: path.name}

    # Find common prefix
    common = os.path.commonpath(file_paths)
    # Make sure we're at a directory boundary
    if not os.path.isdir(common):
        common = os.path.dirname(common)

    # Create mapping
    path_mapping = {}
    for fp in file_paths:
        rel_path = os.path.relpath(fp, common)
        path_mapping[fp] = rel_path

    return common, path_mapping


def find_git_repo_root(start_path: str) -> Optional[Path]:
    """Walk up from start_path to find a git repository root.

    Args:
        start_path: Directory path to start searching from.

    Returns:
        Path to the git repo root, or None if not found.
    """
    current = Path(start_path)
    while current != current.parent:
        if (current / ".git").exists():
            return current
        current = current.parent
    return None


def find_commit_before_timestamp(file_repo: Repo, timestamp: str) -> Optional[Any]:
    """Find the most recent commit before the given ISO timestamp.

    Args:
        file_repo: GitPython Repo object.
        timestamp: ISO format timestamp (e.g., "2025-12-27T16:12:36.904Z").

    Returns:
        Git commit object, or None if not found.
    """
    target_dt = parse_iso_timestamp(timestamp)
    if target_dt is None:
        return None

    # Search through commits to find one before the target time
    try:
        for commit in file_repo.iter_commits():
            commit_dt = datetime.fromtimestamp(
                commit.committed_date, tz=target_dt.tzinfo
            )
            if commit_dt < target_dt:
                return commit
    except Exception:
        pass

    return None


def get_commits_during_session(
    file_repo: Repo, start_timestamp: str, end_timestamp: str
) -> List[Any]:
    """Get all commits that happened during the session timeframe.

    Args:
        file_repo: GitPython Repo object.
        start_timestamp: ISO format timestamp for session start.
        end_timestamp: ISO format timestamp for session end.

    Returns:
        List of commit objects in chronological order (oldest first).
    """
    from datetime import timezone

    start_dt = parse_iso_timestamp(start_timestamp)
    end_dt = parse_iso_timestamp(end_timestamp)
    if start_dt is None or end_dt is None:
        return []

    commits = []

    try:
        for commit in file_repo.iter_commits():
            commit_dt = datetime.fromtimestamp(commit.committed_date, tz=timezone.utc)

            # Skip commits after session end
            if commit_dt > end_dt:
                continue

            # Stop when we reach commits before session start
            if commit_dt < start_dt:
                break

            commits.append(commit)

    except Exception:
        pass

    # Return in chronological order (oldest first)
    return list(reversed(commits))


def find_file_content_at_timestamp(
    file_repo: Repo, file_rel_path: str, timestamp: str, session_commits: List[Any]
) -> Optional[str]:
    """Find the file content from the most recent commit at or before the timestamp.

    Args:
        file_repo: GitPython Repo object.
        file_rel_path: Relative path to the file within the repo.
        timestamp: ISO format timestamp to search for.
        session_commits: List of commits during the session (chronological order).

    Returns:
        File content as string, or None if not found.
    """
    from datetime import timezone

    target_dt = parse_iso_timestamp(timestamp)
    if target_dt is None:
        return None

    try:
        # Find the most recent commit at or before the target timestamp
        best_commit = None
        for commit in session_commits:
            commit_dt = datetime.fromtimestamp(commit.committed_date, tz=timezone.utc)
            if commit_dt <= target_dt:
                best_commit = commit
            else:
                break  # Commits are chronological, so we can stop

        if best_commit:
            content = read_blob_content(best_commit.tree, file_rel_path)
            if content is not None:
                return content

    except Exception:
        pass

    return None


def _init_temp_repo() -> Tuple[Repo, Path]:
    """Create and configure a temporary git repository.

    Returns:
        Tuple of (repo, temp_dir).
    """
    temp_dir = Path(tempfile.mkdtemp(prefix="claude-session-"))
    repo = Repo.init(temp_dir)

    with repo.config_writer() as config:
        config.set_value("user", "name", "Claude")
        config.set_value("user", "email", "claude@session")

    return repo, temp_dir


def _find_actual_repo_context(
    sorted_ops: List[FileOperation], session_start: str, session_end: str
) -> Tuple[Optional[Repo], Optional[Path], List[Any]]:
    """Find the actual git repo and session commits from operation file paths.

    Args:
        sorted_ops: List of operations sorted by timestamp.
        session_start: ISO timestamp of first operation.
        session_end: ISO timestamp of last operation.

    Returns:
        Tuple of (actual_repo, actual_repo_root, session_commits).
    """
    for op in sorted_ops:
        repo_root = find_git_repo_root(str(Path(op.file_path).parent))
        if repo_root:
            try:
                actual_repo = Repo(repo_root)
                session_commits = get_commits_during_session(
                    actual_repo, session_start, session_end
                )
                return actual_repo, repo_root, session_commits
            except InvalidGitRepositoryError:
                pass
    return None, None, []


def _fetch_initial_content(
    op: FileOperation,
    full_path: Path,
    earliest_op_by_file: Dict[str, str],
) -> bool:
    """Fetch initial file content using fallback chain.

    Priority: pre-session git commit > HEAD > disk > original_content

    Args:
        op: The edit operation needing initial content.
        full_path: Path where content should be written.
        earliest_op_by_file: Map of file path to earliest operation timestamp.

    Returns:
        True if content was fetched successfully.
    """
    # Try to find a git repo for this file
    file_repo_root = find_git_repo_root(str(Path(op.file_path).parent))
    if file_repo_root:
        try:
            file_repo = Repo(file_repo_root)
            file_rel_path = os.path.relpath(op.file_path, file_repo_root)

            # Find commit from before the session started for this file
            earliest_ts = earliest_op_by_file.get(op.file_path, op.timestamp)
            pre_session_commit = find_commit_before_timestamp(file_repo, earliest_ts)

            if pre_session_commit:
                content = read_blob_bytes(pre_session_commit.tree, file_rel_path)
                if content is not None:
                    full_path.write_bytes(content)
                    return True

            # Fallback to HEAD (file might be new)
            content = read_blob_bytes(file_repo.head.commit.tree, file_rel_path)
            if content is not None:
                full_path.write_bytes(content)
                return True
        except InvalidGitRepositoryError:
            pass

    # Fallback: read from disk if file exists
    if Path(op.file_path).exists():
        try:
            full_path.write_text(Path(op.file_path).read_text())
            return True
        except Exception:
            pass

    # Fallback: use original_content from tool result (for remote sessions)
    if op.original_content:
        full_path.write_text(op.original_content)
        return True

    return False


def build_file_history_repo(
    operations: List[FileOperation],
) -> Tuple[Repo, Path, Dict[str, str]]:
    """Create a temp git repo that replays all file operations as commits.

    For Edit operations, uses intermediate commits from the actual repo to
    resync state when our reconstruction might have diverged from reality.
    This handles cases where edits fail to match our reconstructed content
    but succeeded on the actual file.

    Args:
        operations: List of FileOperation objects in chronological order.

    Returns:
        Tuple of (repo, temp_dir, path_mapping) where:
        - repo: GitPython Repo object
        - temp_dir: Path to the temp directory
        - path_mapping: Dict mapping original paths to relative paths
    """
    repo, temp_dir = _init_temp_repo()

    # Get path mapping - exclude delete operations since they don't contribute files
    # and may have relative paths that would break os.path.commonpath()
    non_delete_ops = [op for op in operations if op.operation_type != OP_DELETE]
    common_prefix, path_mapping = normalize_file_paths(non_delete_ops)

    # Sort operations by timestamp
    sorted_ops = sorted(operations, key=lambda o: o.timestamp)

    if not sorted_ops:
        return repo, temp_dir, path_mapping

    # Get session timeframe
    session_start = sorted_ops[0].timestamp
    session_end = sorted_ops[-1].timestamp

    # Build a map of file path -> earliest operation timestamp
    earliest_op_by_file: Dict[str, str] = {}
    for op in sorted_ops:
        if op.file_path not in earliest_op_by_file:
            earliest_op_by_file[op.file_path] = op.timestamp

    # Try to find the actual git repo and get commits during the session
    actual_repo, actual_repo_root, session_commits = _find_actual_repo_context(
        sorted_ops, session_start, session_end
    )

    for op in sorted_ops:
        # Delete operations aren't in path_mapping - handle them specially
        if op.operation_type == OP_DELETE:
            rel_path = None  # Will find matching files below
            full_path = None
        else:
            rel_path = path_mapping.get(op.file_path, op.file_path)
            full_path = temp_dir / rel_path
            full_path.parent.mkdir(parents=True, exist_ok=True)

        # For edit operations, try to sync from commits when our reconstruction diverges
        if op.operation_type == OP_EDIT and actual_repo and actual_repo_root:
            file_rel_path = os.path.relpath(op.file_path, actual_repo_root)
            old_str = op.old_string or ""

            if old_str and full_path.exists():
                our_content = full_path.read_text()

                # If old_string doesn't match our content, we may have diverged
                if old_str not in our_content:
                    # Try to find content where old_string DOES exist
                    # First, check intermediate commits during the session
                    commit_content = find_file_content_at_timestamp(
                        actual_repo, file_rel_path, op.timestamp, session_commits
                    )

                    if commit_content and old_str in commit_content:
                        # Resync from this commit
                        full_path.write_text(commit_content)
                        repo.index.add([rel_path])
                        repo.index.commit("{}")  # Sync commit
                    else:
                        # Try HEAD - the final state should be correct
                        head_content = read_blob_content(
                            actual_repo.head.commit.tree, file_rel_path
                        )
                        if head_content and old_str in head_content:
                            # Resync from HEAD
                            full_path.write_text(head_content)
                            repo.index.add([rel_path])
                            repo.index.commit("{}")  # Sync commit

        if op.operation_type == OP_WRITE:
            full_path.write_text(op.content or "")
        elif op.operation_type == OP_EDIT:
            # If file doesn't exist, try to fetch initial content
            if not full_path.exists():
                fetched = _fetch_initial_content(op, full_path, earliest_op_by_file)

                # Commit the initial content first (no metadata = pre-session)
                # This allows git blame to correctly attribute unchanged lines
                if fetched:
                    repo.index.add([rel_path])
                    repo.index.commit("{}")  # Empty metadata = pre-session content

            if full_path.exists():
                content = full_path.read_text()
                old_str = op.old_string or ""

                # If old_string doesn't match, try to resync from original_content
                # This handles remote sessions where we can't access the actual repo
                if old_str and old_str not in content and op.original_content:
                    if old_str in op.original_content:
                        # Resync from original_content before applying this edit
                        content = op.original_content
                        full_path.write_text(content)
                        repo.index.add([rel_path])
                        repo.index.commit("{}")  # Sync commit

                if op.replace_all:
                    content = content.replace(old_str, op.new_string or "")
                else:
                    content = content.replace(old_str, op.new_string or "", 1)
                full_path.write_text(content)
            else:
                # Can't apply edit - file doesn't exist
                continue
        elif op.operation_type == OP_DELETE:
            # Delete operation - remove file or directory contents
            # op.replace_all is True for recursive deletes (rm -r)
            is_recursive = op.replace_all
            delete_path = op.file_path

            # Find files to delete by matching original paths against path_mapping
            # Delete paths may be absolute or relative, and may not be in the mapping
            files_to_remove = []

            if is_recursive:
                # Delete all files whose original path starts with delete_path
                delete_prefix = delete_path.rstrip("/") + "/"
                for orig_path, mapped_rel_path in path_mapping.items():
                    # Check if original path starts with delete prefix or equals delete path
                    if orig_path.startswith(delete_prefix) or orig_path == delete_path:
                        file_abs = temp_dir / mapped_rel_path
                        if file_abs.exists():
                            files_to_remove.append((file_abs, mapped_rel_path))
            else:
                # Single file delete - find by exact original path match
                if delete_path in path_mapping:
                    mapped_rel_path = path_mapping[delete_path]
                    file_abs = temp_dir / mapped_rel_path
                    if file_abs.exists():
                        files_to_remove.append((file_abs, mapped_rel_path))

            if files_to_remove:
                for file_abs, file_rel in files_to_remove:
                    file_abs.unlink()
                    try:
                        repo.index.remove([file_rel])
                    except Exception:
                        pass  # File might not be tracked

                # Commit the deletion
                try:
                    repo.index.commit("{}")  # Delete commit
                except Exception:
                    pass  # Nothing to commit if no files were tracked

            continue  # Skip the normal commit below

        # Stage and commit with metadata
        repo.index.add([rel_path])
        metadata = json.dumps(
            {
                "tool_id": op.tool_id,
                "page_num": op.page_num,
                "msg_id": op.msg_id,
                "timestamp": op.timestamp,
                "operation_type": op.operation_type,
                "file_path": op.file_path,
            }
        )
        repo.index.commit(metadata)

    # Note: We intentionally skip final sync here to preserve blame attribution.
    # The displayed content may not exactly match HEAD, but blame tracking
    # of which operations modified which lines is more important for the
    # code viewer's purpose.

    return repo, temp_dir, path_mapping


def get_file_blame_ranges(repo: Repo, file_path: str) -> List[BlameRange]:
    """Get blame data for a file, grouped into ranges of consecutive lines.

    Args:
        repo: GitPython Repo object.
        file_path: Relative path to the file within the repo.

    Returns:
        List of BlameRange objects, each representing consecutive lines
        from the same operation.
    """
    try:
        blame_data = repo.blame("HEAD", file_path)
    except Exception:
        return []

    ranges = []
    current_line = 1

    for commit, lines in blame_data:
        if not lines:
            continue

        # Parse metadata from commit message
        try:
            metadata = json.loads(commit.message)
        except json.JSONDecodeError:
            metadata = {}

        start_line = current_line
        end_line = current_line + len(lines) - 1

        ranges.append(
            BlameRange(
                start_line=start_line,
                end_line=end_line,
                tool_id=metadata.get("tool_id"),
                page_num=metadata.get("page_num", 1),
                msg_id=metadata.get("msg_id", ""),
                operation_type=metadata.get("operation_type", "unknown"),
                timestamp=metadata.get("timestamp", ""),
            )
        )

        current_line = end_line + 1

    return ranges


def get_file_content_from_repo(repo: Repo, file_path: str) -> Optional[str]:
    """Get the final content of a file from the repo.

    Args:
        repo: GitPython Repo object.
        file_path: Relative path to the file within the repo.

    Returns:
        File content as string, or None if file doesn't exist.
    """
    try:
        return read_blob_content(repo.head.commit.tree, file_path)
    except ValueError:
        # ValueError occurs when repo has no commits yet
        return None


def build_file_tree(file_states: Dict[str, FileState]) -> Dict[str, Any]:
    """Build a nested dict structure for file tree UI.

    Common directory prefixes shared by all files are stripped to keep the
    tree compact.

    Args:
        file_states: Dict mapping file paths to FileState objects.

    Returns:
        Nested dict where keys are path components and leaves are FileState objects.
    """
    if not file_states:
        return {}

    # Split all paths into parts
    all_parts = [Path(fp).parts for fp in file_states.keys()]

    # Find the common prefix (directory components shared by all files)
    # We want to strip directories, not filename components
    common_prefix_len = 0
    if all_parts:
        # Find minimum path depth (excluding filename)
        min_dir_depth = min(len(parts) - 1 for parts in all_parts)

        for i in range(min_dir_depth):
            # Check if all paths have the same component at position i
            first_part = all_parts[0][i]
            if all(parts[i] == first_part for parts in all_parts):
                common_prefix_len = i + 1
            else:
                break

    tree: Dict[str, Any] = {}

    for file_path, file_state in file_states.items():
        # Normalize path and split into components
        parts = Path(file_path).parts

        # Strip common prefix
        parts = parts[common_prefix_len:]

        # Navigate/create the nested structure
        current = tree
        for i, part in enumerate(parts[:-1]):  # All but the last part (directories)
            if part not in current:
                current[part] = {}
            current = current[part]

        # Add the file (last part)
        if parts:
            current[parts[-1]] = file_state

    return tree


def reconstruct_file_with_blame(
    initial_content: Optional[str],
    operations: List[FileOperation],
) -> Tuple[str, List[Tuple[str, Optional[FileOperation]]]]:
    """Reconstruct a file's final state with blame attribution for each line.

    Applies all operations in order and tracks which operation wrote each line.

    Args:
        initial_content: The initial file content (from git), or None if new file.
        operations: List of FileOperation objects in chronological order.

    Returns:
        Tuple of (final_content, blame_lines):
        - final_content: The reconstructed file content as a string
        - blame_lines: List of (line_text, operation) tuples, where operation
          is None for lines from initial_content (pre-session)
    """
    # Initialize with initial content
    if initial_content:
        lines = initial_content.rstrip("\n").split("\n")
        blame_lines: List[Tuple[str, Optional[FileOperation]]] = [
            (line, None) for line in lines
        ]
    else:
        blame_lines = []

    # Apply each operation
    for op in operations:
        if op.operation_type == OP_WRITE:
            # Write replaces all content
            if op.content:
                new_lines = op.content.rstrip("\n").split("\n")
                blame_lines = [(line, op) for line in new_lines]

        elif op.operation_type == OP_EDIT:
            if op.old_string is None or op.new_string is None:
                continue

            # Reconstruct current content for searching
            current_content = "\n".join(line for line, _ in blame_lines)

            # Find where old_string occurs
            pos = current_content.find(op.old_string)
            if pos == -1:
                # old_string not found, skip this operation
                continue

            # Calculate line numbers for the replacement
            prefix = current_content[:pos]
            prefix_lines = prefix.count("\n")
            old_lines_count = op.old_string.count("\n") + 1

            # Build new blame_lines
            new_blame_lines = []

            # Add lines before the edit (keep their original blame)
            for i, (line, attr) in enumerate(blame_lines):
                if i < prefix_lines:
                    new_blame_lines.append((line, attr))

            # Handle partial first line replacement
            if prefix_lines < len(blame_lines):
                first_affected_line = blame_lines[prefix_lines][0]
                # Check if the prefix ends mid-line
                last_newline = prefix.rfind("\n")
                if last_newline == -1:
                    prefix_in_line = prefix
                else:
                    prefix_in_line = prefix[last_newline + 1 :]

                # Build the new content by doing the actual replacement
                new_content = (
                    current_content[:pos]
                    + op.new_string
                    + current_content[pos + len(op.old_string) :]
                )
                new_content_lines = new_content.rstrip("\n").split("\n")

                # All lines from the edit point onward get the new attribution
                for i, line in enumerate(new_content_lines):
                    if i < prefix_lines:
                        continue
                    new_blame_lines.append((line, op))

            blame_lines = new_blame_lines

    # Build final content
    final_content = "\n".join(line for line, _ in blame_lines)
    if final_content:
        final_content += "\n"

    return final_content, blame_lines


def build_file_states(
    operations: List[FileOperation],
) -> Dict[str, FileState]:
    """Build FileState objects from a list of file operations.

    Args:
        operations: List of FileOperation objects.

    Returns:
        Dict mapping file paths to FileState objects.
    """
    # Group operations by file (already sorted by timestamp)
    file_ops = group_operations_by_file(operations)

    file_states = {}
    for file_path, ops in file_ops.items():

        # Determine status based on first operation
        status = STATUS_ADDED if ops[0].operation_type == OP_WRITE else STATUS_MODIFIED

        file_state = FileState(
            file_path=file_path,
            operations=ops,
            diff_only=True,  # Default to diff-only
            status=status,
        )

        # If first operation is a Write (file creation), we can show full content
        if ops[0].operation_type == OP_WRITE:
            final_content, blame_lines = reconstruct_file_with_blame(None, ops)
            file_state.final_content = final_content
            file_state.blame_lines = blame_lines
            file_state.diff_only = False

        file_states[file_path] = file_state

    return file_states


def render_file_tree_html(file_tree: Dict[str, Any], prefix: str = "") -> str:
    """Render file tree as HTML.

    Args:
        file_tree: Nested dict structure from build_file_tree().
        prefix: Path prefix for building full paths.

    Returns:
        HTML string for the file tree.
    """
    html_parts = []

    # Sort items: directories first, then files
    items = sorted(
        file_tree.items(),
        key=lambda x: (
            not isinstance(x[1], dict) or isinstance(x[1], FileState),
            x[0].lower(),
        ),
    )

    for name, value in items:
        full_path = f"{prefix}/{name}" if prefix else name

        if isinstance(value, FileState):
            # It's a file - status shown via CSS color
            status_class = f"status-{value.status}"
            html_parts.append(
                f'<li class="tree-file {status_class}" data-path="{html.escape(value.file_path)}">'
                f'<span class="tree-file-name">{html.escape(name)}</span>'
                f"</li>"
            )
        elif isinstance(value, dict):
            # It's a directory
            children_html = render_file_tree_html(value, full_path)
            html_parts.append(
                f'<li class="tree-dir open">'
                f'<span class="tree-toggle"></span>'
                f'<span class="tree-dir-name">{html.escape(name)}</span>'
                f'<ul class="tree-children">{children_html}</ul>'
                f"</li>"
            )

    return "".join(html_parts)


def file_state_to_dict(file_state: FileState) -> Dict[str, Any]:
    """Convert FileState to a JSON-serializable dict.

    Args:
        file_state: The FileState object.

    Returns:
        Dict suitable for JSON serialization.
    """
    operations = [
        {
            "operation_type": op.operation_type,
            "tool_id": op.tool_id,
            "timestamp": op.timestamp,
            "page_num": op.page_num,
            "msg_id": op.msg_id,
            "content": op.content,
            "old_string": op.old_string,
            "new_string": op.new_string,
        }
        for op in file_state.operations
    ]

    blame_lines = None
    if file_state.blame_lines:
        blame_lines = [
            [
                line,
                (
                    {
                        "operation_type": op.operation_type,
                        "page_num": op.page_num,
                        "msg_id": op.msg_id,
                        "timestamp": op.timestamp,
                    }
                    if op
                    else None
                ),
            ]
            for line, op in file_state.blame_lines
        ]

    return {
        "file_path": file_state.file_path,
        "diff_only": file_state.diff_only,
        "final_content": file_state.final_content,
        "blame_lines": blame_lines,
        "operations": operations,
    }


def generate_code_view_html(
    output_dir: Path,
    operations: List[FileOperation],
    transcript_messages: List[str] = None,
    msg_to_user_html: Dict[str, str] = None,
    msg_to_context_id: Dict[str, str] = None,
    total_pages: int = 1,
) -> None:
    """Generate the code.html file with three-pane layout.

    Args:
        output_dir: Output directory.
        operations: List of FileOperation objects.
        transcript_messages: List of individual message HTML strings.
        msg_to_user_html: Mapping from msg_id to rendered user message HTML for tooltips.
        msg_to_context_id: Mapping from msg_id to context_msg_id for blame coloring.
        total_pages: Total number of transcript pages (for search feature).
    """
    # Import here to avoid circular imports
    from claude_code_transcripts import get_template

    if not operations:
        return

    if transcript_messages is None:
        transcript_messages = []

    if msg_to_user_html is None:
        msg_to_user_html = {}

    if msg_to_context_id is None:
        msg_to_context_id = {}

    # Extract message IDs from HTML for chunked rendering
    # Messages have format: <div class="message ..." id="msg-...">
    import re

    msg_id_pattern = re.compile(r'id="(msg-[^"]+)"')
    messages_data = []
    for msg_html in transcript_messages:
        match = msg_id_pattern.search(msg_html)
        msg_id = match.group(1) if match else None
        messages_data.append({"id": msg_id, "html": msg_html})

    # Build temp git repo with file history
    repo, temp_dir, path_mapping = build_file_history_repo(operations)

    try:
        # Build file data for each file
        file_data = {}

        # Group operations by file (already sorted by timestamp)
        ops_by_file = group_operations_by_file(operations)

        for orig_path, file_ops in ops_by_file.items():
            rel_path = path_mapping.get(orig_path, orig_path)

            # Get file content
            content = get_file_content_from_repo(repo, rel_path)
            if content is None:
                continue

            # Get blame ranges
            blame_ranges = get_file_blame_ranges(repo, rel_path)

            # Determine status
            status = (
                STATUS_ADDED
                if file_ops[0].operation_type == OP_WRITE
                else STATUS_MODIFIED
            )

            # Build file data
            file_data[orig_path] = {
                "file_path": orig_path,
                "rel_path": rel_path,
                "content": content,
                "status": status,
                "blame_ranges": [
                    {
                        "start": r.start_line,
                        "end": r.end_line,
                        "tool_id": r.tool_id,
                        "page_num": r.page_num,
                        "msg_id": r.msg_id,
                        "context_msg_id": msg_to_context_id.get(r.msg_id, r.msg_id),
                        "operation_type": r.operation_type,
                        "timestamp": r.timestamp,
                        "user_html": msg_to_user_html.get(r.msg_id, ""),
                    }
                    for r in blame_ranges
                ],
            }

        # Build file states for tree (reusing existing structure)
        file_states = {}
        for orig_path, data in file_data.items():
            file_states[orig_path] = FileState(
                file_path=orig_path,
                status=data["status"],
            )

        # Build file tree
        file_tree = build_file_tree(file_states)
        file_tree_html = render_file_tree_html(file_tree)

        # Build code data object
        code_data = {
            "fileData": file_data,
            "messagesData": messages_data,
        }

        # Write data to separate JSON file for gistpreview lazy loading
        # (gistpreview has size limits, so it fetches this file separately)
        (output_dir / "code-data.json").write_text(
            json.dumps(code_data), encoding="utf-8"
        )

        # Also embed data inline for local file:// use
        # (fetch() doesn't work with file:// URLs due to CORS)
        code_data_json = json.dumps(code_data)
        # Escape sequences that would confuse the HTML parser inside script tags:
        # - </ sequences (closing tags like </div> would break parsing)
        # - <!-- sequences (HTML comment start has special handling in scripts)
        code_data_json = code_data_json.replace("</", "<\\/")
        code_data_json = code_data_json.replace("<!--", "<\\!--")
        inline_data_script = f"<script>window.CODE_DATA = {code_data_json};</script>"

        # Get templates
        code_view_template = get_template("code.html")
        code_view_js_template = get_template("code_view.js")

        # Render JavaScript
        code_view_js = code_view_js_template.render()

        # Render page
        page_content = code_view_template.render(
            file_tree_html=file_tree_html,
            code_view_js=code_view_js,
            inline_data_script=inline_data_script,
            total_pages=total_pages,
            has_code_view=True,
            active_tab="code",
        )

        # Write file
        (output_dir / "code.html").write_text(page_content, encoding="utf-8")

    finally:
        # Clean up temp directory
        shutil.rmtree(temp_dir, ignore_errors=True)


def _build_tooltip_html(
    prompt_num: int,
    conv_timestamp: str,
    rendered_user: str,
    context_html: str = "",
) -> str:
    """Build HTML for a tooltip item.

    Args:
        prompt_num: The prompt number (e.g., #1, #2).
        conv_timestamp: ISO timestamp for the conversation.
        rendered_user: Pre-rendered user message HTML.
        context_html: Optional HTML for assistant context/thinking blocks.

    Returns:
        Complete HTML string for the tooltip item.
    """
    return f"""<div class="index-item tooltip-item"><div class="index-item-header"><span class="index-item-number">#{prompt_num}</span><time datetime="{conv_timestamp}" data-timestamp="{conv_timestamp}">{conv_timestamp}</time></div><div class="index-item-content">{rendered_user}</div>{context_html}</div>"""


def _truncate_for_tooltip(content: str, max_length: int = 500) -> Tuple[str, bool]:
    """Truncate content for tooltip display, handling code blocks safely.

    Truncation in the middle of a markdown code block can leave unbalanced
    backticks, causing HTML inside code examples to be interpreted as actual
    HTML. This function strips code blocks entirely for tooltip display.

    Args:
        content: The text content to truncate.
        max_length: Maximum length before truncation.

    Returns:
        Tuple of (truncated content, was_truncated flag).
    """
    import re

    original_length = len(content)
    was_truncated = False

    # Remove code blocks entirely (they're too verbose for tooltips)
    # This handles both fenced (```) and indented code blocks
    content = re.sub(r"```[\s\S]*?```", "[code block]", content)
    content = re.sub(r"```[\s\S]*$", "[code block]", content)  # Incomplete fence

    # Also remove inline code that might contain HTML
    content = re.sub(r"`[^`]+`", "`...`", content)

    # Track if we stripped code blocks (significant content removed)
    if len(content) < original_length * 0.7:  # More than 30% was code blocks
        was_truncated = True

    # Now truncate
    if len(content) > max_length:
        content = content[:max_length] + "..."
        was_truncated = True

    return content, was_truncated


def _render_context_block_inner(
    block_type: str, content: str, render_fn
) -> Tuple[str, bool]:
    """Render a context block (text or thinking) as inner HTML.

    Args:
        block_type: Either "text" or "thinking".
        content: The block content to render.
        render_fn: Function to render markdown text to HTML.

    Returns:
        Tuple of (HTML string for the block content, was_truncated flag).
    """
    # Truncate safely, removing code blocks
    content, was_truncated = _truncate_for_tooltip(content)
    rendered = render_fn(content)

    if block_type == "thinking":
        return (
            f"""<div class="context-thinking"><div class="context-thinking-label">Thinking:</div>{rendered}</div>""",
            was_truncated,
        )
    else:  # text
        return f"""<div class="context-text">{rendered}</div>""", was_truncated


def _render_context_section(blocks: List[Tuple[str, str, int, str]], render_fn) -> str:
    """Render all context blocks inside a single Assistant context section.

    Args:
        blocks: List of (block_type, content, order, msg_id) tuples.
        render_fn: Function to render markdown text to HTML.

    Returns:
        HTML string for the complete assistant context section.
    """
    if not blocks:
        return ""

    any_truncated = False
    inner_html_parts = []

    for block_type, content, _, _ in blocks:
        html, was_truncated = _render_context_block_inner(
            block_type, content, render_fn
        )
        inner_html_parts.append(html)
        if was_truncated:
            any_truncated = True

    inner_html = "".join(inner_html_parts)
    truncated_indicator = (
        ' <span class="truncated-indicator">(truncated)</span>' if any_truncated else ""
    )

    return f"""<div class="tooltip-assistant"><div class="tooltip-assistant-label">Assistant context:{truncated_indicator}</div>{inner_html}</div>"""


def _collect_conversation_messages(
    conversations: List[Dict], start_index: int
) -> List[Tuple]:
    """Collect all messages from a conversation and its continuations.

    Args:
        conversations: Full list of conversation dicts.
        start_index: Index of the starting conversation.

    Returns:
        List of (log_type, message_json, timestamp) tuples.
    """
    all_messages = list(conversations[start_index].get("messages", []))
    for j in range(start_index + 1, len(conversations)):
        if not conversations[j].get("is_continuation"):
            break
        all_messages.extend(conversations[j].get("messages", []))
    return all_messages


def build_msg_to_user_html(
    conversations: List[Dict],
) -> Tuple[Dict[str, str], Dict[str, str]]:
    """Build a mapping from msg_id to tooltip HTML and context message ID.

    For each tool call message, render the user prompt followed by the
    assistant text that immediately preceded the tool call.

    Args:
        conversations: List of conversation dicts with user_text, timestamp, and messages.

    Returns:
        Tuple of:
        - Dict mapping msg_id to rendered tooltip HTML
        - Dict mapping msg_id to context_msg_id (the assistant message providing context)
    """
    # Import here to avoid circular imports
    from claude_code_transcripts import (
        make_msg_id,
        render_markdown_text,
    )
    import json

    msg_to_user_html = {}
    msg_to_context_id = {}
    prompt_num = 0

    for i, conv in enumerate(conversations):
        # Skip continuations (they're counted with their parent)
        if conv.get("is_continuation"):
            continue

        user_text = conv.get("user_text", "")
        conv_timestamp = conv.get("timestamp", "")
        if not user_text:
            continue

        prompt_num += 1

        all_messages = _collect_conversation_messages(conversations, i)
        rendered_user = render_markdown_text(user_text)
        user_html = _build_tooltip_html(prompt_num, conv_timestamp, rendered_user)

        # Track most recent thinking and text blocks with order for sequencing
        # Each is (content, order, msg_id) tuple or None
        last_thinking = None
        last_text = None
        block_order = 0

        for log_type, message_json, timestamp in all_messages:
            msg_id = make_msg_id(timestamp)

            try:
                message_data = json.loads(message_json)
            except (json.JSONDecodeError, TypeError):
                msg_to_user_html[msg_id] = user_html
                continue

            content = message_data.get("content", [])

            if log_type == "assistant" and isinstance(content, list):
                has_tool_use = False
                for block in content:
                    if isinstance(block, dict):
                        if block.get("type") == "text":
                            text = block.get("text", "")
                            if text:
                                last_text = (text, block_order, msg_id)
                                block_order += 1
                        elif block.get("type") == "thinking":
                            thinking = block.get("thinking", "")
                            if thinking:
                                last_thinking = (thinking, block_order, msg_id)
                                block_order += 1
                        elif block.get("type") == "tool_use":
                            has_tool_use = True

                # For messages with tool_use, build tooltip with context in original order
                if has_tool_use and (last_thinking or last_text):
                    # Collect blocks and sort by order
                    blocks_to_render = []
                    if last_thinking:
                        blocks_to_render.append(
                            (
                                "thinking",
                                last_thinking[0],
                                last_thinking[1],
                                last_thinking[2],
                            )
                        )
                    if last_text:
                        blocks_to_render.append(
                            ("text", last_text[0], last_text[1], last_text[2])
                        )
                    blocks_to_render.sort(key=lambda x: x[2])

                    # Use the most recent block's msg_id as the context message ID
                    context_msg_id = blocks_to_render[-1][3]
                    msg_to_context_id[msg_id] = context_msg_id

                    context_html = _render_context_section(
                        blocks_to_render, render_markdown_text
                    )

                    msg_to_user_html[msg_id] = _build_tooltip_html(
                        prompt_num, conv_timestamp, rendered_user, context_html
                    )
                else:
                    msg_to_user_html[msg_id] = user_html
            else:
                msg_to_user_html[msg_id] = user_html

    return msg_to_user_html, msg_to_context_id
