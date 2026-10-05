from __future__ import annotations

import json
import sqlite3
from typing import Any, Iterable


def _dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


class LocalizationRepository:
    """封装本地化档案领域的 SQLite 读写。"""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    # -- 档案 ---------------------------------------------------------------

    def dossier_by_code(self, code: str) -> dict[str, Any] | None:
        return _dict(self.connection.execute(
            "SELECT d.*,p.code AS product_code,p.name AS product_name,p.regulatory_status AS product_status,p.active AS product_active,"
            "s.code AS site_code,s.name AS site_name,s.status AS site_status "
            "FROM localization_dossiers d JOIN health_products p ON p.id=d.product_id "
            "JOIN pilot_sites s ON s.id=d.site_id WHERE d.code=?",
            (code,),
        ).fetchone())

    def dossier_by_id(self, dossier_id: int) -> dict[str, Any] | None:
        return _dict(self.connection.execute(
            "SELECT d.*,p.code AS product_code,p.name AS product_name,p.regulatory_status AS product_status,p.active AS product_active,"
            "s.code AS site_code,s.name AS site_name,s.status AS site_status "
            "FROM localization_dossiers d JOIN health_products p ON p.id=d.product_id "
            "JOIN pilot_sites s ON s.id=d.site_id WHERE d.id=?",
            (dossier_id,),
        ).fetchone())

    def create_dossier(self, *, code: str, product_id: int, site_id: int, target_region: str, intended_use_local: str, created_by: str, now: str) -> dict[str, Any]:
        cursor = self.connection.execute(
            "INSERT INTO localization_dossiers(code,product_id,site_id,target_region,intended_use_local,status,review_trigger,created_by,created_at,updated_at)"
            " VALUES(?,?,?,?,?, 'drafting', '',?,?,?)",
            (code, product_id, site_id, target_region, intended_use_local, created_by, now, now),
        )
        return self.dossier_by_id(int(cursor.lastrowid)) or {}

    def update_dossier(self, dossier_id: int, changes: dict[str, Any], now: str) -> dict[str, Any]:
        assignments = [f"{key}=?" for key in changes]
        self.connection.execute(
            f"UPDATE localization_dossiers SET {','.join(assignments)},updated_at=? WHERE id=?",
            (*changes.values(), now, dossier_id),
        )
        return self.dossier_by_id(dossier_id) or {}

    def list_dossiers(self, *, status: str | None, product_code: str | None, target_region: str | None, limit: int) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if status:
            clauses.append("d.status=?")
            params.append(status)
        if product_code:
            clauses.append("p.code=?")
            params.append(product_code)
        if target_region:
            clauses.append("d.target_region=?")
            params.append(target_region)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        params.append(limit)
        rows = self.connection.execute(
            "SELECT d.*,p.code AS product_code,p.name AS product_name,s.code AS site_code,s.name AS site_name,"
            "COUNT(DISTINCT dv.id) AS version_count,"
            "SUM(CASE WHEN dv.status='published' THEN 1 ELSE 0 END) AS published_count "
            "FROM localization_dossiers d JOIN health_products p ON p.id=d.product_id "
            "JOIN pilot_sites s ON s.id=d.site_id "
            "LEFT JOIN localization_versions dv ON dv.dossier_id=d.id" + where +
            " GROUP BY d.id ORDER BY d.updated_at DESC,d.id DESC LIMIT ?",
            tuple(params),
        ).fetchall()
        return [dict(row) for row in rows]

    # -- 版本 ---------------------------------------------------------------

    def next_version_no(self, dossier_id: int) -> int:
        return int(self.connection.execute(
            "SELECT COALESCE(MAX(version_no),0)+1 FROM localization_versions WHERE dossier_id=?",
            (dossier_id,),
        ).fetchone()[0])

    def create_version(self, *, dossier_id: int, version_no: int, content: dict[str, Any], content_digest: str, submitted_by: str, now: str) -> dict[str, Any]:
        cursor = self.connection.execute(
            "INSERT INTO localization_versions(dossier_id,version_no,status,current_stage,content_json,content_digest,submitted_by,submitted_at)"
            " VALUES(?,?,'in_review','medical',?,?,?,?)",
            (dossier_id, version_no, json.dumps(content, ensure_ascii=False, sort_keys=True), content_digest, submitted_by, now),
        )
        return self.version_by_id(int(cursor.lastrowid)) or {}

    def version_by_id(self, version_id: int) -> dict[str, Any] | None:
        return _dict(self.connection.execute("SELECT * FROM localization_versions WHERE id=?", (version_id,)).fetchone())

    def version_by_no(self, dossier_id: int, version_no: int) -> dict[str, Any] | None:
        return _dict(self.connection.execute(
            "SELECT * FROM localization_versions WHERE dossier_id=? AND version_no=?",
            (dossier_id, version_no),
        ).fetchone())

    def versions_of_dossier(self, dossier_id: int) -> list[dict[str, Any]]:
        return [dict(row) for row in self.connection.execute(
            "SELECT * FROM localization_versions WHERE dossier_id=? ORDER BY version_no",
            (dossier_id,),
        ).fetchall()]

    def current_published_version(self, dossier_id: int) -> dict[str, Any] | None:
        return _dict(self.connection.execute(
            "SELECT * FROM localization_versions WHERE dossier_id=? AND status='published' ORDER BY version_no DESC LIMIT 1",
            (dossier_id,),
        ).fetchone())

    def latest_version(self, dossier_id: int) -> dict[str, Any] | None:
        return _dict(self.connection.execute(
            "SELECT * FROM localization_versions WHERE dossier_id=? ORDER BY version_no DESC LIMIT 1",
            (dossier_id,),
        ).fetchone())

    def add_version_evidence(self, version_id: int, evidence_rows: Iterable[tuple[int, str, str, str]]) -> None:
        self.connection.executemany(
            "INSERT INTO localization_version_evidence(version_id,evidence_id,evidence_version,evidence_digest,evidence_status)"
            " VALUES(?,?,?,?,?)",
            [(version_id, *row) for row in evidence_rows],
        )

    def version_evidence(self, version_id: int) -> list[dict[str, Any]]:
        return [dict(row) for row in self.connection.execute(
            "SELECT ve.*,e.title,e.evidence_type,e.source_name,e.source_region "
            "FROM localization_version_evidence ve JOIN evidence_documents e ON e.id=ve.evidence_id "
            "WHERE ve.version_id=? ORDER BY e.evidence_type,e.id",
            (version_id,),
        ).fetchall()]

    # -- 审阅 ---------------------------------------------------------------

    def add_review(self, *, version_id: int, stage: str, decision: str, reviewer: str, note: str, difference_ids: list[int], now: str) -> dict[str, Any]:
        cursor = self.connection.execute(
            "INSERT INTO localization_reviews(version_id,stage,decision,reviewer,note,difference_ids_json,created_at)"
            " VALUES(?,?,?,?,?,?,?)",
            (version_id, stage, decision, reviewer, note, json.dumps(difference_ids), now),
        )
        row = self.connection.execute("SELECT * FROM localization_reviews WHERE id=?", (cursor.lastrowid,)).fetchone()
        return dict(row)

    def reviews_of_version(self, version_id: int) -> list[dict[str, Any]]:
        return [dict(row) for row in self.connection.execute(
            "SELECT * FROM localization_reviews WHERE version_id=? ORDER BY id",
            (version_id,),
        ).fetchall()]

    # -- 差异项 -------------------------------------------------------------

    def next_difference_seq(self, dossier_id: int) -> int:
        return int(self.connection.execute(
            "SELECT COALESCE(MAX(seq),0)+1 FROM localization_differences WHERE dossier_id=?",
            (dossier_id,),
        ).fetchone()[0])

    def create_difference(self, *, dossier_id: int, seq: int, category: str, title: str, detail: str, evidence_id: int | None, opened_by: str, opened_in_version_id: int | None, now: str) -> dict[str, Any]:
        cursor = self.connection.execute(
            "INSERT INTO localization_differences(dossier_id,seq,category,title,detail,evidence_id,status,opened_by,opened_in_version_id,created_at)"
            " VALUES(?,?,?,?,?,?, 'open',?,?,?)",
            (dossier_id, seq, category, title, detail, evidence_id, opened_by, opened_in_version_id, now),
        )
        return dict(self.connection.execute("SELECT * FROM localization_differences WHERE id=?", (cursor.lastrowid,)).fetchone())

    def difference_by_id(self, dossier_id: int, difference_id: int) -> dict[str, Any] | None:
        return _dict(self.connection.execute(
            "SELECT * FROM localization_differences WHERE id=? AND dossier_id=?",
            (difference_id, dossier_id),
        ).fetchone())

    def list_differences(self, dossier_id: int, status: str | None = None) -> list[dict[str, Any]]:
        if status:
            rows = self.connection.execute(
                "SELECT * FROM localization_differences WHERE dossier_id=? AND status=? ORDER BY id",
                (dossier_id, status),
            ).fetchall()
        else:
            rows = self.connection.execute(
                "SELECT * FROM localization_differences WHERE dossier_id=? ORDER BY id",
                (dossier_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def open_differences(self, dossier_id: int) -> list[dict[str, Any]]:
        return self.list_differences(dossier_id, "open")

    def resolve_difference(self, difference_id: int, *, resolution: str, version_id: int, now: str) -> None:
        self.connection.execute(
            "UPDATE localization_differences SET status='resolved',resolution=?,resolved_in_version_id=?,resolved_at=? WHERE id=?",
            (resolution, version_id, now, difference_id),
        )

    # -- 合作引用 -----------------------------------------------------------

    def sign_cooperation(self, *, dossier_id: int, reference: str, version_id: int, signed_by: str, note: str, now: str) -> dict[str, Any]:
        cursor = self.connection.execute(
            "INSERT INTO localization_cooperations(dossier_id,reference,version_id,signed_by,note,status,signed_at,created_at)"
            " VALUES(?,?,?,?,?, 'active',?,?)",
            (dossier_id, reference, version_id, signed_by, note, now, now),
        )
        return dict(self.connection.execute("SELECT * FROM localization_cooperations WHERE id=?", (cursor.lastrowid,)).fetchone())

    def cooperation_by_reference(self, reference: str) -> dict[str, Any] | None:
        return _dict(self.connection.execute(
            "SELECT * FROM localization_cooperations WHERE reference=?", (reference,)
        ).fetchone())

    def list_cooperations(self, dossier_id: int) -> list[dict[str, Any]]:
        return [dict(row) for row in self.connection.execute(
            "SELECT c.*,dv.version_no FROM localization_cooperations c JOIN localization_versions dv ON dv.id=c.version_id "
            "WHERE c.dossier_id=? ORDER BY c.id",
            (dossier_id,),
        ).fetchall()]

    def withdraw_cooperation(self, cooperation_id: int, now: str) -> None:
        self.connection.execute(
            "UPDATE localization_cooperations SET status='withdrawn',withdrawn_at=? WHERE id=?",
            (now, cooperation_id),
        )

    # -- 事件 ---------------------------------------------------------------

    def add_event(self, *, dossier_id: int, event_type: str, detail: dict[str, Any], actor: str, now: str) -> None:
        self.connection.execute(
            "INSERT INTO localization_events(dossier_id,event_type,detail_json,actor,created_at) VALUES(?,?,?,?,?)",
            (dossier_id, event_type, json.dumps(detail, ensure_ascii=False, sort_keys=True, default=str), actor, now),
        )

    def list_events(self, dossier_id: int) -> list[dict[str, Any]]:
        return [dict(row) for row in self.connection.execute(
            "SELECT * FROM localization_events WHERE dossier_id=? ORDER BY id",
            (dossier_id,),
        ).fetchall()]
