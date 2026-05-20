"""Tests for the Lens Inbox feature.

Covers:
- Pure helpers in :mod:`contents_hub.lens_inbox` — bullet parsing, deterministic
  representative selection, source-note relative path, candidate assembly.
- ``query_lens_inbox`` default rendering, status / lens / subscription
  filters, list and Lens-grouped view modes, empty state, malformed Lens
  metadata fallback.
- The ``GET /lens-inbox`` page render and the ``/lens-inbox/data`` JSON
  endpoint.
- The new ``POST /raw-items/{id}/archive`` and ``/restore`` endpoints.
"""

from __future__ import annotations

import datetime as _dt

import pytest
from fastapi.testclient import TestClient

from contents_hub.config import WikiConfig
from contents_hub.db import get_db, init_db
from contents_hub.lens_inbox import (
    LensMetadata,
    build_candidate,
    parse_bullets_json,
    query_lens_inbox,
    select_representative,
    source_note_relative_path,
)
from contents_hub.web.app import create_app


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def vault(tmp_path):
    cfg = WikiConfig(vault_path=tmp_path)
    (tmp_path / ".llm-wiki").mkdir(parents=True, exist_ok=True)
    init_db(cfg)
    return cfg


@pytest.fixture
def client(vault):
    return TestClient(create_app(vault))


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


def _seed_inbox_corpus(cfg) -> None:
    """Seed a mixed corpus for filter / view-mode coverage.

    Items:
        1 raw, sub=1, lenses=[ai, rust]   — multi-lens
        2 raw, sub=1, lenses=[rust]
        3 promoted, sub=2, lenses=[ai]
        4 archived, sub=1, lenses=[rust]
        5 raw, sub=2, no lens metadata    — hidden by default; digest may include
        6 raw, sub=1, lenses=[ai] with malformed bullets_json
    """
    now = _now()
    older = "2026-01-01T00:00:00+00:00"
    newer = "2026-04-10T00:00:00+00:00"
    with get_db(cfg) as conn:
        conn.executemany(
            "INSERT INTO subscriptions (id,url,title,created_at,updated_at) "
            "VALUES (?,?,?,?,?)",
            [
                (1, "https://a.example/", "Sub A", now, now),
                (2, "https://b.example/", "", now, now),
            ],
        )
        conn.executemany(
            "INSERT INTO lenses (id,name,created_at,updated_at) VALUES (?,?,?,?)",
            [
                ("ai", "AI Research", now, now),
                ("rust", "", now, now),
            ],
        )
        conn.executemany(
            "INSERT INTO raw_items "
            "(id,url,title,body,status,subscription_id,content_summary,"
            "collected_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
            [
                (1, "u1", "First", "body1", "raw", 1, "preview1", newer, newer),
                (
                    2,
                    "u2",
                    "Second",
                    "body2",
                    "raw",
                    1,
                    "",
                    "2026-04-09T00:00:00+00:00",
                    "2026-04-09T00:00:00+00:00",
                ),
                (3, "u3", "Promoted", "body3", "promoted", 2, "", older, older),
                (
                    4,
                    "u4",
                    "Archived",
                    "body4",
                    "archived",
                    1,
                    "",
                    "2026-02-01T00:00:00+00:00",
                    "2026-02-01T00:00:00+00:00",
                ),
                (
                    5,
                    "u5",
                    "NoLens",
                    "body5",
                    "raw",
                    2,
                    "preview5",
                    "2026-04-11T00:00:00+00:00",
                    "2026-04-11T00:00:00+00:00",
                ),
                (
                    6,
                    "u6",
                    "Malformed",
                    "body6",
                    "raw",
                    1,
                    "preview6",
                    "2026-04-08T00:00:00+00:00",
                    "2026-04-08T00:00:00+00:00",
                ),
            ],
        )
        conn.executemany(
            "INSERT INTO raw_item_lenses (raw_item_id,lens_id,summary,"
            "bullets_json) VALUES (?,?,?,?)",
            [
                (1, "ai", "ai-summary", '["a","b"]'),
                (1, "rust", "", "[]"),
                (2, "rust", "rust-summary", '["x"]'),
                (3, "ai", "promoted-sum", "[]"),
                (4, "rust", "arch-sum", '["b1","b2","b3"]'),
                (6, "ai", "fall-summary", '{"not":"a list"}'),
            ],
        )


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


