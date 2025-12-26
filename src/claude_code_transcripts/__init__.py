"""Convert Claude Code session JSON to a clean mobile-friendly HTML page with pagination."""

import json
import html
import os
import platform
import re
import shutil
import subprocess
import tempfile
import webbrowser
from pathlib import Path

import click
from click_default_group import DefaultGroup
import httpx
from jinja2 import Environment, PackageLoader
import markdown
import questionary

# Set up Jinja2 environment
_jinja_env = Environment(
    loader=PackageLoader("claude_code_transcripts", "templates"),
    autoescape=True,
)

# Load macros template and expose macros
_macros_template = _jinja_env.get_template("macros.html")
_macros = _macros_template.module


def get_template(name):
    """Get a Jinja2 template by name."""
    return _jinja_env.get_template(name)


# Regex to match git commit output: [branch hash] message
COMMIT_PATTERN = re.compile(r"\[[\w\-/]+ ([a-f0-9]{7,})\] (.+?)(?:\n|$)")

# Regex to detect GitHub repo from git push output (e.g., github.com/owner/repo/pull/new/branch)
GITHUB_REPO_PATTERN = re.compile(
    r"github\.com/([a-zA-Z0-9_-]+/[a-zA-Z0-9_-]+)/pull/new/"
)

PROMPTS_PER_PAGE = 5
LONG_TEXT_THRESHOLD = (
    300  # Characters - text blocks longer than this are shown in index
)

# Module-level variable for GitHub repo (set by generate_html)
_github_repo = None

# API constants
API_BASE_URL = "https://api.anthropic.com/v1"
ANTHROPIC_VERSION = "2023-06-01"


def get_session_summary(filepath, max_length=200):
    """Extract a human-readable summary from a session file.

    Supports both JSON and JSONL formats.
    Returns a summary string or "(no summary)" if none found.
    """
    filepath = Path(filepath)
    try:
        if filepath.suffix == ".jsonl":
            return _get_jsonl_summary(filepath, max_length)
        else:
            # For JSON files, try to get first user message
            with open(filepath, "r", encoding="utf-8") as f:
                data = json.load(f)
            loglines = data.get("loglines", [])
            for entry in loglines:
                if entry.get("type") == "user":
                    msg = entry.get("message", {})
                    content = msg.get("content", "")
                    if isinstance(content, str) and content.strip():
                        if len(content) > max_length:
                            return content[: max_length - 3] + "..."
                        return content
            return "(no summary)"
    except Exception:
        return "(no summary)"


def _get_jsonl_summary(filepath, max_length=200):
    """Extract summary from JSONL file."""
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                    # First priority: summary type entries
                    if obj.get("type") == "summary" and obj.get("summary"):
                        summary = obj["summary"]
                        if len(summary) > max_length:
                            return summary[: max_length - 3] + "..."
                        return summary
                except json.JSONDecodeError:
                    continue

        # Second pass: find first non-meta user message
        with open(filepath, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                    if (
                        obj.get("type") == "user"
                        and not obj.get("isMeta")
                        and obj.get("message", {}).get("content")
                    ):
                        content = obj["message"]["content"]
                        if isinstance(content, str):
                            content = content.strip()
                            if content and not content.startswith("<"):
                                if len(content) > max_length:
                                    return content[: max_length - 3] + "..."
                                return content
                except json.JSONDecodeError:
                    continue
    except Exception:
        pass

    return "(no summary)"


def find_local_sessions(folder, limit=10):
    """Find recent JSONL session files in the given folder.

    Returns a list of (Path, summary) tuples sorted by modification time.
    Excludes agent files and warmup/empty sessions.
    """
    folder = Path(folder)
    if not folder.exists():
        return []

    results = []
    for f in folder.glob("**/*.jsonl"):
        if f.name.startswith("agent-"):
            continue
        summary = get_session_summary(f)
        # Skip boring/empty sessions
        if summary.lower() == "warmup" or summary == "(no summary)":
            continue
        results.append((f, summary))

    # Sort by modification time, most recent first
    results.sort(key=lambda x: x[0].stat().st_mtime, reverse=True)
    return results[:limit]


def parse_session_file(filepath):
    """Parse a session file and return normalized data.

    Supports both JSON and JSONL formats.
    Returns a dict with 'loglines' key containing the normalized entries.
    """
    filepath = Path(filepath)

    if filepath.suffix == ".jsonl":
        return _parse_jsonl_file(filepath)
    else:
        # Standard JSON format
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)


