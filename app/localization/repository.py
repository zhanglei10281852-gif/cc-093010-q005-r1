from __future__ import annotations

import json
import sqlite3
from typing import Any


STAGE_ORDER = ("medical", "compliance", "operations")


def _dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


class LocalizationRepository:
    """封装本地化档案领域的 SQLite 读写。"""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    # ---------- 档案 ----------

    def profile_by_code(self, code: str) -> sqlite3.Row | None:
        return self.connection.execute("SELECT * FROM localization_profiles WHERE code=?", (code,)).fetchone()

    def profile_by_id(self, profile_id: int) -> sqlite3.Row | None:
        return self.connection.execute("SELECT * FROM localization_profiles WHERE id=?", (profile_id,)).fetchone()

    def create_profile(self, data: dict[str, Any], now: str) -> dict[str, Any]:
        cursor = self.connection.execute(
            "INSERT INTO localization_profiles(code,product_id,target_region,partner_site_id,intended_use_local,status,created_by,created_at,updated_at) "
            "VALUES(?,?,?,?,?,'draft',?,?,?)",
            (data["code"], data["product_id"], data["target_region"], data["partner_site_id"], data["intended_use_local"], data["created_by"], now, now),
        )
        return dict(self.profile_by_id(cursor.lastrowid))

    def touch_profile(self, profile_id: int, now: str, **fields: Any) -> None:
        assignments = [f"{key}=?" for key in fields]
        self.connection.execute(
            f"UPDATE localization_profiles SET {','.join(assignments)},updated_at=? WHERE id=?",
            (*fields.values(), now, profile_id),
        )

    def list_profiles(self, *, status: str | None, product_id: int | None, site_id: int | None, region: str | None, limit: int) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if status:
            clauses.append("p.status=?")
            params.append(status)
        if product_id is not None:
            clauses.append("p.product_id=?")
            params.append(product_id)
        if site_id is not None:
            clauses.append("p.partner_site_id=?")
            params.append(site_id)
        if region:
            clauses.append("p.target_region=?")
            params.append(region)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        params.append(limit)
        rows = self.connection.execute(
            "SELECT p.*,hp.code AS product_code,hp.name AS product_name,s.code AS site_code,s.name AS site_name "
            "FROM localization_profiles p "
            "JOIN health_products hp ON hp.id=p.product_id "
            "JOIN pilot_sites s ON s.id=p.partner_site_id" + where +
            " ORDER BY p.updated_at DESC,p.id DESC LIMIT ?",
            tuple(params),
        ).fetchall()
        return [dict(row) for row in rows]

    def profiles_for_product(self, product_id: int) -> list[dict[str, Any]]:
        return [dict(row) for row in self.connection.execute(
            "SELECT * FROM localization_profiles WHERE product_id=?", (product_id,),
        ).fetchall()]

    def profiles_for_site(self, site_id: int) -> list[dict[str, Any]]:
        return [dict(row) for row in self.connection.execute(
            "SELECT * FROM localization_profiles WHERE partner_site_id=?", (site_id,),
        ).fetchall()]

    def profiles_referencing_evidence(self, evidence_id: int) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            "SELECT DISTINCT p.* FROM localization_profiles p "
            "JOIN localization_profile_versions v ON v.profile_id=p.id "
            "JOIN localization_version_sources vs ON vs.version_id=v.id "
            "WHERE vs.evidence_id=?",
            (evidence_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    # ---------- 版本 ----------

    def version_by_id(self, version_id: int) -> sqlite3.Row | None:
        return self.connection.execute("SELECT * FROM localization_profile_versions WHERE id=?", (version_id,)).fetchone()

    def version_detail(self, version_id: int) -> sqlite3.Row | None:
        return self.connection.execute(
            "SELECT v.*,p.code AS profile_code FROM localization_profile_versions v "
            "JOIN localization_profiles p ON p.id=v.profile_id WHERE v.id=?",
            (version_id,),
        ).fetchone()

    def latest_version(self, profile_id: int) -> sqlite3.Row | None:
        return self.connection.execute(
            "SELECT * FROM localization_profile_versions WHERE profile_id=? ORDER BY version_no DESC LIMIT 1",
            (profile_id,),
        ).fetchone()

    def version_by_number(self, profile_id: int, version_no: int) -> sqlite3.Row | None:
        return self.connection.execute(
            "SELECT * FROM localization_profile_versions WHERE profile_id=? AND version_no=?",
            (profile_id, version_no),
        ).fetchone()

    def versions_of_profile(self, profile_id: int) -> list[dict[str, Any]]:
        return [dict(row) for row in self.connection.execute(
            "SELECT * FROM localization_profile_versions WHERE profile_id=? ORDER BY version_no",
            (profile_id,),
        ).fetchall()]

    def create_version(self, profile_id: int, version_no: int, intended_use_local: str, content_digest: str, submitted_by: str, now: str) -> dict[str, Any]:
        cursor = self.connection.execute(
            "INSERT INTO localization_profile_versions(profile_id,version_no,intended_use_local,content_digest,status,current_stage,submitted_by,created_at) "
            "VALUES(?,?,?,?,'in_review','medical',?,?)",
            (profile_id, version_no, intended_use_local, content_digest, submitted_by, now),
        )
        return dict(self.version_by_id(cursor.lastrowid))

    def advance_version(self, version_id: int, *, status: str | None = None, current_stage: str | None | bool = False, published_at: str | None = False) -> None:
        """更新版本状态/当前阶段。current_stage=None 表示清空阶段；published_at=None 表示清空发布时间；未传则不动。"""
        assignments: list[str] = []
        values: list[Any] = []
        if status is not None:
            assignments.append("status=?")
            values.append(status)
        if current_stage is not False:
            if current_stage is None:
                assignments.append("current_stage=NULL")
            else:
                assignments.append("current_stage=?")
                values.append(current_stage)
        if published_at is not False:
            if published_at is None:
                assignments.append("published_at=NULL")
            else:
                assignments.append("published_at=?")
                values.append(published_at)
        if not assignments:
            return
        values.append(version_id)
        self.connection.execute(
            f"UPDATE localization_profile_versions SET {','.join(assignments)} WHERE id=?",
            tuple(values),
        )

    def supersede_other_versions(self, profile_id: int, published_version_id: int) -> None:
        self.connection.execute(
            "UPDATE localization_profile_versions SET status='superseded' WHERE profile_id=? AND id<>? AND status='approved'",
            (profile_id, published_version_id),
        )

    def effective_version_at(self, profile_id: int, moment: str) -> sqlite3.Row | None:
        """返回该时点有效的发布版本。

        完全依据发布时间线：已发布（published_at 非空）且发布时间不晚于 moment、
        且当时不存在更新的已发布版本。与版本当前是否已被替代无关——被替代的旧版本
        在其发布到下一版本发布之间仍然有效，保证历史结论可追溯。
        """
        return self.connection.execute(
            "SELECT v.* FROM localization_profile_versions v WHERE v.profile_id=? AND v.published_at IS NOT NULL AND v.published_at<=? "
            "AND NOT EXISTS(SELECT 1 FROM localization_profile_versions n WHERE n.profile_id=v.profile_id "
            "AND n.published_at IS NOT NULL AND n.published_at<=? AND (n.published_at>v.published_at OR (n.published_at=v.published_at AND n.version_no>v.version_no))) "
            "ORDER BY v.version_no DESC LIMIT 1",
            (profile_id, moment, moment),
        ).fetchone()

    # ---------- 来源证据 ----------

    def add_version_source(self, version_id: int, evidence_id: int, note: str, now: str) -> None:
        self.connection.execute(
            "INSERT OR IGNORE INTO localization_version_sources(version_id,evidence_id,note,added_at) VALUES(?,?,?,?)",
            (version_id, evidence_id, note, now),
        )

    def sources_of_version(self, version_id: int) -> list[dict[str, Any]]:
        return [dict(row) for row in self.connection.execute(
            "SELECT vs.evidence_id,vs.note,e.evidence_type,e.title,e.source_name,e.source_region,e.version AS source_version,e.status AS evidence_status,e.content_digest "
            "FROM localization_version_sources vs JOIN evidence_documents e ON e.id=vs.evidence_id "
            "WHERE vs.version_id=? ORDER BY e.evidence_type,e.id",
            (version_id,),
        ).fetchall()]

    # ---------- 差异项 ----------

    def difference_by_id(self, difference_id: int) -> sqlite3.Row | None:
        return self.connection.execute("SELECT * FROM localization_differences WHERE id=?", (difference_id,)).fetchone()

    def create_difference(self, version_id: int, data: dict[str, Any], now: str) -> dict[str, Any]:
        carried_from = data.get("carried_from")
        cursor = self.connection.execute(
            "INSERT INTO localization_differences(version_id,difference_type,topic,source_text,adjusted_text,rationale,resolution,carried_from_difference_id,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?)",
            (version_id, data["difference_type"], data["topic"], data.get("source_text", ""), data.get("adjusted_text", ""), data.get("rationale", ""),
             "carried" if carried_from else "open", carried_from, now, now),
        )
        return dict(self.difference_by_id(cursor.lastrowid))

    def differences_of_version(self, version_id: int) -> list[dict[str, Any]]:
        return [dict(row) for row in self.connection.execute(
            "SELECT * FROM localization_differences WHERE version_id=? ORDER BY id", (version_id,),
        ).fetchall()]

    def open_difference_ids(self, version_id: int) -> set[int]:
        return {int(row[0]) for row in self.connection.execute(
            "SELECT id FROM localization_differences WHERE version_id=? AND resolution IN ('open','carried')", (version_id,),
        ).fetchall()}

    # ---------- 审阅记录 ----------

    def add_review(self, version_id: int, stage: str, reviewer: str, decision: str, comment: str, difference_ids: list[int], now: str) -> dict[str, Any]:
        cursor = self.connection.execute(
            "INSERT INTO localization_reviews(version_id,stage,reviewer,decision,comment,difference_ids_json,created_at) VALUES(?,?,?,?,?,?,?)",
            (version_id, stage, reviewer, decision, comment, json.dumps(difference_ids, ensure_ascii=False), now),
        )
        return dict(self.connection.execute("SELECT * FROM localization_reviews WHERE id=?", (cursor.lastrowid,)).fetchone())

    def reviews_of_version(self, version_id: int) -> list[dict[str, Any]]:
        return [dict(row) for row in self.connection.execute(
            "SELECT * FROM localization_reviews WHERE version_id=? ORDER BY id", (version_id,),
        ).fetchall()]

    def latest_stage_decision(self, version_id: int, stage: str) -> sqlite3.Row | None:
        return self.connection.execute(
            "SELECT * FROM localization_reviews WHERE version_id=? AND stage=? ORDER BY id DESC LIMIT 1",
            (version_id, stage),
        ).fetchone()

    # ---------- 待复核标志 ----------

    def open_flag(self, profile_id: int, reason_type: str, evidence_id: int | None = None) -> sqlite3.Row | None:
        sql = "SELECT * FROM localization_review_flags WHERE profile_id=? AND reason_type=? AND status='open'"
        params: list[Any] = [profile_id, reason_type]
        if evidence_id is None:
            sql += " AND evidence_id IS NULL"
        else:
            sql += " AND evidence_id=?"
            params.append(evidence_id)
        return self.connection.execute(sql, tuple(params)).fetchone()

    def raise_flag(self, profile_id: int, reason_type: str, detail: str, raised_by: str, now: str, evidence_id: int | None = None) -> dict[str, Any]:
        cursor = self.connection.execute(
            "INSERT INTO localization_review_flags(profile_id,reason_type,detail,evidence_id,status,raised_by,raised_at) VALUES(?,?,?,?,'open',?,?)",
            (profile_id, reason_type, detail, evidence_id, raised_by, now),
        )
        return dict(self.connection.execute("SELECT * FROM localization_review_flags WHERE id=?", (cursor.lastrowid,)).fetchone())

    def flags_of_profile(self, profile_id: int, *, status: str | None = None) -> list[dict[str, Any]]:
        sql = "SELECT * FROM localization_review_flags WHERE profile_id=?"
        params: list[Any] = [profile_id]
        if status:
            sql += " AND status=?"
            params.append(status)
        sql += " ORDER BY id"
        return [dict(row) for row in self.connection.execute(sql, tuple(params)).fetchall()]

    def flag_by_id(self, flag_id: int) -> sqlite3.Row | None:
        return self.connection.execute("SELECT * FROM localization_review_flags WHERE id=?", (flag_id,)).fetchone()

    def resolve_flag(self, flag_id: int, *, resolved_by: str, resolution_note: str, new_version_no: int | None, now: str) -> None:
        self.connection.execute(
            "UPDATE localization_review_flags SET status='resolved',resolved_by=?,resolved_at=?,resolution_note=?,new_version_no=? WHERE id=?",
            (resolved_by, now, resolution_note, new_version_no, flag_id),
        )

    def dismiss_flag(self, flag_id: int, *, resolved_by: str, resolution_note: str, now: str) -> None:
        self.connection.execute(
            "UPDATE localization_review_flags SET status='dismissed',resolved_by=?,resolved_at=?,resolution_note=? WHERE id=?",
            (resolved_by, now, resolution_note, flag_id),
        )

    def reopen_profile_if_cleared(self, profile_id: int, now: str) -> None:
        """所有待复核标志处理完毕且档案处于待复核时，回到已发布状态；若仍有审阅中的版本则保持审阅中。"""
        open_count = self.connection.execute(
            "SELECT COUNT(*) FROM localization_review_flags WHERE profile_id=? AND status='open'", (profile_id,),
        ).fetchone()[0]
        reviewing_count = self.connection.execute(
            "SELECT COUNT(*) FROM localization_profile_versions WHERE profile_id=? AND status='in_review'", (profile_id,),
        ).fetchone()[0]
        profile = self.profile_by_id(profile_id)
        if profile is None or profile["status"] != "under_review":
            return
        if open_count == 0 and reviewing_count == 0 and profile["published_version_no"] is not None:
            self.touch_profile(profile_id, now, status="published")
        elif open_count == 0 and reviewing_count > 0:
            self.touch_profile(profile_id, now, status="in_review")

    # ---------- 已签署合作 ----------

    def agreement_by_code(self, agreement_code: str) -> sqlite3.Row | None:
        return self.connection.execute("SELECT * FROM localization_agreements WHERE agreement_code=?", (agreement_code,)).fetchone()

    def create_agreement(self, profile_id: int, version_id: int, data: dict[str, Any], now: str) -> dict[str, Any]:
        cursor = self.connection.execute(
            "INSERT INTO localization_agreements(profile_id,version_id,agreement_code,signed_by,note,signed_at) VALUES(?,?,?,?,?,?)",
            (profile_id, version_id, data["agreement_code"], data["signed_by"], data.get("note", ""), now),
        )
        return dict(self.connection.execute("SELECT * FROM localization_agreements WHERE id=?", (cursor.lastrowid,)).fetchone())

    def agreements_of_profile(self, profile_id: int) -> list[dict[str, Any]]:
        return [dict(row) for row in self.connection.execute(
            "SELECT a.*,v.version_no FROM localization_agreements a "
            "JOIN localization_profile_versions v ON v.id=a.version_id "
            "WHERE a.profile_id=? ORDER BY a.signed_at,a.id", (profile_id,),
        ).fetchall()]

    def active_agreements_for_version(self, version_id: int) -> int:
        return int(self.connection.execute(
            "SELECT COUNT(*) FROM localization_agreements WHERE version_id=? AND terminated_at IS NULL", (version_id,),
        ).fetchone()[0])

    def terminate_agreement(self, agreement_id: int, now: str) -> None:
        self.connection.execute("UPDATE localization_agreements SET terminated_at=? WHERE id=? AND terminated_at IS NULL", (now, agreement_id))
