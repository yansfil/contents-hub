"""Tests for Lens-based raw note collection logic."""

from __future__ import annotations

import pytest
from datetime import datetime, timezone, timedelta
from pathlib import Path

from llm_wiki.config import WikiConfig
from llm_wiki.lens import Lens, LensStore
from llm_wiki.lens_collector import (
    RawNote,
    LensGroup,
    CollectionResult,
    scan_source_notes,
    collect_by_lens,
    collect_for_lens,
    _parse_source_file,
    _build_searchable_text,
    _match_note_to_lens,
    _ensure_str_list,
    _parse_iso,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def vault_dir(tmp_path: Path) -> Path:
    """Create a vault directory structure."""
    sources = tmp_path / "sources"
    sources.mkdir()
    lenses = tmp_path / "lenses"
    lenses.mkdir()
    meta = tmp_path / ".llm-wiki"
    meta.mkdir()
    return tmp_path


@pytest.fixture
def config(vault_dir: Path) -> WikiConfig:
    """WikiConfig pointing at the temp vault."""
    return WikiConfig(vault_path=vault_dir)


@pytest.fixture
def ai_lens() -> Lens:
    """An AI research lens for testing."""
    return Lens(
        id="ai-research",
        name="AI Research",
        description="Artificial intelligence and machine learning",
        keywords=["machine learning", "transformer", "llm", "neural network"],
        default_tags=["ai", "research"],
        wiki_directory="topics/ai-research",
    )


@pytest.fixture
def frontend_lens() -> Lens:
    """A frontend development lens for testing."""
    return Lens(
        id="frontend",
        name="Frontend Dev",
        description="Frontend web development",
        keywords=["react", "javascript", "css", "typescript", "nextjs"],
        default_tags=["frontend", "web"],
        wiki_directory="topics/frontend",
    )


def _write_source(
    vault_dir: Path,
    filename: str,
    *,
    title: str = "Test Article",
    url: str = "https://example.com/test",
    source_type: str = "rss",
    status: str = "pending",
    lenses: list[str] | None = None,
    tags: list[str] | None = None,
    collected_at: str = "2024-06-15T10:00:00+00:00",
    body: str = "Some content about testing.",
) -> Path:
    """Write a source markdown file to the vault."""
    fm_lines = ["---"]
    fm_lines.append(f"title: {title}")
    fm_lines.append(f"url: {url}")
    fm_lines.append(f"source_type: {source_type}")
    fm_lines.append(f"status: {status}")
    fm_lines.append(f"collected_at: {collected_at}")

    if tags:
        fm_lines.append("tags:")
        for t in tags:
            fm_lines.append(f"  - {t}")
    if lenses:
        fm_lines.append("lenses:")
        for l in lenses:
            fm_lines.append(f"  - {l}")

    fm_lines.append("---")
    fm_lines.append("")
    fm_lines.append(body)

    path = vault_dir / "sources" / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(fm_lines), encoding="utf-8")
    return path


def _save_lens(vault_dir: Path, lens: Lens) -> None:
    """Save a lens YAML file."""
    config = WikiConfig(vault_path=vault_dir)
    store = LensStore(config)
    store.save(lens)


# ---------------------------------------------------------------------------
# RawNote
# ---------------------------------------------------------------------------


