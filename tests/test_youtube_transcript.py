"""Tests for YouTube transcript extractor."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import httpx
import pytest
import respx

from llm_wiki.collectors.youtube_transcript import (
    TranscriptResult,
    TranscriptSegment,
    _decode_transcript_text,
    extract_video_id,
    fetch_transcript,
    is_youtube_video_url,
    transcript_to_fetched_item,
)


# ---------------------------------------------------------------------------
# Tests: extract_video_id
# ---------------------------------------------------------------------------


class TestExtractVideoId:
    def test_standard_watch_url(self):
        assert extract_video_id("https://www.youtube.com/watch?v=dQw4w9WgXcQ") == "dQw4w9WgXcQ"

    def test_short_url(self):
        assert extract_video_id("https://youtu.be/dQw4w9WgXcQ") == "dQw4w9WgXcQ"

    def test_embed_url(self):
        assert extract_video_id("https://www.youtube.com/embed/dQw4w9WgXcQ") == "dQw4w9WgXcQ"

    def test_shorts_url(self):
        assert extract_video_id("https://www.youtube.com/shorts/dQw4w9WgXcQ") == "dQw4w9WgXcQ"

    def test_v_url(self):
        assert extract_video_id("https://www.youtube.com/v/dQw4w9WgXcQ") == "dQw4w9WgXcQ"

    def test_mobile_watch_url(self):
        assert extract_video_id("https://m.youtube.com/watch?v=dQw4w9WgXcQ") == "dQw4w9WgXcQ"

    def test_mobile_shorts_url(self):
        assert extract_video_id("https://m.youtube.com/shorts/dQw4w9WgXcQ") == "dQw4w9WgXcQ"

    def test_raw_video_id(self):
        assert extract_video_id("dQw4w9WgXcQ") == "dQw4w9WgXcQ"

    def test_watch_url_with_extra_params(self):
        assert extract_video_id("https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=120") == "dQw4w9WgXcQ"

    def test_whitespace_trimmed(self):
        assert extract_video_id("  dQw4w9WgXcQ  ") == "dQw4w9WgXcQ"

    def test_channel_url_returns_none(self):
        assert extract_video_id("https://www.youtube.com/channel/UCxyz123") is None

    def test_handle_url_returns_none(self):
        assert extract_video_id("https://www.youtube.com/@username") is None

    def test_empty_string_returns_none(self):
        assert extract_video_id("") is None

    def test_non_youtube_url_returns_none(self):
        assert extract_video_id("https://example.com/page") is None

    def test_invalid_id_length(self):
        assert extract_video_id("short") is None

    def test_hyphen_and_underscore_in_id(self):
        """IDs with hyphens/underscores work in full URLs (11 chars)."""
        assert extract_video_id("https://www.youtube.com/watch?v=abc-_123-AB") == "abc-_123-AB"


# ---------------------------------------------------------------------------
# Tests: is_youtube_video_url
# ---------------------------------------------------------------------------


class TestIsYoutubeVideoUrl:
    def test_watch_url(self):
        assert is_youtube_video_url("https://www.youtube.com/watch?v=dQw4w9WgXcQ") is True

    def test_channel_url(self):
        assert is_youtube_video_url("https://www.youtube.com/channel/UCxyz") is False

    def test_raw_id(self):
        assert is_youtube_video_url("dQw4w9WgXcQ") is True


# ---------------------------------------------------------------------------
# Tests: _decode_transcript_text
# ---------------------------------------------------------------------------


class TestDecodeTranscriptText:
    def test_plain_text(self):
        assert _decode_transcript_text("Hello world") == "Hello world"

    def test_html_entities(self):
        assert _decode_transcript_text("it&#39;s a &quot;test&quot;") == "it's a \"test\""

    def test_double_encoded_amp(self):
        """YouTube double-encodes: &amp;#39; → should become '"""
        assert _decode_transcript_text("it&amp;#39;s great") == "it's great"

    def test_whitespace_normalization(self):
        assert _decode_transcript_text("hello   world\n\tfoo") == "hello world foo"

    def test_lt_gt(self):
        assert _decode_transcript_text("a &lt; b &gt; c") == "a < b > c"

    def test_empty_string(self):
        assert _decode_transcript_text("") == ""

    def test_mixed_entities_and_whitespace(self):
        text = "&amp;amp;  multiple   &lt;spaces&gt;  "
        result = _decode_transcript_text(text)
        assert "&amp;" in result or "&" in result
        assert "  " not in result  # no double spaces


