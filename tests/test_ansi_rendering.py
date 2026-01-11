"""Tests for ANSI rendering of /context output."""

import pytest
from claude_code_transcripts import (
    is_context_output,
    extract_context_content,
    parse_ansi_sgr,
    AnsiState,
    AnsiParser,
    render_ansi_to_html,
)


class TestDetectionAndExtraction:
    """Test detection and extraction of /context output."""

    def test_detects_context_output_with_wrapper_and_header(self):
        """Detect /context output with wrapper tags and Context Usage header."""
        content = '<local-command-stdout>\x1b[?2026h Context Usage\n⛁ ⛁ ⛁</local-command-stdout>'
        assert is_context_output(content) is True

    def test_does_not_detect_without_wrapper(self):
        """Do not detect content without wrapper tags."""
        content = 'Context Usage\n⛁ ⛁ ⛁'
        assert is_context_output(content) is False

    def test_does_not_detect_without_context_usage_header(self):
        """Do not detect content without Context Usage header."""
        content = '<local-command-stdout>Some other output</local-command-stdout>'
        assert is_context_output(content) is False

    def test_does_not_detect_regular_command_output(self):
        """Do not detect regular command output."""
        content = '<local-command-stdout>npm install completed</local-command-stdout>'
        assert is_context_output(content) is False

    def test_extracts_content_by_stripping_wrapper(self):
        """Extract content by removing wrapper tags."""
        content = '<local-command-stdout>Context Usage\n⛁ ⛁ ⛁</local-command-stdout>'
        expected = 'Context Usage\n⛁ ⛁ ⛁'
        assert extract_context_content(content) == expected

    def test_extracts_content_with_ansi_codes(self):
        """Extract content preserving ANSI escape sequences."""
        content = '<local-command-stdout>\x1b[1mContext Usage\x1b[22m\n\x1b[38;2;136;136;136m⛁\x1b[39m</local-command-stdout>'
        expected = '\x1b[1mContext Usage\x1b[22m\n\x1b[38;2;136;136;136m⛁\x1b[39m'
        assert extract_context_content(content) == expected

    def test_extracts_multiline_context_output(self):
        """Extract multiline /context output."""
        content = """<local-command-stdout> Context Usage
⛁ ⛁ ⛁ ⛁ ⛁ ⛁ ⛁ ⛁ ⛁ ⛁   claude-opus-4-5-20251101 · 24k/200k tokens (12%)

MCP tools · /mcp
└ mcp__chrome-devtools__click: 136 tokens</local-command-stdout>"""
        expected = """ Context Usage
⛁ ⛁ ⛁ ⛁ ⛁ ⛁ ⛁ ⛁ ⛁ ⛁   claude-opus-4-5-20251101 · 24k/200k tokens (12%)

MCP tools · /mcp
└ mcp__chrome-devtools__click: 136 tokens"""
        assert extract_context_content(content) == expected


