from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import UTC, datetime, time
from typing import Any

from app.core.clock import Clock, SystemClock, from_storage, to_storage
from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.database import get_connection, transaction
from app.localization.repository import STAGE_ORDER, LocalizationRepository


NEXT_STAGE = {"medical": "compliance", "compliance": "operations", "operations": None}
STAGE_NAMES = {"medical": "医学", "compliance": "合规", "operations": "运营"}


def content_digest(intended_use_local: str, evidence_ids: list[int], differences: list[dict[str, Any]]) -> str:
    payload = {
        "intended_use_local": intended_use_local,
        "evidence_ids": sorted(evidence_ids),
        "differences": [
            {k: item.get(k, "") for k in ("difference_type", "topic", "source_text", "adjusted_text", "rationale", "carried_from")}
            for item in differences
        ],
    }
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode()).hexdigest()


def parse_moment(raw: str) -> str:
    """接受 YYYY-MM-DD（按当日结束，含全天）或完整时间，返回 UTC 存储格式字符串。"""
    raw = raw.strip()
    if len(raw) == 10:
        try:
            day = datetime.strptime(raw, "%Y-%m-%d").date()
        except ValueError as exc:
            raise ValidationError("日期格式应为 YYYY-MM-DD") from exc
        return to_storage(datetime.combine(day, time(hour=23, minute=59, second=59), tzinfo=UTC))
    parsed = from_storage(raw)
    if parsed is None:
        raise ValidationError("时间格式不合法")
    return to_storage(parsed)