# ---------------------------------------------------------------------------
# Tests: TranscriptResult dataclass
# ---------------------------------------------------------------------------


class TestTranscriptResult:
    def test_frozen(self):
        r = TranscriptResult(ok=True, video_id="abc")
        with pytest.raises(AttributeError):
            r.ok = False  # type: ignore[misc]

    def test_defaults(self):
        r = TranscriptResult(ok=False)
        assert r.video_id == ""
        assert r.transcript == ""
        assert r.segments == []
        assert r.language == ""
        assert r.title == ""
        assert r.error_type == ""


# ---------------------------------------------------------------------------
# Tests: TranscriptSegment dataclass
# ---------------------------------------------------------------------------


class TestTranscriptSegment:
    def test_frozen(self):
        s = TranscriptSegment(text="hello", start=1.0, duration=2.5)
        with pytest.raises(AttributeError):
            s.text = "changed"  # type: ignore[misc]

    def test_defaults(self):
        s = TranscriptSegment(text="test")
        assert s.start == 0.0
        assert s.duration == 0.0


# ---------------------------------------------------------------------------
# Tests: fetch_transcript (with mocked youtube-transcript-api)
# ---------------------------------------------------------------------------


class TestFetchTranscript:
    async def test_invalid_url(self):
        result = await fetch_transcript("https://example.com/not-youtube")
        assert result.ok is False
        assert result.error_type == "INVALID_URL"

    async def test_empty_url(self):
        result = await fetch_transcript("")
        assert result.ok is False
        assert result.error_type == "INVALID_URL"

    async def test_successful_transcript_fetch(self):
        """Test successful transcript extraction with mocked API."""
        mock_segments = [
            {"text": "Hello world", "start": 0.0, "duration": 2.0},
            {"text": "this is a test", "start": 2.0, "duration": 3.0},
        ]

        with patch.dict("sys.modules", {
            "youtube_transcript_api": MagicMock(),
            "youtube_transcript_api._errors": MagicMock(),
        }):
            import sys
            mock_api = sys.modules["youtube_transcript_api"]
            mock_errors = sys.modules["youtube_transcript_api._errors"]

            mock_api.YouTubeTranscriptApi.get_transcript.return_value = mock_segments
            mock_errors.TranscriptsDisabled = type("TranscriptsDisabled", (Exception,), {})
            mock_errors.NoTranscriptFound = type("NoTranscriptFound", (Exception,), {})
            mock_errors.VideoUnavailable = type("VideoUnavailable", (Exception,), {})
            mock_errors.NoTranscriptAvailable = type("NoTranscriptAvailable", (Exception,), {})

            # Mock oEmbed
            with respx.mock:
                respx.get("https://www.youtube.com/oembed").respond(200, json={
                    "title": "Test Video Title",
                    "author_name": "Test Author",
                    "thumbnail_url": "https://i.ytimg.com/vi/dQw4w9WgXcQ/maxresdefault.jpg",
                })

                result = await fetch_transcript("dQw4w9WgXcQ")

        assert result.ok is True
        assert result.video_id == "dQw4w9WgXcQ"
        assert "Hello world" in result.transcript
        assert "this is a test" in result.transcript
        assert len(result.segments) == 2

    async def test_transcript_with_html_entities(self):
        """Test that HTML entities in transcript are decoded."""
        mock_segments = [
            {"text": "it&amp;#39;s &quot;great&quot;", "start": 0.0, "duration": 2.0},
        ]

        with patch.dict("sys.modules", {
            "youtube_transcript_api": MagicMock(),
            "youtube_transcript_api._errors": MagicMock(),
        }):
            import sys
            mock_api = sys.modules["youtube_transcript_api"]
            mock_errors = sys.modules["youtube_transcript_api._errors"]

            mock_api.YouTubeTranscriptApi.get_transcript.return_value = mock_segments
            mock_errors.TranscriptsDisabled = type("TranscriptsDisabled", (Exception,), {})
            mock_errors.NoTranscriptFound = type("NoTranscriptFound", (Exception,), {})
            mock_errors.VideoUnavailable = type("VideoUnavailable", (Exception,), {})
            mock_errors.NoTranscriptAvailable = type("NoTranscriptAvailable", (Exception,), {})

            result = await fetch_transcript(
                "dQw4w9WgXcQ", include_metadata=False
            )

        assert result.ok is True
        assert "it's" in result.transcript
        assert '"great"' in result.transcript

    async def test_transcripts_disabled(self):
        """Test handling of disabled transcripts."""
        DisabledError = type("TranscriptsDisabled", (Exception,), {})

        with patch.dict("sys.modules", {
            "youtube_transcript_api": MagicMock(),
            "youtube_transcript_api._errors": MagicMock(),
        }):
            import sys
            mock_api = sys.modules["youtube_transcript_api"]
            mock_errors = sys.modules["youtube_transcript_api._errors"]

            mock_errors.TranscriptsDisabled = DisabledError
            mock_errors.NoTranscriptFound = type("NoTranscriptFound", (Exception,), {})
            mock_errors.VideoUnavailable = type("VideoUnavailable", (Exception,), {})
            mock_errors.NoTranscriptAvailable = type("NoTranscriptAvailable", (Exception,), {})

            mock_api.YouTubeTranscriptApi.get_transcript.side_effect = DisabledError("disabled")

            result = await fetch_transcript("dQw4w9WgXcQ")

        assert result.ok is False
        assert result.error_type == "DISABLED"

    async def test_video_unavailable(self):
        """Test handling of unavailable video."""
        UnavailableError = type("VideoUnavailable", (Exception,), {})

        with patch.dict("sys.modules", {
            "youtube_transcript_api": MagicMock(),
            "youtube_transcript_api._errors": MagicMock(),
        }):
            import sys
            mock_api = sys.modules["youtube_transcript_api"]
            mock_errors = sys.modules["youtube_transcript_api._errors"]

            mock_errors.TranscriptsDisabled = type("TranscriptsDisabled", (Exception,), {})
            mock_errors.NoTranscriptFound = type("NoTranscriptFound", (Exception,), {})
            mock_errors.VideoUnavailable = UnavailableError
            mock_errors.NoTranscriptAvailable = type("NoTranscriptAvailable", (Exception,), {})

            mock_api.YouTubeTranscriptApi.get_transcript.side_effect = UnavailableError("not found")

            result = await fetch_transcript("dQw4w9WgXcQ")

        assert result.ok is False
        assert result.error_type == "NOT_FOUND"

    async def test_language_fallback(self):
        """Test that language fallback works (auto fails, en succeeds)."""
        NoTranscriptFound = type("NoTranscriptFound", (Exception,), {})

        call_count = 0

        def mock_get_transcript(video_id, languages=None):
            nonlocal call_count
            call_count += 1
            if languages is None:
                raise NoTranscriptFound("no auto")
            if languages == ["en"]:
                return [{"text": "English transcript", "start": 0, "duration": 5}]
            raise NoTranscriptFound("no such lang")

        with patch.dict("sys.modules", {
            "youtube_transcript_api": MagicMock(),
            "youtube_transcript_api._errors": MagicMock(),
        }):
            import sys
            mock_api = sys.modules["youtube_transcript_api"]
            mock_errors = sys.modules["youtube_transcript_api._errors"]

            mock_errors.TranscriptsDisabled = type("TranscriptsDisabled", (Exception,), {})
            mock_errors.NoTranscriptFound = NoTranscriptFound
            mock_errors.VideoUnavailable = type("VideoUnavailable", (Exception,), {})
            mock_errors.NoTranscriptAvailable = type("NoTranscriptAvailable", (Exception,), {})

            mock_api.YouTubeTranscriptApi.get_transcript.side_effect = mock_get_transcript

            result = await fetch_transcript(
                "dQw4w9WgXcQ", include_metadata=False
            )

        assert result.ok is True
        assert result.language == "en"
        assert "English transcript" in result.transcript

    async def test_all_languages_fail(self):
        """Test when all language attempts fail."""
        NoTranscriptFound = type("NoTranscriptFound", (Exception,), {})

        with patch.dict("sys.modules", {
            "youtube_transcript_api": MagicMock(),
            "youtube_transcript_api._errors": MagicMock(),
        }):
            import sys
            mock_api = sys.modules["youtube_transcript_api"]
            mock_errors = sys.modules["youtube_transcript_api._errors"]

            mock_errors.TranscriptsDisabled = type("TranscriptsDisabled", (Exception,), {})
            mock_errors.NoTranscriptFound = NoTranscriptFound
            mock_errors.VideoUnavailable = type("VideoUnavailable", (Exception,), {})
            mock_errors.NoTranscriptAvailable = type("NoTranscriptAvailable", (Exception,), {})

            mock_api.YouTubeTranscriptApi.get_transcript.side_effect = NoTranscriptFound("none")

            result = await fetch_transcript(
                "dQw4w9WgXcQ", include_metadata=False
            )

        assert result.ok is False
        assert result.error_type == "UNAVAILABLE"


