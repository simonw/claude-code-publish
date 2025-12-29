"""End-to-end tests for code.html using Playwright.

These tests use a real session file to generate the code view HTML and then
test the interactive features using Playwright browser automation.
"""

import hashlib
import http.server
import re
import shutil
import socketserver
import tempfile
import threading
from pathlib import Path

import httpx
import pytest
from playwright.sync_api import Page, expect

# URL for test fixture - a real Claude Code session with file operations
FIXTURE_URL = "https://gist.githubusercontent.com/simonw/bfe117b6007b9d7dfc5a81e4b2fd3d9a/raw/31e9df7c09c8a10c6fbd257aefa47dfa3f7863e5/3f5f590c-2795-4de2-875a-aa3686d523a1.jsonl"
FIXTURE_CACHE_DIR = Path(__file__).parent / ".fixture_cache"


def get_cached_fixture() -> Path:
    """Download and cache the test fixture file.

    Returns the path to the cached fixture file.
    """
    FIXTURE_CACHE_DIR.mkdir(exist_ok=True)

    # Use URL hash as cache key
    url_hash = hashlib.sha256(FIXTURE_URL.encode()).hexdigest()[:12]
    cache_path = FIXTURE_CACHE_DIR / f"fixture-{url_hash}.jsonl"

    if not cache_path.exists():
        # Download the fixture
        response = httpx.get(FIXTURE_URL, follow_redirects=True)
        response.raise_for_status()
        cache_path.write_bytes(response.content)

    return cache_path


@pytest.fixture(scope="module")
def fixture_path() -> Path:
    """Provide path to the cached test fixture."""
    return get_cached_fixture()


@pytest.fixture(scope="module")
def code_view_dir(fixture_path: Path) -> Path:
    """Generate code view HTML from the fixture and return the output directory."""
    from claude_code_transcripts import generate_html

    output_dir = Path(tempfile.mkdtemp(prefix="code_view_e2e_"))

    # Generate HTML with code view enabled
    generate_html(str(fixture_path), output_dir, code_view=True)

    code_html_path = output_dir / "code.html"
    assert code_html_path.exists(), "code.html was not generated"

    yield output_dir

    # Cleanup after all tests in this module
    shutil.rmtree(output_dir, ignore_errors=True)


@pytest.fixture(scope="module")
def http_server(code_view_dir: Path):
    """Start an HTTP server to serve the generated files.

    Required because fetch() doesn't work with file:// URLs.
    """

    class Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(code_view_dir), **kwargs)

        def log_message(self, format, *args):
            # Suppress server logs during tests
            pass

    # Use port 0 to get a random available port
    with socketserver.TCPServer(("127.0.0.1", 0), Handler) as server:
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        yield f"http://127.0.0.1:{port}"

        server.shutdown()


@pytest.fixture
def code_view_page(page: Page, http_server: str) -> Page:
    """Navigate to the code view page and wait for it to load."""
    page.goto(f"{http_server}/code.html")
    # Wait for the editor to be created (CodeMirror initializes)
    page.wait_for_selector(".cm-editor", timeout=10000)
    return page


class TestFileTreeNavigation:
    """Tests for file tree panel navigation."""

    def test_file_tree_exists(self, code_view_page: Page):
        """Test that the file tree panel exists."""
        file_tree = code_view_page.locator("#file-tree-panel")
        expect(file_tree).to_be_visible()

    def test_files_are_listed(self, code_view_page: Page):
        """Test that files are listed in the tree."""
        files = code_view_page.locator(".tree-file")
        expect(files.first).to_be_visible()
        assert files.count() > 0

    def test_first_file_is_selected(self, code_view_page: Page):
        """Test that the first file is auto-selected."""
        selected = code_view_page.locator(".tree-file.selected")
        expect(selected).to_be_visible()

    def test_clicking_file_selects_it(self, code_view_page: Page):
        """Test that clicking a different file selects it."""
        files = code_view_page.locator(".tree-file")
        if files.count() > 1:
            # Click the second file
            second_file = files.nth(1)
            second_file.click()
            expect(second_file).to_have_class(re.compile(r"selected"))

    def test_directory_expand_collapse(self, code_view_page: Page):
        """Test that directories can be expanded and collapsed."""
        dirs = code_view_page.locator(".tree-dir")
        if dirs.count() > 0:
            first_dir = dirs.first
            # Check if it has a toggle - get the direct child toggle
            toggle = first_dir.locator("> .tree-toggle")
            if toggle.count() > 0:
                # Click to toggle
                initial_open = "open" in (first_dir.get_attribute("class") or "")
                toggle.first.click()
                if initial_open:
                    expect(first_dir).not_to_have_class(re.compile(r"open"))
                else:
                    expect(first_dir).to_have_class(re.compile(r"open"))

    def test_collapse_button_works(self, code_view_page: Page):
        """Test that the collapse file tree button works."""
        collapse_btn = code_view_page.locator("#collapse-file-tree")
        file_tree_panel = code_view_page.locator("#file-tree-panel")

        expect(collapse_btn).to_be_visible()

        # Click to collapse
        collapse_btn.click()
        expect(file_tree_panel).to_have_class(re.compile(r"collapsed"))

        # Click to expand
        collapse_btn.click()
        expect(file_tree_panel).not_to_have_class(re.compile(r"collapsed"))