class TestRawNote:
    def test_body_preview_short(self):
        note = RawNote(
            path=Path("/vault/sources/test.md"),
            relative_path="sources/test.md",
            body="Short body",
        )
        assert note.body_preview == "Short body"

    def test_body_preview_long(self):
        long_body = "x" * 300
        note = RawNote(
            path=Path("/vault/sources/test.md"),
            relative_path="sources/test.md",
            body=long_body,
        )
        assert len(note.body_preview) == 201  # 200 + "…"
        assert note.body_preview.endswith("…")

    def test_filename(self):
        note = RawNote(
            path=Path("/vault/sources/20240615-article-abc123.md"),
            relative_path="sources/20240615-article-abc123.md",
        )
        assert note.filename == "20240615-article-abc123.md"

    def test_collected_datetime_valid(self):
        note = RawNote(
            path=Path("/test.md"),
            relative_path="test.md",
            collected_at="2024-06-15T10:00:00+00:00",
        )
        dt = note.collected_datetime
        assert dt is not None
        assert dt.year == 2024
        assert dt.month == 6
        assert dt.day == 15

    def test_collected_datetime_none(self):
        note = RawNote(path=Path("/test.md"), relative_path="test.md")
        assert note.collected_datetime is None

    def test_matches_lens_keywords_match(self, ai_lens: Lens):
        note = RawNote(
            path=Path("/test.md"),
            relative_path="test.md",
            title="Understanding Transformer Architectures",
            body="This article discusses neural network improvements.",
        )
        assert note.matches_lens_keywords(ai_lens) is True

    def test_matches_lens_keywords_no_match(self, ai_lens: Lens):
        note = RawNote(
            path=Path("/test.md"),
            relative_path="test.md",
            title="How to Cook Pasta",
            body="A recipe for delicious pasta.",
        )
        assert note.matches_lens_keywords(ai_lens) is False

    def test_matches_lens_keywords_threshold(self, ai_lens: Lens):
        note = RawNote(
            path=Path("/test.md"),
            relative_path="test.md",
            title="Transformer Architecture",  # matches "transformer"
            body="Some general text with no other keywords.",
        )
        # threshold=1 → should match (1 keyword)
        assert note.matches_lens_keywords(ai_lens, threshold=1) is True
        # threshold=2 → should NOT match (only 1 keyword)
        assert note.matches_lens_keywords(ai_lens, threshold=2) is False

    def test_matches_lens_keywords_empty(self):
        lens = Lens(id="empty", name="Empty", keywords=[])
        note = RawNote(
            path=Path("/test.md"),
            relative_path="test.md",
            title="anything",
        )
        assert note.matches_lens_keywords(lens) is False

    def test_matches_lens_keywords_via_tags(self, ai_lens: Lens):
        note = RawNote(
            path=Path("/test.md"),
            relative_path="test.md",
            title="Some Article",
            tags=["machine learning", "python"],
            body="",
        )
        assert note.matches_lens_keywords(ai_lens) is True


# ---------------------------------------------------------------------------
# LensGroup
# ---------------------------------------------------------------------------


class TestLensGroup:
    def test_total(self, ai_lens: Lens):
        group = LensGroup(
            lens=ai_lens,
            notes=[
                RawNote(path=Path("/a.md"), relative_path="a.md"),
                RawNote(path=Path("/b.md"), relative_path="b.md"),
            ],
        )
        assert group.total == 2

    def test_pending_count(self, ai_lens: Lens):
        group = LensGroup(
            lens=ai_lens,
            notes=[
                RawNote(path=Path("/a.md"), relative_path="a.md", status="pending"),
                RawNote(path=Path("/b.md"), relative_path="b.md", status="compiled"),
                RawNote(path=Path("/c.md"), relative_path="c.md", status="pending"),
            ],
        )
        assert group.pending_count == 2


# ---------------------------------------------------------------------------
# CollectionResult
# ---------------------------------------------------------------------------


class TestCollectionResult:
    def test_is_empty(self):
        result = CollectionResult()
        assert result.is_empty is True

    def test_not_empty(self, ai_lens: Lens):
        result = CollectionResult(
            groups=[LensGroup(
                lens=ai_lens,
                notes=[RawNote(path=Path("/a.md"), relative_path="a.md")],
            )],
            total_matched=1,
        )
        assert result.is_empty is False

    def test_group_for_lens(self, ai_lens: Lens, frontend_lens: Lens):
        g1 = LensGroup(lens=ai_lens, notes=[])
        g2 = LensGroup(lens=frontend_lens, notes=[])
        result = CollectionResult(groups=[g1, g2])
        assert result.group_for_lens("ai-research") is g1
        assert result.group_for_lens("nonexistent") is None

    def test_all_notes_deduplication(self, ai_lens: Lens, frontend_lens: Lens):
        note_a = RawNote(path=Path("/a.md"), relative_path="a.md")
        note_b = RawNote(path=Path("/b.md"), relative_path="b.md")
        # note_a appears in both groups
        result = CollectionResult(
            groups=[
                LensGroup(lens=ai_lens, notes=[note_a, note_b]),
                LensGroup(lens=frontend_lens, notes=[note_a]),
            ],
        )
        all_notes = result.all_notes()
        assert len(all_notes) == 2
        paths = [n.relative_path for n in all_notes]
        assert "a.md" in paths
        assert "b.md" in paths

    def test_summary(self, ai_lens: Lens):
        result = CollectionResult(
            groups=[LensGroup(
                lens=ai_lens,
                notes=[
                    RawNote(path=Path("/a.md"), relative_path="a.md", status="pending"),
                ],
                explicit_count=1,
            )],
            total_scanned=5,
            total_matched=1,
        )
        summary = result.summary()
        assert "5 source files" in summary
        assert "ai-research" in summary

    def test_to_dict(self, ai_lens: Lens):
        result = CollectionResult(
            groups=[LensGroup(
                lens=ai_lens,
                notes=[
                    RawNote(
                        path=Path("/a.md"),
                        relative_path="a.md",
                        title="Test",
                        source_type="rss",
                        status="pending",
                    ),
                ],
                explicit_count=1,
            )],
            total_scanned=1,
            total_matched=1,
        )
        d = result.to_dict()
        assert d["total_scanned"] == 1
        assert d["total_matched"] == 1
        assert len(d["groups"]) == 1
        assert d["groups"][0]["lens_id"] == "ai-research"
        assert d["groups"][0]["notes"][0]["title"] == "Test"