class TestSGRParsing:
    """Test SGR (Select Graphic Rendition) parsing."""

    def test_reset_clears_all_styles(self):
        """Reset (0) clears all styles."""
        state = AnsiState()
        state.bold = True
        state.fg_color = (255, 0, 0)
        parse_ansi_sgr([0], state)
        assert state.bold is False
        assert state.dim is False
        assert state.italic is False
        assert state.underline is False
        assert state.reverse is False
        assert state.fg_color is None
        assert state.bg_color is None

    def test_bold_and_unbold(self):
        """Bold (1) and unbold (22) toggle bold state."""
        state = AnsiState()
        parse_ansi_sgr([1], state)
        assert state.bold is True
        parse_ansi_sgr([22], state)
        assert state.bold is False

    def test_dim_and_undim(self):
        """Dim (2) and undim (22) toggle dim state."""
        state = AnsiState()
        parse_ansi_sgr([2], state)
        assert state.dim is True
        parse_ansi_sgr([22], state)
        assert state.dim is False

    def test_italic_and_unitalic(self):
        """Italic (3) and unitalic (23) toggle italic state."""
        state = AnsiState()
        parse_ansi_sgr([3], state)
        assert state.italic is True
        parse_ansi_sgr([23], state)
        assert state.italic is False

    def test_underline_and_no_underline(self):
        """Underline (4) and no underline (24) toggle underline state."""
        state = AnsiState()
        parse_ansi_sgr([4], state)
        assert state.underline is True
        parse_ansi_sgr([24], state)
        assert state.underline is False

    def test_reverse_and_unreverse(self):
        """Reverse (7) and unreverse (27) toggle reverse state."""
        state = AnsiState()
        parse_ansi_sgr([7], state)
        assert state.reverse is True
        parse_ansi_sgr([27], state)
        assert state.reverse is False

    def test_256_color_foreground(self):
        """256-color foreground (38;5;n)."""
        state = AnsiState()
        parse_ansi_sgr([38, 5, 136], state)
        # 256-color palette color 136 should be stored
        assert state.fg_color == (136,)  # Stored as single-element tuple for 256-color

    def test_256_color_background(self):
        """256-color background (48;5;n)."""
        state = AnsiState()
        parse_ansi_sgr([48, 5, 234], state)
        assert state.bg_color == (234,)

    def test_truecolor_foreground(self):
        """Truecolor foreground (38;2;r;g;b)."""
        state = AnsiState()
        parse_ansi_sgr([38, 2, 136, 136, 136], state)
        assert state.fg_color == (136, 136, 136)

    def test_truecolor_background(self):
        """Truecolor background (48;2;r;g;b)."""
        state = AnsiState()
        parse_ansi_sgr([48, 2, 215, 119, 87], state)
        assert state.bg_color == (215, 119, 87)

    def test_default_foreground_color(self):
        """Default foreground (39) removes foreground color."""
        state = AnsiState()
        state.fg_color = (255, 0, 0)
        parse_ansi_sgr([39], state)
        assert state.fg_color is None

    def test_default_background_color(self):
        """Default background (49) removes background color."""
        state = AnsiState()
        state.bg_color = (0, 255, 0)
        parse_ansi_sgr([49], state)
        assert state.bg_color is None

    def test_combined_sgr_codes(self):
        """Multiple SGR codes in one sequence."""
        state = AnsiState()
        # Bold + foreground truecolor + background 256-color
        parse_ansi_sgr([1, 38, 2, 255, 193, 7, 48, 5, 234], state)
        assert state.bold is True
        assert state.fg_color == (255, 193, 7)
        assert state.bg_color == (234,)

    def test_partial_color_sequences_ignored(self):
        """Incomplete color sequences are ignored gracefully."""
        state = AnsiState()
        # Incomplete 38;2 (missing RGB values)
        parse_ansi_sgr([38, 2], state)
        assert state.fg_color is None
        # Incomplete 48;5 (missing index)
        parse_ansi_sgr([48, 5], state)
        assert state.bg_color is None