# ---------------------------------------------------------------------------
# Tests: transcript_to_fetched_item
# ---------------------------------------------------------------------------


class TestTranscriptToFetchedItem:
    def test_basic_conversion(self):
        result = TranscriptResult(
            ok=True,
            video_id="abc123",
            transcript="This is the full transcript text of the video.",
            segments=[TranscriptSegment(text="This is the full transcript text of the video.", start=0, duration=10)],
            language="en",
            title="My Video Title",
            author="Channel Name",
            thumbnail_url="https://i.ytimg.com/vi/abc123/hqdefault.jpg",
        )

        item = transcript_to_fetched_item(result)

        assert item.url == "https://www.youtube.com/watch?v=abc123"
        assert item.title == "My Video Title"
        assert item.author == "Channel Name"
        assert item.source_type == "youtube"
        assert item.content_html == "This is the full transcript text of the video."
        assert item.extra["video_id"] == "abc123"
        assert item.extra["transcript_language"] == "en"
        assert item.extra["transcript_length"] == len(result.transcript)
        assert item.extra["thumbnail_url"] == "https://i.ytimg.com/vi/abc123/hqdefault.jpg"

    def test_summary_is_truncated_transcript(self):
        long_text = "x" * 1000
        result = TranscriptResult(
            ok=True,
            video_id="abc123",
            transcript=long_text,
            title="Title",
        )
        item = transcript_to_fetched_item(result)
        assert len(item.summary) == 500

    def test_with_published_at(self):
        dt = datetime(2024, 6, 15, tzinfo=timezone.utc)
        result = TranscriptResult(
            ok=True,
            video_id="abc123",
            transcript="text",
            title="Title",
        )
        item = transcript_to_fetched_item(result, published_at=dt)
        assert item.published_at == dt

    def test_without_published_at(self):
        result = TranscriptResult(
            ok=True,
            video_id="abc123",
            transcript="text",
            title="Title",
        )
        item = transcript_to_fetched_item(result)
        assert item.published_at is None