# ---------------------------------------------------------------------------
# _parse_source_file
# ---------------------------------------------------------------------------


class TestParseSourceFile:
    def test_parse_complete(self, vault_dir: Path):
        path = _write_source(
            vault_dir,
            "20240615-test-abc.md",
            title="Test Article",
            url="https://example.com/article",
            source_type="rss",
            status="pending",
            lenses=["ai-research"],
            tags=["ai", "ml"],
            collected_at="2024-06-15T10:00:00+00:00",
            body="Article body about machine learning.",
        )

        note = _parse_source_file(path, vault_dir)
        assert note.title == "Test Article"
        assert note.url == "https://example.com/article"
        assert note.source_type == "rss"
        assert note.status == "pending"
        assert note.lenses == ["ai-research"]
        assert note.tags == ["ai", "ml"]
        assert "machine learning" in note.body

    def test_parse_minimal(self, vault_dir: Path):
        path = vault_dir / "sources" / "minimal.md"
        path.write_text("---\ntitle: Minimal\n---\nBody.", encoding="utf-8")

        note = _parse_source_file(path, vault_dir)
        assert note.title == "Minimal"
        assert note.status == "pending"
        assert note.lenses == []
        assert note.tags == []

    def test_parse_no_frontmatter(self, vault_dir: Path):
        path = vault_dir / "sources" / "bare.md"
        path.write_text("Just a plain markdown file.", encoding="utf-8")

        note = _parse_source_file(path, vault_dir)
        assert note.title == ""
        assert note.body == "Just a plain markdown file."


# ---------------------------------------------------------------------------
# scan_source_notes
# ---------------------------------------------------------------------------


class TestScanSourceNotes:
    def test_scan_empty_vault(self, config: WikiConfig):
        notes = scan_source_notes(config)
        assert notes == []

    def test_scan_no_sources_dir(self, tmp_path: Path):
        cfg = WikiConfig(vault_path=tmp_path)
        notes = scan_source_notes(cfg)
        assert notes == []

    def test_scan_pending_only(self, vault_dir: Path, config: WikiConfig):
        _write_source(vault_dir, "pending.md", status="pending")
        _write_source(vault_dir, "compiled.md", status="compiled")
        _write_source(vault_dir, "skipped.md", status="skipped")

        notes = scan_source_notes(config, status_filter="pending")
        assert len(notes) == 1
        assert notes[0].status == "pending"

    def test_scan_all_statuses(self, vault_dir: Path, config: WikiConfig):
        _write_source(vault_dir, "a.md", status="pending")
        _write_source(vault_dir, "b.md", status="compiled")

        notes = scan_source_notes(config, status_filter="all")
        assert len(notes) == 2

    def test_scan_by_source_type(self, vault_dir: Path, config: WikiConfig):
        _write_source(vault_dir, "rss.md", source_type="rss")
        _write_source(vault_dir, "yt.md", source_type="youtube")

        notes = scan_source_notes(config, status_filter="all", source_type="rss")
        assert len(notes) == 1
        assert notes[0].source_type == "rss"

    def test_scan_with_limit(self, vault_dir: Path, config: WikiConfig):
        for i in range(5):
            _write_source(vault_dir, f"note-{i}.md")

        notes = scan_source_notes(config, limit=3)
        assert len(notes) == 3

    def test_scan_date_filter_since(self, vault_dir: Path, config: WikiConfig):
        _write_source(
            vault_dir, "old.md",
            collected_at="2024-01-01T00:00:00+00:00",
        )
        _write_source(
            vault_dir, "new.md",
            collected_at="2024-07-01T00:00:00+00:00",
        )

        since = datetime(2024, 6, 1, tzinfo=timezone.utc)
        notes = scan_source_notes(config, status_filter="all", since=since)
        assert len(notes) == 1
        assert "new" in notes[0].filename

    def test_scan_date_filter_until(self, vault_dir: Path, config: WikiConfig):
        _write_source(
            vault_dir, "old.md",
            collected_at="2024-01-01T00:00:00+00:00",
        )
        _write_source(
            vault_dir, "new.md",
            collected_at="2024-07-01T00:00:00+00:00",
        )

        until = datetime(2024, 6, 1, tzinfo=timezone.utc)
        notes = scan_source_notes(config, status_filter="all", until=until)
        assert len(notes) == 1
        assert "old" in notes[0].filename

    def test_scan_subdirectory(self, vault_dir: Path, config: WikiConfig):
        """Source files in subdirectories are also found."""
        subdir = vault_dir / "sources" / "rss"
        subdir.mkdir()
        _write_source(vault_dir, "rss/feed-article.md")
        _write_source(vault_dir, "top-level.md")

        notes = scan_source_notes(config)
        assert len(notes) == 2


