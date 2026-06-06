"""Post-fetch Lens matching helpers.

This module records metadata-only Lens matches for freshly inserted
``raw_items``.  It deliberately keeps row loading and classification separate
from the short ``raw_item_lenses`` write transaction so external classifier
calls never hold a SQLite write lock.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass
from typing import Any, Iterable

from contents_hub.config import WikiConfig
from contents_hub.db import init_db

logger = logging.getLogger(__name__)

_LENS_RUN_TIMEOUT = 60.0
_LENS_PROMPT_MAX_CHARS = 7000
_KEYWORD_SUMMARY_MAX_ITEMS = 5


@dataclass(frozen=True)
class LensCriteria:
    """Enabled Lens row selected for a subscription or exploration owner."""

    id: str
    name: str = ""
    description: str = ""
    keywords: tuple[str, ...] = ()


@dataclass(frozen=True)
class RawLensItem:
    """Raw item candidate for Lens matching."""

    id: int
    title: str = ""
    content_summary: str = ""
    body: str = ""


@dataclass(frozen=True)
class LensMatch:
    """A raw item / Lens pair to persist in ``raw_item_lenses``."""

    raw_item_id: int
    lens_id: str
    summary: str = ""
    bullets: tuple[str, ...] = ()
    enriched: dict[str, Any] | None = None


@dataclass(frozen=True)
class LensOwnerContext:
    """Lens selection context for subscription and exploration owners."""

    owner_type: str
    owner_id: int | None = None
    lens_ids: tuple[str, ...] = ()
    subscription_id: int | None = None


def _json_string_list(raw: object) -> list[str]:
    if raw in (None, ""):
        return []
    if isinstance(raw, str):
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            return []
    else:
        value = raw
    if not isinstance(value, list):
        return []

    result: list[str] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, str):
            continue
        item = item.strip()
        if item and item not in seen:
            seen.add(item)
            result.append(item)
    return result


def _int_id_list(raw_ids: Iterable[int | str]) -> list[int]:
    result: list[int] = []
    seen: set[int] = set()
    for raw in raw_ids:
        try:
            item_id = int(raw)
        except (TypeError, ValueError):
            continue
        if item_id <= 0 or item_id in seen:
            continue
        seen.add(item_id)
        result.append(item_id)
    return result


def _string_id_list(raw_ids: Iterable[object]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for raw in raw_ids:
        if not isinstance(raw, str):
            continue
        lens_id = raw.strip()
        if not lens_id or lens_id in seen:
            continue
        seen.add(lens_id)
        result.append(lens_id)
    return result


def load_enabled_lenses(
    conn: sqlite3.Connection,
    lens_ids: Iterable[object],
) -> list[LensCriteria]:
    """Load enabled Lens rows by explicit id list, preserving input order."""
    selected_ids = _string_id_list(lens_ids)
    if not selected_ids:
        return []

    placeholders = ",".join("?" for _ in selected_ids)
    rows = conn.execute(
        f"""SELECT id, name, description, keywords
            FROM lenses
            WHERE enabled = 1 AND id IN ({placeholders})""",
        selected_ids,
    ).fetchall()
    by_id = {str(r["id"]): r for r in rows}

    lenses: list[LensCriteria] = []
    for lens_id in selected_ids:
        lens_row = by_id.get(lens_id)
        if lens_row is None:
            continue
        lenses.append(
            LensCriteria(
                id=lens_id,
                name=str(lens_row["name"] or ""),
                description=str(lens_row["description"] or ""),
                keywords=tuple(_json_string_list(lens_row["keywords"])),
            )
        )
    return lenses


def _lens_from_row(row: sqlite3.Row) -> LensCriteria:
    return LensCriteria(
        id=str(row["id"]),
        name=str(row["name"] or ""),
        description=str(row["description"] or ""),
        keywords=tuple(_json_string_list(row["keywords"])),
    )


def load_all_enabled_lenses(conn: sqlite3.Connection) -> list[LensCriteria]:
    """Load every enabled Lens row, ordered by id for stable iteration."""
    rows = conn.execute(
        """SELECT id, name, description, keywords
           FROM lenses
           WHERE enabled = 1
           ORDER BY id"""
    ).fetchall()
    return [_lens_from_row(row) for row in rows]


def load_all_enabled_routing_lenses(conn: sqlite3.Connection) -> list[LensCriteria]:
    """Load enabled Lenses suitable for automatic subscription routing.

    ``manual-inbox`` is reserved for user-added one-off raw items. Including it
    in automatic fallback routing makes subscription items appear as manual
    saves and leaks noisy semantic matches into downstream notification flows.
    """
    rows = conn.execute(
        """SELECT id, name, description, keywords
           FROM lenses
           WHERE enabled = 1
             AND id != 'manual-inbox'
           ORDER BY id"""
    ).fetchall()
    return [_lens_from_row(row) for row in rows]


def load_enabled_default_lenses(
    conn: sqlite3.Connection,
    subscription_id: int,
) -> list[LensCriteria]:
    """Load enabled Lens rows listed in ``subscriptions.default_lens_ids``.

    Missing, disabled, malformed, and non-default Lens rows are ignored.  The
    returned order follows the subscription's default Lens order.  When the
    subscription has no explicit ``default_lens_ids`` configured, fall back to
    every enabled Lens so the routing layer does not silently swallow items.
    """
    row = conn.execute(
        "SELECT default_lens_ids FROM subscriptions WHERE id = ?",
        (subscription_id,),
    ).fetchone()
    if row is None:
        return []

    lens_ids = _json_string_list(row["default_lens_ids"])
    if not lens_ids:
        return load_all_enabled_routing_lenses(conn)
    return load_enabled_lenses(conn, lens_ids)


def load_exploration_lenses(
    conn: sqlite3.Connection,
    exploration_id: int,
) -> list[LensCriteria]:
    """Load enabled Lens rows listed in ``explorations.lens_ids``.

    Falls back to every enabled automatic-routing Lens when the exploration has
    no explicit ``lens_ids`` configured, mirroring the subscription fallback
    policy and excluding the legacy ``manual-inbox`` Lens.
    """
    row = conn.execute(
        "SELECT lens_ids FROM explorations WHERE id = ?",
        (exploration_id,),
    ).fetchone()
    if row is None:
        return []
    lens_ids = _json_string_list(row["lens_ids"])
    if not lens_ids:
        return load_all_enabled_routing_lenses(conn)
    return load_enabled_lenses(conn, lens_ids)


def load_lenses_for_owner(
    conn: sqlite3.Connection,
    owner: LensOwnerContext,
) -> list[LensCriteria]:
    """Resolve Lens criteria from a generalized owner context."""
    if owner.lens_ids:
        return load_enabled_lenses(conn, owner.lens_ids)
    if owner.owner_type == "subscription" and owner.subscription_id is not None:
        return load_enabled_default_lenses(conn, owner.subscription_id)
    if owner.owner_type == "exploration" and owner.owner_id is not None:
        return load_exploration_lenses(conn, owner.owner_id)
    return []


def load_subscription_raw_items(
    conn: sqlite3.Connection,
    subscription_id: int,
    raw_item_ids: Iterable[int | str],
) -> list[RawLensItem]:
    """Load only supplied raw item ids belonging to ``subscription_id``."""
    return load_raw_items(
        conn,
        raw_item_ids,
        subscription_id=subscription_id,
    )


def load_raw_items(
    conn: sqlite3.Connection,
    raw_item_ids: Iterable[int | str],
    *,
    subscription_id: int | None = None,
) -> list[RawLensItem]:
    """Load supplied raw item ids, optionally constrained to one subscription."""
    item_ids = _int_id_list(raw_item_ids)
    if not item_ids:
        return []

    placeholders = ",".join("?" for _ in item_ids)
    where = [f"id IN ({placeholders})"]
    params: list[object] = [*item_ids]
    if subscription_id is not None:
        where.insert(0, "subscription_id = ?")
        params.insert(0, subscription_id)
    rows = conn.execute(
        f"""SELECT id, title, content_summary, body
            FROM raw_items
            WHERE {" AND ".join(where)}""",
        params,
    ).fetchall()
    by_id = {int(r["id"]): r for r in rows}

    items: list[RawLensItem] = []
    for item_id in item_ids:
        row = by_id.get(item_id)
        if row is None:
            continue
        items.append(
            RawLensItem(
                id=item_id,
                title=str(row["title"] or ""),
                content_summary=str(row["content_summary"] or ""),
                body=str(row["body"] or ""),
            )
        )
    return items


def _keyword_matches(lens: LensCriteria, item: RawLensItem) -> bool:
    haystack = "\n".join((item.title, item.content_summary, item.body)).lower()
    return any(
        keyword.strip().lower() in haystack
        for keyword in lens.keywords
        if keyword.strip()
    )


def _classifier_criteria(lens: LensCriteria) -> str:
    """Render Lens criteria for semantic classification.

    Keywords are hints/examples, not a hard substring gate.  The classifier must
    be allowed to match semantically adjacent phrasing such as ``AI 코드`` for a
    Lens that contains the keyword ``AI 코딩``.
    """
    parts: list[str] = []
    description = lens.description.strip()
    name = lens.name.strip()
    if description:
        parts.append(description)
    elif name:
        parts.append(name)
    keywords = [keyword.strip() for keyword in lens.keywords if keyword.strip()]
    if keywords:
        parts.append(
            "Keywords / examples that indicate the Lens, not exact-match requirements: "
            + ", ".join(keywords)
        )
    return "\n".join(parts)


def _extract_json_object(text: str) -> dict:
    if not text:
        return {}
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        stripped = "\n".join(lines).strip()
    try:
        value = json.loads(stripped)
    except json.JSONDecodeError:
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return {}
        try:
            value = json.loads(stripped[start : end + 1])
        except json.JSONDecodeError:
            return {}
    return value if isinstance(value, dict) else {}


def _clean_summary(raw: object) -> str:
    if not isinstance(raw, str):
        return ""
    return " ".join(raw.strip().split())[:800]


def _clean_bullets(raw: object) -> tuple[str, ...]:
    if not isinstance(raw, list):
        return ()
    bullets: list[str] = []
    for item in raw:
        if not isinstance(item, str):
            continue
        cleaned = " ".join(item.strip().split())
        if cleaned:
            bullets.append(cleaned[:300])
    return tuple(bullets)


def _clean_text(raw: object, *, max_len: int = 1200) -> str:
    if not isinstance(raw, str):
        return ""
    return " ".join(raw.strip().split())[:max_len]


def _clean_text_list(
    raw: object,
    *,
    max_items: int = 20,
    max_len: int = 500,
) -> list[str]:
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    for item in raw:
        cleaned = _clean_text(item, max_len=max_len)
        if cleaned:
            out.append(cleaned)
        if len(out) >= max_items:
            break
    return out


def _clean_quotes(raw: object) -> list[dict[str, str]]:
    if not isinstance(raw, list):
        return []
    out: list[dict[str, str]] = []
    for item in raw:
        if isinstance(item, str):
            text = _clean_text(item, max_len=500)
            if text:
                out.append({"text": text})
        elif isinstance(item, dict):
            text = _clean_text(item.get("text"), max_len=500)
            if not text:
                continue
            speaker = _clean_text(item.get("speaker"), max_len=120)
            payload = {"text": text}
            if speaker:
                payload["speaker"] = speaker
            out.append(payload)
        if len(out) >= 5:
            break
    return out


def _clean_named_objects(raw: object) -> list[dict[str, str]]:
    if not isinstance(raw, list):
        return []
    out: list[dict[str, str]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        name = _clean_text(item.get("name"), max_len=160)
        if not name:
            continue
        payload = {"name": name}
        kind = _clean_text(item.get("type"), max_len=80)
        if kind:
            payload["type"] = kind
        out.append(payload)
        if len(out) >= 8:
            break
    return out


def _normalize_enriched(
    raw: object,
    *,
    fallback_summary: str = "",
    fallback_bullets: tuple[str, ...] = (),
) -> dict[str, Any]:
    """Normalize current and legacy model shapes into one synthesis payload."""
    data = raw if isinstance(raw, dict) else {}
    summary = _clean_summary(
        data.get("oneLiner")
        or data.get("one_liner")
        or data.get("oneLine")
        or data.get("summary")
        or fallback_summary
    )
    key_points = _clean_text_list(
        data.get("keyPoints")
        or data.get("key_points")
        or data.get("bullets")
        or list(fallback_bullets),
        max_items=12,
        max_len=400,
    )
    details = _clean_text_list(
        data.get("details") or data.get("bullets") or key_points,
        max_items=30,
        max_len=700,
    )
    tags = _clean_text_list(data.get("tags"), max_items=10, max_len=80)

    enriched: dict[str, Any] = {
        "shortTitle": _clean_text(
            data.get("shortTitle") or data.get("short_title"),
            max_len=80,
        ),
        "oneLiner": summary,
        "keyPoints": key_points,
        "details": details,
        "tags": tags,
        "quotes": _clean_quotes(data.get("quotes")),
        "entities": _clean_named_objects(data.get("entities")),
        "narrativeHook": _clean_text(
            data.get("narrativeHook") or data.get("narrative_hook"),
            max_len=500,
        ),
        "contentQuality": _clean_text(data.get("contentQuality"), max_len=80),
    }
    return {
        key: value
        for key, value in enriched.items()
        if value not in ("", [], None)
    }


def _fallback_summary(item: RawLensItem) -> tuple[str, tuple[str, ...]]:
    body = " ".join((item.body or item.content_summary or item.title).split())
    summary = body[:260]
    paragraphs = [
        " ".join(p.split())[:220]
        for p in (item.body or item.content_summary or "").splitlines()
        if p.strip()
    ]
    bullets = tuple(p for p in paragraphs if p and p != summary)
    return summary, bullets


def _item_payload(items: Iterable[RawLensItem]) -> list[dict[str, object]]:
    return [
        {
            "id": item.id,
            "title": item.title,
            "summary": item.content_summary,
            "body": item.body[:3000],
        }
        for item in items
    ]


def _summary_prompt(*, criteria: str, payload: list[dict[str, object]]) -> str:
    return (
        "You summarize Lens-matched raw items for a personal knowledge system.\n"
        "Return ONLY a JSON object with this shape:\n"
        '{"items":[{"id":123,"summary":"핵심 요약 1-2문장",'
        '"bullets":["주요 내용","주요 근거","실행/해석 포인트"],'
        '"shortTitle":"짧은 제목","oneLiner":"핵심 한 문장",'
        '"keyPoints":["분류용 핵심 주장"],'
        '"details":["스토리텔링에 쓸 구체적 근거"],'
        '"tags":["topic"],'
        '"quotes":[{"text":"인용문","speaker":"화자"}],'
        '"entities":[{"name":"이름","type":"company|person|product|concept"}],'
        '"narrativeHook":"왜 중요한지"}]}\n'
        "Rules:\n"
        "- Write summary and bullets in the same language as the item.\n"
        "- The summary should capture the main point, not just shorten the title.\n"
        "- Bullets should preserve concrete claims, distinctions, or useful ideas.\n"
        "- details should preserve examples, numbers, tradeoffs, and context for later synthesis.\n"
        "- quotes should include only memorable direct quotes actually present in the item.\n"
        "- Use as many bullets as needed to capture the important points without padding.\n\n"
        f"Lens criteria: {criteria}\n\n"
        f"Items:\n{json.dumps(payload, ensure_ascii=False)}"
    )


def _classify_prompt(*, criteria: str, payload: list[dict[str, object]]) -> str:
    return (
        "You are evaluating whether raw items match a Lens in a personal knowledge system.\n"
        "Return ONLY a JSON object with this shape:\n"
        '{"matches":[{"id":123,"summary":"핵심 요약 1-2문장",'
        '"bullets":["주요 내용","주요 근거","실행/해석 포인트"],'
        '"shortTitle":"짧은 제목","oneLiner":"핵심 한 문장",'
        '"keyPoints":["분류용 핵심 주장"],'
        '"details":["스토리텔링에 쓸 구체적 근거"],'
        '"tags":["topic"],'
        '"quotes":[{"text":"인용문","speaker":"화자"}],'
        '"entities":[{"name":"이름","type":"company|person|product|concept"}],'
        '"narrativeHook":"왜 중요한지"}]}\n'
        "Rules:\n"
        "- Include only items that clearly match the Lens criteria.\n"
        "- Write summary and bullets in the same language as the item.\n"
        "- The summary should capture the main point, not just shorten the title.\n"
        "- Bullets should preserve concrete claims, distinctions, or useful ideas.\n"
        "- details should preserve examples, numbers, tradeoffs, and context for later synthesis.\n"
        "- quotes should include only memorable direct quotes actually present in the item.\n"
        "- Use as many bullets as needed to capture the important points without padding.\n\n"
        f"Lens criteria: {criteria}\n\n"
        f"Items:\n{json.dumps(payload, ensure_ascii=False)}"
    )


def _payload_chunks(
    payload: list[dict[str, object]],
    *,
    render_prompt,
    max_chars: int = _LENS_PROMPT_MAX_CHARS,
) -> list[list[dict[str, object]]]:
    chunks: list[list[dict[str, object]]] = []
    current: list[dict[str, object]] = []
    for entry in payload:
        candidate = [*current, entry]
        if current and len(render_prompt(candidate)) > max_chars:
            chunks.append(current)
            current = [entry]
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


def _fallback_payload_summaries(
    items: Iterable[RawLensItem],
) -> dict[int, tuple[str, tuple[str, ...], dict[str, Any]]]:
    result: dict[int, tuple[str, tuple[str, ...], dict[str, Any]]] = {}
    for item in items:
        summary, bullets = _fallback_summary(item)
        result[item.id] = (
            summary,
            bullets,
            _normalize_enriched(
                {},
                fallback_summary=summary,
                fallback_bullets=bullets,
            ),
        )
    return result


async def _summarize_lens_matches(
    *,
    lens: LensCriteria,
    items: list[RawLensItem],
    matched_ids: set[int],
) -> dict[int, tuple[str, tuple[str, ...], dict[str, Any]]]:
    if not matched_ids:
        return {}

    selected = [item for item in items if item.id in matched_ids]
    if not selected:
        return {}

    criteria = _classifier_criteria(lens) or lens.name or lens.id
    payload = _item_payload(selected)

    from contents_hub.runners import get_default_text_runner

    raw_items: list[object] = []
    chunks = _payload_chunks(
        payload,
        render_prompt=lambda chunk: _summary_prompt(criteria=criteria, payload=chunk),
    )
    for chunk in chunks:
        prompt = _summary_prompt(criteria=criteria, payload=chunk)
        text = await get_default_text_runner().run(
            prompt,
            max_turns=1,
            timeout=_LENS_RUN_TIMEOUT,
        )
        parsed = _extract_json_object(text or "")
        chunk_items = parsed.get("items", [])
        if isinstance(chunk_items, list):
            raw_items.extend(chunk_items)

    result: dict[int, tuple[str, tuple[str, ...], dict[str, Any]]] = {}
    for raw in raw_items:
        if not isinstance(raw, dict):
            continue
        try:
            item_id = int(raw.get("id"))
        except (TypeError, ValueError):
            continue
        if item_id not in matched_ids:
            continue
        summary = _clean_summary(raw.get("summary") or raw.get("oneLiner"))
        bullets = _clean_bullets(
            raw.get("bullets") or raw.get("keyPoints") or raw.get("details")
        )
        if summary or bullets:
            result[item_id] = (
                summary,
                bullets,
                _normalize_enriched(
                    raw,
                    fallback_summary=summary,
                    fallback_bullets=bullets,
                ),
            )
    return result


async def _classify_and_summarize_lens(
    *,
    lens: LensCriteria,
    items: list[RawLensItem],
) -> dict[int, tuple[str, tuple[str, ...], dict[str, Any]]]:
    criteria = _classifier_criteria(lens)
    if not criteria:
        return {}

    payload = _item_payload(items)

    from contents_hub.runners import get_default_text_runner

    raw_matches: list[object] = []
    chunks = _payload_chunks(
        payload,
        render_prompt=lambda chunk: _classify_prompt(criteria=criteria, payload=chunk),
    )
    fallback_summaries: dict[int, tuple[str, tuple[str, ...], dict[str, Any]]] = {}
    items_by_id = {item.id: item for item in items}
    for chunk in chunks:
        prompt = _classify_prompt(criteria=criteria, payload=chunk)
        try:
            text = await get_default_text_runner().run(
                prompt,
                max_turns=1,
                timeout=_LENS_RUN_TIMEOUT,
            )
        except TimeoutError:
            chunk_ids: set[int] = set()
            for entry in chunk:
                raw_id = entry.get("id")
                if isinstance(raw_id, int):
                    chunk_ids.add(raw_id)
            keyword_items = [
                item
                for item_id, item in items_by_id.items()
                if item_id in chunk_ids and _keyword_matches(lens, item)
            ]
            if keyword_items:
                fallback_summaries.update(_fallback_payload_summaries(keyword_items))
            logger.warning(
                "Lens classifier timed out for lens %s; keyword fallback matched %s/%s items",
                lens.id,
                len(keyword_items),
                len(chunk),
            )
            continue
        parsed = _extract_json_object(text or "")
        chunk_matches = parsed.get("matches") or parsed.get("items", [])
        if isinstance(chunk_matches, list):
            raw_matches.extend(chunk_matches)

    valid_ids = {item.id for item in items}
    result: dict[int, tuple[str, tuple[str, ...], dict[str, Any]]] = dict(
        fallback_summaries
    )
    for raw in raw_matches:
        if not isinstance(raw, dict):
            continue
        try:
            item_id = int(raw.get("id"))
        except (TypeError, ValueError):
            continue
        if item_id not in valid_ids:
            continue
        summary = _clean_summary(raw.get("summary") or raw.get("oneLiner"))
        bullets = _clean_bullets(
            raw.get("bullets") or raw.get("keyPoints") or raw.get("details")
        )
        if summary or bullets:
            result[item_id] = (
                summary,
                bullets,
                _normalize_enriched(
                    raw,
                    fallback_summary=summary,
                    fallback_bullets=bullets,
                ),
            )
        else:
            fallback_summary, fallback_bullets = _fallback_summary(
                next(item for item in items if item.id == item_id)
            )
            result[item_id] = (
                fallback_summary,
                fallback_bullets,
                _normalize_enriched(
                    {},
                    fallback_summary=fallback_summary,
                    fallback_bullets=fallback_bullets,
                ),
            )
    return result


async def classify_items_for_lenses(
    lenses: Iterable[LensCriteria],
    items: Iterable[RawLensItem],
) -> list[LensMatch]:
    """Classify Lens matches without writing to the database."""
    lens_list = list(lenses)
    item_list = list(items)
    if not lens_list or not item_list:
        return []

    matches: list[LensMatch] = []
    matched_pairs: set[tuple[int, str]] = set()
    valid_item_ids = {item.id for item in item_list}

    for lens in lens_list:
        summaries = await _classify_and_summarize_lens(
            lens=lens,
            items=item_list,
        )
        matched_ids = set(summaries)

        for item_id in matched_ids:
            try:
                matched_item_id = int(item_id)
            except (TypeError, ValueError):
                continue
            if matched_item_id not in valid_item_ids:
                continue
            pair = (matched_item_id, lens.id)
            if pair in matched_pairs:
                continue
            matched_pairs.add(pair)
            summary, bullets, enriched = summaries.get(matched_item_id, ("", (), {}))
            if not summary and not bullets:
                item = next(i for i in item_list if i.id == matched_item_id)
                summary, bullets = _fallback_summary(item)
                enriched = _normalize_enriched(
                    {},
                    fallback_summary=summary,
                    fallback_bullets=bullets,
                )
            matches.append(
                LensMatch(
                    raw_item_id=pair[0],
                    lens_id=pair[1],
                    summary=summary,
                    bullets=bullets,
                    enriched=enriched,
                )
            )

    return matches


def insert_lens_matches(
    conn: sqlite3.Connection,
    matches: Iterable[LensMatch],
) -> int:
    """Persist Lens matches in one short ``INSERT OR IGNORE`` transaction."""
    match_list = list(matches)
    if not match_list:
        return 0

    inserted = 0
    try:
        with conn:
            for match in match_list:
                cursor = conn.execute(
                    "INSERT OR IGNORE INTO raw_item_lenses "
                    "(raw_item_id, lens_id, summary, bullets_json, enriched_json) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (
                        match.raw_item_id,
                        match.lens_id,
                        match.summary,
                        json.dumps(list(match.bullets), ensure_ascii=False),
                        json.dumps(match.enriched or {}, ensure_ascii=False),
                    ),
                )
                if cursor.rowcount and cursor.rowcount > 0:
                    inserted += 1
                elif match.summary or match.bullets or match.enriched:
                    conn.execute(
                        """UPDATE raw_item_lenses
                           SET summary = CASE
                                   WHEN summary = '' THEN ?
                                   ELSE summary
                               END,
                               bullets_json = CASE
                                   WHEN bullets_json = '[]' THEN ?
                                   ELSE bullets_json
                               END,
                               enriched_json = CASE
                                   WHEN enriched_json = '{}' THEN ?
                                   ELSE enriched_json
                               END
                           WHERE raw_item_id = ? AND lens_id = ?""",
                        (
                            match.summary,
                            json.dumps(list(match.bullets), ensure_ascii=False),
                            json.dumps(match.enriched or {}, ensure_ascii=False),
                            match.raw_item_id,
                            match.lens_id,
                        ),
                    )
    except Exception:
        conn.rollback()
        raise
    return inserted


def _preview_item_text(raw: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = raw.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def preview_items_for_lens_matching(
    preview_items: Iterable[dict[str, Any]],
) -> tuple[list[RawLensItem], dict[int, dict[str, Any]]]:
    """Convert validation preview artifacts into in-memory Lens candidates."""
    items: list[RawLensItem] = []
    metadata: dict[int, dict[str, Any]] = {}
    for index, raw in enumerate(preview_items):
        if not isinstance(raw, dict):
            continue
        item_id = index + 1
        metadata[item_id] = raw
        items.append(
            RawLensItem(
                id=item_id,
                title=_preview_item_text(raw, "title", "title_hint"),
                content_summary=_preview_item_text(
                    raw,
                    "content_summary",
                    "summary",
                    "snippet",
                ),
                body=_preview_item_text(
                    raw,
                    "body",
                    "body_markdown",
                    "content_html",
                    "text",
                ),
            )
        )
    return items, metadata


def lens_matches_to_artifact_payload(
    matches: Iterable[LensMatch],
    preview_metadata: dict[int, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Serialize preview Lens matches without committing Lens table rows."""
    payload: list[dict[str, Any]] = []
    for match in matches:
        meta = preview_metadata.get(match.raw_item_id, {})
        payload.append(
            {
                "candidate_index": match.raw_item_id - 1,
                "url": str(meta.get("url") or ""),
                "title": _preview_item_text(meta, "title", "title_hint"),
                "lens_id": match.lens_id,
                "summary": match.summary,
                "bullets": list(match.bullets),
            }
        )
    return payload


