When reviewing work aided by coding agents I've often wanted to explore the chronological transcript side-by-side with the code. I find having the provanence easily explorable is valuable to keeping my mental model aligned with the codebase. I've been messing with different UI concepts around this & was inspired by the release of claude-code-transcripts to bring these ideas here.

Here's an example of what this looks like, [the complete session of building this feature]().

Along the way building this, "we" (Claude Code & I) discovered an [infinite loop bug in python-markdown](https://github.com/Python-Markdown/markdown/pull/1579). The discovery & fix are memorialized in this session. Humoriously, this lead to the discovery of another bug in `claude-code-transcripts` if the transcript contains unterminated HTML comments. This PR also fixes this.

In a nutshell, this PR adds a new flag, `--code-view` that works for `local`, `json`, & `web` generation.  Under the hood, this is how it works:

  1. Extract Operations

- Parses session loglines for Write, Edit, and Bash tool calls
- Creates FileOperation objects with file path, content/diff, timestamp, and message ID
- Detects rm commands in Bash calls → OP_DELETE operations

  2. Build Temp Git Repo

- Creates a temporary git repository
- Replays operations chronologically as commits:
  - Write → writes file content
  - Edit → applies string replacement (with resync from actual repo if needed)
  - Delete → removes file from repo
- Each commit stores metadata (tool_id, msg_id, timestamp) in the commit message

  3. Generate Blame Data

- Runs git blame on each file in the final repo state
- Groups consecutive lines by commit → BlameRange objects
- Maps ranges back to the original prompt/message that made the change

  4. Render HTML

- File tree: Built from files that exist in final repo state (deleted files excluded)
- Code panel: Uses CodeMirror 6 with custom decorations for blame highlighting
- Transcript panel: Full conversation with click-to-navigate links
- Blame ranges link code lines ↔ transcript messages bidirectionally

The UI is far from perfect. I tried to stay true to not introducing any frontend frameworks with the exception of CodeMirror (which is loaded from `esm.sh`). We did some work on performance optimizations for huge sessions. For smaller sessions, it's much more nimble. For example:

  ```bash
  $ uv run claude-code-transcripts json https://gist.githubusercontent.com/simonw/bfe117b6007b9d7dfc5a81e4b2fd3d9a/raw/31e9df7c09c8a10c6fbd257aefa47dfa3f7863e5/3f5f590c-2795-4de2-875a-aa3686d523a1.jsonl --code-view --gist

  Fetching session from URL...
  Warning: Could not auto-detect GitHub repo. Commit links will be disabled.
  Generated page-001.html
  Generated /private/var/folders/sl/rhfr008x7s56dc6bsbnwh3qh0000gn/T/claude-session-tmp9n674xpt/index.html (2 prompts, 1 pages)
  Generated code.html (7 files)
  Output: /private/var/folders/sl/rhfr008x7s56dc6bsbnwh3qh0000gn/T/claude-session-tmp9n674xpt
  Creating GitHub gist...
  Gist: https://gist.github.com/btucker/c0fdb0e0a763a983a04a5475bb63954e
  Preview: https://gistpreview.github.io/?c0fdb0e0a763a983a04a5475bb63954e/index.html
  ```

-> [See resulting code view](https://gistpreview.github.io/?c0fdb0e0a763a983a04a5475bb63954e/code.html#%2Ftmp%2Fhttp-proxy-server%2Ftests%2Ftest_proxy.py:L10)

I ran into an issue that gistpreview relies listing all files in the gist. This fails if you exceed the size limit as then every file after you hit the limit is blank. To work around this I adopted an approach of putting data files in a separate gist. These can then be loaded via the raw API & doesn't have the same limitations.  This was a pre-existing issue, but exacerbated by the code view. Search is also updated to make use of these data files instead of the HTML if available. This ended up being a large part of the PR.

Also added are e2e tests with python-playwright. This was very helpful as the complexity of the code view UI increased.

This is obviously a huge PR, and no hard feelings if you do not want to accept it into the main codebase.