# ---------------------------------------------------------------------------
# collect_by_lens
# ---------------------------------------------------------------------------


class TestCollectByLens:
    def test_explicit_lens_match(
        self, vault_dir: Path, config: WikiConfig, ai_lens: Lens
    ):
        _save_lens(vault_dir, ai_lens)
        _write_source(
            vault_dir, "ai-article.md",
            title="LLM Advances",
            lenses=["ai-research"],
        )
        _write_source(
            vault_dir, "unrelated.md",
            title="Cooking Tips",
        )

        result = collect_by_lens(config, include_keyword_matches=False)
        assert result.total_matched == 1
        assert len(result.groups) == 1
        assert result.groups[0].lens.id == "ai-research"
        assert result.groups[0].explicit_count == 1

    def test_keyword_match(
        self, vault_dir: Path, config: WikiConfig, ai_lens: Lens
    ):
        _save_lens(vault_dir, ai_lens)
        _write_source(
            vault_dir, "transformer-article.md",
            title="Understanding Transformer Models",
            body="A deep dive into transformer architecture and LLM training.",
        )

        result = collect_by_lens(config, include_keyword_matches=True)
        assert result.total_matched == 1
        assert result.groups[0].keyword_count == 1

    def test_multi_lens_assignment(
        self, vault_dir: Path, config: WikiConfig, ai_lens: Lens, frontend_lens: Lens
    ):
        _save_lens(vault_dir, ai_lens)
        _save_lens(vault_dir, frontend_lens)
        _write_source(
            vault_dir, "multi.md",
            title="AI-Powered React Components",
            lenses=["ai-research", "frontend"],
        )

        result = collect_by_lens(config, include_keyword_matches=False)
        assert result.total_matched == 1
        assert len(result.groups) == 2

    def test_ungrouped_notes(
        self, vault_dir: Path, config: WikiConfig, ai_lens: Lens
    ):
        _save_lens(vault_dir, ai_lens)
        _write_source(
            vault_dir, "unrelated.md",
            title="Random Thoughts",
            body="Nothing about AI here.",
        )

        result = collect_by_lens(config, include_ungrouped=True)
        assert result.total_ungrouped == 1
        assert len(result.ungrouped) == 1

    def test_filter_specific_lens(
        self, vault_dir: Path, config: WikiConfig, ai_lens: Lens, frontend_lens: Lens
    ):
        _save_lens(vault_dir, ai_lens)
        _save_lens(vault_dir, frontend_lens)
        _write_source(vault_dir, "ai.md", lenses=["ai-research"])
        _write_source(vault_dir, "fe.md", lenses=["frontend"])

        result = collect_by_lens(
            config,
            lens_ids=["ai-research"],
            include_keyword_matches=False,
        )
        assert len(result.groups) == 1
        assert result.groups[0].lens.id == "ai-research"
        assert result.groups[0].total == 1

    def test_empty_vault(self, vault_dir: Path, config: WikiConfig, ai_lens: Lens):
        _save_lens(vault_dir, ai_lens)
        result = collect_by_lens(config)
        assert result.is_empty
        assert result.total_scanned == 0

    def test_no_lenses_configured(self, vault_dir: Path, config: WikiConfig):
        _write_source(vault_dir, "orphan.md", title="Orphan Note")
        result = collect_by_lens(config, include_ungrouped=True)
        assert len(result.groups) == 0
        assert result.total_ungrouped == 1

    def test_disabled_lens_excluded(
        self, vault_dir: Path, config: WikiConfig
    ):
        disabled = Lens(
            id="disabled",
            name="Disabled Lens",
            enabled=False,
            keywords=["test"],
        )
        _save_lens(vault_dir, disabled)
        _write_source(vault_dir, "test.md", lenses=["disabled"])

        # collect_by_lens uses list_enabled() by default
        result = collect_by_lens(config, include_keyword_matches=False)
        assert len(result.groups) == 0

    def test_status_filter(
        self, vault_dir: Path, config: WikiConfig, ai_lens: Lens
    ):
        _save_lens(vault_dir, ai_lens)
        _write_source(vault_dir, "pending.md", lenses=["ai-research"], status="pending")
        _write_source(vault_dir, "compiled.md", lenses=["ai-research"], status="compiled")

        # Default: pending only
        result = collect_by_lens(config, include_keyword_matches=False)
        assert result.groups[0].total == 1

        # All statuses
        result = collect_by_lens(
            config,
            status_filter="all",
            include_keyword_matches=False,
        )
        assert result.groups[0].total == 2


