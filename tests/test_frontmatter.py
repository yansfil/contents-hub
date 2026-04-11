"""Tests for the frontmatter metadata utility."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from llm_wiki.frontmatter import (
    Frontmatter,
    assemble_markdown,
    extract_raw_frontmatter,
    parse_frontmatter,
    parse_to_frontmatter,
    serialize_frontmatter,
    update_file_frontmatter,
    _yaml_scalar,
    _needs_quoting,
    _to_iso,
    F_SOURCE_TYPE,
    F_URL,
    F_TITLE,
    F_TAGS,
    F_LENSES,
    F_COLLECTED_AT,
    F_STATUS,
    F_TYPE,
    F_COMPILED_AT,
    F_CREATED_AT,
    STATUS_PENDING,
    STATUS_COMPILED,
    TYPE_WIKI,
    TYPE_LENS,
)

FIXED_TIME = datetime(2024, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
FIXED_ISO = "2024-06-15T12:00:00+00:00"


# ---------------------------------------------------------------------------
# Frontmatter.for_source
# ---------------------------------------------------------------------------


class TestForSource:
    def test_minimal_source(self) -> None:
        fm = Frontmatter.for_source(
            source_type="rss",
            url="https://example.com/post",
            title="My Post",
        )
        d = fm.to_dict()
        assert d[F_SOURCE_TYPE] == "rss"
        assert d[F_URL] == "https://example.com/post"
        assert d[F_TITLE] == "My Post"
        assert d[F_STATUS] == STATUS_PENDING
        assert F_COLLECTED_AT in d  # auto-populated

    def test_full_source(self) -> None:
        fm = Frontmatter.for_source(
            source_type="youtube",
            url="https://youtube.com/watch?v=abc",
            title="Video Title",
            author="Channel",
            published=FIXED_TIME,
            collected_at=FIXED_TIME,
            feed="My Channel",
            tags=["tech", "ai"],
            lenses=["research"],
            extra={"video_id": "abc"},
        )
        d = fm.to_dict()
        assert d["published"] == FIXED_ISO
        assert d["collected_at"] == FIXED_ISO
        assert d["author"] == "Channel"
        assert d["feed"] == "My Channel"
        assert d["tags"] == ["tech", "ai"]
        assert d["lenses"] == ["research"]
        assert d["video_id"] == "abc"

    def test_published_as_string(self) -> None:
        fm = Frontmatter.for_source(
            source_type="rss",
            url="https://x.com",
            title="T",
            published="2024-01-01",
        )
        assert fm.published == "2024-01-01"


# ---------------------------------------------------------------------------
# Frontmatter.for_wiki
# ---------------------------------------------------------------------------


class TestForWiki:
    def test_basic_wiki(self) -> None:
        fm = Frontmatter.for_wiki(
            title="Transformer Architecture",
            tags=["deep-learning"],
            lenses=["ai-research"],
        )
        d = fm.to_dict()
        assert d[F_TYPE] == TYPE_WIKI
        assert d[F_TITLE] == "Transformer Architecture"
        assert d[F_TAGS] == ["deep-learning"]
        assert d[F_LENSES] == ["ai-research"]
        assert F_COMPILED_AT in d
        assert F_CREATED_AT in d

    def test_wiki_with_sources(self) -> None:
        fm = Frontmatter.for_wiki(
            title="Test",
            sources=["sources/rss/post.md"],
            source_urls=["https://example.com"],
            aliases=["Testing"],
        )
        d = fm.to_dict()
        assert d["sources"] == ["sources/rss/post.md"]
        assert d["source_urls"] == ["https://example.com"]
        assert d["aliases"] == ["Testing"]


# ---------------------------------------------------------------------------
# to_dict - field ordering and omission
# ---------------------------------------------------------------------------


class TestToDict:
    def test_empty_fields_omitted(self) -> None:
        fm = Frontmatter(source_type="rss", url="https://x.com", title="T")
        d = fm.to_dict()
        assert "author" not in d
        assert "tags" not in d
        assert "lenses" not in d
        assert "aliases" not in d
        assert "compiled_at" not in d

    def test_extra_fields_appended(self) -> None:
        fm = Frontmatter(
            source_type="twitter",
            url="https://x.com/status/1",
            title="Tweet",
            extra={"tweet_id": "123", "like_count": 42},
        )
        d = fm.to_dict()
        assert d["tweet_id"] == "123"
        assert d["like_count"] == 42

    def test_extra_does_not_override_standard(self) -> None:
        fm = Frontmatter(
            title="Test",
            extra={"title": "OVERRIDDEN"},
        )
        d = fm.to_dict()
        assert d["title"] == "Test"


# ---------------------------------------------------------------------------
# serialize / serialize_frontmatter
# ---------------------------------------------------------------------------


class TestSerialize:
    def test_basic_serialize(self) -> None:
        fm = Frontmatter.for_source(
            source_type="rss",
            url="https://example.com",
            title="Hello World",
            collected_at=FIXED_TIME,
        )
        result = fm.serialize()
        assert result.startswith("---\n")
        assert result.endswith("---")
        assert "source_type: rss" in result
        assert "title: Hello World" in result

    def test_list_serialization(self) -> None:
        result = serialize_frontmatter({
            "title": "Test",
            "tags": ["a", "b", "c"],
        })
        assert "tags:\n  - a\n  - b\n  - c" in result

    def test_empty_list_serialization(self) -> None:
        result = serialize_frontmatter({"tags": []})
        assert "tags: []" in result

    def test_special_chars_quoted(self) -> None:
        result = serialize_frontmatter({
            "title": "Hello: World",
            "url": "https://example.com",
        })
        assert 'title: "Hello: World"' in result
        # URL without colon-space should not be quoted
        assert "url: https://example.com" in result

    def test_boolean_like_values_quoted(self) -> None:
        result = serialize_frontmatter({"flag": "true", "active": True})
        assert 'flag: "true"' in result
        assert "active: true" in result

    def test_integer_values(self) -> None:
        result = serialize_frontmatter({"views": 1500})
        assert "views: 1500" in result

    def test_none_value(self) -> None:
        result = serialize_frontmatter({"key": None})
        assert "key: null" in result


# ---------------------------------------------------------------------------
# assemble_markdown
# ---------------------------------------------------------------------------


class TestAssembleMarkdown:
    def test_basic_assembly(self) -> None:
        result = assemble_markdown(
            {"title": "Hello", "tags": ["a"]},
            "# Hello\n\nBody content.",
        )
        assert result.startswith("---\n")
        assert "title: Hello" in result
        assert "---\n\n# Hello" in result
        assert result.endswith("Body content.\n")

    def test_trailing_newline(self) -> None:
        result = assemble_markdown({"k": "v"}, "body")
        assert result.endswith("\n")

    def test_already_trailing_newline(self) -> None:
        result = assemble_markdown({"k": "v"}, "body\n")
        assert result.endswith("body\n")
        assert not result.endswith("body\n\n")

    def test_double_fence(self) -> None:
        result = assemble_markdown({"k": "v"}, "body")
        fences = [line for line in result.split("\n") if line == "---"]
        assert len(fences) == 2


# ---------------------------------------------------------------------------
# parse_frontmatter
# ---------------------------------------------------------------------------


class TestParseFrontmatter:
    def test_basic_parse(self) -> None:
        content = "---\ntitle: Hello\ntags:\n  - a\n  - b\n---\n\n# Hello\n\nBody."
        fm, body = parse_frontmatter(content)
        assert fm["title"] == "Hello"
        assert fm["tags"] == ["a", "b"]
        assert "# Hello" in body

    def test_no_frontmatter(self) -> None:
        content = "# Just a heading\n\nNo frontmatter here."
        fm, body = parse_frontmatter(content)
        assert fm == {}
        assert body == content

    def test_unclosed_frontmatter(self) -> None:
        content = "---\ntitle: Hello\nNo closing fence"
        fm, body = parse_frontmatter(content)
        assert fm == {}
        assert body == content

    def test_roundtrip(self) -> None:
        original = Frontmatter.for_source(
            source_type="rss",
            url="https://example.com",
            title="Roundtrip Test",
            tags=["python", "test"],
            lenses=["dev"],
            collected_at=FIXED_TIME,
        )
        md = assemble_markdown(original.to_dict(), "# Content\n\nBody here.")
        parsed_fm, parsed_body = parse_frontmatter(md)

        assert parsed_fm["source_type"] == "rss"
        assert parsed_fm["title"] == "Roundtrip Test"
        assert parsed_fm["tags"] == ["python", "test"]
        assert parsed_fm["lenses"] == ["dev"]
        assert "Body here." in parsed_body


# ---------------------------------------------------------------------------
# parse_to_frontmatter
# ---------------------------------------------------------------------------


class TestParseToFrontmatter:
    def test_hydrate(self) -> None:
        content = "---\nsource_type: youtube\ntitle: Video\nurl: https://yt.com\ntags:\n  - tech\nvideo_id: abc\n---\n\nBody"
        fm, body = parse_to_frontmatter(content)
        assert isinstance(fm, Frontmatter)
        assert fm.source_type == "youtube"
        assert fm.title == "Video"
        assert fm.tags == ["tech"]
        assert fm.extra["video_id"] == "abc"
        assert "Body" in body


# ---------------------------------------------------------------------------
# Mutation helpers
# ---------------------------------------------------------------------------


class TestMutationHelpers:
    def test_set_compiled(self) -> None:
        fm = Frontmatter.for_source(
            source_type="rss",
            url="https://x.com",
            title="T",
        )
        assert fm.status == STATUS_PENDING
        fm.set_compiled(at=FIXED_TIME)
        assert fm.status == STATUS_COMPILED
        assert fm.compiled_at == FIXED_ISO

    def test_add_tags_dedup(self) -> None:
        fm = Frontmatter(tags=["a", "b"])
        fm.add_tags("b", "c", "a", "d")
        assert fm.tags == ["a", "b", "c", "d"]

    def test_add_lenses_dedup(self) -> None:
        fm = Frontmatter(lenses=["x"])
        fm.add_lenses("x", "y")
        assert fm.lenses == ["x", "y"]

    def test_merge_lists(self) -> None:
        fm = Frontmatter(tags=["a"], lenses=["x"])
        fm.merge({"tags": ["a", "b"], "lenses": ["y"]})
        assert fm.tags == ["a", "b"]
        assert fm.lenses == ["x", "y"]

    def test_merge_scalars(self) -> None:
        fm = Frontmatter(title="Old")
        fm.merge({"title": "New", "custom_key": "val"})
        assert fm.title == "New"
        assert fm.extra["custom_key"] == "val"


# ---------------------------------------------------------------------------
# YAML scalar helpers
# ---------------------------------------------------------------------------


class TestYamlScalar:
    def test_simple_string(self) -> None:
        assert _yaml_scalar("hello") == "hello"

    def test_colon_space_quoted(self) -> None:
        assert _yaml_scalar("key: value") == '"key: value"'

    def test_boolean_string_quoted(self) -> None:
        assert _yaml_scalar("true") == '"true"'
        assert _yaml_scalar("yes") == '"yes"'

    def test_actual_bool(self) -> None:
        assert _yaml_scalar(True) == "true"
        assert _yaml_scalar(False) == "false"

    def test_integer(self) -> None:
        assert _yaml_scalar(42) == "42"

    def test_none(self) -> None:
        assert _yaml_scalar(None) == "null"

    def test_empty_string_quoted(self) -> None:
        assert _yaml_scalar("") == '""'

    def test_url_not_quoted(self) -> None:
        # URLs have colons but no "colon-space" pattern
        assert _yaml_scalar("https://example.com") == "https://example.com"


class TestNeedsQuoting:
    def test_plain(self) -> None:
        assert not _needs_quoting("hello")

    def test_special_start_chars(self) -> None:
        for ch in ("*", "&", "!", "|", ">", "%", "@", "`", "[", "{"):
            assert _needs_quoting(f"{ch}value"), f"Should quote: {ch}value"

    def test_newline(self) -> None:
        assert _needs_quoting("line1\nline2")

    def test_null_like(self) -> None:
        assert _needs_quoting("null")
        assert _needs_quoting("~")


# ---------------------------------------------------------------------------
# _to_iso helper
# ---------------------------------------------------------------------------


class TestToIso:
    def test_datetime(self) -> None:
        assert _to_iso(FIXED_TIME) == FIXED_ISO

    def test_string_passthrough(self) -> None:
        assert _to_iso("2024-01-01") == "2024-01-01"

    def test_none(self) -> None:
        assert _to_iso(None) is None


# ---------------------------------------------------------------------------
# Frontmatter.for_lens
# ---------------------------------------------------------------------------


class TestForLens:
    def test_basic_lens(self) -> None:
        fm = Frontmatter.for_lens(
            lens_id="ai-research",
            name="AI Research",
            tags=["ai", "research"],
            created_at=FIXED_TIME,
        )
        d = fm.to_dict()
        assert d[F_TYPE] == TYPE_LENS
        assert d[F_TAGS] == ["ai", "research"]
        assert d["id"] == "ai-research"
        assert d["name"] == "AI Research"
        assert d["compile_strategy"] == "merge"
        assert d["priority"] == 0
        assert d["enabled"] is True
        assert d["created_at"] == FIXED_ISO

    def test_lens_no_timestamps_when_none(self) -> None:
        """When created_at/updated_at are None, they should not appear."""
        fm = Frontmatter.for_lens(
            lens_id="minimal",
            name="Minimal",
        )
        d = fm.to_dict()
        assert "created_at" not in d
        assert "updated_at" not in d

    def test_lens_with_keywords(self) -> None:
        fm = Frontmatter.for_lens(
            lens_id="frontend",
            name="Frontend Dev",
            keywords=["react", "vue", "css"],
            wiki_directory="topics/frontend",
            source_ids=["uuid-1", "uuid-2"],
            created_at=FIXED_TIME,
        )
        d = fm.to_dict()
        assert d["keywords"] == ["react", "vue", "css"]
        assert d["wiki_directory"] == "topics/frontend"
        assert d["source_ids"] == ["uuid-1", "uuid-2"]

    def test_lens_roundtrip(self) -> None:
        fm = Frontmatter.for_lens(
            lens_id="devops",
            name="DevOps",
            compile_strategy="replace",
            priority=5,
            enabled=False,
            tags=["infra"],
            created_at=FIXED_TIME,
        )
        md = assemble_markdown(fm.to_dict(), "# DevOps\n\nLens content.")
        parsed_fm, parsed_body = parse_frontmatter(md)
        assert parsed_fm["type"] == "lens"
        assert parsed_fm["id"] == "devops"
        assert parsed_fm["name"] == "DevOps"
        assert parsed_fm["enabled"] is False
        assert parsed_fm["priority"] == 5
        assert "Lens content." in parsed_body


# ---------------------------------------------------------------------------
# extract_raw_frontmatter
# ---------------------------------------------------------------------------


class TestExtractRawFrontmatter:
    def test_basic_extraction(self) -> None:
        content = "---\ntitle: Hello\ntags:\n  - a\n---\n\n# Hello"
        raw = extract_raw_frontmatter(content)
        assert raw == "---\ntitle: Hello\ntags:\n  - a\n---"

    def test_no_frontmatter(self) -> None:
        content = "# Just a heading"
        assert extract_raw_frontmatter(content) == ""

    def test_unclosed_frontmatter(self) -> None:
        content = "---\ntitle: Hello\nNo closing"
        assert extract_raw_frontmatter(content) == ""

    def test_exact_match_for_edit(self) -> None:
        """Extracted raw frontmatter can be found as substring in original."""
        content = "---\ntype: wiki\ntitle: Test\n---\n\nBody text."
        raw = extract_raw_frontmatter(content)
        assert content.startswith(raw)


# ---------------------------------------------------------------------------
# update_file_frontmatter
# ---------------------------------------------------------------------------


class TestUpdateFileFrontmatter:
    def test_update_scalar(self, tmp_path: Path) -> None:
        p = tmp_path / "test.md"
        p.write_text("---\ntitle: Old\nstatus: pending\n---\n\n# Body\n")

        result = update_file_frontmatter(p, {"status": "compiled"})
        assert result["status"] == "compiled"
        assert result["title"] == "Old"

        # Verify written content
        content = p.read_text()
        assert "status: compiled" in content
        assert "# Body" in content

    def test_merge_list_tags(self, tmp_path: Path) -> None:
        p = tmp_path / "test.md"
        p.write_text("---\ntitle: Test\ntags:\n  - a\n  - b\n---\n\nBody\n")

        result = update_file_frontmatter(p, {"tags": ["b", "c", "d"]})
        assert result["tags"] == ["a", "b", "c", "d"]

    def test_replace_list_no_merge(self, tmp_path: Path) -> None:
        p = tmp_path / "test.md"
        p.write_text("---\ntitle: Test\ntags:\n  - a\n  - b\n---\n\nBody\n")

        result = update_file_frontmatter(
            p, {"tags": ["x", "y"]}, list_merge=False
        )
        assert result["tags"] == ["x", "y"]

    def test_add_new_fields(self, tmp_path: Path) -> None:
        p = tmp_path / "test.md"
        p.write_text("---\ntitle: Test\n---\n\nBody\n")

        result = update_file_frontmatter(p, {
            "lenses": ["ai-research"],
            "compiled_at": FIXED_ISO,
        })
        assert result["lenses"] == ["ai-research"]
        assert result["compiled_at"] == FIXED_ISO

    def test_preserves_body(self, tmp_path: Path) -> None:
        body = "# My Note\n\nSome **rich** content with [[wikilinks]].\n"
        p = tmp_path / "test.md"
        p.write_text(f"---\ntitle: My Note\n---\n\n{body}")

        update_file_frontmatter(p, {"status": "compiled"})
        content = p.read_text()
        assert "[[wikilinks]]" in content
        assert "Some **rich** content" in content

    def test_file_not_found(self, tmp_path: Path) -> None:
        p = tmp_path / "nonexistent.md"
        with pytest.raises(FileNotFoundError):
            update_file_frontmatter(p, {"status": "compiled"})

    def test_no_existing_frontmatter(self, tmp_path: Path) -> None:
        p = tmp_path / "test.md"
        p.write_text("# Just content\n\nNo frontmatter here.\n")

        result = update_file_frontmatter(p, {"title": "Added"})
        assert result["title"] == "Added"

        content = p.read_text()
        assert content.startswith("---\n")
        assert "title: Added" in content

    def test_merge_lenses_dedup(self, tmp_path: Path) -> None:
        p = tmp_path / "test.md"
        p.write_text("---\nlenses:\n  - ai\n  - devops\n---\n\nBody\n")

        result = update_file_frontmatter(p, {"lenses": ["ai", "frontend"]})
        assert result["lenses"] == ["ai", "devops", "frontend"]
