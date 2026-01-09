import json
from pathlib import Path

from claude_code_transcripts import generate_html, parse_session_file


def test_parses_windsurf_export_json_to_loglines():
    fixture_path = Path(__file__).parent / "sample_windsurf_export.json"
    result = parse_session_file(fixture_path)

    assert "loglines" in result
    assert [e["type"] for e in result["loglines"]] == [
        "user",
        "assistant",
        "user",
        "assistant",
    ]

    first = result["loglines"][0]
    assert first["message"]["role"] == "user"
    assert first["message"]["content"] == "Write a function that returns 'hello world'."

    assistant = result["loglines"][1]
    assert assistant["message"]["role"] == "assistant"
    assert isinstance(assistant["message"]["content"], list)
    assert assistant["message"]["content"][0]["type"] == "text"
    assert "hello_world" in assistant["message"]["content"][0]["text"]


def test_windsurf_export_generates_html(tmp_path):
    fixture_path = Path(__file__).parent / "sample_windsurf_export.json"
    generate_html(fixture_path, tmp_path)

    index_html = (tmp_path / "index.html").read_text(encoding="utf-8")
    assert "hello world" in index_html.lower()

    page_html = (tmp_path / "page-001.html").read_text(encoding="utf-8")
    assert "pytest" in page_html.lower()
    assert "language-python" in page_html.lower()