class LocalizationService:
    """管理本地化档案、不可变版本、三段串行审阅、待复核标志与时点查询。"""

    def __init__(self, connection: sqlite3.Connection | None = None, clock: Clock | None = None) -> None:
        self.connection = connection or get_connection()
        self.clock = clock or SystemClock()
        self.repository = LocalizationRepository(self.connection)

    # ---------- 档案 ----------

    def create_profile(self, data: dict[str, Any]) -> dict[str, Any]:
        code = data["code"].strip().lower()
        product = self.connection.execute("SELECT * FROM health_products WHERE code=?", (data["product_code"],)).fetchone()
        if product is None:
            raise NotFoundError("健康创新产品不存在")
        site = self.connection.execute("SELECT * FROM pilot_sites WHERE code=?", (data["site_code"],)).fetchone()
        if site is None:
            raise NotFoundError("合作机构场地不存在")
        if site["site_type"] != "医院":
            raise ValidationError("本地化档案的合作机构必须是医院")
        with transaction(immediate=True) as connection:
            repository = LocalizationRepository(connection)
            if repository.profile_by_code(code):
                raise ConflictError("本地化档案编码已存在")
            payload = {
                "code": code,
                "product_id": product["id"],
                "target_region": data["target_region"].strip(),
                "partner_site_id": site["id"],
                "intended_use_local": data["intended_use_local"].strip(),
                "created_by": data["created_by"].strip(),
            }
            profile = repository.create_profile(payload, to_storage(self.clock.now()))
            profile_code = profile["code"]
        return self.get_profile(profile_code)

    def list_profiles(self, *, status: str | None, product_code: str | None, site_code: str | None, region: str | None, limit: int) -> list[dict[str, Any]]:
        product_id = site_id = None
        if product_code:
            product = self.connection.execute("SELECT id FROM health_products WHERE code=?", (product_code,)).fetchone()
            if product is None:
                raise NotFoundError("健康创新产品不存在")
            product_id = product["id"]
        if site_code:
            site = self.connection.execute("SELECT id FROM pilot_sites WHERE code=?", (site_code,)).fetchone()
            if site is None:
                raise NotFoundError("合作机构场地不存在")
            site_id = site["id"]
        items = self.repository.list_profiles(status=status, product_id=product_id, site_id=site_id, region=region, limit=max(1, min(limit, 500)))
        for item in items:
            item["open_flag_count"] = self.connection.execute(
                "SELECT COUNT(*) FROM localization_review_flags WHERE profile_id=? AND status='open'", (item["id"],),
            ).fetchone()[0]
        return items

    def get_profile(self, code: str) -> dict[str, Any]:
        profile = self._require_profile_row(code)
        result = dict(profile)
        product = self.connection.execute("SELECT code,name,regulatory_status,active FROM health_products WHERE id=?", (profile["product_id"],)).fetchone()
        site = self.connection.execute("SELECT code,name,site_type,region,status FROM pilot_sites WHERE id=?", (profile["partner_site_id"],)).fetchone()
        result["product"] = dict(product)
        result["partner_site"] = dict(site)
        result["versions"] = self.repository.versions_of_profile(profile["id"])
        result["review_flags"] = self.repository.flags_of_profile(profile["id"])
        result["agreements"] = self.repository.agreements_of_profile(profile["id"])
        return result

    def withdraw_profile(self, code: str, actor: str, reason: str) -> dict[str, Any]:
        """永久撤回档案（合作彻底终止后的归档）；已签署合作引用的旧版本仍保留可追溯。"""
        profile = self._require_profile_row(code)
        if profile["status"] == "withdrawn":
            raise ConflictError("本地化档案已经撤回")
        now = to_storage(self.clock.now())
        with transaction(immediate=True) as connection:
            repository = LocalizationRepository(connection)
            if not repository.open_flag(profile["id"], "manual"):
                repository.raise_flag(profile["id"], "manual", f"档案撤回：{reason[:800]}", actor, now)
            repository.touch_profile(profile["id"], now, status="withdrawn", withdrawn_at=now)
        return self.get_profile(code)

    # ---------- 版本提交 ----------

    def submit_version(self, code: str, data: dict[str, Any]) -> dict[str, Any]:
        profile = self._require_profile_row(code)
        if profile["status"] == "withdrawn":
            raise ConflictError("档案已撤回，不能再提交版本")
        intended_use = (data.get("intended_use_local") or profile["intended_use_local"]).strip()
        now = to_storage(self.clock.now())
        with transaction(immediate=True) as connection:
            repository = LocalizationRepository(connection)
            latest = repository.latest_version(profile["id"])
            if latest is not None and latest["status"] == "in_review":
                raise ConflictError("当前已有审阅中的版本，需等待审阅结束")
            previous_open_ids = repository.open_difference_ids(latest["id"]) if latest is not None else set()
            version_no = 1 if latest is None else int(latest["version_no"]) + 1

            evidence_ids = list(dict.fromkeys(data["source_evidence_ids"]))
            if not evidence_ids:
                raise ValidationError("每个本地化版本至少汇集一项来源证据")
            for evidence_id in evidence_ids:
                row = connection.execute("SELECT * FROM evidence_documents WHERE id=?", (evidence_id,)).fetchone()
                if row is None:
                    raise NotFoundError(f"来源证据 {evidence_id} 不存在")
                if row["product_id"] != profile["product_id"]:
                    raise ValidationError(f"来源证据 {evidence_id} 不属于该档案绑定的产品")
                if row["status"] != "accepted":
                    raise ConflictError(f"来源证据 {evidence_id} 尚未通过目录审阅或已被替代，不能作为本地化依据")

            differences = data["differences"]
            if not differences:
                raise ValidationError("每个本地化版本至少记录一项翻译、术语校准或临床场景差异")
            for item in differences:
                carried = item.get("carried_from")
                if carried is not None and carried not in previous_open_ids:
                    raise ValidationError(f"差异 {carried} 不是上一版本未解决的差异，不能结转")

            digest_value = content_digest(intended_use, evidence_ids, differences)
            version = repository.create_version(profile["id"], version_no, intended_use, digest_value, data["submitted_by"].strip(), now)
            for evidence_id in evidence_ids:
                repository.add_version_source(version["id"], evidence_id, "", now)
            for item in differences:
                repository.create_difference(version["id"], item, now)
            repository.touch_profile(profile["id"], now, status="in_review")
            version_id = version["id"]
        return self.get_version(version_id)

    def get_version(self, version_id: int) -> dict[str, Any]:
        row = self.repository.version_detail(version_id)
        if row is None:
            raise NotFoundError("本地化版本不存在")
        result = dict(row)
        result["sources"] = self.repository.sources_of_version(version_id)
        result["differences"] = self.repository.differences_of_version(version_id)
        result["reviews"] = self.repository.reviews_of_version(version_id)
        return result

    def list_versions(self, code: str) -> list[dict[str, Any]]:
        profile = self._require_profile_row(code)
        versions = self.repository.versions_of_profile(profile["id"])
        for version in versions:
            version["open_difference_count"] = len(self.repository.open_difference_ids(version["id"]))
        return versions

    # ---------- 三段串行审阅 ----------

    def review(self, code: str, payload: dict[str, Any]) -> dict[str, Any]:
        stage = payload["stage"]
        if stage not in STAGE_ORDER:
            raise ValidationError("审阅环节必须是 medical、compliance 或 operations")
        profile = self._require_profile_row(code)
        comment = (payload.get("comment") or "").strip()
        now = to_storage(self.clock.now())
        with transaction(immediate=True) as connection:
            repository = LocalizationRepository(connection)
            version = repository.latest_version(profile["id"])
            if version is None or version["status"] != "in_review":
                raise ConflictError("该档案没有审阅中的版本")
            if version["current_stage"] != stage:
                raise ConflictError(f"当前应先完成{STAGE_NAMES.get(version['current_stage'], '前置')}审阅")
            if payload["decision"] == "returned":
                if len(comment) < 4:
                    raise ValidationError("退回时需要说明可执行的原因")
                difference_ids = list(dict.fromkeys(payload.get("difference_ids") or []))
                if not difference_ids:
                    raise ValidationError("退回时必须指向至少一项具体差异")
                owned = {item["id"] for item in repository.differences_of_version(version["id"])}
                unknown = [item for item in difference_ids if item not in owned]
                if unknown:
                    raise ValidationError("退回指向了不属于该版本的差异", context={"difference_ids": unknown})
                repository.add_review(version["id"], stage, payload["reviewer"].strip(), "returned", comment, difference_ids, now)
                repository.advance_version(version["id"], status="returned")
                repository.touch_profile(profile["id"], now, status=self._idle_status(repository, profile))
                version_id = version["id"]
            else:
                repository.add_review(version["id"], stage, payload["reviewer"].strip(), "approved", comment, [], now)
                next_stage = NEXT_STAGE[stage]
                if next_stage is None:
                    self._publish(connection, repository, profile, version, payload.get("resolves_flag_ids") or [], now)
                else:
                    repository.advance_version(version["id"], current_stage=next_stage)
                version_id = version["id"]
        return self.get_version(version_id)

    @staticmethod
    def _idle_status(repository: LocalizationRepository, profile: sqlite3.Row) -> str:
        """审阅结束（退回）后档案应处的状态：已发布档案若有挂起事项进入待复核，否则回到已发布或草稿。"""
        if repository.flags_of_profile(profile["id"], status="open") and profile["published_version_no"] is not None:
            return "under_review"
        return "published" if profile["published_version_no"] is not None else "draft"

    def _publish(self, connection: sqlite3.Connection, repository: LocalizationRepository, profile: sqlite3.Row, version: sqlite3.Row, resolves_flag_ids: list[int], now: str) -> None:
        repository.advance_version(version["id"], status="approved", current_stage=None, published_at=now)
        # 本版本差异视为在发布时处理完毕；由旧版本结转来的差异同时在其原版本闭环
        carried_sources = [row["carried_from_difference_id"] for row in repository.differences_of_version(version["id"]) if row["carried_from_difference_id"] is not None]
        connection.execute(
            "UPDATE localization_differences SET resolution='addressed',updated_at=? WHERE version_id=?",
            (now, version["id"]),
        )
        if carried_sources:
            placeholders = ",".join("?" for _ in carried_sources)
            connection.execute(
                f"UPDATE localization_differences SET resolution='addressed',updated_at=? WHERE id IN ({placeholders})",
                (now, *carried_sources),
            )
        repository.supersede_other_versions(profile["id"], version["id"])
        # 仅关闭本次补交明确回应的待复核标志；其余标志继续挂起
        for flag_id in dict.fromkeys(resolves_flag_ids):
            flag = repository.flag_by_id(int(flag_id))
            if flag is not None and flag["profile_id"] == profile["id"] and flag["status"] == "open":
                repository.resolve_flag(
                    flag["id"], resolved_by="system",
                    resolution_note=f"新版本 v{version['version_no']} 发布并声明处理该事项",
                    new_version_no=version["version_no"], now=now,
                )
        final_status = "under_review" if repository.flags_of_profile(profile["id"], status="open") else "published"
        repository.touch_profile(profile["id"], now, status=final_status, published_version_no=version["version_no"])

    # ---------- 待复核标志 ----------

    def raise_manual_flag(self, code: str, actor: str, detail: str) -> dict[str, Any]:
        profile = self._require_profile_row(code)
        now = to_storage(self.clock.now())
        with transaction(immediate=True) as connection:
            repository = LocalizationRepository(connection)
            flag = repository.raise_flag(profile["id"], "manual", detail[:1000], actor, now)
            self._mark_under_review(repository, profile["id"], now)
            flag_id = flag["id"]
        return dict(self.repository.flag_by_id(flag_id))

    def resolve_flag(self, code: str, flag_id: int, actor: str, action: str, note: str) -> dict[str, Any]:
        profile = self._require_profile_row(code)
        if action not in {"resolved", "dismissed"}:
            raise ValidationError("处理方式必须是 resolved 或 dismissed")
        if len(note.strip()) < 4:
            raise ValidationError("处理待复核标志时需要说明结论")
        now = to_storage(self.clock.now())
        with transaction(immediate=True) as connection:
            repository = LocalizationRepository(connection)
            flag = repository.flag_by_id(flag_id)
            if flag is None or flag["profile_id"] != profile["id"]:
                raise NotFoundError("待复核标志不存在")
            if flag["status"] != "open":
                raise ConflictError("该待复核标志已经处理")
            new_version = connection.execute(
                "SELECT version_no FROM localization_profile_versions WHERE profile_id=? AND status='approved' AND published_at>? ORDER BY version_no DESC LIMIT 1",
                (profile["id"], flag["raised_at"]),
            ).fetchone()
            new_version_no = int(new_version["version_no"]) if action == "resolved" and new_version is not None else None
            if action == "resolved":
                repository.resolve_flag(flag["id"], resolved_by=actor, resolution_note=note.strip(), new_version_no=new_version_no, now=now)
            else:
                repository.dismiss_flag(flag["id"], resolved_by=actor, resolution_note=note.strip(), now=now)
            repository.reopen_profile_if_cleared(profile["id"], now)
        return dict(self.repository.flag_by_id(flag_id))

    @staticmethod
    def _mark_under_review(repository: LocalizationRepository, profile_id: int, now: str) -> None:
        profile = repository.profile_by_id(profile_id)
        if profile is not None and profile["status"] == "published":
            repository.touch_profile(profile_id, now, status="under_review")

    # ---------- 已签署合作 ----------

    def sign_agreement(self, code: str, data: dict[str, Any]) -> dict[str, Any]:
        profile = self._require_profile_row(code)
        now = to_storage(self.clock.now())
        with transaction(immediate=True) as connection:
            repository = LocalizationRepository(connection)
            if repository.agreement_by_code(data["agreement_code"]):
                raise ConflictError("合作协议编码已存在")
            if data.get("version_no") is not None:
                version = repository.version_by_number(profile["id"], int(data["version_no"]))
            elif profile["published_version_no"] is not None:
                version = repository.version_by_number(profile["id"], int(profile["published_version_no"]))
            else:
                version = None
            if version is None or version["status"] != "approved":
                raise ConflictError("只能引用当前已发布的本地化版本签署合作")
            agreement = repository.create_agreement(profile["id"], version["id"], data, now)
            agreement_id = agreement["id"]
        row = self.connection.execute(
            "SELECT a.*,v.version_no FROM localization_agreements a JOIN localization_profile_versions v ON v.id=a.version_id WHERE a.id=?",
            (agreement_id,),
        ).fetchone()
        return dict(row)

    def terminate_agreement(self, code: str, agreement_code: str, actor: str) -> dict[str, Any]:
        profile = self._require_profile_row(code)
        now = to_storage(self.clock.now())
        with transaction(immediate=True) as connection:
            repository = LocalizationRepository(connection)
            agreement = repository.agreement_by_code(agreement_code)
            if agreement is None or agreement["profile_id"] != profile["id"]:
                raise NotFoundError("合作协议不存在")
            if agreement["terminated_at"] is not None:
                raise ConflictError("合作协议已经终止")
            repository.terminate_agreement(agreement["id"], now)
        result = dict(self.repository.agreement_by_code(agreement_code))
        result["terminated_by"] = actor
        return result

    # ---------- 时点查询 ----------

    def effective_at(self, code: str, at: str) -> dict[str, Any]:
        profile = self._require_profile_row(code)
        moment = parse_moment(at)
        result: dict[str, Any] = {
            "at": moment,
            "profile_code": profile["code"],
            "profile_status_today": profile["status"],
            "effective_version": None,
            "unresolved_differences": [],
            "open_review_flags": self._flags_open_at(profile["id"], moment),
            "agreements": [
                {"agreement_code": row["agreement_code"], "version_no": row["version_no"], "signed_by": row["signed_by"], "signed_at": row["signed_at"]}
                for row in self.repository.agreements_of_profile(profile["id"])
                if row["signed_at"] <= moment and (not row["terminated_at"] or row["terminated_at"] > moment)
            ],
        }
        version = self.repository.effective_version_at(profile["id"], moment)
        if version is not None:
            detail = self.get_version(int(version["id"]))
            result["effective_version"] = {
                "version_no": detail["version_no"],
                "intended_use_local": detail["intended_use_local"],
                "published_at": detail["published_at"],
                "sources": detail["sources"],
                "reviews": [
                    {"stage": review["stage"], "reviewer": review["reviewer"], "decision": review["decision"], "created_at": review["created_at"]}
                    for review in detail["reviews"] if review["created_at"] <= moment
                ],
            }
        result["unresolved_differences"] = self._unresolved_differences_at(profile["id"], moment)
        return result

    def _unresolved_differences_at(self, profile_id: int, moment: str) -> list[dict[str, Any]]:
        """该时点仍未闭环的差异：open，或闭环时间晚于该时点。结转链上只保留最新一项。"""
        rows = self.connection.execute(
            "SELECT d.* FROM localization_differences d JOIN localization_profile_versions v ON v.id=d.version_id "
            "WHERE v.profile_id=? AND d.created_at<=? AND (d.resolution IN ('open','carried') OR d.updated_at>?) ORDER BY d.id",
            (profile_id, moment, moment),
        ).fetchall()
        carried_sources = {int(row["carried_from_difference_id"]) for row in rows if row["carried_from_difference_id"] is not None}
        return [
            {"id": row["id"], "version_no": self.connection.execute("SELECT version_no FROM localization_profile_versions WHERE id=?", (row["version_id"],)).fetchone()[0],
             "difference_type": row["difference_type"], "topic": row["topic"], "resolution": row["resolution"]}
            for row in rows if int(row["id"]) not in carried_sources
        ]

    def _flags_open_at(self, profile_id: int, moment: str) -> list[dict[str, Any]]:
        flags = []
        for row in self.repository.flags_of_profile(profile_id):
            if row["raised_at"] > moment:
                continue
            resolved_at = row["resolved_at"]
            if row["status"] == "open" or not resolved_at or resolved_at > moment:
                flags.append({
                    "id": row["id"], "reason_type": row["reason_type"], "detail": row["detail"],
                    "raised_by": row["raised_by"], "raised_at": row["raised_at"],
                })
        return flags

    # ---------- 内部 ----------

    def _require_profile_row(self, code: str) -> sqlite3.Row:
        profile = self.repository.profile_by_code(code.strip().lower())
        if profile is None:
            raise NotFoundError("本地化档案不存在")
        return profile


def raise_change_flags(connection: sqlite3.Connection, *, reason_type: str, detail: str, raised_by: str, now: str, profile_ids: list[int], evidence_id: int | None = None) -> int:
    """供目录领域在上游变化（机构退出、产品暂停、证据被替代）时调用：挂旗并把已发布档案转入待复核。"""
    repository = LocalizationRepository(connection)
    affected = 0
    for profile_id in dict.fromkeys(profile_ids):
        profile = repository.profile_by_id(profile_id)
        if profile is None or profile["status"] == "withdrawn":
            continue
        if repository.open_flag(profile_id, reason_type, evidence_id) is not None:
            continue
        repository.raise_flag(profile_id, reason_type, detail, raised_by, now, evidence_id)
        if profile["status"] == "published":
            repository.touch_profile(profile_id, now, status="under_review")
        affected += 1
    return affected