class TestPureHelpers:
    def test_parse_bullets_json_string_only(self):
        assert parse_bullets_json('["a","b","c"]') == ("a", "b", "c")

    def test_parse_bullets_json_drops_non_strings(self):
        assert parse_bullets_json('[1,"a",null,"b",{},true]') == ("a", "b")

    def test_parse_bullets_json_malformed_returns_empty(self):
        # R-T1.5 — malformed / non-list / empty / None all yield ()
        for blob in (None, "", "not json", '{"a":1}', "null", "42"):
            assert parse_bullets_json(blob) == ()

    def test_select_representative_prefers_non_empty(self):
        empty = LensMetadata(id="b", summary="", bullets=())
        filled = LensMetadata(id="a", summary="hello", bullets=("x",))
        assert select_representative([empty, filled]).id == "a"

    def test_select_representative_prefers_more_bullets(self):
        a = LensMetadata(id="a", summary="x", bullets=("1",))
        b = LensMetadata(id="b", summary="y", bullets=("1", "2"))
        assert select_representative([a, b]).id == "b"

    def test_select_representative_tiebreak_lens_id(self):
        a = LensMetadata(id="z", summary="x", bullets=("1",))
        b = LensMetadata(id="a", summary="y", bullets=("1",))
        assert select_representative([a, b]).id == "a"

    def test_select_representative_empty_input(self):
        assert select_representative([]) is None

    def test_source_note_relative_path_deterministic(self):
        p1 = source_note_relative_path(
            title="Hello World!",
            url="https://x.com/y",
            collected_at="2026-01-15T12:00:00+00:00",
            sources_dirname="sources",
        )
        p2 = source_note_relative_path(
            title="Hello World!",
            url="https://x.com/y",
            collected_at="2026-01-15T12:00:00+00:00",
            sources_dirname="sources",
        )
        assert p1 == p2  # deterministic
        assert p1.startswith("sources/20260115-hello-world-")
        assert p1.endswith(".md")

    def test_build_candidate_aggregates_lenses(self):
        c = build_candidate(
            item={
                "id": 7,
                "title": "T",
                "url": "https://x",
                "status": "raw",
                "collected_at": "2026-01-01T00:00:00+00:00",
                "body": "bbb",
                "content_summary": "cs",
                "subscription_id": 3,
            },
            lens_rows=[
                {"lens_id": "ai", "summary": "s1", "bullets_json": '["x"]'},
                {"lens_id": "rust", "summary": "", "bullets_json": "[]"},
            ],
            sub_title="Sub",
            sub_url="https://sub",
            sources_dirname="sources",
        )
        assert [lm.id for lm in c.lenses] == ["ai", "rust"]
        assert c.representative.id == "ai"
        assert c.subscription_label == "Sub"
        assert c.source_note_path is None  # raw

    def test_build_candidate_grouped_mode_locks_representative(self):
        c = build_candidate(
            item={
                "id": 1,
                "title": "T",
                "url": "u",
                "status": "raw",
                "collected_at": "",
                "subscription_id": None,
            },
            lens_rows=[
                {"lens_id": "a", "summary": "x", "bullets_json": '["1"]'},
                {"lens_id": "b", "summary": "y", "bullets_json": '["1","2"]'},
            ],
            sub_title=None,
            sub_url="https://s",
            sources_dirname="sources",
            representative_lens_id="a",
        )
        assert c.representative.id == "a"
        # falls back to URL when title is missing (R-U1.8)
        assert c.subscription_label == "https://s"

    def test_build_candidate_promoted_exposes_path(self):
        c = build_candidate(
            item={
                "id": 9,
                "title": "P",
                "url": "https://p",
                "status": "promoted",
                "collected_at": "2026-02-02T01:02:03+00:00",
                "subscription_id": None,
            },
            lens_rows=[{"lens_id": "ai", "summary": "s", "bullets_json": "[]"}],
            sub_title=None,
            sub_url=None,
            sources_dirname="sources",
        )
        assert c.source_note_path is not None
        assert c.source_note_path.startswith("sources/20260202-")


# ---------------------------------------------------------------------------
# query_lens_inbox
# ---------------------------------------------------------------------------