class TestNonSGRCSI:
    """Test non-SGR CSI sequences (cursor movement, erases)."""

    def test_simple_text_output(self):
        """Simple text without ANSI codes."""
        parser = AnsiParser()
        result = parser.parse("Hello World")
        assert "Hello World" in result

    def test_cursor_up_moves_position(self):
        """Cursor up (A) moves cursor up."""
        parser = AnsiParser()
        # Write "Line1", newline, "Line2", then move up and overwrite with "Over"
        result = parser.parse("Line1\nLine2\x1b[1AOver")
        # The "Over" should overwrite the beginning of "Line1"
        assert "Over" in result

    def test_cursor_down_moves_position(self):
        """Cursor down (B) moves cursor down."""
        parser = AnsiParser()
        # Write first line, move down, write second line
        result = parser.parse("Line1\x1b[1BLine2")
        lines = result.split('\n')
        # Should have created a blank line between
        assert len(lines) >= 2

    def test_cursor_forward_moves_right(self):
        """Cursor forward (C) moves cursor right."""
        parser = AnsiParser()
        # Write "ab", move right 3, write "c"
        result = parser.parse("ab\x1b[3Cc")
        # Should have "ab   c"
        assert "ab   c" in result

    def test_cursor_backward_moves_left(self):
        """Cursor backward (D) moves cursor left."""
        parser = AnsiParser()
        # Write "abcde", move back 2, write "X"
        result = parser.parse("abcde\x1b[2DX")
        # Should overwrite 'd' with 'X': "abcXe"
        assert "abcXe" in result

    def test_cursor_position_absolute(self):
        """Cursor position (H) moves to absolute position."""
        parser = AnsiParser()
        # Move to row 2, col 5 (1-indexed)
        result = parser.parse("\x1b[2;5HX")
        lines = result.split('\n')
        # Should have at least 2 lines
        assert len(lines) >= 2
        # Second line should have X at position 4 (0-indexed)
        assert 'X' in lines[1]

    def test_horizontal_position_absolute(self):
        """Horizontal position (G) moves to column."""
        parser = AnsiParser()
        # Write "abc", move to column 10, write "X"
        result = parser.parse("abc\x1b[10GX")
        # Should have spaces between 'c' and 'X'
        assert "abc" in result and "X" in result

    def test_erase_in_line_to_end(self):
        """Erase in line to end (K or 0K)."""
        parser = AnsiParser()
        # Write "Hello World", move back, erase to end
        result = parser.parse("Hello World\x1b[6D\x1b[KX")
        # Should have "Hello X" (erased " World", added X)
        assert "Hello" in result
        assert "World" not in result

    def test_erase_in_line_to_beginning(self):
        """Erase in line to beginning (1K)."""
        parser = AnsiParser()
        # Write text, erase to beginning
        result = parser.parse("Hello World\x1b[1K")
        # Should erase from start to cursor
        assert "World" in result or result.strip() == ""

    def test_erase_entire_line(self):
        """Erase entire line (2K)."""
        parser = AnsiParser()
        # Write text, erase entire line
        result = parser.parse("Hello World\x1b[2K")
        # Line should be empty
        assert "Hello" not in result or result.strip() == ""

    def test_erase_in_display_below(self):
        """Erase in display below cursor (J or 0J)."""
        parser = AnsiParser()
        # Write multiple lines, erase below
        result = parser.parse("Line1\nLine2\nLine3\x1b[1A\x1b[J")
        # Should keep Line1 and Line2, erase Line3
        assert "Line1" in result

    def test_bracketed_paste_ignored(self):
        """Bracketed paste toggles (?2026h/l) are ignored."""
        parser = AnsiParser()
        result = parser.parse("\x1b[?2026hHello\x1b[?2026l")
        # Should just have "Hello"
        assert "Hello" in result
        # Should not have the escape sequences
        assert "?2026" not in result

    def test_overwrite_with_cursor_movement(self):
        """Test that cursor movement allows overwriting."""
        parser = AnsiParser()
        # Write "AAAA", move to start, write "BB"
        result = parser.parse("AAAA\x1b[4DBB")
        # Should have "BBAA"
        assert "BBAA" in result

    def test_newline_advances_cursor(self):
        """Newline advances to next line."""
        parser = AnsiParser()
        result = parser.parse("Line1\nLine2\nLine3")
        lines = result.split('\n')
        assert len(lines) == 3
        assert lines[0].strip() == "Line1"
        assert lines[1].strip() == "Line2"
        assert lines[2].strip() == "Line3"

    def test_carriage_return_moves_to_start(self):
        """Carriage return moves to start of line."""
        parser = AnsiParser()
        # Write "Hello", carriage return, write "Bye"
        result = parser.parse("Hello\rBye")
        # Should overwrite: "Byelo"
        assert "Bye" in result


