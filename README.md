# claude-code-publish

[![PyPI](https://img.shields.io/pypi/v/claude-code-publish.svg)](https://pypi.org/project/claude-code-publish/)
[![Changelog](https://img.shields.io/github/v/release/simonw/claude-code-publish?include_prereleases&label=changelog)](https://github.com/simonw/claude-code-publish/releases)
[![Tests](https://github.com/simonw/claude-code-publish/workflows/Test/badge.svg)](https://github.com/simonw/claude-code-publish/actions?query=workflow%3ATest)
[![License](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](https://github.com/simonw/claude-code-publish/blob/main/LICENSE)

Convert Claude Code `session.json` files to clean, mobile-friendly HTML pages with pagination.

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

When using [Claude Code for web](https://claude.ai/code) you can export your session as a `session.json` file using the `teleport` command (and then hunting around on disk).

This tool converts that JSON into a browseable multi-page HTML transcript.

```bash
claude-code-publish session.json -o output-directory/
```

This will generate:
- `index.html` - an index page with a timeline of prompts and commits
- `page-001.html`, `page-002.html`, etc. - paginated transcript pages

### Options

- `-o, --output DIRECTORY` - output directory (default: current directory)
- `--repo OWNER/NAME` - GitHub repo for commit links (auto-detected from git push output if not specified)
- `--gist` - upload the generated HTML files to a GitHub Gist and output a preview URL
- `--json` - include the original JSON session file in the output directory

### Publishing to GitHub Gist

Use the `--gist` option to automatically upload your transcript to a GitHub Gist and get a shareable preview URL.

If you use that with the `import` command with no other options you can directly select a session to publish to a Gist:

```bash
claude-code-publish import --gist
```
The `--gist` option is available for other commands too:

```bash
claude-code-publish session.json --gist
claude-code-publish import session_01BU6ZZoB7zTHrh9DAspF5hj --gist
```

Each of these will output something like:
```
Gist: https://gist.github.com/username/abc123def456
Preview: https://gistpreview.github.io/?abc123def456/index.html
Files: /var/folders/.../session-id
```

The preview URL uses [gistpreview.github.io](https://gistpreview.github.io/) to render your HTML gist. The tool automatically injects JavaScript to fix relative links when served through gistpreview.

When using `--gist` without `-o`, files are written to a temporary directory (shown in the output). You can combine both options to keep a local copy:

```bash
claude-code-publish session.json -o ./my-transcript --gist
```

**Requirements:** The `--gist` option requires the [GitHub CLI](https://cli.github.com/) (`gh`) to be installed and authenticated (`gh auth login`).

### Including the JSON source

Use the `--json` option to include the original session JSON file in the output directory:

```bash
claude-code-publish session.json -o ./my-transcript --json
```

This will output:
```
JSON: ./my-transcript/session_ABC.json (245.3 KB)
```

The JSON file preserves its original filename. This is useful for archiving the source data alongside the HTML output.

## Importing from Claude API

You can import sessions directly from the Claude API without needing to export a `session.json` file:

```bash
# List available sessions
claude-code-publish list-web

# Import a specific session
claude-code-publish import SESSION_ID -o output-directory/

# Import with interactive session picker
claude-code-publish import

# Import and publish to gist
claude-code-publish import SESSION_ID --gist

# Import and save the JSON session data
claude-code-publish import SESSION_ID --json
```

On macOS, the API credentials are automatically retrieved from your keychain (requires being logged into Claude Code). On other platforms, provide `--token` and `--org-uuid` manually.

The `--json` option for the import command saves the session data fetched from the API as `{session_id}.json` in the output directory.

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