# ---------------------------------------------------------------------------
# collect_for_lens
# ---------------------------------------------------------------------------


class TestCollectForLens:
    def test_collect_existing_lens(
        self, vault_dir: Path, config: WikiConfig, ai_lens: Lens
    ):
        _save_lens(vault_dir, ai_lens)
        _write_source(vault_dir, "ai.md", lenses=["ai-research"])

        group = collect_for_lens(config, "ai-research", include_keyword_matches=False)
        assert group is not None
        assert group.lens.id == "ai-research"
        assert group.total == 1

    def test_collect_nonexistent_lens(self, config: WikiConfig):
        group = collect_for_lens(config, "nonexistent")
        assert group is None


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


class TestBuildSearchableText:
    def test_combines_fields(self):
        text = _build_searchable_text("My Title", ["tag1", "TAG2"], "Body content")
        assert "my title" in text
        assert "tag1" in text
        assert "tag2" in text
        assert "body content" in text

    def test_truncates_body(self):
        long_body = "x" * 5000
        text = _build_searchable_text("", [], long_body, max_body_chars=100)
        assert len(text) < 200  # title + body truncated


class TestMatchNoteToLens:
    def test_explicit_match(self, ai_lens: Lens):
        note = RawNote(
            path=Path("/test.md"),
            relative_path="test.md",
            lenses=["ai-research"],
        )
        assert _match_note_to_lens(note, ai_lens) == "explicit"

    def test_keyword_match(self, ai_lens: Lens):
        note = RawNote(
            path=Path("/test.md"),
            relative_path="test.md",
            title="Transformer Architecture Deep Dive",
        )
        assert _match_note_to_lens(note, ai_lens) == "keyword"

    def test_no_match(self, ai_lens: Lens):
        note = RawNote(
            path=Path("/test.md"),
            relative_path="test.md",
            title="Cooking Recipes",
            body="How to make pasta",
        )
        assert _match_note_to_lens(note, ai_lens) is None

    def test_explicit_takes_priority(self, ai_lens: Lens):
        """Even if keywords match, explicit is returned first."""
        note = RawNote(
            path=Path("/test.md"),
            relative_path="test.md",
            title="Transformer Research",
            lenses=["ai-research"],
        )
        assert _match_note_to_lens(note, ai_lens) == "explicit"

    def test_keyword_disabled(self, ai_lens: Lens):
        note = RawNote(
            path=Path("/test.md"),
            relative_path="test.md",
            title="Transformer Research",
        )
        assert _match_note_to_lens(
            note, ai_lens, include_keyword_matches=False
        ) is None


class TestEnsureStrList:
    def test_list_input(self):
        assert _ensure_str_list(["a", "b"]) == ["a", "b"]

    def test_string_input(self):
        assert _ensure_str_list("single") == ["single"]

    def test_empty_string(self):
        assert _ensure_str_list("") == []

    def test_none(self):
        assert _ensure_str_list(None) == []

    def test_mixed_list(self):
        assert _ensure_str_list(["a", 1, None]) == ["a", "1"]


class TestParseIso:
    def test_valid(self):
        dt = _parse_iso("2024-06-15T10:00:00+00:00")
        assert dt is not None
        assert dt.year == 2024

    def test_naive_gets_utc(self):
        dt = _parse_iso("2024-06-15T10:00:00")
        assert dt is not None
        assert dt.tzinfo == timezone.utc

    def test_none(self):
        assert _parse_iso(None) is None

    def test_empty(self):
        assert _parse_iso("") is None

    def test_invalid(self):
        assert _parse_iso("not-a-date") is None