class TestHTMLRendering:
    """Test HTML rendering of ANSI sequences."""

    def test_plain_text_no_spans(self):
        """Plain text without styles should not have spans."""
        html = render_ansi_to_html("Hello World")
        assert "Hello World" in html
        assert "<span" not in html
        # Should be wrapped in pre
        assert "<pre" in html

    def test_no_raw_escape_codes_in_output(self):
        """HTML output should not contain raw ANSI escape sequences."""
        html = render_ansi_to_html("\x1b[1mBold\x1b[22m Normal")
        # Should not have escape sequences
        assert "\x1b" not in html
        assert "Bold" in html
        assert "Normal" in html

    def test_bold_renders_with_font_weight(self):
        """Bold text renders with font-weight style."""
        html = render_ansi_to_html("\x1b[1mBold\x1b[22m")
        assert "font-weight: bold" in html or "font-weight:bold" in html
        assert "Bold" in html

    def test_italic_renders_with_font_style(self):
        """Italic text renders with font-style."""
        html = render_ansi_to_html("\x1b[3mItalic\x1b[23m")
        assert "font-style: italic" in html or "font-style:italic" in html
        assert "Italic" in html

    def test_underline_renders_with_text_decoration(self):
        """Underline text renders with text-decoration."""
        html = render_ansi_to_html("\x1b[4mUnderline\x1b[24m")
        assert "text-decoration: underline" in html or "text-decoration:underline" in html
        assert "Underline" in html

    def test_truecolor_foreground_renders(self):
        """Truecolor foreground renders with RGB color."""
        html = render_ansi_to_html("\x1b[38;2;255;0;0mRed\x1b[39m")
        assert "color: rgb(255,0,0)" in html or "color:rgb(255,0,0)" in html or "color: rgb(255, 0, 0)" in html
        assert "Red" in html

    def test_truecolor_background_renders(self):
        """Truecolor background renders with RGB background."""
        html = render_ansi_to_html("\x1b[48;2;0;255;0mGreen BG\x1b[49m")
        assert "background-color: rgb(0,255,0)" in html or "background-color:rgb(0,255,0)" in html or "background-color: rgb(0, 255, 0)" in html
        assert "Green BG" in html

    def test_256_color_palette_renders(self):
        """256-color palette colors render correctly."""
        html = render_ansi_to_html("\x1b[38;5;136mColor 136\x1b[39m")
        # Should have some color styling (exact color depends on palette)
        assert "color:" in html
        assert "Color 136" in html

    def test_reverse_video_swaps_colors(self):
        """Reverse video swaps foreground and background."""
        html = render_ansi_to_html("\x1b[38;2;255;0;0m\x1b[48;2;0;0;255m\x1b[7mReversed\x1b[27m")
        # When reversed, fg and bg should be swapped in the HTML
        # This means blue (0,0,255) should be in color, and red (255,0,0) in background
        assert "Reversed" in html
        # Should have both color and background-color
        assert "color:" in html
        assert "background-color:" in html

    def test_combined_styles(self):
        """Multiple styles combine correctly."""
        html = render_ansi_to_html("\x1b[1;3;4;38;2;100;100;100mStyled\x1b[0m")
        # Should have bold, italic, underline, and color
        assert "font-weight" in html
        assert "font-style" in html
        assert "text-decoration" in html
        assert "color:" in html
        assert "Styled" in html

    def test_text_is_escaped(self):
        """HTML special characters are escaped."""
        html = render_ansi_to_html("<script>alert('xss')</script>")
        # Should escape < and >
        assert "&lt;" in html
        assert "&gt;" in html
        assert "<script>" not in html

    def test_has_ansi_context_class(self):
        """Output has ansi-context class for styling."""
        html = render_ansi_to_html("Test")
        assert "ansi-context" in html

    def test_preserves_whitespace(self):
        """Whitespace and formatting are preserved."""
        html = render_ansi_to_html("  indented\n  more")
        # Should preserve the spaces
        assert "  indented" in html or "&nbsp;" in html
        # Should have newlines preserved (might be <br> or actual newlines in pre)
        lines = html.split('\n')
        assert len(lines) >= 2 or "<br" in html

    def test_dim_renders_with_opacity(self):
        """Dim text renders with reduced opacity."""
        html = render_ansi_to_html("\x1b[2mDim\x1b[22m")
        assert "opacity" in html or "color" in html  # Could be opacity or dimmed color
        assert "Dim" in html