class TestQueryLensInbox:
    def test_default_returns_raw_lens_matched_only(self, vault):
        _seed_inbox_corpus(vault)
        with get_db(vault) as conn:
            view = query_lens_inbox(conn, sources_dirname="sources")
        assert view["scope_status"] == "raw"
        assert view["view_mode"] == "list"
        ids = [c.id for c in view["candidates"]]
        # 1 (newer) > 2 > 6 — collected_at DESC; 5 excluded (no lens)
        assert ids == [1, 2, 6]
        assert view["candidate_count"] == 3
        assert view["is_empty"] is False

    def test_digest_scope_can_include_unmatched_raw_items(self, vault):
        _seed_inbox_corpus(vault)
        with get_db(vault) as conn:
            view = query_lens_inbox(
                conn,
                sources_dirname="sources",
                include_unmatched=True,
            )
        ids = [c.id for c in view["candidates"]]
        # item 5 has no raw_item_lenses row, but digest needs to carry it.
        assert ids == [5, 1, 2, 6]
        item5 = view["candidates"][0]
        assert item5.lenses == ()
        assert item5.representative.id == ""

    def test_default_dedups_multi_lens_into_single_candidate(self, vault):
        _seed_inbox_corpus(vault)
        with get_db(vault) as conn:
            view = query_lens_inbox(conn, sources_dirname="sources")
        first = view["candidates"][0]
        assert first.id == 1
        assert [lm.id for lm in first.lenses] == ["ai", "rust"]
        # Representative is "ai" because non-empty summary wins (R-T1.3)
        assert first.representative.id == "ai"

    def test_promoted_status_exposes_source_note_path(self, vault):
        _seed_inbox_corpus(vault)
        with get_db(vault) as conn:
            view = query_lens_inbox(
                conn, sources_dirname="sources", status="promoted"
            )
        assert [c.id for c in view["candidates"]] == [3]
        assert view["candidates"][0].source_note_path is not None
        assert view["candidates"][0].source_note_path.startswith(
            "sources/20260101-"
        )

    def test_archived_status_filter(self, vault):
        _seed_inbox_corpus(vault)
        with get_db(vault) as conn:
            view = query_lens_inbox(
                conn, sources_dirname="sources", status="archived"
            )
        assert [c.id for c in view["candidates"]] == [4]

    def test_lens_filter(self, vault):
        _seed_inbox_corpus(vault)
        with get_db(vault) as conn:
            v_rust = query_lens_inbox(
                conn, sources_dirname="sources", lens_id="rust"
            )
            v_ai = query_lens_inbox(
                conn, sources_dirname="sources", lens_id="ai"
            )
        assert {c.id for c in v_rust["candidates"]} == {1, 2}
        assert {c.id for c in v_ai["candidates"]} == {1, 6}

    def test_subscription_filter(self, vault):
        _seed_inbox_corpus(vault)
        with get_db(vault) as conn:
            view = query_lens_inbox(
                conn, sources_dirname="sources", subscription_id=2
            )
        # Sub 2 has only item 5 (no lens) and item 3 (promoted) — neither
        # is in the default raw scope.
        assert view["candidates"] == []

    def test_combined_filters(self, vault):
        _seed_inbox_corpus(vault)
        with get_db(vault) as conn:
            view = query_lens_inbox(
                conn,
                sources_dirname="sources",
                lens_id="ai",
                subscription_id=1,
            )
        assert {c.id for c in view["candidates"]} == {1, 6}

    def test_empty_state(self, vault):
        _seed_inbox_corpus(vault)
        with get_db(vault) as conn:
            view = query_lens_inbox(
                conn,
                sources_dirname="sources",
                lens_id="nonexistent",
            )
        assert view["is_empty"] is True
        assert view["candidate_count"] == 0
        assert view["candidates"] == []

    def test_grouped_view_repeats_multi_lens_items(self, vault):
        _seed_inbox_corpus(vault)
        with get_db(vault) as conn:
            view = query_lens_inbox(
                conn, sources_dirname="sources", view_mode="grouped"
            )
        assert view["view_mode"] == "grouped"
        section_ids = {g["lens_id"] for g in view["groups"]}
        assert section_ids == {"ai", "rust"}
        ai_section = next(g for g in view["groups"] if g["lens_id"] == "ai")
        rust_section = next(g for g in view["groups"] if g["lens_id"] == "rust")
        # item 1 appears under both lens sections
        assert {c.id for c in ai_section["candidates"]} == {1, 6}
        assert {c.id for c in rust_section["candidates"]} == {1, 2}
        # representative is locked to the section's lens
        item1_in_ai = next(c for c in ai_section["candidates"] if c.id == 1)
        item1_in_rust = next(c for c in rust_section["candidates"] if c.id == 1)
        assert item1_in_ai.representative.id == "ai"
        assert item1_in_rust.representative.id == "rust"
        # lens name uses the lenses table when present; falls back to id
        assert ai_section["lens_name"] == "AI Research"
        assert rust_section["lens_name"] == "rust"

    def test_malformed_bullets_does_not_crash(self, vault):
        _seed_inbox_corpus(vault)
        with get_db(vault) as conn:
            view = query_lens_inbox(
                conn,
                sources_dirname="sources",
                lens_id="ai",
            )
        item6 = next(c for c in view["candidates"] if c.id == 6)
        # malformed JSON object → empty bullets, but Lens metadata still kept
        assert item6.lenses[0].id == "ai"
        assert item6.lenses[0].bullets == ()