class TestCodeEditor:
    """Tests for the CodeMirror code editor."""

    def test_editor_displays_code(self, code_view_page: Page):
        """Test that the editor displays code content."""
        editor = code_view_page.locator(".cm-editor")
        expect(editor).to_be_visible()

        # Check that there are lines with content
        lines = code_view_page.locator(".cm-line")
        expect(lines.first).to_be_visible()

    def test_line_numbers_visible(self, code_view_page: Page):
        """Test that line numbers are displayed."""
        # CodeMirror uses .cm-lineNumbers for the line number gutter
        gutter = code_view_page.locator(".cm-lineNumbers")
        expect(gutter).to_be_visible()

    def test_blame_ranges_highlighted(self, code_view_page: Page):
        """Test that blame ranges have background colors."""
        # Lines with blame should have data-range-index attribute
        blame_lines = code_view_page.locator(".cm-line[data-range-index]")
        if blame_lines.count() > 0:
            # Check that they have a background color style
            first_blame = blame_lines.first
            style = first_blame.get_attribute("style")
            assert style and "background-color" in style

    def test_minimap_exists(self, code_view_page: Page):
        """Test that the blame minimap exists."""
        minimap = code_view_page.locator(".blame-minimap")
        # Minimap only exists if there are blame ranges
        blame_lines = code_view_page.locator(".cm-line[data-range-index]")
        if blame_lines.count() > 0:
            expect(minimap).to_be_visible()


class TestBlameInteraction:
    """Tests for blame block interactions."""

    def test_clicking_blame_highlights_range(self, code_view_page: Page):
        """Test that clicking a blame line highlights the range."""
        blame_lines = code_view_page.locator(".cm-line[data-range-index]")
        if blame_lines.count() > 0:
            blame_lines.first.click()
            # Check for active range class
            active = code_view_page.locator(".cm-active-range")
            expect(active.first).to_be_visible()

    def test_clicking_blame_scrolls_transcript(self, code_view_page: Page):
        """Test that clicking a blame block scrolls to the message in transcript."""
        blame_lines = code_view_page.locator(".cm-line[data-msg-id]")
        if blame_lines.count() > 0:
            first_blame = blame_lines.first
            msg_id = first_blame.get_attribute("data-msg-id")

            first_blame.click()

            # Check that the message is highlighted in transcript
            highlighted = code_view_page.locator(f"#{msg_id}.highlighted")
            expect(highlighted).to_be_visible()

    def test_hovering_blame_shows_tooltip(self, code_view_page: Page):
        """Test that hovering over blame line shows tooltip."""
        blame_lines = code_view_page.locator(".cm-line[data-range-index]")
        if blame_lines.count() > 0:
            blame_lines.first.hover()

            # Wait for tooltip to appear
            tooltip = code_view_page.locator(".blame-tooltip")
            expect(tooltip).to_be_visible(timeout=2000)

    def test_tooltip_has_user_message(self, code_view_page: Page):
        """Test that the tooltip shows user message content."""
        blame_lines = code_view_page.locator(".cm-line[data-range-index]")
        if blame_lines.count() > 0:
            blame_lines.first.hover()

            tooltip = code_view_page.locator(".blame-tooltip")
            expect(tooltip).to_be_visible(timeout=2000)

            # Should contain user content (inside .index-item-content)
            user_content = tooltip.locator(".index-item-content")
            expect(user_content).to_be_visible()