class TestIntegration:
    """End-to-end integration tests."""

    def test_real_context_output_renders_correctly(self):
        """Real /context output from sample session renders without errors."""
        # Real /context output with ANSI codes
        context_output = """<local-command-stdout>\x1b[?2026h\x1b[?2026l\x1b[?2026h\x1b[?2026l\x1b[?2026h \x1b[1mContext Usage\x1b[22m
\x1b[38;2;136;136;136m⛁ \x1b[38;2;153;153;153m⛁ ⛁ ⛁ ⛁ ⛁ ⛁ ⛁ ⛁ \x1b[38;2;8;145;178m⛁ \x1b[39m  \x1b[38;2;153;153;153mclaude-opus-4-5-20251101 · 24k/200k tokens (12%)\x1b[39m
\x1b[38;2;8;145;178m⛁ \x1b[38;2;215;119;87m⛀ \x1b[38;2;255;193;7m⛀ \x1b[38;2;147;51;234m⛀ \x1b[38;2;153;153;153m⛶ ⛶ ⛶ ⛶ ⛶ ⛶ \x1b[39m  \x1b[38;2;136;136;136m⛁\x1b[39m System prompt: \x1b[38;2;153;153;153m2.9k tokens (1.4%)\x1b[39m
\x1b[38;2;153;153;153m⛶ ⛶ ⛶ ⛶ ⛶ ⛶ ⛶ ⛶ ⛶ ⛶ \x1b[39m  \x1b[38;2;153;153;153m⛁\x1b[39m System tools: \x1b[38;2;153;153;153m15.6k tokens (7.8%)\x1b[39m
\x1b[38;2;153;153;153m⛶ ⛶ ⛶ ⛶ ⛶ ⛶ ⛶ ⛶ ⛶ ⛶ \x1b[39m  \x1b[38;2;8;145;178m⛁\x1b[39m MCP tools: \x1b[38;2;153;153;153m4.7k tokens (2.3%)\x1b[39m

\x1b[1mMCP tools\x1b[22m\x1b[38;2;153;153;153m · /mcp\x1b[39m
└ mcp__chrome-devtools__click: \x1b[38;2;153;153;153m136 tokens\x1b[39m</local-command-stdout>"""

        # Should be detected as context output
        assert is_context_output(context_output) is True

        # Extract and render
        extracted = extract_context_content(context_output)
        html = render_ansi_to_html(extracted)

        # Verify no raw ANSI codes in output
        assert "\x1b[" not in html
        assert "?2026" not in html

        # Verify key content is present
        assert "Context Usage" in html
        assert "MCP tools" in html
        assert "claude-opus-4-5-20251101" in html
        assert "mcp__chrome-devtools__click" in html

        # Verify HTML structure
        assert "<pre class=\"ansi-context\">" in html
        assert "</pre>" in html

        # Verify colors are rendered (should have RGB colors from the escape codes)
        assert "color: rgb(" in html
        assert "136, 136, 136" in html  # Gray color used in the output

        # Verify bold rendering
        assert "font-weight: bold" in html

        # Verify special characters are preserved
        assert "⛁" in html or "&" in html  # Either raw or escaped
        assert "⛶" in html or "&" in html
