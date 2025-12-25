# claude-code-publish

[![PyPI](https://img.shields.io/pypi/v/claude-code-publish.svg)](https://pypi.org/project/claude-code-publish/)
[![Changelog](https://img.shields.io/github/v/release/simonw/claude-code-publish?include_prereleases&label=changelog)](https://github.com/simonw/claude-code-publish/releases)
[![Tests](https://github.com/simonw/claude-code-publish/workflows/Test/badge.svg)](https://github.com/simonw/claude-code-publish/actions?query=workflow%3ATest)
[![License](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](https://github.com/simonw/claude-code-publish/blob/main/LICENSE)

Convert Claude Code session files (JSON or JSONL) to clean, mobile-friendly HTML pages with pagination.

[Example transcript](https://static.simonwillison.net/static/2025/claude-code-microjs/index.html) produced using this tool.


## Installation

Install this tool using `uv`:
```bash
uv tool install claude-code-publish
```
Or run it without installing:
```bash
uvx claude-code-publish --help
```

## Usage

This tool converts Claude Code session files into browseable multi-page HTML transcripts.

There are three commands available:

- `local` (default) - select from local Claude Code sessions stored in `~/.claude/projects`
- `web` - select from web sessions via the Claude API
- `json` - convert a specific JSON or JSONL session file

The quickest way to view a recent local session:

```bash
claude-code-publish
```

This shows an interactive picker to select a session, generates HTML, and opens it in your default browser.

### Output options

All commands support these options:

- `-o, --output DIRECTORY` - output directory (default: writes to temp dir and opens browser)
- `-a, --output-auto` - auto-name output subdirectory based on session ID or filename
- `--repo OWNER/NAME` - GitHub repo for commit links (auto-detected from git push output if not specified)
- `--open` - open the generated `index.html` in your default browser (default if no `-o` specified)
- `--gist` - upload the generated HTML files to a GitHub Gist and output a preview URL
- `--json` - include the original session file in the output directory

The generated output includes:
- `index.html` - an index page with a timeline of prompts and commits
- `page-001.html`, `page-002.html`, etc. - paginated transcript pages

### Local sessions

Local Claude Code sessions are stored as JSONL files in `~/.claude/projects`. Run with no arguments to select from recent sessions:

```bash
claude-code-publish
# or explicitly:
claude-code-publish local
```

Use `--limit` to control how many sessions are shown (default: 10):

```bash
claude-code-publish local --limit 20
```

### Web sessions

Import sessions directly from the Claude API:

```bash
# Interactive session picker
claude-code-publish web

# Import a specific session by ID
claude-code-publish web SESSION_ID

# Import and publish to gist
claude-code-publish web SESSION_ID --gist
```

On macOS, API credentials are automatically retrieved from your keychain (requires being logged into Claude Code). On other platforms, provide `--token` and `--org-uuid` manually.

### JSON/JSONL files

Convert a specific session file directly:

```bash
claude-code-publish json session.json -o output-directory/
claude-code-publish json session.jsonl --open
```

When using [Claude Code for web](https://claude.ai/code) you can export your session as a `session.json` file using the `teleport` command.

### Auto-naming output directories

Use `-a/--output-auto` to automatically create a subdirectory named after the session:

```bash
# Creates ./session_ABC123/ subdirectory
claude-code-publish web SESSION_ABC123 -a

# Creates ./transcripts/session_ABC123/ subdirectory
claude-code-publish web SESSION_ABC123 -o ./transcripts -a
```

### Publishing to GitHub Gist

Use the `--gist` option to automatically upload your transcript to a GitHub Gist and get a shareable preview URL:

```bash
claude-code-publish --gist
claude-code-publish web --gist
claude-code-publish json session.json --gist
```

This will output something like:
```
Gist: https://gist.github.com/username/abc123def456
Preview: https://gistpreview.github.io/?abc123def456/index.html
Files: /var/folders/.../session-id
```

The preview URL uses [gistpreview.github.io](https://gistpreview.github.io/) to render your HTML gist. The tool automatically injects JavaScript to fix relative links when served through gistpreview.

Combine with `-o` to keep a local copy:

```bash
claude-code-publish json session.json -o ./my-transcript --gist
```

**Requirements:** The `--gist` option requires the [GitHub CLI](https://cli.github.com/) (`gh`) to be installed and authenticated (`gh auth login`).

### Including the source file

Use the `--json` option to include the original session file in the output directory:

```bash
claude-code-publish json session.json -o ./my-transcript --json
```

This will output:
```
JSON: ./my-transcript/session_ABC.json (245.3 KB)
```

This is useful for archiving the source data alongside the HTML output.

## Development

To contribute to this tool, first checkout the code. You can run the tests using `uv run`:
```bash
cd claude-code-publish
uv run pytest
```
And run your local development copy of the tool like this:
```bash
uv run claude-code-publish --help
```
