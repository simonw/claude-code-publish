"""Playwright browser tests for copy button and search functionality.

These tests are skipped if Playwright browsers are not installed.
Install browsers with: playwright install chromium
"""

import json
import tempfile
from pathlib import Path

import pytest

# Check if Playwright browsers are available
try:
    from playwright.sync_api import sync_playwright

    def _check_browser_available():
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch()
                browser.close()
                return True
        except Exception:
            return False

    PLAYWRIGHT_AVAILABLE = _check_browser_available()
except ImportError:
    PLAYWRIGHT_AVAILABLE = False

pytestmark = pytest.mark.skipif(
    not PLAYWRIGHT_AVAILABLE,
    reason="Playwright browsers not available. Install with: playwright install chromium",
)


@pytest.fixture
def generated_html(tmp_path):
    """Generate HTML files from the sample session for testing."""
    from claude_code_transcripts import generate_html

    fixture_path = Path(__file__).parent / "sample_session.json"
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    generate_html(fixture_path, output_dir, github_repo="example/project")
    return output_dir


@pytest.fixture
def page(generated_html):
    """Create a Playwright page for testing."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        yield page, generated_html
        browser.close()


class TestCopyButtonPlaywright:
    """Playwright tests for the copy button feature."""

    def test_copy_button_visible_in_message(self, page):
        """Test that copy buttons are visible in messages."""
        page_obj, output_dir = page
        page_obj.goto(f"file://{output_dir}/page-001.html")

        # Wait for copy buttons to be rendered
        copy_buttons = page_obj.locator("copy-button")
        assert copy_buttons.count() > 0, "Copy buttons should be present in messages"

    def test_copy_button_visible_in_index(self, page):
        """Test that copy buttons are visible in index items."""
        page_obj, output_dir = page
        page_obj.goto(f"file://{output_dir}/index.html")

        # Wait for copy buttons to be rendered
        copy_buttons = page_obj.locator("copy-button")
        assert copy_buttons.count() > 0, "Copy buttons should be present in index"

    def test_copy_button_click_changes_text(self, page):
        """Test that clicking a copy button changes the text to 'Copied!'."""
        page_obj, output_dir = page
        page_obj.goto(f"file://{output_dir}/page-001.html")

        # Grant clipboard permissions
        page_obj.context.grant_permissions(["clipboard-write", "clipboard-read"])

        # Find first copy button and click it
        copy_button = page_obj.locator("copy-button").first
        copy_button.wait_for(state="attached")

        # Get the shadow DOM button
        button = copy_button.locator("button")
        original_text = button.inner_text()

        # Click the button
        button.click()

        # Check that text changed to "Copied!"
        assert (
            button.inner_text() == "Copied!"
        ), "Button text should change to 'Copied!' after click"

        # Wait 2.5 seconds and check it reverts
        page_obj.wait_for_timeout(2500)
        assert (
            button.inner_text() == original_text
        ), "Button text should revert after 2 seconds"

    def test_copy_button_copies_markdown_content(self, page):
        """Test that clicking a copy button actually copies content."""
        page_obj, output_dir = page
        page_obj.goto(f"file://{output_dir}/page-001.html")

        # Grant clipboard permissions
        page_obj.context.grant_permissions(["clipboard-write", "clipboard-read"])

        # Find a copy button with data-content (markdown)
        copy_button = page_obj.locator("copy-button[data-content]").first
        copy_button.wait_for(state="attached")

        # Get the content that should be copied
        expected_content = copy_button.get_attribute("data-content")

        # Click the button
        button = copy_button.locator("button")
        button.click()

        # Read from clipboard
        clipboard_content = page_obj.evaluate("navigator.clipboard.readText()")

        assert (
            clipboard_content == expected_content
        ), "Clipboard should contain the data-content value"


class TestSearchPlaywright:
    """Playwright tests for the search feature."""

    def test_search_box_hidden_by_default(self, page):
        """Test that search box is hidden by default (progressive enhancement)."""
        page_obj, output_dir = page
        # Load the page with JS disabled to test progressive enhancement
        page_obj.context.set_offline(False)
        page_obj.goto(f"file://{output_dir}/index.html", wait_until="domcontentloaded")

        # Note: With file:// protocol, search is hidden
        # This test verifies the file:// protocol behavior
        search_box = page_obj.locator("#search-box")
        # On file:// protocol, search box should remain hidden
        assert not search_box.is_visible() or search_box.evaluate(
            "el => getComputedStyle(el).display === 'none'"
        )

    def test_search_modal_exists(self, page):
        """Test that the search modal element exists."""
        page_obj, output_dir = page
        page_obj.goto(f"file://{output_dir}/index.html")

        # The modal should exist in the DOM
        modal = page_obj.locator("#search-modal")
        assert modal.count() == 1, "Search modal should exist"


class TestTimestampFormatting:
    """Test that timestamps are formatted correctly by JavaScript."""

    def test_timestamps_formatted(self, page):
        """Test that timestamps are formatted by JavaScript."""
        page_obj, output_dir = page
        page_obj.goto(f"file://{output_dir}/page-001.html")

        # Wait for JS to run
        page_obj.wait_for_load_state("networkidle")

        # Get a timestamp element
        time_element = page_obj.locator("time[data-timestamp]").first
        time_element.wait_for(state="attached")

        # The text should be formatted (not the raw ISO timestamp)
        text = time_element.inner_text()
        # Raw format is like "2025-01-01T12:00:00.000Z"
        # Formatted should be like "Jan 1 12:00" or just "12:00"
        assert "T" not in text, "Timestamp should be formatted, not raw ISO format"
        assert "Z" not in text, "Timestamp should be formatted, not raw ISO format"