async def evaluate_exploration_preview_lenses(
    config: WikiConfig,
    exploration_id: int,
    preview_items: Iterable[dict[str, Any]],
    *,
    conn: sqlite3.Connection | None = None,
) -> list[dict[str, Any]]:
    """Return validation-only Lens match summaries for exploration previews.

    This intentionally does not write ``raw_items`` or ``raw_item_lenses``.
    Callers store the returned payload on the validation artifact.
    """
    owns_conn = conn is None
    c = conn if conn is not None else init_db(config)
    try:
        owner = LensOwnerContext(owner_type="exploration", owner_id=exploration_id)
        lenses = load_lenses_for_owner(c, owner)
        items, metadata = preview_items_for_lens_matching(preview_items)
        matches = await classify_items_for_lenses(lenses, items)
        return lens_matches_to_artifact_payload(matches, metadata)
    except Exception as exc:  # noqa: BLE001 - validation Lens preview is optional
        logger.warning(
            "exploration preview Lens evaluation failed for exploration %s: %s",
            exploration_id,
            exc,
        )
        return []
    finally:
        if owns_conn:
            c.close()


async def evaluate_exploration_run_lenses(
    config: WikiConfig,
    exploration_id: int,
    raw_item_ids: Iterable[int | str],
    *,
    conn: sqlite3.Connection | None = None,
) -> int:
    """Persist Lens matches for registered exploration-run raw items."""
    owns_conn = conn is None
    c = conn if conn is not None else init_db(config)
    try:
        owner = LensOwnerContext(owner_type="exploration", owner_id=exploration_id)
        lenses = load_lenses_for_owner(c, owner)
        items = load_raw_items(c, raw_item_ids)
        matches = await classify_items_for_lenses(lenses, items)
        return insert_lens_matches(c, matches)
    except Exception as exc:  # noqa: BLE001 - run Lens failures are isolated
        try:
            c.rollback()
        except Exception:  # noqa: BLE001
            pass
        logger.warning(
            "exploration run Lens evaluation failed for exploration %s: %s",
            exploration_id,
            exc,
        )
        return 0
    finally:
        if owns_conn:
            c.close()


