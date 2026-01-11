# Plan: Display readable /context output

## Goals
- Detect /context output in user messages using the <local-command-stdout> wrapper plus the "Context Usage" header.
- Convert ANSI escape sequences to styled HTML so the output renders like the CLI.
- Preserve exact spacing with a monospace font and white-space: pre.
- Approximate the CLI look only for the /context block.
- Simulate relevant non-SGR control sequences (cursor moves and erases) to keep layout accurate.
- Keep /context prompts visible in the index.

## Non-goals
- Do not apply ANSI parsing to other messages or tool results.
- Do not change the global transcript styling outside the /context block.

## Implementation steps
1. Detection and extraction
   - Add a helper to identify /context outputs: content is a string containing <local-command-stdout> and "Context Usage".
   - Strip the wrapper tags before rendering.
   - Tests: unit tests for detection/extraction (positive match, negative match, wrapper removal).

2. ANSI parsing: SGR + colors (no new dependency)
   - Implement SGR parsing for reset, bold, dim, italic, underline, reverse, default fg/bg.
   - Support 256-color (38;5 / 48;5) and truecolor (38;2 / 48;2).
   - Tests: unit tests covering SGR toggles, 256-color, truecolor, reverse.

3. ANSI parsing: non-SGR CSI simulation
   - Add cursor moves (A/B/C/D), absolute positioning (H/f), horizontal position (G), save/restore (s/u).
   - Add erases: erase in line (K) and erase in display (J).
   - Ignore bracketed-paste toggles (?2026h/l) and other unsupported sequences gracefully.
   - Maintain a screen buffer with cursor position to preserve layout.
   - Tests: unit tests for cursor movement, overwrite behavior, and erase semantics.

4. HTML rendering
   - Emit HTML with <pre class="ansi-context"> containing spans for styled runs.
   - Escape text content before wrapping in spans.
   - Apply reverse by swapping fg/bg at render time.
   - Tests: unit tests asserting no raw escape codes remain and spans are emitted with expected inline styles.

5. Integration points
   - In user rendering, if /context is detected, render ANSI HTML instead of Markdown.
   - In index rendering, if the prompt is /context, use the same ANSI HTML so it remains visible in the index.
   - Tests: snapshot updates covering /context in both page output and index output.

6. Styling
   - Add .ansi-context CSS for a dark terminal-like background, monospace font, padding, and white-space: pre.
   - Keep existing pre styling untouched for non-/context blocks.
   - Tests: verify snapshots include the new class and styles without affecting other pre blocks.

7. Integration test (end-to-end)
   - Add a focused integration/snapshot test that renders a /context fixture and confirms the output matches the CLI look (no visible ANSI sequences, correct spacing, and colors applied).

8. Validation and commits
   - Run: uv run pytest
   - Run: uv run black .
   - Commit after tests and implementation are green.

## Open questions to confirm during implementation
- Whether to keep or strip the <local-command-stdout> wrapper in the displayed output (current plan: strip).