# ---------------------------------------------------------------------------
# /lens-inbox page + /lens-inbox/data
# ---------------------------------------------------------------------------


class TestLensInboxPage:
    def test_sidebar_link_present_globally(self, vault, client):
        r = client.get("/")
        assert r.status_code == 200
        assert '/lens-inbox' in r.text  # R-U1.1

    def test_page_renders_with_default_scope(self, vault, client):
        _seed_inbox_corpus(vault)
        r = client.get("/lens-inbox")
        assert r.status_code == 200
        assert 'id="lens-inbox"' in r.text
        # Default raw scope: items 1, 2, 6 visible; 3, 4, 5 absent
        assert 'data-candidate-id="1"' in r.text
        assert 'data-candidate-id="2"' in r.text
        assert 'data-candidate-id="6"' in r.text
        assert 'data-candidate-id="3"' not in r.text
        assert 'data-candidate-id="4"' not in r.text
        assert 'data-candidate-id="5"' not in r.text
        # Multi-lens badges aggregated on item 1
        assert 'data-lens-ids="ai,rust"' in r.text
        # Filter options
        assert '<option value="ai"' in r.text
        assert '<option value="rust"' in r.text
        # Action buttons for raw
        assert 'data-action="save"' in r.text
        assert 'data-action="archive"' in r.text

    def test_page_shows_empty_state_when_no_candidates(self, vault, client):
        # No lens metadata seeded — empty Lens Inbox.
        r = client.get("/lens-inbox")
        assert r.status_code == 200
        assert "empty-state" in r.text  # R-U2.4

    def test_data_endpoint_returns_json(self, vault, client):
        _seed_inbox_corpus(vault)
        r = client.get("/lens-inbox/data")
        assert r.status_code == 200
        data = r.json()
        assert data["ok"] is True
        assert data["view_mode"] == "list"
        assert data["scope_status"] == "raw"
        assert {c["id"] for c in data["candidates"]} == {1, 2, 6}
        assert "html" in data and 'data-candidate-id="1"' in data["html"]

    def test_data_endpoint_promoted_exposes_path(self, vault, client):
        _seed_inbox_corpus(vault)
        r = client.get("/lens-inbox/data?status=promoted")
        data = r.json()
        assert data["scope_status"] == "promoted"
        assert len(data["candidates"]) == 1
        assert data["candidates"][0]["source_note_path"].startswith(
            "sources/20260101-"
        )

    def test_data_endpoint_grouped(self, vault, client):
        _seed_inbox_corpus(vault)
        r = client.get("/lens-inbox/data?view_mode=grouped")
        data = r.json()
        assert data["view_mode"] == "grouped"
        ids = {g["lens_id"] for g in data["groups"]}
        assert ids == {"ai", "rust"}

    def test_data_endpoint_empty_state(self, vault, client):
        _seed_inbox_corpus(vault)
        r = client.get("/lens-inbox/data?lens_id=nonexistent")
        data = r.json()
        assert data["is_empty"] is True
        assert data["candidates"] == []
        assert "empty-state" in data["html"]

    def test_data_endpoint_coerces_invalid_params(self, vault, client):
        _seed_inbox_corpus(vault)
        r = client.get(
            "/lens-inbox/data?status=garbage&view_mode=foo&subscription_id=abc"
        )
        data = r.json()
        assert data["scope_status"] == "raw"
        assert data["view_mode"] == "list"
        assert data["applied_filters"]["subscription_id"] is None