# ---------------------------------------------------------------------------
# Tests: oEmbed metadata (async, with mocked HTTP)
# ---------------------------------------------------------------------------


class TestOembedMetadata:
    @respx.mock
    async def test_oembed_success(self):
        respx.get("https://www.youtube.com/oembed").respond(200, json={
            "title": "Rick Astley - Never Gonna Give You Up",
            "author_name": "Rick Astley",
            "thumbnail_url": "https://i.ytimg.com/vi/dQw4w9WgXcQ/maxresdefault.jpg",
        })

        from llm_wiki.collectors.youtube_transcript import _fetch_video_metadata
        meta = await _fetch_video_metadata("dQw4w9WgXcQ")
        assert meta["title"] == "Rick Astley - Never Gonna Give You Up"
        assert meta["author"] == "Rick Astley"

    @respx.mock
    async def test_oembed_failure_fallback(self):
        respx.get("https://www.youtube.com/oembed").respond(404)

        from llm_wiki.collectors.youtube_transcript import _fetch_video_metadata
        meta = await _fetch_video_metadata("dQw4w9WgXcQ")
        assert meta["title"] == ""
        assert meta["thumbnail_url"] == "https://i.ytimg.com/vi/dQw4w9WgXcQ/hqdefault.jpg"

    @respx.mock
    async def test_oembed_timeout_fallback(self):
        respx.get("https://www.youtube.com/oembed").mock(
            side_effect=httpx.ReadTimeout("timeout")
        )

        from llm_wiki.collectors.youtube_transcript import _fetch_video_metadata
        meta = await _fetch_video_metadata("dQw4w9WgXcQ")
        assert meta["title"] == ""
        assert "hqdefault" in meta["thumbnail_url"]
