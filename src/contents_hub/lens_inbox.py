"""Lens Inbox candidate contract.

Pure data + helpers for the Lens Inbox review surface. No I/O lives here:
retrieval is in :func:`lens_inbox_candidates` (T2) and rendering is in the
``/lens-inbox`` route (T4). Everything in this module is side-effect free and
unit-testable.

Contract summary:

- one :class:`LensInboxCandidate` per raw item (default mode) or per matching
  Lens (Lens-grouped mode, via ``representative_lens_id``);
- multi-Lens metadata is preserved on every candidate (R-B1.2 / R-T1.2);
- :func:`select_representative` picks one Lens metadata block deterministically
  for collapsed display without making a new LLM call (R-T1.3, R-B3.2);
- :func:`parse_bullets_json` treats malformed or non-list JSON as empty
  bullets (R-T1.5);
- :func:`source_note_relative_path` reuses :func:`promote.source_filename` so
  the relative path text is available without persisting it (R-T2.5, R-B2.3).
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Iterable, Mapping

from contents_hub.promote import source_filename

DEFAULT_CANDIDATE_LIMIT = 100
DEFAULT_STATUS = "raw"
ALLOWED_STATUSES: tuple[str, ...] = ("raw", "promoted", "archived")
VIEW_MODE_LIST = "list"
VIEW_MODE_GROUPED = "grouped"
ALLOWED_VIEW_MODES: tuple[str, ...] = (VIEW_MODE_LIST, VIEW_MODE_GROUPED)


@dataclass(frozen=True)
class LensMetadata:
    """One ``raw_item_lenses`` membership for a candidate.

    Bullets are stored as a tuple so the dataclass is hashable and stable for
    template rendering.
    """

    id: str
    summary: str = ""
    bullets: tuple[str, ...] = ()
    enriched_json: str = "{}"


@dataclass(frozen=True)
class LensInboxCandidate:
    """View model for one row in the Lens Inbox.

    Default list mode emits one candidate per ``raw_items.id``; Lens-grouped
    mode may emit one candidate per (raw_item, lens) pair, with
    ``representative`` pointing at the section's lens.
    """

    id: int
    title: str
    url: str
    status: str
    collected_at: str
    body_preview: str
    subscription_id: int | None
    subscription_label: str
    lenses: tuple[LensMetadata, ...]
    representative: LensMetadata
    source_note_path: str | None


def parse_bullets_json(blob: str | None) -> tuple[str, ...]:
    """Parse ``raw_item_lenses.bullets_json`` defensively.

    Only string bullets are kept. Malformed or non-list JSON yields an empty
    tuple instead of raising (R-T1.5).
    """
    if not blob:
        return ()
    try:
        data = json.loads(blob)
    except (json.JSONDecodeError, TypeError, ValueError):
        return ()
    if not isinstance(data, list):
        return ()
    return tuple(b for b in data if isinstance(b, str))


def lens_metadata_from_rows(
    rows: Iterable[Mapping[str, object]],
) -> tuple[LensMetadata, ...]:
    """Build a deterministic tuple of :class:`LensMetadata` from DB rows.

    Each row must expose ``lens_id``, ``summary``, ``bullets_json``; optional
    ``enriched_json`` is carried through for synthesis. Rows missing
    ``lens_id`` are dropped. Output is sorted by lens id ascending so template
    rendering and tests are stable.
    """
    out: list[LensMetadata] = []
    for r in rows:
        lid = (r.get("lens_id") or "") if isinstance(r, Mapping) else ""
        if not lid:
            continue
        summary = r.get("summary") or ""
        bullets_json = r.get("bullets_json")
        enriched_json = r.get("enriched_json") or "{}"
        out.append(
            LensMetadata(
                id=str(lid),
                summary=str(summary) if not isinstance(summary, str) else summary,
                bullets=parse_bullets_json(
                    bullets_json if isinstance(bullets_json, str) else None
                ),
                enriched_json=(
                    enriched_json if isinstance(enriched_json, str) else "{}"
                ),
            )
        )
    out.sort(key=lambda lm: lm.id)
    return tuple(out)


def select_representative(
    lenses: Iterable[LensMetadata],
) -> LensMetadata | None:
    """Pick the richest Lens metadata for collapsed display.

    Order (deterministic — R-T1.3):
        1. non-empty summaries beat empty summaries,
        2. more string bullets beats fewer,
        3. lens id ascending breaks remaining ties.

    Never makes a new LLM call: the returned block is one of the inputs
    verbatim (R-T1.3, R-B3.2).
    """
    items = list(lenses)
    if not items:
        return None
    items.sort(
        key=lambda lm: (
            0 if lm.summary.strip() else 1,
            -len(lm.bullets),
            lm.id,
        )
    )
    return items[0]


def short_body_preview(
    body: str | None,
    content_summary: str | None,
    *,
    max_len: int = 200,
) -> str:
    """Collapse whitespace and truncate to a one- to two-line preview (R-B3.4)."""
    src = (content_summary or "").strip() or (body or "").strip()
    if not src:
        return ""
    collapsed = " ".join(src.split())
    if len(collapsed) > max_len:
        return collapsed[:max_len].rstrip() + "…"
    return collapsed


def subscription_label(sub_title: str | None, sub_url: str | None) -> str:
    """Title if present, otherwise URL (R-U1.8)."""
    title = (sub_title or "").strip()
    if title:
        return title
    return (sub_url or "").strip()


def source_note_relative_path(
    *,
    title: str,
    url: str,
    collected_at: str,
    sources_dirname: str,
) -> str:
    """Deterministic relative path text for a promoted source note.

    Reuses :func:`promote.source_filename` so the path matches the actual file
    written by :func:`promote.promote_raw_item` (R-T2.5, R-B2.3). No DB
    migration is required.
    """
    fname = source_filename(title or url, url, collected_at)
    sdir = (sources_dirname or "sources").strip("/")
    return f"{sdir}/{fname}" if sdir else fname


def build_candidate(
    item: Mapping[str, object],
    lens_rows: Iterable[Mapping[str, object]],
    *,
    sub_title: str | None,
    sub_url: str | None,
    sources_dirname: str,
    representative_lens_id: str | None = None,
) -> LensInboxCandidate:
    """Assemble one :class:`LensInboxCandidate`.

    Caller must guarantee ``item`` has at least one row in ``lens_rows`` per
    R-B1.1; an empty iterable yields a candidate with an empty representative
    rather than raising, so a row deleted between query stages does not crash
    the page.

    When ``representative_lens_id`` is set (Lens-grouped mode), it forces the
    representative onto that Lens; otherwise the deterministic
    :func:`select_representative` rule applies.
    """
    lenses = lens_metadata_from_rows(lens_rows)
    if not lenses:
        rep = LensMetadata(id="", summary="", bullets=())
    elif representative_lens_id:
        match = next(
            (lm for lm in lenses if lm.id == representative_lens_id),
            None,
        )
        rep = match or select_representative(lenses) or lenses[0]
    else:
        rep = select_representative(lenses) or lenses[0]

    status = str(item.get("status") or "raw")
    snote: str | None = None
    if status == "promoted":
        snote = source_note_relative_path(
            title=str(item.get("title") or ""),
            url=str(item.get("url") or ""),
            collected_at=str(item.get("collected_at") or ""),
            sources_dirname=sources_dirname,
        )

    raw_id = item.get("id")
    sub_id = item.get("subscription_id")

    return LensInboxCandidate(
        id=int(raw_id) if raw_id is not None else 0,
        title=str(item.get("title") or "").strip(),
        url=str(item.get("url") or "").strip(),
        status=status,
        collected_at=str(item.get("collected_at") or ""),
        body_preview=short_body_preview(
            str(item.get("body")) if item.get("body") is not None else None,
            str(item.get("content_summary"))
            if item.get("content_summary") is not None
            else None,
        ),
        subscription_id=int(sub_id) if sub_id is not None else None,
        subscription_label=subscription_label(sub_title, sub_url),
        lenses=lenses,
        representative=rep,
        source_note_path=snote,
    )


# ---------------------------------------------------------------------------
# Retrieval — joins raw_items, raw_item_lenses, subscriptions, lenses.
# Filter and view-mode arguments are validated here so the route stays thin.
# ---------------------------------------------------------------------------


def _coerce_status(value: str | None) -> str:
    v = (value or "").strip()
    return v if v in ALLOWED_STATUSES else DEFAULT_STATUS


def _coerce_view_mode(value: str | None) -> str:
    v = (value or "").strip()
    return v if v in ALLOWED_VIEW_MODES else VIEW_MODE_LIST


def _coerce_limit(value: int | str | None) -> int:
    """Coerce a candidate-limit value.

    - ``None`` → :data:`DEFAULT_CANDIDATE_LIMIT` (100) — the Lens-Inbox web
      surface intentionally caps at 100 newest candidates per filter scope.
    - An explicit int (or numeric string) is honored as-is (clamped to ≥ 1)
      so callers needing unbounded retrieval — notably
      :mod:`contents_hub.digest` per R-B4.1 "no upper-count truncation" — can
      pass a very large value (e.g. ``10**9``) and bypass the default cap
      without affecting Lens Inbox web routes (which never pass ``limit``
      and therefore continue to receive the default 100 cap).
    """
    if value is None:
        return DEFAULT_CANDIDATE_LIMIT
    try:
        n = int(value)
    except (TypeError, ValueError):
        return DEFAULT_CANDIDATE_LIMIT
    return max(1, n)


def list_lens_filter_options(conn: sqlite3.Connection) -> list[dict]:
    """Lens ids that have at least one ``raw_item_lenses`` row, with names.

    Used to populate the Lens filter dropdown (R-U2.1).
    """
    rows = conn.execute(
        """
        SELECT l.id AS id,
               COALESCE(NULLIF(TRIM(l.name), ''), l.id) AS name
        FROM lenses l
        WHERE l.id IN (SELECT DISTINCT lens_id FROM raw_item_lenses)
        UNION
        SELECT ril.lens_id AS id, ril.lens_id AS name
        FROM raw_item_lenses ril
        WHERE ril.lens_id NOT IN (SELECT id FROM lenses)
        ORDER BY id
        """
    ).fetchall()
    return [{"id": r["id"], "name": r["name"]} for r in rows]


def list_subscription_filter_options(conn: sqlite3.Connection) -> list[dict]:
    """Subscriptions that have at least one Lens-matched raw item (R-U2.2)."""
    rows = conn.execute(
        """
        SELECT s.id AS id, s.title AS title, s.url AS url
        FROM subscriptions s
        WHERE s.id IN (
            SELECT DISTINCT ri.subscription_id
            FROM raw_items ri
            JOIN raw_item_lenses ril ON ril.raw_item_id = ri.id
            WHERE ri.subscription_id IS NOT NULL
        )
        ORDER BY COALESCE(NULLIF(TRIM(s.title), ''), s.url)
        """
    ).fetchall()
    out: list[dict] = []
    for r in rows:
        title = (r["title"] or "").strip()
        out.append(
            {
                "id": int(r["id"]),
                "label": title or (r["url"] or ""),
            }
        )
    return out


def query_lens_inbox(
    conn: sqlite3.Connection,
    *,
    sources_dirname: str,
    status: str | None = DEFAULT_STATUS,
    lens_id: str | None = None,
    subscription_id: int | str | None = None,
    view_mode: str | None = VIEW_MODE_LIST,
    limit: int | None = DEFAULT_CANDIDATE_LIMIT,
    digest_id_null: bool = False,
    include_unmatched: bool = False,
) -> dict:
    """Return Lens Inbox view data for one filter/view-mode combination.

    Output shape::

        {
            "view_mode":         "list" | "grouped",
            "scope_status":      "raw" | "promoted" | "archived",
            "candidates":        [LensInboxCandidate, ...],   # list mode
            "groups": [
                {"lens_id": str, "lens_name": str,
                 "candidates": [LensInboxCandidate, ...]},
                ...
            ],                                                # grouped mode
            "candidate_count":   int,
            "is_empty":          bool,
            "applied_filters":   {"status": str,
                                  "lens_id": str | None,
                                  "subscription_id": int | None},
        }

    Joins ``raw_items``, ``raw_item_lenses``, ``subscriptions``; orders by
    ``raw_items.collected_at DESC`` and caps at 100 newest candidates per the
    selected status / filter / view scope (R-T1.1, R-T1.6).

    When ``digest_id_null=True`` (digest-pipeline R-T9.1), the WHERE clause is
    additionally constrained with ``ri.digest_id IS NULL`` so digest.build_digest
    only sees unclaimed candidates (R-B3.3). Default ``False`` preserves the
    existing Lens Inbox behavior with no SQL change.

    By default this remains a lens-matched review surface. Specialized callers
    may pass ``include_unmatched=True`` with no lens filter to also carry raw
    items that have not matched any Lens.
    """
    status = _coerce_status(status)
    view_mode = _coerce_view_mode(view_mode)
    limit = _coerce_limit(limit)

    sub_id_int: int | None
    if subscription_id is None or subscription_id == "":
        sub_id_int = None
    else:
        try:
            sub_id_int = int(subscription_id)
        except (TypeError, ValueError):
            sub_id_int = None

    where = ["ri.status = ?"]
    params: list[object] = [status]
    if not include_unmatched or (lens_id or "").strip():
        where.append(
            "EXISTS (SELECT 1 FROM raw_item_lenses ril_e "
            "WHERE ril_e.raw_item_id = ri.id)"
        )
    if (lens_id or "").strip():
        where.append(
            "EXISTS (SELECT 1 FROM raw_item_lenses ril_f "
            "WHERE ril_f.raw_item_id = ri.id AND ril_f.lens_id = ?)"
        )
        params.append(lens_id.strip())
    if sub_id_int is not None:
        where.append("ri.subscription_id = ?")
        params.append(sub_id_int)
    if digest_id_null:
        where.append("ri.digest_id IS NULL")

    sql = f"""
        SELECT ri.id, ri.title, ri.url, ri.status, ri.collected_at,
               ri.body, ri.content_summary, ri.subscription_id,
               s.title AS sub_title, s.url AS sub_url
        FROM raw_items ri
        LEFT JOIN subscriptions s ON s.id = ri.subscription_id
        WHERE {" AND ".join(where)}
        ORDER BY ri.collected_at DESC
        LIMIT ?
    """
    params.append(limit)
    item_rows = [dict(r) for r in conn.execute(sql, params).fetchall()]

    applied_filters = {
        "status": status,
        "lens_id": (lens_id or None),
        "subscription_id": sub_id_int,
    }

    if not item_rows:
        return {
            "view_mode": view_mode,
            "scope_status": status,
            "candidates": [],
            "groups": [],
            "candidate_count": 0,
            "is_empty": True,
            "applied_filters": applied_filters,
        }

    item_ids = [r["id"] for r in item_rows]
    placeholders = ",".join("?" * len(item_ids))
    lens_rows = conn.execute(
        f"""SELECT raw_item_id, lens_id, summary, bullets_json, enriched_json
            FROM raw_item_lenses
            WHERE raw_item_id IN ({placeholders})""",
        item_ids,
    ).fetchall()
    lens_map: dict[int, list[dict]] = {}
    for lr in lens_rows:
        lens_map.setdefault(lr["raw_item_id"], []).append(dict(lr))

    lens_name_map: dict[str, str] = {}
    if lens_map:
        lens_id_set = {lr["lens_id"] for rows in lens_map.values() for lr in rows}
        if lens_id_set:
            ph = ",".join("?" * len(lens_id_set))
            for lr in conn.execute(
                f"SELECT id, name FROM lenses WHERE id IN ({ph})",
                list(lens_id_set),
            ).fetchall():
                name = (lr["name"] or "").strip()
                lens_name_map[lr["id"]] = name or lr["id"]

    if view_mode == VIEW_MODE_GROUPED:
        sections: dict[str, list[LensInboxCandidate]] = {}
        for it in item_rows:
            this_lenses = lens_map.get(it["id"], [])
            for lr in sorted(this_lenses, key=lambda r: r["lens_id"]):
                cand = build_candidate(
                    it,
                    this_lenses,
                    sub_title=it.get("sub_title"),
                    sub_url=it.get("sub_url"),
                    sources_dirname=sources_dirname,
                    representative_lens_id=lr["lens_id"],
                )
                sections.setdefault(lr["lens_id"], []).append(cand)
        groups = [
            {
                "lens_id": lid,
                "lens_name": lens_name_map.get(lid, lid),
                "candidates": sections[lid],
            }
            for lid in sorted(sections.keys())
        ]
        total_placements = sum(len(g["candidates"]) for g in groups)
        return {
            "view_mode": view_mode,
            "scope_status": status,
            "candidates": [],
            "groups": groups,
            "candidate_count": total_placements,
            "is_empty": total_placements == 0,
            "applied_filters": applied_filters,
        }

    candidates = [
        build_candidate(
            it,
            lens_map.get(it["id"], []),
            sub_title=it.get("sub_title"),
            sub_url=it.get("sub_url"),
            sources_dirname=sources_dirname,
        )
        for it in item_rows
    ]
    return {
        "view_mode": view_mode,
        "scope_status": status,
        "candidates": candidates,
        "groups": [],
        "candidate_count": len(candidates),
        "is_empty": len(candidates) == 0,
        "applied_filters": applied_filters,
    }