# ---------------------------------------------------------------------------
# Archive / Restore endpoints
# ---------------------------------------------------------------------------


class TestArchiveRestore:
    def _seed_simple(self, vault):
        now = _now()
        with get_db(vault) as conn:
            conn.execute(
                "INSERT INTO raw_items (id,url,title,status,collected_at,"
                "updated_at) VALUES (1,'u1','T','raw',?,?)",
                (now, now),
            )
            conn.execute(
                "INSERT INTO raw_items (id,url,title,status,collected_at,"
                "updated_at) VALUES (2,'u2','T2','promoted',?,?)",
                (now, now),
            )
            conn.execute(
                "INSERT INTO raw_items (id,url,title,status,collected_at,"
                "updated_at) VALUES (3,'u3','T3','archived',?,?)",
                (now, now),
            )
            conn.execute(
                "INSERT INTO lenses (id,name,created_at,updated_at) "
                "VALUES ('ai','AI',?,?)",
                (now, now),
            )
            conn.execute(
                "INSERT INTO raw_item_lenses (raw_item_id,lens_id,summary,"
                "bullets_json) VALUES (1,'ai','sum','[\"a\"]')"
            )

    def test_archive_raw_succeeds(self, vault, client):
        self._seed_simple(vault)
        r = client.post("/raw-items/1/archive")
        assert r.status_code == 200
        assert r.json() == {"ok": True, "status": "archived"}
        with get_db(vault) as conn:
            row = conn.execute(
                "SELECT status FROM raw_items WHERE id=1"
            ).fetchone()
            assert row["status"] == "archived"

    def test_archive_preserves_lens_metadata(self, vault, client):
        self._seed_simple(vault)
        client.post("/raw-items/1/archive")
        # R-B2.6 — Lens rows untouched
        with get_db(vault) as conn:
            row = conn.execute(
                "SELECT summary, bullets_json FROM raw_item_lenses "
                "WHERE raw_item_id = 1"
            ).fetchone()
            assert row is not None
            assert row["summary"] == "sum"
            assert row["bullets_json"] == '["a"]'

    def test_archive_promoted_refused(self, vault, client):
        self._seed_simple(vault)
        r = client.post("/raw-items/2/archive")
        assert r.status_code == 409
        body = r.json()
        assert body["ok"] is False
        assert body["status"] == "promoted"

    def test_archive_already_archived_is_noop(self, vault, client):
        self._seed_simple(vault)
        r = client.post("/raw-items/3/archive")
        assert r.status_code == 200
        assert r.json()["status"] == "archived"
        assert r.json().get("noop") is True

    def test_archive_not_found(self, vault, client):
        r = client.post("/raw-items/9999/archive")
        assert r.status_code == 404
        assert r.json()["ok"] is False

    def test_restore_archived(self, vault, client):
        self._seed_simple(vault)
        r = client.post("/raw-items/3/restore")
        assert r.status_code == 200
        assert r.json() == {"ok": True, "status": "raw"}
        with get_db(vault) as conn:
            row = conn.execute(
                "SELECT status FROM raw_items WHERE id=3"
            ).fetchone()
            assert row["status"] == "raw"

    def test_restore_raw_is_noop(self, vault, client):
        self._seed_simple(vault)
        r = client.post("/raw-items/1/restore")
        assert r.status_code == 200
        assert r.json()["status"] == "raw"
        assert r.json().get("noop") is True

    def test_restore_promoted_refused(self, vault, client):
        self._seed_simple(vault)
        r = client.post("/raw-items/2/restore")
        assert r.status_code == 409
        assert r.json()["ok"] is False
        assert r.json()["status"] == "promoted"

    def test_restore_not_found(self, vault, client):
        r = client.post("/raw-items/9999/restore")
        assert r.status_code == 404