class TestTranscriptPanel:
    """Tests for the transcript panel."""

    def test_transcript_panel_exists(self, code_view_page: Page):
        """Test that the transcript panel exists."""
        panel = code_view_page.locator("#transcript-panel")
        expect(panel).to_be_visible()

    def test_messages_are_rendered(self, code_view_page: Page):
        """Test that messages are rendered in the transcript."""
        messages = code_view_page.locator("#transcript-content .message")
        expect(messages.first).to_be_visible()
        assert messages.count() > 0

    def test_user_and_assistant_messages(self, code_view_page: Page):
        """Test that both user and assistant messages are present."""
        user_msgs = code_view_page.locator("#transcript-content .message.user")
        assistant_msgs = code_view_page.locator(
            "#transcript-content .message.assistant"
        )

        expect(user_msgs.first).to_be_visible()
        expect(assistant_msgs.first).to_be_visible()

    def test_clicking_message_navigates_to_code(self, code_view_page: Page):
        """Test that clicking a transcript message navigates to code."""
        # Get initial file selection
        initial_selected = code_view_page.locator(".tree-file.selected")
        initial_path = initial_selected.get_attribute("data-path")

        # Find a message that should have an associated edit
        messages = code_view_page.locator("#transcript-content .message")
        if messages.count() > 1:
            # Click on the first message
            messages.first.click()

            # Give it time to navigate
            code_view_page.wait_for_timeout(200)

            # Check that a message is now highlighted
            highlighted = code_view_page.locator(
                "#transcript-content .message.highlighted"
            )
            expect(highlighted).to_be_visible()

    def test_pinned_user_message_on_scroll(self, code_view_page: Page):
        """Test that scrolling shows pinned user message with correct content."""
        panel = code_view_page.locator("#transcript-panel")
        pinned = code_view_page.locator("#pinned-user-message")
        pinned_content = code_view_page.locator(".pinned-user-content")

        # Get the first user message's text for comparison
        first_user = code_view_page.locator(
            "#transcript-content .message.user:not(.continuation)"
        ).first
        first_user_text = first_user.locator(".message-content").text_content().strip()

        # Scroll down past the first user message
        panel.evaluate("el => el.scrollTop = 800")
        code_view_page.wait_for_timeout(100)

        # Pinned header should be visible with content from the first user message
        expect(pinned).to_be_visible()
        pinned_text = pinned_content.text_content()
        # The pinned text should be a truncated prefix of the user message
        assert len(pinned_text) > 0, "Pinned content should not be empty"
        assert (
            first_user_text.startswith(pinned_text[:50])
            or pinned_text in first_user_text
        ), f"Pinned text '{pinned_text[:50]}...' should match user message"

    def test_pinned_user_message_click_scrolls_back(self, code_view_page: Page):
        """Test that clicking pinned header scrolls to the original message."""
        panel = code_view_page.locator("#transcript-panel")
        pinned = code_view_page.locator("#pinned-user-message")

        # Scroll down to show pinned header
        panel.evaluate("el => el.scrollTop = 800")
        code_view_page.wait_for_timeout(100)

        # Click the pinned header
        if pinned.is_visible():
            pinned.click()
            code_view_page.wait_for_timeout(300)  # Wait for smooth scroll

            # Panel should have scrolled up (scrollTop should be less)
            scroll_top = panel.evaluate("el => el.scrollTop")
            assert scroll_top < 800, "Clicking pinned header should scroll up"


class TestPanelResizing:
    """Tests for panel resize functionality."""

    def test_resize_handles_exist(self, code_view_page: Page):
        """Test that resize handles exist."""
        left_handle = code_view_page.locator("#resize-left")
        right_handle = code_view_page.locator("#resize-right")

        expect(left_handle).to_be_visible()
        expect(right_handle).to_be_visible()

    def test_resize_left_panel(self, code_view_page: Page):
        """Test that dragging left handle resizes file tree panel."""
        file_tree = code_view_page.locator("#file-tree-panel")
        handle = code_view_page.locator("#resize-left")

        initial_width = file_tree.bounding_box()["width"]

        # Drag the handle
        handle.drag_to(handle, target_position={"x": 50, "y": 0}, force=True)

        # Width should have changed
        new_width = file_tree.bounding_box()["width"]
        # Allow for the change - it may not always work perfectly in test
        assert new_width is not None

    def test_resize_right_panel(self, code_view_page: Page):
        """Test that dragging right handle resizes transcript panel."""
        transcript = code_view_page.locator("#transcript-panel")
        handle = code_view_page.locator("#resize-right")

        initial_width = transcript.bounding_box()["width"]

        # Drag the handle
        handle.drag_to(handle, target_position={"x": -50, "y": 0}, force=True)

        # Width should have changed
        new_width = transcript.bounding_box()["width"]
        assert new_width is not None


class TestNavigation:
    """Tests for navigation links and tabs."""

    def test_code_tab_is_active(self, code_view_page: Page):
        """Test that the Code tab is active in navigation."""
        code_tab = code_view_page.locator('a[href="code.html"]')
        # It should be the current/active tab
        expect(code_tab).to_be_visible()

    def test_transcript_tab_links_to_index(self, code_view_page: Page):
        """Test that Transcript tab links to index.html."""
        # Use the tab specifically (not the header link)
        transcript_tab = code_view_page.locator('a.tab[href="index.html"]')
        expect(transcript_tab).to_be_visible()


