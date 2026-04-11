"""Tests for browser collector utilities."""

import json
import pytest

import sys
from pathlib import Path

# Allow importing from src/llm_wiki
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from llm_wiki.collectors.browser import (
    SearchResult,
    ExtractedPage,
    build_search_url,
    parse_search_results_json,
    parse_extracted_page_json,
    derive_tags_from_query,
    truncate_content,
    chromux_search_extract_js,
    chromux_extract_content_js,
)


class TestBuildSearchUrl:
    def test_google_default(self):
        url = build_search_url("LLM fine tuning best practices")
        assert url.startswith("https://www.google.com/search?q=")
        assert "LLM+fine+tuning" in url

    def test_duckduckgo(self):
        url = build_search_url("rust async", engine="duckduckgo")
        assert url.startswith("https://duckduckgo.com/?q=")
        assert "rust+async" in url

    def test_special_characters_encoded(self):
        url = build_search_url("C++ templates <T>")
        assert "%3CT%3E" in url or "%3Ct%3E" in url.lower()


class TestParseSearchResults:
    def test_valid_results(self):
        data = [
            {"url": "https://example.com/article", "title": "Example Article"},
            {"url": "https://blog.dev/post", "title": "Dev Blog Post"},
        ]
        results = parse_search_results_json(json.dumps(data))
        assert len(results) == 2
        assert results[0].url == "https://example.com/article"
        assert results[0].title == "Example Article"

    def test_filters_google_urls(self):
        data = [
            {"url": "https://www.google.com/search?q=test", "title": "Google"},
            {"url": "https://example.com/real", "title": "Real Result"},
        ]
        results = parse_search_results_json(json.dumps(data))
        assert len(results) == 1
        assert results[0].url == "https://example.com/real"

    def test_deduplicates_domains(self):
        data = [
            {"url": "https://example.com/page1", "title": "Page 1"},
            {"url": "https://example.com/page2", "title": "Page 2"},
            {"url": "https://other.com/page", "title": "Other"},
        ]
        results = parse_search_results_json(json.dumps(data))
        assert len(results) == 2
        assert results[0].url == "https://example.com/page1"
        assert results[1].url == "https://other.com/page"

    def test_invalid_json(self):
        results = parse_search_results_json("not json")
        assert results == []

    def test_empty_list(self):
        results = parse_search_results_json("[]")
        assert results == []

    def test_skips_invalid_urls(self):
        data = [
            {"url": "", "title": "Empty"},
            {"url": "javascript:void(0)", "title": "JS"},
            {"url": "https://valid.com", "title": "Valid"},
        ]
        results = parse_search_results_json(json.dumps(data))
        assert len(results) == 1


class TestParseExtractedPage:
    def test_valid_page(self):
        data = {
            "url": "https://example.com/article",
            "title": "  Article Title  ",
            "content": "This is the article content with multiple words.",
        }
        page = parse_extracted_page_json(json.dumps(data))
        assert page is not None
        assert page.url == "https://example.com/article"
        assert page.title == "Article Title"
        assert page.domain == "example.com"
        assert page.word_count > 0

    def test_missing_content(self):
        data = {"url": "https://example.com", "title": "Test", "content": ""}
        page = parse_extracted_page_json(json.dumps(data))
        assert page is None

    def test_invalid_json(self):
        page = parse_extracted_page_json("broken")
        assert page is None


class TestDeriveTagsFromQuery:
    def test_basic_query(self):
        tags = derive_tags_from_query("machine learning transformers")
        assert "machine" in tags
        assert "learning" in tags
        assert "transformers" in tags

    def test_filters_stopwords(self):
        tags = derive_tags_from_query("what is the best way to learn rust")
        assert "the" not in tags
        assert "way" in tags
        assert "learn" in tags
        assert "rust" in tags

    def test_filters_short_words(self):
        tags = derive_tags_from_query("AI ML NLP deep learning")
        # "AI" and "ML" are <= 2 chars, filtered out
        assert "nlp" in tags
        assert "deep" in tags
        assert "learning" in tags

    def test_max_five_tags(self):
        tags = derive_tags_from_query(
            "advanced distributed systems consensus algorithms byzantine fault tolerance"
        )
        assert len(tags) <= 5

    def test_lowercase(self):
        tags = derive_tags_from_query("React NextJS TypeScript")
        assert all(t == t.lower() for t in tags)


class TestTruncateContent:
    def test_short_content_unchanged(self):
        assert truncate_content("short text", 500) == "short text"

    def test_truncates_at_word_boundary(self):
        content = "word " * 200  # 1000 chars
        result = truncate_content(content, 100)
        assert len(result) <= 104  # 100 + "..."
        assert result.endswith("...")
        assert not result.endswith(" ...")  # should break at word

    def test_exact_limit(self):
        content = "x" * 500
        assert truncate_content(content, 500) == content


class TestChromuxJs:
    def test_search_extract_google(self):
        js = chromux_search_extract_js("google")
        assert "div.g" in js
        assert "JSON.stringify" in js

    def test_search_extract_duckduckgo(self):
        js = chromux_search_extract_js("duckduckgo")
        assert ".result" in js

    def test_extract_content(self):
        js = chromux_extract_content_js(3000)
        assert "3000" in js
        assert "article" in js
        assert "JSON.stringify" in js