async def evaluate_post_fetch_lenses(
    config: WikiConfig,
    subscription_id: int,
    raw_item_ids: Iterable[int | str],
    *,
    conn: sqlite3.Connection | None = None,
) -> int:
    """Record metadata-only Lens matches for freshly inserted raw items.

    Failures are isolated from ingestion callers: any pending Lens transaction
    is rolled back, the error is logged, and ``0`` is returned.
    """
    owns_conn = conn is None
    c = conn if conn is not None else init_db(config)
    try:
        lenses = load_enabled_default_lenses(c, subscription_id)
        items = load_subscription_raw_items(c, subscription_id, raw_item_ids)
        matches = await classify_items_for_lenses(lenses, items)
        return insert_lens_matches(c, matches)
    except Exception as exc:  # noqa: BLE001 - post-fetch Lens failures are isolated
        try:
            c.rollback()
        except Exception:  # noqa: BLE001
            pass
        logger.warning(
            "post-fetch Lens evaluation failed for subscription %s: %s",
            subscription_id,
            exc,
        )
        return 0
    finally:
        if owns_conn:
            c.close()


__all__ = [
    "LensCriteria",
    "LensMatch",
    "LensOwnerContext",
    "RawLensItem",
    "classify_items_for_lenses",
    "evaluate_exploration_preview_lenses",
    "evaluate_exploration_run_lenses",
    "evaluate_post_fetch_lenses",
    "insert_lens_matches",
    "lens_matches_to_artifact_payload",
    "load_all_enabled_lenses",
    "load_all_enabled_routing_lenses",
    "load_enabled_lenses",
    "load_enabled_default_lenses",
    "load_exploration_lenses",
    "load_lenses_for_owner",
    "load_raw_items",
    "load_subscription_raw_items",
    "preview_items_for_lens_matching",
]
