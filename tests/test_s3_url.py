"""Tests for S3 URL support."""

import json
import tempfile
from pathlib import Path

import boto3
import pytest
from moto import mock_aws

from claude_code_transcripts import is_s3_url, fetch_s3_to_tempfile


class TestIsS3Url:
    """Tests for S3 URL detection."""

    def test_detects_s3_url(self):
        """Test that s3:// URLs are detected."""
        assert is_s3_url("s3://my-bucket/path/to/file.jsonl") is True

    def test_detects_s3_url_with_nested_path(self):
        """Test that s3:// URLs with nested paths are detected."""
        assert is_s3_url("s3://bucket/a/b/c/session.json") is True

    def test_rejects_http_url(self):
        """Test that http:// URLs are not detected as S3."""
        assert is_s3_url("http://example.com/file.jsonl") is False

    def test_rejects_https_url(self):
        """Test that https:// URLs are not detected as S3."""
        assert is_s3_url("https://example.com/file.jsonl") is False

    def test_rejects_local_path(self):
        """Test that local paths are not detected as S3."""
        assert is_s3_url("/path/to/file.jsonl") is False
        assert is_s3_url("relative/path.json") is False


class TestFetchS3ToTempfile:
    """Tests for S3 file fetching."""

    @mock_aws
    def test_fetches_jsonl_file(self):
        """Test fetching a JSONL file from S3."""
        # Set up mock S3
        s3 = boto3.client("s3", region_name="us-east-1")
        s3.create_bucket(Bucket="test-bucket")

        # Upload test content
        content = '{"type":"user","message":{"content":"Hello"}}\n'
        s3.put_object(Bucket="test-bucket", Key="sessions/test.jsonl", Body=content)

        # Fetch the file
        temp_file = fetch_s3_to_tempfile("s3://test-bucket/sessions/test.jsonl")

        assert temp_file.exists()
        assert temp_file.suffix == ".jsonl"
        assert temp_file.read_text() == content

    @mock_aws
    def test_fetches_json_file(self):
        """Test fetching a JSON file from S3."""
        # Set up mock S3
        s3 = boto3.client("s3", region_name="us-east-1")
        s3.create_bucket(Bucket="test-bucket")

        # Upload test content
        content = json.dumps({"loglines": []})
        s3.put_object(Bucket="test-bucket", Key="session.json", Body=content)

        # Fetch the file
        temp_file = fetch_s3_to_tempfile("s3://test-bucket/session.json")

        assert temp_file.exists()
        assert temp_file.suffix == ".json"
        assert temp_file.read_text() == content

    @mock_aws
    def test_raises_on_missing_bucket(self):
        """Test that missing bucket raises an error."""
        import click

        with pytest.raises(click.ClickException) as exc_info:
            fetch_s3_to_tempfile("s3://nonexistent-bucket/file.jsonl")

        assert "Failed to fetch S3 object" in str(exc_info.value)

    @mock_aws
    def test_raises_on_missing_key(self):
        """Test that missing key raises an error."""
        import click

        # Set up mock S3 with empty bucket
        s3 = boto3.client("s3", region_name="us-east-1")
        s3.create_bucket(Bucket="test-bucket")

        with pytest.raises(click.ClickException) as exc_info:
            fetch_s3_to_tempfile("s3://test-bucket/nonexistent.jsonl")

        assert "Failed to fetch S3 object" in str(exc_info.value)
