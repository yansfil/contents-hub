from __future__ import annotations

import json

from contents_hub.cli import main
from contents_hub.config import WikiConfig
from contents_hub.db import get_db, init_db


def _read_json(capsys):
    out = capsys.readouterr().out.strip()
    return json.loads(out)


def test_lens_cli_create_list_update_delete(tmp_path, capsys):
    cfg = WikiConfig(vault_path=tmp_path)
    init_db(cfg).close()

    create_rc = main(
        [
            "--vault",
            str(tmp_path),
            "lens",
            "create",
            "vibe-coding",
            "--name",
            "Vibe coding",
            "--description",
            "Concrete vibe coding workflows and lessons",
            "--keyword",
            "바이브코딩",
            "--keyword",
            "Claude Code",
        ]
    )
    created = _read_json(capsys)
    assert create_rc == 0
    assert created["ok"] is True
    assert created["lens"]["id"] == "vibe-coding"
    assert created["lens"]["name"] == "Vibe coding"
    assert created["lens"]["keywords"] == ["바이브코딩", "Claude Code"]
    assert created["lens"]["enabled"] is True

    list_rc = main(["--vault", str(tmp_path), "lens", "list", "--format", "json"])
    listed = _read_json(capsys)
    assert list_rc == 0
    assert [lens["id"] for lens in listed] == ["vibe-coding"]

    update_rc = main(
        [
            "--vault",
            str(tmp_path),
            "lens",
            "update",
            "vibe-coding",
            "--name",
            "Vibe Coding Tips",
            "--keyword",
            "바이브 코딩",
            "--disable",
        ]
    )
    updated = _read_json(capsys)
    assert update_rc == 0
    assert updated["ok"] is True
    assert updated["lens"]["name"] == "Vibe Coding Tips"
    assert updated["lens"]["keywords"] == ["바이브 코딩"]
    assert updated["lens"]["enabled"] is False

    with get_db(cfg) as conn:
        row = conn.execute(
            "SELECT name, description, keywords, enabled FROM lenses WHERE id = ?",
            ("vibe-coding",),
        ).fetchone()
        assert row["name"] == "Vibe Coding Tips"
        assert row["description"] == "Concrete vibe coding workflows and lessons"
        assert json.loads(row["keywords"]) == ["바이브 코딩"]
        assert row["enabled"] == 0

    delete_rc = main(["--vault", str(tmp_path), "lens", "delete", "vibe-coding"])
    deleted = _read_json(capsys)
    assert delete_rc == 0
    assert deleted == {"ok": True, "lens_id": "vibe-coding", "deleted": True}

    with get_db(cfg) as conn:
        assert conn.execute("SELECT COUNT(*) FROM lenses").fetchone()[0] == 0


def test_lens_cli_create_rejects_invalid_id(tmp_path, capsys):
    cfg = WikiConfig(vault_path=tmp_path)
    init_db(cfg).close()

    rc = main(["--vault", str(tmp_path), "lens", "create", "bad id"])
    payload = _read_json(capsys)

    assert rc == 1
    assert payload["ok"] is False
    assert "lens_id must" in payload["error"]