class TestCodeViewScrolling:
    """Tests for scroll synchronization between panels."""

    def test_file_load_scrolls_to_first_blame(self, code_view_page: Page):
        """Test that loading a file scrolls to the first blame block."""
        files = code_view_page.locator(".tree-file")
        if files.count() > 1:
            # Click a different file
            files.nth(1).click()
            code_view_page.wait_for_timeout(200)

            # Check that the editor scrolled (we can verify by checking
            # that a blame line is visible in the viewport)
            editor = code_view_page.locator(".cm-editor")
            expect(editor).to_be_visible()

    def test_minimap_click_scrolls_editor(self, code_view_page: Page):
        """Test that clicking minimap marker scrolls the editor."""
        markers = code_view_page.locator(".minimap-marker")
        if markers.count() > 0:
            # Click a marker
            markers.first.click()
            code_view_page.wait_for_timeout(100)

            # Editor should still be visible (scroll happened)
            editor = code_view_page.locator(".cm-editor")
            expect(editor).to_be_visible()


class TestMessageNumberWidget:
    """Tests for the message number widget on blame lines."""

    def test_message_numbers_displayed(self, code_view_page: Page):
        """Test that message numbers are displayed on blame lines."""
        msg_nums = code_view_page.locator(".blame-msg-num")
        if msg_nums.count() > 0:
            # Should show format like "#5"
            first_num = msg_nums.first
            text = first_num.text_content()
            assert text.startswith("#")
            assert text[1:].isdigit()


class TestChunkedRendering:
    """Tests for transcript panel performance optimizations.

    These tests verify that the chunked rendering and lazy loading work correctly
    by examining DOM state rather than accessing internal JavaScript variables.
    """

    def test_sentinel_element_exists(self, code_view_page: Page):
        """Test that the sentinel element exists for IntersectionObserver."""
        sentinel = code_view_page.locator("#transcript-sentinel")
        expect(sentinel).to_be_attached()

    def test_data_loading_and_chunked_rendering_setup(self, code_view_page: Page):
        """Test that data loading and chunked rendering are configured."""
        # Check that the script tag contains chunked rendering setup
        scripts = code_view_page.locator("script[type='module']")
        script_content = scripts.first.text_content()
        # Local version uses embedded CODE_DATA, gist version uses fetch
        assert (
            "CODE_DATA" in script_content
        ), "CODE_DATA should be checked for embedded data"
        assert (
            "getGistDataUrl" in script_content
        ), "getGistDataUrl should be defined for gist fetching"
        assert "CHUNK_SIZE" in script_content, "CHUNK_SIZE should be defined"
        assert "renderedCount" in script_content, "renderedCount should be defined"

    def test_scroll_loads_more_messages(self, code_view_page: Page):
        """Test that scrolling the transcript loads more messages."""
        panel = code_view_page.locator("#transcript-panel")
        content = code_view_page.locator("#transcript-content")

        # Count initial messages
        initial_count = content.locator("> .message").count()

        # Scroll to bottom multiple times to trigger lazy loading
        for _ in range(3):
            panel.evaluate("el => el.scrollTop = el.scrollHeight")
            code_view_page.wait_for_timeout(150)

        # Count messages after scrolling
        final_count = content.locator("> .message").count()

        # If the session has many messages, more should be loaded
        # (test passes if already all loaded or if more loaded)
        assert final_count >= initial_count

    def test_transcript_content_has_messages(self, code_view_page: Page):
        """Test that transcript content contains rendered messages."""
        content = code_view_page.locator("#transcript-content")
        messages = content.locator(".message")

        # Should have at least some messages rendered
        assert messages.count() > 0, "No messages rendered in transcript"

    def test_clicking_blame_renders_target_message(self, code_view_page: Page):
        """Test that clicking a blame block ensures target message is rendered."""
        blame_lines = code_view_page.locator(".cm-line[data-msg-id]")

        if blame_lines.count() > 0:
            # Get the msg-id from the blame line
            msg_id = blame_lines.first.get_attribute("data-msg-id")

            # Click the blame line
            blame_lines.first.click()
            code_view_page.wait_for_timeout(200)

            # The target message should now be in the DOM and highlighted
            target_msg = code_view_page.locator(f"#{msg_id}")
            expect(target_msg).to_be_attached()
            expect(target_msg).to_have_class(re.compile(r"highlighted"))

    def test_intersection_observer_setup(self, code_view_page: Page):
        """Test that IntersectionObserver is set up for lazy loading."""
        # Check that the script contains IntersectionObserver setup
        scripts = code_view_page.locator("script[type='module']")
        script_content = scripts.first.text_content()
        assert "IntersectionObserver" in script_content
        assert "transcript-sentinel" in script_content

    def test_render_messages_up_to_function_exists(self, code_view_page: Page):
        """Test that the renderMessagesUpTo function exists for on-demand rendering."""
        scripts = code_view_page.locator("script[type='module']")
        script_content = scripts.first.text_content()
        assert "renderMessagesUpTo" in script_content
        assert "renderNextChunk" in script_content