def _parse_jsonl_file(filepath):
    """Parse JSONL file and convert to standard format."""
    loglines = []

    with open(filepath, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                entry_type = obj.get("type")

                # Skip non-message entries
                if entry_type not in ("user", "assistant"):
                    continue

                # Convert to standard format
                entry = {
                    "type": entry_type,
                    "timestamp": obj.get("timestamp", ""),
                    "message": obj.get("message", {}),
                }

                # Preserve isCompactSummary if present
                if obj.get("isCompactSummary"):
                    entry["isCompactSummary"] = True

                loglines.append(entry)
            except json.JSONDecodeError:
                continue

    return {"loglines": loglines}


class CredentialsError(Exception):
    """Raised when credentials cannot be obtained."""

    pass


def get_access_token_from_keychain():
    """Get access token from macOS keychain.

    Returns the access token or None if not found.
    Raises CredentialsError with helpful message on failure.
    """
    if platform.system() != "Darwin":
        return None

    try:
        result = subprocess.run(
            [
                "security",
                "find-generic-password",
                "-a",
                os.environ.get("USER", ""),
                "-s",
                "Claude Code-credentials",
                "-w",
            ],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            return None

        # Parse the JSON to get the access token
        creds = json.loads(result.stdout.strip())
        return creds.get("claudeAiOauth", {}).get("accessToken")
    except (json.JSONDecodeError, subprocess.SubprocessError):
        return None


def get_org_uuid_from_config():
    """Get organization UUID from ~/.claude.json.

    Returns the organization UUID or None if not found.
    """
    config_path = Path.home() / ".claude.json"
    if not config_path.exists():
        return None

    try:
        with open(config_path) as f:
            config = json.load(f)
        return config.get("oauthAccount", {}).get("organizationUuid")
    except (json.JSONDecodeError, IOError):
        return None


def get_api_headers(token, org_uuid):
    """Build API request headers."""
    return {
        "Authorization": f"Bearer {token}",
        "anthropic-version": ANTHROPIC_VERSION,
        "Content-Type": "application/json",
        "x-organization-uuid": org_uuid,
    }


def fetch_sessions(token, org_uuid):
    """Fetch list of sessions from the API.

    Returns the sessions data as a dict.
    Raises httpx.HTTPError on network/API errors.
    """
    headers = get_api_headers(token, org_uuid)
    response = httpx.get(f"{API_BASE_URL}/sessions", headers=headers, timeout=30.0)
    response.raise_for_status()
    return response.json()


def fetch_session(token, org_uuid, session_id):
    """Fetch a specific session from the API.

    Returns the session data as a dict.
    Raises httpx.HTTPError on network/API errors.
    """
    headers = get_api_headers(token, org_uuid)
    response = httpx.get(
        f"{API_BASE_URL}/session_ingress/session/{session_id}",
        headers=headers,
        timeout=60.0,
    )
    response.raise_for_status()
    return response.json()


def detect_github_repo(loglines):
    """
    Detect GitHub repo from git push output in tool results.

    Looks for patterns like:
    - github.com/owner/repo/pull/new/branch (from git push messages)

    Returns the first detected repo (owner/name) or None.
    """
    for entry in loglines:
        message = entry.get("message", {})
        content = message.get("content", [])
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_result":
                result_content = block.get("content", "")
                if isinstance(result_content, str):
                    match = GITHUB_REPO_PATTERN.search(result_content)
                    if match:
                        return match.group(1)
    return None


def format_json(obj):
    try:
        if isinstance(obj, str):
            obj = json.loads(obj)
        formatted = json.dumps(obj, indent=2, ensure_ascii=False)
        return f'<pre class="json">{html.escape(formatted)}</pre>'
    except (json.JSONDecodeError, TypeError):
        return f"<pre>{html.escape(str(obj))}</pre>"


def render_markdown_text(text):
    if not text:
        return ""
    return markdown.markdown(text, extensions=["fenced_code", "tables"])


def is_json_like(text):
    if not text or not isinstance(text, str):
        return False
    text = text.strip()
    return (text.startswith("{") and text.endswith("}")) or (
        text.startswith("[") and text.endswith("]")
    )


def render_todo_write(tool_input, tool_id, tool_result_html=""):
    todos = tool_input.get("todos", [])
    if not todos:
        return ""
    return _macros.todo_list(todos, tool_id)


def render_write_tool(tool_input, tool_id, tool_result_html=""):
    """Render Write tool calls with file path header and content preview."""
    file_path = tool_input.get("file_path", "Unknown file")
    content = tool_input.get("content", "")
    return _macros.write_tool(file_path, content, tool_id, tool_result_html)


def render_edit_tool(tool_input, tool_id, tool_result_html=""):
    """Render Edit tool calls with diff-like old/new display."""
    file_path = tool_input.get("file_path", "Unknown file")
    old_string = tool_input.get("old_string", "")
    new_string = tool_input.get("new_string", "")
    replace_all = tool_input.get("replace_all", False)
    return _macros.edit_tool(file_path, old_string, new_string, replace_all, tool_id, tool_result_html)


def render_bash_tool(tool_input, tool_id, tool_result_html=""):
    """Render Bash tool calls with command as plain text."""
    command = tool_input.get("command", "")
    description = tool_input.get("description", "")
    return _macros.bash_tool(command, description, tool_id, tool_result_html)


def render_tool_result_block(block):
    """Render tool result block content (without the message wrapper)."""
    content = block.get("content", "")
    is_error = block.get("is_error", False)

    # Check for git commits and render with styled cards
    if isinstance(content, str):
        commits_found = list(COMMIT_PATTERN.finditer(content))
        if commits_found:
            # Build commit cards + remaining content
            parts = []
            last_end = 0
            for match in commits_found:
                # Add any content before this commit
                before = content[last_end : match.start()].strip()
                if before:
                    parts.append(f"<pre>{html.escape(before)}</pre>")

                commit_hash = match.group(1)
                commit_msg = match.group(2)
                parts.append(
                    _macros.commit_card(commit_hash, commit_msg, _github_repo)
                )
                last_end = match.end()

            # Add any remaining content after last commit
            after = content[last_end:].strip()
            if after:
                parts.append(f"<pre>{html.escape(after)}</pre>")

            content_html = "".join(parts)
        else:
            content_html = f"<pre>{html.escape(content)}</pre>"
    elif isinstance(content, list) or is_json_like(content):
        content_html = format_json(content)
    else:
        content_html = format_json(content)

    return _macros.tool_result(content_html, is_error)


def render_content_block(block, tool_results_map=None):
    """Render a content block, optionally including its tool result if available.

    Args:
        block: The content block to render
        tool_results_map: Optional dict mapping tool_id -> tool_result block
    """
    if not isinstance(block, dict):
        return f"<p>{html.escape(str(block))}</p>"
    block_type = block.get("type", "")
    if block_type == "thinking":
        content_html = render_markdown_text(block.get("thinking", ""))
        return _macros.thinking(content_html)
    elif block_type == "text":
        content_html = render_markdown_text(block.get("text", ""))
        return _macros.assistant_text(content_html)
    elif block_type == "tool_use":
        tool_name = block.get("name", "Unknown tool")
        tool_input = block.get("input", {})
        tool_id = block.get("id", "")

        # Get the corresponding tool result if available
        tool_result_html = ""
        if tool_results_map and tool_id in tool_results_map:
            result_block = tool_results_map[tool_id]
            tool_result_html = render_tool_result_block(result_block)

        if tool_name == "TodoWrite":
            return render_todo_write(tool_input, tool_id, tool_result_html)
        if tool_name == "Write":
            return render_write_tool(tool_input, tool_id, tool_result_html)
        if tool_name == "Edit":
            return render_edit_tool(tool_input, tool_id, tool_result_html)
        if tool_name == "Bash":
            return render_bash_tool(tool_input, tool_id, tool_result_html)
        description = tool_input.get("description", "")
        display_input = {k: v for k, v in tool_input.items() if k != "description"}
        input_json = json.dumps(display_input, indent=2, ensure_ascii=False)
        return _macros.tool_use(tool_name, description, input_json, tool_id, tool_result_html)
    elif block_type == "tool_result":
        content = block.get("content", "")
        is_error = block.get("is_error", False)

        # Check for git commits and render with styled cards
        if isinstance(content, str):
            commits_found = list(COMMIT_PATTERN.finditer(content))
            if commits_found:
                # Build commit cards + remaining content
                parts = []
                last_end = 0
                for match in commits_found:
                    # Add any content before this commit
                    before = content[last_end : match.start()].strip()
                    if before:
                        parts.append(f"<pre>{html.escape(before)}</pre>")

                    commit_hash = match.group(1)
                    commit_msg = match.group(2)
                    parts.append(
                        _macros.commit_card(commit_hash, commit_msg, _github_repo)
                    )
                    last_end = match.end()

                # Add any remaining content after last commit
                after = content[last_end:].strip()
                if after:
                    parts.append(f"<pre>{html.escape(after)}</pre>")

                content_html = "".join(parts)
            else:
                content_html = f"<pre>{html.escape(content)}</pre>"
        elif isinstance(content, list) or is_json_like(content):
            content_html = format_json(content)
        else:
            content_html = format_json(content)
        return _macros.tool_result(content_html, is_error)
    else:
        return format_json(block)


def render_user_message_content(message_data):
    content = message_data.get("content", "")
    if isinstance(content, str):
        if is_json_like(content):
            return _macros.user_content(format_json(content))
        return _macros.user_content(render_markdown_text(content))
    elif isinstance(content, list):
        return "".join(render_content_block(block) for block in content)
    return f"<p>{html.escape(str(content))}</p>"


def render_assistant_message(message_data, tool_results_map=None):
    """Render assistant message content, optionally with tool results nested inline.

    Args:
        message_data: The message data dict
        tool_results_map: Optional dict mapping tool_id -> tool_result block
    """
    content = message_data.get("content", [])
    if not isinstance(content, list):
        return f"<p>{html.escape(str(content))}</p>"
    return "".join(render_content_block(block, tool_results_map) for block in content)


def make_msg_id(timestamp):
    return f"msg-{timestamp.replace(':', '-').replace('.', '-')}"


def analyze_conversation(messages):
    """Analyze messages in a conversation to extract stats and long texts."""
    tool_counts = {}  # tool_name -> count
    long_texts = []
    commits = []  # list of (hash, message, timestamp)

    for log_type, message_json, timestamp in messages:
        if not message_json:
            continue
        try:
            message_data = json.loads(message_json)
        except json.JSONDecodeError:
            continue

        content = message_data.get("content", [])
        if not isinstance(content, list):
            continue

        for block in content:
            if not isinstance(block, dict):
                continue
            block_type = block.get("type", "")

            if block_type == "tool_use":
                tool_name = block.get("name", "Unknown")
                tool_counts[tool_name] = tool_counts.get(tool_name, 0) + 1
            elif block_type == "tool_result":
                # Check for git commit output
                result_content = block.get("content", "")
                if isinstance(result_content, str):
                    for match in COMMIT_PATTERN.finditer(result_content):
                        commits.append((match.group(1), match.group(2), timestamp))
            elif block_type == "text":
                text = block.get("text", "")
                if len(text) >= LONG_TEXT_THRESHOLD:
                    long_texts.append(text)

    return {
        "tool_counts": tool_counts,
        "long_texts": long_texts,
        "commits": commits,
    }


def format_tool_stats(tool_counts):
    """Format tool counts into a concise summary string."""
    if not tool_counts:
        return ""

    # Abbreviate common tool names
    abbrev = {
        "Bash": "bash",
        "Read": "read",
        "Write": "write",
        "Edit": "edit",
        "Glob": "glob",
        "Grep": "grep",
        "Task": "task",
        "TodoWrite": "todo",
        "WebFetch": "fetch",
        "WebSearch": "search",
    }

    parts = []
    for name, count in sorted(tool_counts.items(), key=lambda x: -x[1]):
        short_name = abbrev.get(name, name.lower())
        parts.append(f"{count} {short_name}")

    return " · ".join(parts)


def is_tool_result_message(message_data):
    """Check if a message contains only tool_result blocks."""
    content = message_data.get("content", [])
    if not isinstance(content, list):
        return False
    if not content:
        return False
    return all(
        isinstance(block, dict) and block.get("type") == "tool_result"
        for block in content
    )


def render_message(log_type, message_json, timestamp, tool_results_map=None):
    """Render a message.

    Args:
        log_type: Type of message ('user' or 'assistant')
        message_json: JSON string of message data
        timestamp: Timestamp string
        tool_results_map: Optional dict mapping tool_id -> tool_result block (for assistant messages)
    """
    if not message_json:
        return ""
    try:
        message_data = json.loads(message_json)
    except json.JSONDecodeError:
        return ""
    if log_type == "user":
        content_html = render_user_message_content(message_data)
        # Check if this is a tool result message - we'll skip these since they're nested
        if is_tool_result_message(message_data):
            return ""  # Skip tool reply messages - they're rendered inline
        role_class, role_label = "user", "User"
    elif log_type == "assistant":
        content_html = render_assistant_message(message_data, tool_results_map)
        role_class, role_label = "assistant", "Assistant"
    else:
        return ""
    if not content_html.strip():
        return ""
    msg_id = make_msg_id(timestamp)
    return _macros.message(role_class, role_label, msg_id, timestamp, content_html)


CSS = """
@import url('https://fonts.googleapis.com/css2?family=Newsreader:ital,wght@0,400;0,600;1,400&family=IBM+Plex+Mono:wght@400;500&family=DM+Sans:wght@400;500;700&family=Outfit:wght@400;500;600&display=swap');

:root {
  --bg-color: #fafaf9;
  --text-primary: #0a0a0a;
  --text-secondary: #525252;
  --text-tertiary: #a3a3a3;
  --border-light: #e7e5e4;
  --border-medium: #d6d3d1;
  --accent-user: #2563eb;
  --accent-thinking: #d97706;
  --accent-tool: #7c3aed;
  --surface-elevated: #ffffff;
  --surface-subtle: #f5f5f4;
  --code-bg: #18181b;
  --code-text: #a1a1aa;

  /* Tinted backgrounds for message types */
  --bg-user: #eff6ff;
  --bg-assistant: #f5f5f4;
  --bg-thinking: #fefce8;
  --bg-tool: #faf5ff;
}

* { box-sizing: border-box; }

body {
  font-family: 'DM Sans', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
  background: var(--bg-color);
  color: var(--text-primary);
  margin: 0;
  padding: 32px 24px;
  line-height: 1.75;
  font-size: 17px;
  -webkit-font-smoothing: antialiased;
  -moz-osx-font-smoothing: grayscale;
}
.container {
  max-width: 720px;
  margin: 0 auto;
}

h1 {
  font-family: 'DM Sans', sans-serif;
  font-size: 1.25rem;
  font-weight: 700;
  margin: 0 0 48px 0;
  padding: 0 0 16px 0;
  border-bottom: 1px solid var(--border-light);
  letter-spacing: -0.02em;
  color: var(--text-primary);
}

/* Message containers with typographic differentiation */
.message {
  margin-bottom: 56px;
  position: relative;
  animation: fadeIn 0.4s ease-out;
}

@keyframes fadeIn {
  from { opacity: 0; transform: translateY(8px); }
  to { opacity: 1; transform: translateY(0); }
}

.message.user {
  border-left: 3px solid var(--accent-user);
  padding-left: 24px;
  background: var(--bg-user);
  padding: 20px 20px 20px 24px;
  border-radius: 4px;
}

.message.assistant {
  border-left: 3px solid var(--border-medium);
  padding-left: 24px;
  background: var(--bg-assistant);
  padding: 20px 20px 20px 24px;
  border-radius: 4px;
}

.message.tool-reply {
  border-left: 3px solid var(--accent-tool);
  padding-left: 24px;
  background: var(--bg-tool);
  padding: 20px 20px 20px 24px;
  border-radius: 4px;
}

.message-header {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  margin-bottom: 16px;
}

.role-label {
  font-family: 'DM Sans', sans-serif;
  font-size: 0.6875rem;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.1em;
  color: var(--text-tertiary);
}

.user .role-label {
  color: var(--accent-user);
}

.assistant .role-label {
  color: var(--text-secondary);
}

.tool-reply .role-label {
  color: var(--accent-tool);
}

time {
  font-family: 'IBM Plex Mono', monospace;
  font-size: 0.6875rem;
  color: var(--text-tertiary);
  letter-spacing: 0.02em;
}

.timestamp-link {
  color: inherit;
  text-decoration: none;
  transition: color 0.2s ease;
}

.timestamp-link:hover {
  color: var(--text-secondary);
}

.message:target {
  animation: highlight 1.5s ease-out;
}

@keyframes highlight {
  0% { border-left-color: var(--accent-user); border-left-width: 6px; }
  100% { border-left-width: 3px; }
}

.message-content {
  font-size: 17px;
  line-height: 1.75;
}

.message-content p {
  margin: 0 0 1.25em 0;
}

.message-content p:last-child {
  margin-bottom: 0;
}

/* User messages: larger type with Futura-like font */
.user .message-content {
  font-family: Futura, 'Outfit', 'Trebuchet MS', 'Arial Narrow', sans-serif;
  font-size: 1.25rem;
  line-height: 1.5;
  font-weight: 500;
  color: var(--text-primary);
  letter-spacing: -0.015em;
}
/* Thinking blocks: italicized, smaller serif */
.thinking {
  margin: 24px 0;
  padding: 16px 16px 16px 24px;
  border-left: 2px solid var(--accent-thinking);
  background: var(--bg-thinking);
  border-radius: 4px;
}

.thinking-label {
  font-family: 'DM Sans', sans-serif;
  font-size: 0.6875rem;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.1em;
  color: var(--accent-thinking);
  margin-bottom: 12px;
  display: block;
}

.thinking p {
  margin: 0 0 1em 0;
  font-family: 'Newsreader', Georgia, serif;
  font-style: italic;
  font-size: 0.9375rem;
  line-height: 1.65;
  color: var(--text-secondary);
}

.thinking p:last-child {
  margin-bottom: 0;
}

.assistant-text {
  margin: 16px 0;
  font-family: 'DM Sans', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
}
/* Tool use: compact, monospaced headers */
.tool-use {
  background: #e7e5e4;
  border: 1px solid var(--border-light);
  border-radius: 6px;
  padding: 16px;
  margin: 20px 0;
}

.tool-header {
  font-family: 'IBM Plex Mono', monospace;
  font-size: 0.8125rem;
  font-weight: 500;
  color: var(--text-primary);
  margin-bottom: 12px;
  display: flex;
  align-items: center;
  gap: 8px;
  letter-spacing: -0.01em;
}

.tool-icon {
  font-size: 1rem;
  opacity: 0.5;
}

.tool-description {
  font-family: 'DM Sans', sans-serif;
  font-size: 0.8125rem;
  color: var(--text-secondary);
  margin-bottom: 12px;
  line-height: 1.5;
}

.tool-result {
  background: transparent;
  border-left: 2px solid var(--border-light);
  padding: 16px 0 16px 20px;
  margin: 16px 0;
}

.tool-result.tool-error {
  border-left-color: #dc2626;
}

/* Tool response section - nested inside tool use */
.tool-response-section {
  margin-top: 16px;
  padding-top: 16px;
  border-top: 1px solid var(--border-light);
}

.tool-response-label {
  font-family: 'DM Sans', sans-serif;
  font-size: 0.6875rem;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.1em;
  color: var(--text-secondary);
  margin-bottom: 12px;
}

.tool-command-section {
  margin-bottom: 0;
}

.tool-command-label {
  font-family: 'DM Sans', sans-serif;
  font-size: 0.6875rem;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.1em;
  color: var(--text-secondary);
  margin-bottom: 12px;
}

/* Nested tool results have simplified styling */
.tool-response-section .tool-result {
  padding: 0;
  margin: 0;
  border: none;
}

.tool-response-section .tool-result.tool-error pre {
  color: #dc2626;
}

.tool-reply .tool-result {
  background: transparent;
  padding: 0;
  margin: 0;
  border: none;
}

.tool-reply .tool-result .truncatable.truncated::after {
  background: linear-gradient(to bottom, transparent, var(--bg-color));
}
/* File tools: Write and Edit */
.file-tool {
  border-radius: 6px;
  padding: 16px;
  margin: 20px 0;
  background: #e7e5e4;
  border: 1px solid var(--border-light);
}

.write-tool {
  border-left: 3px solid #10b981;
}

.edit-tool {
  border-left: 3px solid #f59e0b;
}

.file-tool-header {
  font-family: 'IBM Plex Mono', monospace;
  font-size: 0.8125rem;
  font-weight: 500;
  margin-bottom: 8px;
  display: flex;
  align-items: center;
  gap: 8px;
  letter-spacing: -0.01em;
}

.write-header {
  color: #10b981;
}

.edit-header {
  color: #f59e0b;
}

.file-tool-icon {
  font-size: 1rem;
  opacity: 0.7;
}

.file-tool-path {
  font-family: 'IBM Plex Mono', monospace;
  background: var(--surface-subtle);
  padding: 2px 8px;
  border-radius: 4px;
  font-size: 0.8125rem;
}

.file-tool-fullpath {
  font-family: 'IBM Plex Mono', monospace;
  font-size: 0.75rem;
  color: var(--text-tertiary);
  margin-bottom: 12px;
  word-break: break-all;
}

.file-content {
  margin: 0;
}

.edit-section {
  display: flex;
  margin: 8px 0;
  border-radius: 4px;
  overflow: hidden;
  border: 1px solid var(--border-light);
}

.edit-label {
  padding: 12px;
  font-weight: 500;
  font-family: 'IBM Plex Mono', monospace;
  display: flex;
  align-items: flex-start;
  font-size: 0.75rem;
  min-width: 32px;
  justify-content: center;
}

.edit-old {
  background: #fef2f2;
}

.edit-old .edit-label {
  color: #dc2626;
  background: #fee2e2;
}

.edit-old .edit-content {
  color: #991b1b;
}

.edit-new {
  background: #f0fdf4;
}

.edit-new .edit-label {
  color: #16a34a;
  background: #dcfce7;
}

.edit-new .edit-content {
  color: #166534;
}

.edit-content {
  margin: 0;
  flex: 1;
  background: transparent;
  font-size: 0.8125rem;
  padding: 12px;
}

.edit-replace-all {
  font-size: 0.6875rem;
  font-weight: normal;
  color: var(--text-tertiary);
  font-family: 'DM Sans', sans-serif;
}

.write-tool .truncatable.truncated::after {
  background: linear-gradient(to bottom, transparent, #e7e5e4);
}

.edit-tool .truncatable.truncated::after {
  background: linear-gradient(to bottom, transparent, #e7e5e4);
}
/* Todo list */
.todo-list {
  background: #e7e5e4;
  border: 1px solid var(--border-light);
  border-left: 3px solid #10b981;
  border-radius: 6px;
  padding: 16px;
  margin: 20px 0;
}

.todo-header {
  font-family: 'IBM Plex Mono', monospace;
  font-size: 0.8125rem;
  font-weight: 500;
  color: #10b981;
  margin-bottom: 16px;
  display: flex;
  align-items: center;
  gap: 8px;
  letter-spacing: -0.01em;
}

.todo-items {
  list-style: none;
  margin: 0;
  padding: 0;
}

.todo-item {
  display: flex;
  align-items: flex-start;
  gap: 12px;
  padding: 8px 0;
  border-bottom: 1px solid var(--border-light);
  font-family: 'DM Sans', sans-serif;
  font-size: 0.875rem;
}

.todo-item:last-child {
  border-bottom: none;
}

.todo-icon {
  flex-shrink: 0;
  width: 18px;
  height: 18px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 0.75rem;
  font-weight: 600;
}

.todo-completed .todo-icon {
  color: #10b981;
}

.todo-completed .todo-content {
  color: var(--text-tertiary);
  text-decoration: line-through;
}

.todo-in-progress .todo-icon {
  color: #f59e0b;
}

.todo-in-progress .todo-content {
  color: var(--text-primary);
  font-weight: 500;
}

.todo-pending .todo-icon {
  color: var(--text-tertiary);
}

.todo-pending .todo-content {
  color: var(--text-secondary);
}

/* Code blocks */
pre {
  font-family: 'IBM Plex Mono', monospace;
  background: var(--code-bg);
  color: var(--code-text);
  padding: 16px;
  border-radius: 6px;
  overflow-x: auto;
  font-size: 0.8125rem;
  line-height: 1.6;
  margin: 16px 0;
  white-space: pre-wrap;
  word-wrap: break-word;
}

pre.json {
  color: #e4e4e7;
}

code {
  font-family: 'IBM Plex Mono', monospace;
  background: var(--surface-subtle);
  padding: 2px 6px;
  border-radius: 3px;
  font-size: 0.875em;
  color: var(--text-primary);
}

pre code {
  background: none;
  padding: 0;
  color: inherit;
}

.user-content {
  margin: 0;
  font-family: Futura, 'Outfit', 'Trebuchet MS', 'Arial Narrow', sans-serif;
}
/* Truncatable content */
.truncatable {
  position: relative;
}

.truncatable.truncated .truncatable-content {
  max-height: 240px;
  overflow: hidden;
}

.truncatable.truncated::after {
  content: '';
  position: absolute;
  bottom: 40px;
  left: 0;
  right: 0;
  height: 80px;
  background: linear-gradient(to bottom, transparent, var(--bg-color));
  pointer-events: none;
}

.message.user .truncatable.truncated::after {
  background: linear-gradient(to bottom, transparent, var(--bg-user));
}

.message.assistant .truncatable.truncated::after {
  background: linear-gradient(to bottom, transparent, var(--bg-assistant));
}

.message.tool-reply .truncatable.truncated::after {
  background: linear-gradient(to bottom, transparent, var(--bg-tool));
}

.thinking .truncatable.truncated::after {
  background: linear-gradient(to bottom, transparent, var(--bg-thinking));
}

.tool-use .truncatable.truncated::after {
  background: linear-gradient(to bottom, transparent, #e7e5e4);
}

.tool-result .truncatable.truncated::after {
  background: linear-gradient(to bottom, transparent, var(--bg-color));
}

.expand-btn {
  display: none;
  width: 100%;
  padding: 10px 16px;
  margin-top: 8px;
  background: transparent;
  border: 1px solid var(--border-light);
  border-radius: 4px;
  cursor: pointer;
  font-family: 'DM Sans', sans-serif;
  font-size: 0.8125rem;
  color: var(--text-secondary);
  transition: all 0.2s ease;
}

.expand-btn:hover {
  background: var(--surface-subtle);
  border-color: var(--border-medium);
  color: var(--text-primary);
}

.truncatable.truncated .expand-btn,
.truncatable.expanded .expand-btn {
  display: block;
}
/* Pagination */
.pagination {
  display: flex;
  justify-content: center;
  gap: 6px;
  margin: 48px 0 32px 0;
  flex-wrap: wrap;
}

.pagination a,
.pagination span {
  font-family: 'IBM Plex Mono', monospace;
  padding: 8px 12px;
  border-radius: 4px;
  text-decoration: none;
  font-size: 0.8125rem;
  transition: all 0.2s ease;
}

.pagination a {
  background: transparent;
  color: var(--text-secondary);
  border: 1px solid var(--border-light);
}

.pagination a:hover {
  background: var(--surface-subtle);
  border-color: var(--border-medium);
  color: var(--text-primary);
}

.pagination .current {
  background: var(--text-primary);
  color: var(--bg-color);
  border: 1px solid var(--text-primary);
  font-weight: 500;
}

.pagination .disabled {
  color: var(--text-tertiary);
  border: 1px solid var(--border-light);
  opacity: 0.5;
}

.pagination .index-link {
  background: transparent;
  color: var(--text-secondary);
  border: 1px solid var(--border-light);
}

.pagination .index-link:hover {
  background: var(--surface-subtle);
  border-color: var(--border-medium);
  color: var(--text-primary);
}

/* Continuation details */
details.continuation {
  margin-bottom: 32px;
}

details.continuation summary {
  cursor: pointer;
  padding: 16px 20px;
  background: var(--surface-elevated);
  border: 1px solid var(--border-light);
  border-left: 3px solid var(--accent-user);
  border-radius: 6px;
  font-family: 'DM Sans', sans-serif;
  font-size: 0.875rem;
  font-weight: 500;
  color: var(--text-secondary);
  transition: all 0.2s ease;
}

details.continuation summary:hover {
  background: var(--surface-subtle);
  color: var(--text-primary);
}

details.continuation[open] summary {
  border-radius: 6px 6px 0 0;
  margin-bottom: 0;
  border-bottom-color: transparent;
}
/* Index page items */
.index-item {
  margin-bottom: 32px;
  border-radius: 6px;
  overflow: hidden;
  background: var(--surface-elevated);
  border: 1px solid var(--border-light);
  border-left: 3px solid var(--accent-user);
  transition: all 0.2s ease;
}

.index-item a {
  display: block;
  text-decoration: none;
  color: inherit;
}

.index-item a:hover {
  background: var(--surface-subtle);
}

.index-item-header {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  padding: 16px 20px 12px 20px;
  border-bottom: 1px solid var(--border-light);
}

.index-item-number {
  font-family: 'IBM Plex Mono', monospace;
  font-size: 0.75rem;
  font-weight: 600;
  color: var(--accent-user);
  letter-spacing: 0.02em;
}

.index-item-content {
  padding: 16px 20px;
  font-family: Futura, 'Outfit', 'Trebuchet MS', 'Arial Narrow', sans-serif;
  font-size: 1.125rem;
  line-height: 1.6;
  font-weight: 500;
  color: var(--text-primary);
  letter-spacing: -0.01em;
}

.index-item-stats {
  padding: 12px 20px 16px 20px;
  font-family: 'DM Sans', sans-serif;
  font-size: 0.8125rem;
  color: var(--text-tertiary);
  border-top: 1px solid var(--border-light);
}

.index-item-commit {
  margin-top: 8px;
  padding: 6px 10px;
  background: var(--surface-subtle);
  border-radius: 4px;
  font-size: 0.8125rem;
  color: var(--text-secondary);
}

.index-item-commit code {
  font-family: 'IBM Plex Mono', monospace;
  background: var(--surface-elevated);
  padding: 2px 6px;
  border-radius: 3px;
  font-size: 0.75rem;
  margin-right: 6px;
}

/* Commit cards */
.commit-card {
  margin: 16px 0;
  padding: 14px 16px;
  background: var(--surface-elevated);
  border: 1px solid var(--border-light);
  border-left: 3px solid #f59e0b;
  border-radius: 6px;
}

.commit-card a {
  text-decoration: none;
  color: var(--text-primary);
  display: block;
  transition: color 0.2s ease;
}

.commit-card a:hover {
  color: #f59e0b;
}

.commit-card-hash {
  font-family: 'IBM Plex Mono', monospace;
  color: #f59e0b;
  font-weight: 600;
  margin-right: 8px;
  font-size: 0.8125rem;
}

.index-commit {
  margin-bottom: 24px;
  padding: 14px 18px;
  background: var(--surface-elevated);
  border: 1px solid var(--border-light);
  border-left: 3px solid #f59e0b;
  border-radius: 6px;
  transition: all 0.2s ease;
}

.index-commit a {
  display: block;
  text-decoration: none;
  color: inherit;
}

.index-commit a:hover {
  color: #f59e0b;
}

.index-commit:hover {
  background: var(--surface-subtle);
}

.index-commit-header {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  font-size: 0.8125rem;
  margin-bottom: 6px;
}

.index-commit-hash {
  font-family: 'IBM Plex Mono', monospace;
  color: #f59e0b;
  font-weight: 600;
  letter-spacing: 0.02em;
}

.index-commit-msg {
  color: var(--text-primary);
  font-size: 0.9375rem;
  line-height: 1.5;
}

.index-item-long-text {
  margin-top: 12px;
  padding: 16px;
  background: var(--surface-subtle);
  border-radius: 4px;
  border-left: 2px solid var(--border-medium);
}

.index-item-long-text .truncatable.truncated::after {
  background: linear-gradient(to bottom, transparent, var(--surface-subtle));
}

.index-item-long-text-content {
  color: var(--text-secondary);
  font-size: 0.9375rem;
  line-height: 1.65;
}
/* Responsive design */
@media (max-width: 768px) {
  body {
    padding: 24px 16px;
    font-size: 16px;
  }

  .container {
    max-width: 100%;
  }

  h1 {
    font-size: 1.125rem;
    margin-bottom: 32px;
  }

  .message {
    margin-bottom: 40px;
  }

  .user .message-content {
    font-size: 1.125rem;
  }

  .message-content {
    font-size: 16px;
  }

  .thinking p {
    font-size: 0.875rem;
  }

  .index-item-content {
    font-size: 1rem;
  }

  pre {
    font-size: 0.75rem;
    padding: 12px;
  }

  .pagination {
    gap: 4px;
  }

  .pagination a,
  .pagination span {
    padding: 6px 10px;
    font-size: 0.75rem;
  }
}

@media (max-width: 480px) {
  body {
    padding: 20px 12px;
  }

  .message {
    padding-left: 16px;
    margin-bottom: 32px;
  }

  .thinking {
    padding-left: 16px;
  }

  .tool-use,
  .file-tool,
  .todo-list {
    padding: 12px;
  }

  .index-item-header,
  .index-item-content,
  .index-item-stats {
    padding-left: 16px;
    padding-right: 16px;
  }
}
"""

JS = """
document.querySelectorAll('time[data-timestamp]').forEach(function(el) {
    const timestamp = el.getAttribute('data-timestamp');
    const date = new Date(timestamp);
    const now = new Date();
    const isToday = date.toDateString() === now.toDateString();
    const timeStr = date.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' });
    if (isToday) { el.textContent = timeStr; }
    else { el.textContent = date.toLocaleDateString(undefined, { month: 'short', day: 'numeric' }) + ' ' + timeStr; }
});
document.querySelectorAll('pre.json').forEach(function(el) {
    let text = el.textContent;
    text = text.replace(/"([^"]+)":/g, '<span style="color: #ce93d8">"$1"</span>:');
    text = text.replace(/: "([^"]*)"/g, ': <span style="color: #81d4fa">"$1"</span>');
    text = text.replace(/: (\\d+)/g, ': <span style="color: #ffcc80">$1</span>');
    text = text.replace(/: (true|false|null)/g, ': <span style="color: #f48fb1">$1</span>');
    el.innerHTML = text;
});
document.querySelectorAll('.truncatable').forEach(function(wrapper) {
    const content = wrapper.querySelector('.truncatable-content');
    const btn = wrapper.querySelector('.expand-btn');
    if (content.scrollHeight > 250) {
        wrapper.classList.add('truncated');
        btn.addEventListener('click', function() {
            if (wrapper.classList.contains('truncated')) { wrapper.classList.remove('truncated'); wrapper.classList.add('expanded'); btn.textContent = 'Show less'; }
            else { wrapper.classList.remove('expanded'); wrapper.classList.add('truncated'); btn.textContent = 'Show more'; }
        });
    }
});
"""

# JavaScript to fix relative URLs when served via gistpreview.github.io
GIST_PREVIEW_JS = r"""
(function() {
    if (window.location.hostname !== 'gistpreview.github.io') return;
    // URL format: https://gistpreview.github.io/?GIST_ID/filename.html
    var match = window.location.search.match(/^\?([^/]+)/);
    if (!match) return;
    var gistId = match[1];
    document.querySelectorAll('a[href]').forEach(function(link) {
        var href = link.getAttribute('href');
        // Skip external links and anchors
        if (href.startsWith('http') || href.startsWith('#') || href.startsWith('//')) return;
        // Handle anchor in relative URL (e.g., page-001.html#msg-123)
        var parts = href.split('#');
        var filename = parts[0];
        var anchor = parts.length > 1 ? '#' + parts[1] : '';
        link.setAttribute('href', '?' + gistId + '/' + filename + anchor);
    });
})();
"""


def inject_gist_preview_js(output_dir):
    """Inject gist preview JavaScript into all HTML files in the output directory."""
    output_dir = Path(output_dir)
    for html_file in output_dir.glob("*.html"):
        content = html_file.read_text(encoding="utf-8")
        # Insert the gist preview JS before the closing </body> tag
        if "</body>" in content:
            content = content.replace(
                "</body>", f"<script>{GIST_PREVIEW_JS}</script>\n</body>"
            )
            html_file.write_text(content, encoding="utf-8")


def create_gist(output_dir, public=False):
    """Create a GitHub gist from the HTML files in output_dir.

    Returns the gist ID on success, or raises click.ClickException on failure.
    """
    output_dir = Path(output_dir)
    html_files = list(output_dir.glob("*.html"))
    if not html_files:
        raise click.ClickException("No HTML files found to upload to gist.")

    # Build the gh gist create command
    # gh gist create file1 file2 ... --public/--private
    cmd = ["gh", "gist", "create"]
    cmd.extend(str(f) for f in sorted(html_files))
    if public:
        cmd.append("--public")

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=True,
        )
        # Output is the gist URL, e.g., https://gist.github.com/username/GIST_ID
        gist_url = result.stdout.strip()
        # Extract gist ID from URL
        gist_id = gist_url.rstrip("/").split("/")[-1]
        return gist_id, gist_url
    except subprocess.CalledProcessError as e:
        error_msg = e.stderr.strip() if e.stderr else str(e)
        raise click.ClickException(f"Failed to create gist: {error_msg}")
    except FileNotFoundError:
        raise click.ClickException(
            "gh CLI not found. Install it from https://cli.github.com/ and run 'gh auth login'."
        )


def generate_pagination_html(current_page, total_pages):
    return _macros.pagination(current_page, total_pages)


def generate_index_pagination_html(total_pages):
    """Generate pagination for index page where Index is current (first page)."""
    return _macros.index_pagination(total_pages)


def generate_html(json_path, output_dir, github_repo=None):
    output_dir = Path(output_dir)
    output_dir.mkdir(exist_ok=True)

    # Load session file (supports both JSON and JSONL)
    data = parse_session_file(json_path)

    loglines = data.get("loglines", [])

    # Auto-detect GitHub repo if not provided
    if github_repo is None:
        github_repo = detect_github_repo(loglines)
        if github_repo:
            print(f"Auto-detected GitHub repo: {github_repo}")
        else:
            print(
                "Warning: Could not auto-detect GitHub repo. Commit links will be disabled."
            )

    # Set module-level variable for render functions
    global _github_repo
    _github_repo = github_repo

    conversations = []
    current_conv = None
    for entry in loglines:
        log_type = entry.get("type")
        timestamp = entry.get("timestamp", "")
        is_compact_summary = entry.get("isCompactSummary", False)
        message_data = entry.get("message", {})
        if not message_data:
            continue
        # Convert message dict to JSON string for compatibility with existing render functions
        message_json = json.dumps(message_data)
        is_user_prompt = False
        user_text = None
        if log_type == "user":
            content = message_data.get("content", "")
            if isinstance(content, str) and content.strip():
                is_user_prompt = True
                user_text = content
        if is_user_prompt:
            if current_conv:
                conversations.append(current_conv)
            current_conv = {
                "user_text": user_text,
                "timestamp": timestamp,
                "messages": [(log_type, message_json, timestamp)],
                "is_continuation": bool(is_compact_summary),
            }
        elif current_conv:
            current_conv["messages"].append((log_type, message_json, timestamp))
    if current_conv:
        conversations.append(current_conv)

    total_convs = len(conversations)
    total_pages = (total_convs + PROMPTS_PER_PAGE - 1) // PROMPTS_PER_PAGE

    for page_num in range(1, total_pages + 1):
        start_idx = (page_num - 1) * PROMPTS_PER_PAGE
        end_idx = min(start_idx + PROMPTS_PER_PAGE, total_convs)
        page_convs = conversations[start_idx:end_idx]
        messages_html = []
        for conv in page_convs:
            is_first = True
            messages = conv["messages"]

            # Build a global tool_results_map for the entire conversation
            # This maps tool_use_id -> tool_result block across ALL messages
            global_tool_results_map = {}
            for log_type, message_json, _ in messages:
                if log_type == "user" and message_json:
                    try:
                        message_data = json.loads(message_json)
                        if is_tool_result_message(message_data):
                            for block in message_data.get("content", []):
                                if isinstance(block, dict) and block.get("type") == "tool_result":
                                    tool_id = block.get("tool_use_id", "")
                                    if tool_id:
                                        global_tool_results_map[tool_id] = block
                    except json.JSONDecodeError:
                        pass

            for i, (log_type, message_json, timestamp) in enumerate(messages):
                # Pass the global tool results map to assistant messages
                tool_results_map = global_tool_results_map if log_type == "assistant" else None

                msg_html = render_message(log_type, message_json, timestamp, tool_results_map)
                if msg_html:
                    # Wrap continuation summaries in collapsed details
                    if is_first and conv.get("is_continuation"):
                        msg_html = f'<details class="continuation"><summary>Session continuation summary</summary>{msg_html}</details>'
                    messages_html.append(msg_html)
                is_first = False
        pagination_html = generate_pagination_html(page_num, total_pages)
        page_template = get_template("page.html")
        page_content = page_template.render(
            css=CSS,
            js=JS,
            page_num=page_num,
            total_pages=total_pages,
            pagination_html=pagination_html,
            messages_html="".join(messages_html),
        )
        (output_dir / f"page-{page_num:03d}.html").write_text(
            page_content, encoding="utf-8"
        )
        print(f"Generated page-{page_num:03d}.html")

    # Calculate overall stats and collect all commits for timeline
    total_tool_counts = {}
    total_messages = 0
    all_commits = []  # (timestamp, hash, message, page_num, conv_index)
    for i, conv in enumerate(conversations):
        total_messages += len(conv["messages"])
        stats = analyze_conversation(conv["messages"])
        for tool, count in stats["tool_counts"].items():
            total_tool_counts[tool] = total_tool_counts.get(tool, 0) + count
        page_num = (i // PROMPTS_PER_PAGE) + 1
        for commit_hash, commit_msg, commit_ts in stats["commits"]:
            all_commits.append((commit_ts, commit_hash, commit_msg, page_num, i))
    total_tool_calls = sum(total_tool_counts.values())
    total_commits = len(all_commits)

    # Build timeline items: prompts and commits merged by timestamp
    timeline_items = []

    # Add prompts
    prompt_num = 0
    for i, conv in enumerate(conversations):
        if conv.get("is_continuation"):
            continue
        if conv["user_text"].startswith("Stop hook feedback:"):
            continue
        prompt_num += 1
        page_num = (i // PROMPTS_PER_PAGE) + 1
        msg_id = make_msg_id(conv["timestamp"])
        link = f"page-{page_num:03d}.html#{msg_id}"
        rendered_content = render_markdown_text(conv["user_text"])

        # Collect all messages including from subsequent continuation conversations
        # This ensures long_texts from continuations appear with the original prompt
        all_messages = list(conv["messages"])
        for j in range(i + 1, len(conversations)):
            if not conversations[j].get("is_continuation"):
                break
            all_messages.extend(conversations[j]["messages"])

        # Analyze conversation for stats (excluding commits from inline display now)
        stats = analyze_conversation(all_messages)
        tool_stats_str = format_tool_stats(stats["tool_counts"])

        long_texts_html = ""
        for lt in stats["long_texts"]:
            rendered_lt = render_markdown_text(lt)
            long_texts_html += _macros.index_long_text(rendered_lt)

        stats_html = _macros.index_stats(tool_stats_str, long_texts_html)

        item_html = _macros.index_item(
            prompt_num, link, conv["timestamp"], rendered_content, stats_html
        )
        timeline_items.append((conv["timestamp"], "prompt", item_html))

    # Add commits as separate timeline items
    for commit_ts, commit_hash, commit_msg, page_num, conv_idx in all_commits:
        item_html = _macros.index_commit(
            commit_hash, commit_msg, commit_ts, _github_repo
        )
        timeline_items.append((commit_ts, "commit", item_html))

    # Sort by timestamp
    timeline_items.sort(key=lambda x: x[0])
    index_items = [item[2] for item in timeline_items]

    index_pagination = generate_index_pagination_html(total_pages)
    index_template = get_template("index.html")
    index_content = index_template.render(
        css=CSS,
        js=JS,
        pagination_html=index_pagination,
        prompt_num=prompt_num,
        total_messages=total_messages,
        total_tool_calls=total_tool_calls,
        total_commits=total_commits,
        total_pages=total_pages,
        index_items_html="".join(index_items),
    )
    index_path = output_dir / "index.html"
    index_path.write_text(index_content, encoding="utf-8")
    print(
        f"Generated {index_path.resolve()} ({total_convs} prompts, {total_pages} pages)"
    )


@click.group(cls=DefaultGroup, default="local", default_if_no_args=True)
@click.version_option(None, "-v", "--version", package_name="claude-code-transcripts")
def cli():
    """Convert Claude Code session JSON to mobile-friendly HTML pages."""
    pass


@cli.command("local")
@click.option(
    "-o",
    "--output",
    type=click.Path(),
    help="Output directory. If not specified, writes to temp dir and opens in browser.",
)
@click.option(
    "-a",
    "--output-auto",
    is_flag=True,
    help="Auto-name output subdirectory based on session filename (uses -o as parent, or current dir).",
)
@click.option(
    "--repo",
    help="GitHub repo (owner/name) for commit links. Auto-detected from git push output if not specified.",
)
@click.option(
    "--gist",
    is_flag=True,
    help="Upload to GitHub Gist and output a gistpreview.github.io URL.",
)
@click.option(
    "--json",
    "include_json",
    is_flag=True,
    help="Include the original JSONL session file in the output directory.",
)
@click.option(
    "--open",
    "open_browser",
    is_flag=True,
    help="Open the generated index.html in your default browser (default if no -o specified).",
)
@click.option(
    "--limit",
    default=10,
    help="Maximum number of sessions to show (default: 10)",
)
def local_cmd(output, output_auto, repo, gist, include_json, open_browser, limit):
    """Select and convert a local Claude Code session to HTML."""
    from datetime import datetime

    projects_folder = Path.home() / ".claude" / "projects"

    if not projects_folder.exists():
        click.echo(f"Projects folder not found: {projects_folder}")
        click.echo("No local Claude Code sessions available.")
        return

    click.echo("Loading local sessions...")
    results = find_local_sessions(projects_folder, limit=limit)

    if not results:
        click.echo("No local sessions found.")
        return

    # Build choices for questionary
    choices = []
    for filepath, summary in results:
        stat = filepath.stat()
        mod_time = datetime.fromtimestamp(stat.st_mtime)
        size_kb = stat.st_size / 1024
        date_str = mod_time.strftime("%Y-%m-%d %H:%M")
        # Truncate summary if too long
        if len(summary) > 50:
            summary = summary[:47] + "..."
        display = f"{date_str}  {size_kb:5.0f} KB  {summary}"
        choices.append(questionary.Choice(title=display, value=filepath))

    selected = questionary.select(
        "Select a session to convert:",
        choices=choices,
    ).ask()

    if selected is None:
        click.echo("No session selected.")
        return

    session_file = selected

    # Determine output directory and whether to open browser
    # If no -o specified, use temp dir and open browser by default
    auto_open = output is None and not gist and not output_auto
    if output_auto:
        # Use -o as parent dir (or current dir), with auto-named subdirectory
        parent_dir = Path(output) if output else Path(".")
        output = parent_dir / session_file.stem
    elif output is None:
        output = Path(tempfile.gettempdir()) / f"claude-session-{session_file.stem}"

    output = Path(output)
    generate_html(session_file, output, github_repo=repo)

    # Show output directory
    click.echo(f"Output: {output.resolve()}")

    # Copy JSONL file to output directory if requested
    if include_json:
        output.mkdir(exist_ok=True)
        json_dest = output / session_file.name
        shutil.copy(session_file, json_dest)
        json_size_kb = json_dest.stat().st_size / 1024
        click.echo(f"JSONL: {json_dest} ({json_size_kb:.1f} KB)")

    if gist:
        # Inject gist preview JS and create gist
        inject_gist_preview_js(output)
        click.echo("Creating GitHub gist...")
        gist_id, gist_url = create_gist(output)
        preview_url = f"https://gistpreview.github.io/?{gist_id}/index.html"
        click.echo(f"Gist: {gist_url}")
        click.echo(f"Preview: {preview_url}")

    if open_browser or auto_open:
        index_url = (output / "index.html").resolve().as_uri()
        webbrowser.open(index_url)


@cli.command("json")
@click.argument("json_file", type=click.Path(exists=True))
@click.option(
    "-o",
    "--output",
    type=click.Path(),
    help="Output directory. If not specified, writes to temp dir and opens in browser.",
)
@click.option(
    "-a",
    "--output-auto",
    is_flag=True,
    help="Auto-name output subdirectory based on filename (uses -o as parent, or current dir).",
)
@click.option(
    "--repo",
    help="GitHub repo (owner/name) for commit links. Auto-detected from git push output if not specified.",
)
@click.option(
    "--gist",
    is_flag=True,
    help="Upload to GitHub Gist and output a gistpreview.github.io URL.",
)
@click.option(
    "--json",
    "include_json",
    is_flag=True,
    help="Include the original JSON session file in the output directory.",
)
@click.option(
    "--open",
    "open_browser",
    is_flag=True,
    help="Open the generated index.html in your default browser (default if no -o specified).",
)
def json_cmd(json_file, output, output_auto, repo, gist, include_json, open_browser):
    """Convert a Claude Code session JSON/JSONL file to HTML."""
    # Determine output directory and whether to open browser
    # If no -o specified, use temp dir and open browser by default
    auto_open = output is None and not gist and not output_auto
    if output_auto:
        # Use -o as parent dir (or current dir), with auto-named subdirectory
        parent_dir = Path(output) if output else Path(".")
        output = parent_dir / Path(json_file).stem
    elif output is None:
        output = Path(tempfile.gettempdir()) / f"claude-session-{Path(json_file).stem}"

    output = Path(output)
    generate_html(json_file, output, github_repo=repo)

    # Show output directory
    click.echo(f"Output: {output.resolve()}")

    # Copy JSON file to output directory if requested
    if include_json:
        output.mkdir(exist_ok=True)
        json_source = Path(json_file)
        json_dest = output / json_source.name
        shutil.copy(json_file, json_dest)
        json_size_kb = json_dest.stat().st_size / 1024
        click.echo(f"JSON: {json_dest} ({json_size_kb:.1f} KB)")

    if gist:
        # Inject gist preview JS and create gist
        inject_gist_preview_js(output)
        click.echo("Creating GitHub gist...")
        gist_id, gist_url = create_gist(output)
        preview_url = f"https://gistpreview.github.io/?{gist_id}/index.html"
        click.echo(f"Gist: {gist_url}")
        click.echo(f"Preview: {preview_url}")

    if open_browser or auto_open:
        index_url = (output / "index.html").resolve().as_uri()
        webbrowser.open(index_url)


def resolve_credentials(token, org_uuid):
    """Resolve token and org_uuid from arguments or auto-detect.

    Returns (token, org_uuid) tuple.
    Raises click.ClickException if credentials cannot be resolved.
    """
    # Get token
    if token is None:
        token = get_access_token_from_keychain()
        if token is None:
            if platform.system() == "Darwin":
                raise click.ClickException(
                    "Could not retrieve access token from macOS keychain. "
                    "Make sure you are logged into Claude Code, or provide --token."
                )
            else:
                raise click.ClickException(
                    "On non-macOS platforms, you must provide --token with your access token."
                )

    # Get org UUID
    if org_uuid is None:
        org_uuid = get_org_uuid_from_config()
        if org_uuid is None:
            raise click.ClickException(
                "Could not find organization UUID in ~/.claude.json. "
                "Provide --org-uuid with your organization UUID."
            )

    return token, org_uuid


def format_session_for_display(session_data):
    """Format a session for display in the list or picker.

    Returns a formatted string.
    """
    session_id = session_data.get("id", "unknown")
    title = session_data.get("title", "Untitled")
    created_at = session_data.get("created_at", "")
    # Truncate title if too long
    if len(title) > 60:
        title = title[:57] + "..."
    return f"{session_id}  {created_at[:19] if created_at else 'N/A':19}  {title}"


def generate_html_from_session_data(session_data, output_dir, github_repo=None):
    """Generate HTML from session data dict (instead of file path)."""
    output_dir = Path(output_dir)
    output_dir.mkdir(exist_ok=True, parents=True)

    loglines = session_data.get("loglines", [])

    # Auto-detect GitHub repo if not provided
    if github_repo is None:
        github_repo = detect_github_repo(loglines)
        if github_repo:
            click.echo(f"Auto-detected GitHub repo: {github_repo}")

    # Set module-level variable for render functions
    global _github_repo
    _github_repo = github_repo

    conversations = []
    current_conv = None
    for entry in loglines:
        log_type = entry.get("type")
        timestamp = entry.get("timestamp", "")
        is_compact_summary = entry.get("isCompactSummary", False)
        message_data = entry.get("message", {})
        if not message_data:
            continue
        # Convert message dict to JSON string for compatibility with existing render functions
        message_json = json.dumps(message_data)
        is_user_prompt = False
        user_text = None
        if log_type == "user":
            content = message_data.get("content", "")
            if isinstance(content, str) and content.strip():
                is_user_prompt = True
                user_text = content
        if is_user_prompt:
            if current_conv:
                conversations.append(current_conv)
            current_conv = {
                "user_text": user_text,
                "timestamp": timestamp,
                "messages": [(log_type, message_json, timestamp)],
                "is_continuation": bool(is_compact_summary),
            }
        elif current_conv:
            current_conv["messages"].append((log_type, message_json, timestamp))
    if current_conv:
        conversations.append(current_conv)

    total_convs = len(conversations)
    total_pages = (total_convs + PROMPTS_PER_PAGE - 1) // PROMPTS_PER_PAGE

    for page_num in range(1, total_pages + 1):
        start_idx = (page_num - 1) * PROMPTS_PER_PAGE
        end_idx = min(start_idx + PROMPTS_PER_PAGE, total_convs)
        page_convs = conversations[start_idx:end_idx]
        messages_html = []
        for conv in page_convs:
            is_first = True
            messages = conv["messages"]

            # Build a global tool_results_map for the entire conversation
            # This maps tool_use_id -> tool_result block across ALL messages
            global_tool_results_map = {}
            for log_type, message_json, _ in messages:
                if log_type == "user" and message_json:
                    try:
                        message_data = json.loads(message_json)
                        if is_tool_result_message(message_data):
                            for block in message_data.get("content", []):
                                if isinstance(block, dict) and block.get("type") == "tool_result":
                                    tool_id = block.get("tool_use_id", "")
                                    if tool_id:
                                        global_tool_results_map[tool_id] = block
                    except json.JSONDecodeError:
                        pass

            for i, (log_type, message_json, timestamp) in enumerate(messages):
                # Pass the global tool results map to assistant messages
                tool_results_map = global_tool_results_map if log_type == "assistant" else None

                msg_html = render_message(log_type, message_json, timestamp, tool_results_map)
                if msg_html:
                    # Wrap continuation summaries in collapsed details
                    if is_first and conv.get("is_continuation"):
                        msg_html = f'<details class="continuation"><summary>Session continuation summary</summary>{msg_html}</details>'
                    messages_html.append(msg_html)
                is_first = False
        pagination_html = generate_pagination_html(page_num, total_pages)
        page_template = get_template("page.html")
        page_content = page_template.render(
            css=CSS,
            js=JS,
            page_num=page_num,
            total_pages=total_pages,
            pagination_html=pagination_html,
            messages_html="".join(messages_html),
        )
        (output_dir / f"page-{page_num:03d}.html").write_text(
            page_content, encoding="utf-8"
        )
        click.echo(f"Generated page-{page_num:03d}.html")

    # Calculate overall stats and collect all commits for timeline
    total_tool_counts = {}
    total_messages = 0
    all_commits = []  # (timestamp, hash, message, page_num, conv_index)
    for i, conv in enumerate(conversations):
        total_messages += len(conv["messages"])
        stats = analyze_conversation(conv["messages"])
        for tool, count in stats["tool_counts"].items():
            total_tool_counts[tool] = total_tool_counts.get(tool, 0) + count
        page_num = (i // PROMPTS_PER_PAGE) + 1
        for commit_hash, commit_msg, commit_ts in stats["commits"]:
            all_commits.append((commit_ts, commit_hash, commit_msg, page_num, i))
    total_tool_calls = sum(total_tool_counts.values())
    total_commits = len(all_commits)

    # Build timeline items: prompts and commits merged by timestamp
    timeline_items = []

    # Add prompts
    prompt_num = 0
    for i, conv in enumerate(conversations):
        if conv.get("is_continuation"):
            continue
        if conv["user_text"].startswith("Stop hook feedback:"):
            continue
        prompt_num += 1
        page_num = (i // PROMPTS_PER_PAGE) + 1
        msg_id = make_msg_id(conv["timestamp"])
        link = f"page-{page_num:03d}.html#{msg_id}"
        rendered_content = render_markdown_text(conv["user_text"])

        # Collect all messages including from subsequent continuation conversations
        # This ensures long_texts from continuations appear with the original prompt
        all_messages = list(conv["messages"])
        for j in range(i + 1, len(conversations)):
            if not conversations[j].get("is_continuation"):
                break
            all_messages.extend(conversations[j]["messages"])

        # Analyze conversation for stats (excluding commits from inline display now)
        stats = analyze_conversation(all_messages)
        tool_stats_str = format_tool_stats(stats["tool_counts"])

        long_texts_html = ""
        for lt in stats["long_texts"]:
            rendered_lt = render_markdown_text(lt)
            long_texts_html += _macros.index_long_text(rendered_lt)

        stats_html = _macros.index_stats(tool_stats_str, long_texts_html)

        item_html = _macros.index_item(
            prompt_num, link, conv["timestamp"], rendered_content, stats_html
        )
        timeline_items.append((conv["timestamp"], "prompt", item_html))

    # Add commits as separate timeline items
    for commit_ts, commit_hash, commit_msg, page_num, conv_idx in all_commits:
        item_html = _macros.index_commit(
            commit_hash, commit_msg, commit_ts, _github_repo
        )
        timeline_items.append((commit_ts, "commit", item_html))

    # Sort by timestamp
    timeline_items.sort(key=lambda x: x[0])
    index_items = [item[2] for item in timeline_items]

    index_pagination = generate_index_pagination_html(total_pages)
    index_template = get_template("index.html")
    index_content = index_template.render(
        css=CSS,
        js=JS,
        pagination_html=index_pagination,
        prompt_num=prompt_num,
        total_messages=total_messages,
        total_tool_calls=total_tool_calls,
        total_commits=total_commits,
        total_pages=total_pages,
        index_items_html="".join(index_items),
    )
    index_path = output_dir / "index.html"
    index_path.write_text(index_content, encoding="utf-8")
    click.echo(
        f"Generated {index_path.resolve()} ({total_convs} prompts, {total_pages} pages)"
    )


@cli.command("web")
@click.argument("session_id", required=False)
@click.option(
    "-o",
    "--output",
    type=click.Path(),
    help="Output directory. If not specified, writes to temp dir and opens in browser.",
)
@click.option(
    "-a",
    "--output-auto",
    is_flag=True,
    help="Auto-name output subdirectory based on session ID (uses -o as parent, or current dir).",
)
@click.option("--token", help="API access token (auto-detected from keychain on macOS)")
@click.option(
    "--org-uuid", help="Organization UUID (auto-detected from ~/.claude.json)"
)
@click.option(
    "--repo",
    help="GitHub repo (owner/name) for commit links. Auto-detected from git push output if not specified.",
)
@click.option(
    "--gist",
    is_flag=True,
    help="Upload to GitHub Gist and output a gistpreview.github.io URL.",
)
@click.option(
    "--json",
    "include_json",
    is_flag=True,
    help="Include the JSON session data in the output directory.",
)
@click.option(
    "--open",
    "open_browser",
    is_flag=True,
    help="Open the generated index.html in your default browser (default if no -o specified).",
)
def web_cmd(
    session_id,
    output,
    output_auto,
    token,
    org_uuid,
    repo,
    gist,
    include_json,
    open_browser,
):
    """Select and convert a web session from the Claude API to HTML.

    If SESSION_ID is not provided, displays an interactive picker to select a session.
    """
    try:
        token, org_uuid = resolve_credentials(token, org_uuid)
    except click.ClickException:
        raise

    # If no session ID provided, show interactive picker
    if session_id is None:
        try:
            sessions_data = fetch_sessions(token, org_uuid)
        except httpx.HTTPStatusError as e:
            raise click.ClickException(
                f"API request failed: {e.response.status_code} {e.response.text}"
            )
        except httpx.RequestError as e:
            raise click.ClickException(f"Network error: {e}")

        sessions = sessions_data.get("data", [])
        if not sessions:
            raise click.ClickException("No sessions found.")

        # Build choices for questionary
        choices = []
        for s in sessions:
            sid = s.get("id", "unknown")
            title = s.get("title", "Untitled")
            created_at = s.get("created_at", "")
            # Truncate title if too long
            if len(title) > 50:
                title = title[:47] + "..."
            display = f"{created_at[:19] if created_at else 'N/A':19}  {title}"
            choices.append(questionary.Choice(title=display, value=sid))

        selected = questionary.select(
            "Select a session to import:",
            choices=choices,
        ).ask()

        if selected is None:
            # User cancelled
            raise click.ClickException("No session selected.")

        session_id = selected

    # Fetch the session
    click.echo(f"Fetching session {session_id}...")
    try:
        session_data = fetch_session(token, org_uuid, session_id)
    except httpx.HTTPStatusError as e:
        raise click.ClickException(
            f"API request failed: {e.response.status_code} {e.response.text}"
        )
    except httpx.RequestError as e:
        raise click.ClickException(f"Network error: {e}")

    # Determine output directory and whether to open browser
    # If no -o specified, use temp dir and open browser by default
    auto_open = output is None and not gist and not output_auto
    if output_auto:
        # Use -o as parent dir (or current dir), with auto-named subdirectory
        parent_dir = Path(output) if output else Path(".")
        output = parent_dir / session_id
    elif output is None:
        output = Path(tempfile.gettempdir()) / f"claude-session-{session_id}"

    output = Path(output)
    click.echo(f"Generating HTML in {output}/...")
    generate_html_from_session_data(session_data, output, github_repo=repo)

    # Show output directory
    click.echo(f"Output: {output.resolve()}")

    # Save JSON session data if requested
    if include_json:
        output.mkdir(exist_ok=True)
        json_dest = output / f"{session_id}.json"
        with open(json_dest, "w") as f:
            json.dump(session_data, f, indent=2)
        json_size_kb = json_dest.stat().st_size / 1024
        click.echo(f"JSON: {json_dest} ({json_size_kb:.1f} KB)")

    if gist:
        # Inject gist preview JS and create gist
        inject_gist_preview_js(output)
        click.echo("Creating GitHub gist...")
        gist_id, gist_url = create_gist(output)
        preview_url = f"https://gistpreview.github.io/?{gist_id}/index.html"
        click.echo(f"Gist: {gist_url}")
        click.echo(f"Preview: {preview_url}")

    if open_browser or auto_open:
        index_url = (output / "index.html").resolve().as_uri()
        webbrowser.open(index_url)


def main():
    cli()
