from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import UTC, datetime
from typing import Any

from app.catalog.repository import CatalogRepository
from app.core.clock import Clock, SystemClock, to_storage
from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.database import get_connection, transaction
from app.localization.repository import LocalizationRepository


STAGES = ("medical", "compliance", "operations")
STAGE_LABELS = {"medical": "医学", "compliance": "合规", "operations": "运营"}

EVENT_STATUS = {
    "dossier_created": "drafting",
    "version_submitted": "reviewing",
    "review_approved": "reviewing",
    "version_rejected": "drafting",
    "version_published": "published",
    "review_triggered": "review_pending",
    "dossier_closed": "closed",
}


def digest(value: Any) -> str:
    text = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode()).hexdigest()


class LocalizationService:
    """管理本地化档案、不可变版本、三级审阅、差异项、触发复核与合作引用。"""

    def __init__(self, connection: sqlite3.Connection | None = None, clock: Clock | None = None) -> None:
        self.connection = connection or get_connection()
        self.clock = clock or SystemClock()
        self.repository = LocalizationRepository(self.connection)

    # -- 档案 ---------------------------------------------------------------

    def create_dossier(self, data: dict[str, Any]) -> dict[str, Any]:
        code = data["code"].strip().lower()
        now = to_storage(self.clock.now())
        with transaction(immediate=True) as connection:
            repository = LocalizationRepository(connection)
            if repository.dossier_by_code(code):
                raise ConflictError("本地化档案编码已存在")
            product = CatalogRepository(connection).product_by_code(data["product_code"])
            if product is None:
                raise NotFoundError("健康创新产品不存在")
            site = CatalogRepository(connection).site_by_code(data["site_code"])
            if site is None:
                raise NotFoundError("合作机构场地不存在")
            if site["site_type"] != "医院":
                raise ValidationError("本地化档案只能绑定医院类合作机构")
            dossier = repository.create_dossier(
                code=code, product_id=product["id"], site_id=site["id"],
                target_region=data["target_region"].strip(),
                intended_use_local=data["intended_use_local"].strip(),
                created_by=data["created_by"].strip(), now=now,
            )
            repository.add_event(dossier_id=dossier["id"], event_type="dossier_created",
                                 detail={"product_code": product["code"], "site_code": site["code"]},
                                 actor=data["created_by"].strip(), now=now)
            return self._load_detail(connection, dossier)

    def update_dossier(self, code: str, intended_use_local: str) -> dict[str, Any]:
        now = to_storage(self.clock.now())
        with transaction(immediate=True) as connection:
            repository = LocalizationRepository(connection)
            dossier = self._require_dossier(repository, code)
            in_review = repository.latest_version(dossier["id"])
            if in_review and in_review["status"] == "in_review":
                raise ConflictError("存在审阅中的版本，不能修改档案预期用途")
            if dossier["status"] not in ("drafting", "review_pending"):
                raise ConflictError("只有起草或待复核档案可以修改预期用途")
            dossier = repository.update_dossier(dossier["id"], {"intended_use_local": intended_use_local.strip()}, now)
            repository.add_event(dossier_id=dossier["id"], event_type="dossier_updated",
                                 detail={"intended_use_local": intended_use_local.strip()}, actor="", now=now)
            return self._load_detail(connection, dossier)

    def list_dossiers(self, status: str | None, product_code: str | None, target_region: str | None, limit: int) -> list[dict[str, Any]]:
        return self.repository.list_dossiers(status=status, product_code=product_code, target_region=target_region, limit=limit)

    def get_dossier(self, code: str) -> dict[str, Any]:
        dossier = self.repository.dossier_by_code(code)
        if dossier is None:
            raise NotFoundError("本地化档案不存在")
        return self._load_detail(self.connection, dossier)

    def close_dossier(self, code: str, actor: str, reason: str) -> dict[str, Any]:
        now = to_storage(self.clock.now())
        with transaction(immediate=True) as connection:
            repository = LocalizationRepository(connection)
            dossier = self._require_dossier(repository, code)
            if dossier["status"] == "closed":
                raise ConflictError("档案已经关闭")
            dossier = repository.update_dossier(dossier["id"], {"status": "closed", "review_trigger": ""}, now)
            repository.add_event(dossier_id=dossier["id"], event_type="dossier_closed",
                                 detail={"reason": reason}, actor=actor, now=now)
            return self._load_detail(connection, dossier)

    # -- 差异项 -------------------------------------------------------------

    def add_difference(self, code: str, data: dict[str, Any]) -> dict[str, Any]:
        now = to_storage(self.clock.now())
        with transaction(immediate=True) as connection:
            repository = LocalizationRepository(connection)
            dossier = self._require_dossier(repository, code)
            if dossier["status"] == "closed":
                raise ConflictError("档案已关闭，不能登记差异项")
            evidence_id = data.get("evidence_id")
            if evidence_id is not None:
                evidence = CatalogRepository(connection).evidence_by_id(evidence_id)
                if evidence is None or evidence["product_id"] != dossier["product_id"]:
                    raise ValidationError("差异项引用的证据不存在或不属于该档案产品")
            latest = repository.latest_version(dossier["id"])
            opened_in = latest["id"] if latest and latest["status"] == "in_review" else None
            seq = repository.next_difference_seq(dossier["id"])
            difference = repository.create_difference(
                dossier_id=dossier["id"], seq=seq, category=data["category"],
                title=data["title"].strip(), detail=data.get("detail", ""), evidence_id=evidence_id,
                opened_by=data["opened_by"].strip(), opened_in_version_id=opened_in, now=now,
            )
            repository.add_event(dossier_id=dossier["id"], event_type="difference_opened",
                                 detail={"difference_id": difference["id"], "seq": seq, "category": data["category"], "title": difference["title"]},
                                 actor=data["opened_by"].strip(), now=now)
            return difference

    def list_differences(self, code: str, status: str | None) -> list[dict[str, Any]]:
        dossier = self.repository.dossier_by_code(code)
        if dossier is None:
            raise NotFoundError("本地化档案不存在")
        return self.repository.list_differences(dossier["id"], status)

    # -- 版本提交（补交产生新版本，永不覆盖旧决定） ---------------------------

    def submit_version(self, code: str, data: dict[str, Any]) -> dict[str, Any]:
        now = to_storage(self.clock.now())
        with transaction(immediate=True) as connection:
            repository = LocalizationRepository(connection)
            catalog = CatalogRepository(connection)
            dossier = self._require_dossier(repository, code)
            if dossier["status"] == "closed":
                raise ConflictError("档案已关闭，不能提交版本")
            latest = repository.latest_version(dossier["id"])
            if latest and latest["status"] == "in_review":
                raise ConflictError("已有审阅中的版本，请等待审阅结束")
            if dossier["status"] == "review_pending" and dossier["review_trigger"] != "evidence_superseded":
                raise ConflictError("档案因机构或产品变化处于待复核状态，请先完成复核处理再补交版本")
            version_no = repository.next_version_no(dossier["id"])
            if version_no > 1 and not data.get("change_note", "").strip():
                raise ValidationError("补交版本必须说明相对上一版的变更")
            evidence_ids = list(dict.fromkeys(data["evidence_ids"]))
            evidence_rows: list[tuple[int, str, str, str]] = []
            for evidence_id in evidence_ids:
                evidence = catalog.evidence_by_id(evidence_id)
                if evidence is None or evidence["product_id"] != dossier["product_id"]:
                    raise ValidationError("来源证据不存在或不属于该档案产品", context={"evidence_id": evidence_id})
                if evidence["status"] != "accepted":
                    raise ConflictError(f"证据《{evidence['title']}》当前状态为 {evidence['status']}，不能作为本地化依据",
                                        context={"evidence_id": evidence_id})
                evidence_rows.append((evidence_id, evidence["version"], evidence["content_digest"], evidence["status"]))
            resolutions = data.get("resolutions", [])
            open_differences = repository.open_differences(dossier["id"])
            resolved_ids = {item["difference_id"] for item in resolutions}
            unknown = resolved_ids - {item["id"] for item in open_differences}
            if unknown:
                raise ValidationError("补交说明只能指向该档案尚未解决的差异项", context={"difference_ids": sorted(unknown)})
            if open_differences and resolved_ids != {item["id"] for item in open_differences}:
                raise ConflictError("补交前必须逐项回应全部未解决差异")
            content = {
                "intended_use_local": dossier["intended_use_local"],
                "translation_note": data["translation_note"],
                "terminology_note": data["terminology_note"],
                "clinical_scenario_note": data["clinical_scenario_note"],
                "change_note": data.get("change_note", ""),
                "extra": data.get("extra", {}),
                "evidence_ids": evidence_ids,
            }
            version = repository.create_version(
                dossier_id=dossier["id"], version_no=version_no, content=content,
                content_digest=digest(content), submitted_by=data["submitted_by"].strip(), now=now,
            )
            repository.add_version_evidence(version["id"], evidence_rows)
            for item in resolutions:
                repository.resolve_difference(item["difference_id"], resolution=item["resolution"].strip(),
                                              version_id=version["id"], now=now)
            repository.update_dossier(dossier["id"], {"status": "reviewing", "review_trigger": ""}, now)
            repository.add_event(dossier_id=dossier["id"], event_type="version_submitted",
                                 detail={"version_no": version_no, "evidence_ids": evidence_ids,
                                         "resolved_difference_ids": sorted(resolved_ids)},
                                 actor=data["submitted_by"].strip(), now=now)
            return self._version_detail(repository, version)

    # -- 三级依次审阅 --------------------------------------------------------

    def review_version(self, code: str, version_no: int, stage: str, data: dict[str, Any]) -> dict[str, Any]:
        if stage not in STAGES:
            raise ValidationError("审阅环节必须是 medical、compliance 或 operations")
        now = to_storage(self.clock.now())
        with transaction(immediate=True) as connection:
            repository = LocalizationRepository(connection)
            dossier = self._require_dossier(repository, code)
            version = repository.version_by_no(dossier["id"], version_no)
            if version is None:
                raise NotFoundError("本地化版本不存在")
            if version["status"] != "in_review":
                raise ConflictError("该版本已经结束审阅，补交请提交新版本")
            if dossier["status"] == "review_pending":
                raise ConflictError("档案处于待复核状态，须先完成复核处理后再继续审阅")
            if version["current_stage"] != stage:
                raise ConflictError("三个角色须依次审阅：医学 → 合规 → 运营",
                                    context={"current_stage": STAGE_LABELS[version["current_stage"]]})
            reviewer = data["reviewer"].strip()
            note = data.get("note", "")
            difference_ids = list(dict.fromkeys(data.get("difference_ids", [])))
            if data["decision"] == "rejected":
                if len(note.strip()) < 4:
                    raise ValidationError("退回时需要说明可执行的原因")
                for item in data.get("new_differences", []):
                    seq = repository.next_difference_seq(dossier["id"])
                    created = repository.create_difference(
                        dossier_id=dossier["id"], seq=seq, category=item["category"],
                        title=item["title"].strip(), detail=item.get("detail", ""), evidence_id=None,
                        opened_by=reviewer, opened_in_version_id=version["id"], now=now,
                    )
                    difference_ids.append(created["id"])
                if not difference_ids:
                    raise ValidationError("退回必须指向具体差异项")
                open_ids = {item["id"] for item in repository.open_differences(dossier["id"])}
                invalid = set(difference_ids) - open_ids
                if invalid:
                    raise ValidationError("退回引用的差异项不存在或已解决", context={"difference_ids": sorted(invalid)})
                review = repository.add_review(version_id=version["id"], stage=stage, decision="rejected",
                                               reviewer=reviewer, note=note.strip(), difference_ids=difference_ids, now=now)
                connection.execute(
                    "UPDATE localization_versions SET status='rejected' WHERE id=?", (version["id"],),
                )
                repository.update_dossier(dossier["id"], {"status": "drafting"}, now)
                repository.add_event(dossier_id=dossier["id"], event_type="version_rejected",
                                     detail={"version_no": version_no, "stage": stage,
                                             "difference_ids": difference_ids, "note": note.strip()},
                                     actor=reviewer, now=now)
                version = repository.version_by_id(version["id"])
                return {"review": review, "version": self._version_detail(repository, version)}
            # 通过
            if dossier["status"] != "reviewing":
                raise ConflictError("档案处于待复核状态，不能继续通过审阅；请先完成复核或退回该版本")
            review = repository.add_review(version_id=version["id"], stage=stage, decision="approved",
                                           reviewer=reviewer, note=note.strip(), difference_ids=[], now=now)
            if stage == "operations":
                self._ensure_evidence_still_valid(repository, version)
                connection.execute(
                    "UPDATE localization_versions SET status='published',current_stage='done',published_at=? WHERE id=?",
                    (now, version["id"]),
                )
                repository.update_dossier(dossier["id"], {"status": "published", "review_trigger": "",
                                                          "current_version_id": version["id"]}, now)
                repository.add_event(dossier_id=dossier["id"], event_type="version_published",
                                     detail={"version_no": version_no}, actor=reviewer, now=now)
            else:
                next_stage = STAGES[STAGES.index(stage) + 1]
                connection.execute(
                    "UPDATE localization_versions SET current_stage=? WHERE id=?", (next_stage, version["id"]),
                )
                repository.update_dossier(dossier["id"], {"updated_at": now}, now)
                repository.add_event(dossier_id=dossier["id"], event_type="review_approved",
                                     detail={"version_no": version_no, "stage": stage, "next_stage": next_stage},
                                     actor=reviewer, now=now)
            version = repository.version_by_id(version["id"])
            return {"review": review, "version": self._version_detail(repository, version)}

    # -- 待复核触发 ----------------------------------------------------------

    def trigger_review(self, code: str, reason_type: str, actor: str, note: str) -> dict[str, Any]:
        now = to_storage(self.clock.now())
        with transaction(immediate=True) as connection:
            repository = LocalizationRepository(connection)
            dossier = self._require_dossier(repository, code)
            return self._flag_pending(repository, dossier, reason_type, actor, note, now)

    def clear_review_pending(self, code: str, actor: str, note: str) -> dict[str, Any]:
        now = to_storage(self.clock.now())
        with transaction(immediate=True) as connection:
            repository = LocalizationRepository(connection)
            dossier = self._require_dossier(repository, code)
            if dossier["status"] != "review_pending":
                raise ConflictError("档案当前不在待复核状态")
            target = "reviewing" if repository.latest_version(dossier["id"]) and repository.latest_version(dossier["id"])["status"] == "in_review" else (
                "published" if dossier["current_version_id"] else "drafting"
            )
            dossier = repository.update_dossier(dossier["id"], {"status": target, "review_trigger": ""}, now)
            repository.add_event(dossier_id=dossier["id"], event_type="review_cleared",
                                 detail={"note": note, "restored_status": target}, actor=actor, now=now)
            return self._load_detail(connection, dossier)

    def notify_site_withdrawal(self, site_code: str, actor: str, note: str, *, close_site: bool = True) -> dict[str, Any]:
        now = to_storage(self.clock.now())
        affected: list[dict[str, Any]] = []
        with transaction(immediate=True) as connection:
            repository = LocalizationRepository(connection)
            catalog = CatalogRepository(connection)
            site = catalog.site_by_code(site_code)
            if site is None:
                raise NotFoundError("合作机构场地不存在")
            if close_site and site["status"] != "closed":
                catalog.update_site(site["id"], {"status": "closed"}, now)
            rows = connection.execute(
                "SELECT d.* FROM localization_dossiers d WHERE d.site_id=? AND d.status IN ('drafting','reviewing','published') ORDER BY d.id",
                (site["id"],),
            ).fetchall()
            for row in rows:
                affected.append(self._flag_pending(repository, dict(row), "site_withdrawal", actor, note, now))
        return {"site_code": site_code, "affected": [item["code"] for item in affected]}

    def notify_product_suspension(self, product_code: str, actor: str, note: str, *, suspend_product: bool = True) -> dict[str, Any]:
        now = to_storage(self.clock.now())
        affected: list[dict[str, Any]] = []
        with transaction(immediate=True) as connection:
            repository = LocalizationRepository(connection)
            catalog = CatalogRepository(connection)
            product = catalog.product_by_code(product_code)
            if product is None:
                raise NotFoundError("健康创新产品不存在")
            if suspend_product and product["regulatory_status"] != "暂停":
                catalog.update_product(product["id"], {"regulatory_status": "暂停", "active": 0}, now)
            rows = connection.execute(
                "SELECT * FROM localization_dossiers WHERE product_id=? AND status IN ('drafting','reviewing','published') ORDER BY id",
                (product["id"],),
            ).fetchall()
            for row in rows:
                affected.append(self._flag_pending(repository, dict(row), "product_suspension", actor, note, now))
        return {"product_code": product_code, "affected": [item["code"] for item in affected]}

    def notify_evidence_superseded(self, evidence_id: int, actor: str, note: str) -> dict[str, Any]:
        now = to_storage(self.clock.now())
        with transaction(immediate=True) as connection:
            repository = LocalizationRepository(connection)
            evidence = CatalogRepository(connection).evidence_by_id(evidence_id)
            if evidence is None:
                raise NotFoundError("证据材料不存在")
            connection.execute("UPDATE evidence_documents SET status='superseded' WHERE id=?", (evidence_id,))
            rows = connection.execute(
                "SELECT DISTINCT d.* FROM localization_dossiers d "
                "JOIN localization_versions dv ON dv.dossier_id=d.id "
                "JOIN localization_version_evidence ve ON ve.version_id=dv.id "
                "WHERE ve.evidence_id=? AND d.status IN ('drafting','reviewing','published') ORDER BY d.id",
                (evidence_id,),
            ).fetchall()
            affected = [self._flag_pending(repository, dict(row), "evidence_superseded", actor, note, now) for row in rows]
        return {"evidence_id": evidence_id, "affected": [item["code"] for item in affected]}

    def scan_impacts(self, actor: str) -> dict[str, Any]:
        """兜底扫描：当前有效版本引用了非 accepted 证据的已发布档案，自动转入待复核。"""
        now = to_storage(self.clock.now())
        affected: list[str] = []
        with transaction(immediate=True) as connection:
            repository = LocalizationRepository(connection)
            rows = connection.execute(
                "SELECT DISTINCT d.* FROM localization_dossiers d "
                "JOIN localization_versions dv ON dv.id=d.current_version_id "
                "JOIN localization_version_evidence ve ON ve.version_id=dv.id "
                "JOIN evidence_documents e ON e.id=ve.evidence_id "
                "WHERE d.status='published' AND e.status!='accepted' ORDER BY d.id"
            ).fetchall()
            for row in rows:
                dossier = self._flag_pending(repository, dict(row), "evidence_superseded", actor,
                                             "扫描发现当前有效版本引用的来源证据已不再是 accepted", now)
                affected.append(dossier["code"])
        return {"affected": affected}

    # -- 合作引用 ------------------------------------------------------------

    def sign_cooperation(self, code: str, data: dict[str, Any]) -> dict[str, Any]:
        now = to_storage(self.clock.now())
        with transaction(immediate=True) as connection:
            repository = LocalizationRepository(connection)
            dossier = self._require_dossier(repository, code)
            if dossier["status"] != "published":
                raise ConflictError("只有处于已发布状态的档案可以签署合作；待复核档案须先完成复核")
            if repository.cooperation_by_reference(data["reference"]):
                raise ConflictError("合作引用编号已存在")
            if data.get("version_no") is None:
                version = repository.current_published_version(dossier["id"])
                if version is None:
                    raise ConflictError("档案尚无已发布版本，不能签署合作")
            else:
                version = repository.version_by_no(dossier["id"], data["version_no"])
                if version is None:
                    raise NotFoundError("本地化版本不存在")
                if version["status"] != "published":
                    raise ConflictError("合作只能引用已发布版本")
            cooperation = repository.sign_cooperation(
                dossier_id=dossier["id"], reference=data["reference"], version_id=version["id"],
                signed_by=data["signed_by"].strip(), note=data.get("note", ""), now=now,
            )
            repository.add_event(dossier_id=dossier["id"], event_type="cooperation_signed",
                                 detail={"reference": data["reference"], "version_no": version["version_no"]},
                                 actor=data["signed_by"].strip(), now=now)
            return {**cooperation, "version_no": version["version_no"]}

    def withdraw_cooperation(self, reference: str, actor: str, reason: str) -> dict[str, Any]:
        now = to_storage(self.clock.now())
        with transaction(immediate=True) as connection:
            repository = LocalizationRepository(connection)
            cooperation = repository.cooperation_by_reference(reference)
            if cooperation is None:
                raise NotFoundError("合作引用不存在")
            if cooperation["status"] != "active":
                raise ConflictError("合作引用已撤销")
            repository.withdraw_cooperation(cooperation["id"], now)
            repository.add_event(dossier_id=cooperation["dossier_id"], event_type="cooperation_withdrawn",
                                 detail={"reference": reference, "reason": reason}, actor=actor, now=now)
            return {**repository.cooperation_by_reference(reference), "reason": reason}

    # -- 按日期追溯 ----------------------------------------------------------

    def as_of(self, date_text: str, product_code: str | None, site_code: str | None) -> dict[str, Any]:
        cutoff = self._day_cutoff(date_text)
        cutoff_text = to_storage(cutoff)
        connection = self.connection
        repository = LocalizationRepository(connection)
        dossiers = repository.list_dossiers(status=None, product_code=product_code,
                                            target_region=None, limit=500)
        if site_code:
            site = CatalogRepository(connection).site_by_code(site_code)
            if site is None:
                raise NotFoundError("合作机构场地不存在")
            dossiers = [item for item in dossiers if item["site_code"] == site_code]
        # 只保留该日期结束前已经建档的档案
        dossiers = [item for item in dossiers if item["created_at"] <= cutoff_text]
        results = []
        for item in dossiers:
            dossier_id = item["id"]
            version = connection.execute(
                "SELECT * FROM localization_versions WHERE dossier_id=? AND status='published' AND published_at<=?"
                " ORDER BY version_no DESC LIMIT 1",
                (dossier_id, cutoff_text),
            ).fetchone()
            differences = [dict(row) for row in connection.execute(
                "SELECT id,seq,category,title,detail,status,resolution,opened_by,created_at,resolved_at "
                "FROM localization_differences WHERE dossier_id=? AND created_at<=?"
                " AND (resolved_at IS NULL OR resolved_at>?) ORDER BY id",
                (dossier_id, cutoff_text, cutoff_text),
            ).fetchall()]
            cooperations = [dict(row) for row in connection.execute(
                "SELECT c.reference,c.version_id,c.signed_by,c.signed_at,c.withdrawn_at,dv.version_no "
                "FROM localization_cooperations c JOIN localization_versions dv ON dv.id=c.version_id "
                "WHERE c.dossier_id=? AND c.signed_at<=? ORDER BY c.id",
                (dossier_id, cutoff_text),
            ).fetchall()]
            for cooperation in cooperations:
                cooperation["status"] = "withdrawn" if cooperation.get("withdrawn_at") and cooperation["withdrawn_at"] <= cutoff_text else "active"
            status_as_of = self._status_as_of(connection, dossier_id, cutoff_text)
            entry = {
                "code": item["code"],
                "product_code": item["product_code"],
                "site_code": item["site_code"],
                "target_region": item["target_region"],
                "status_as_of": status_as_of,
                "effective_version": None,
                "open_differences": differences,
                "cooperations": cooperations,
            }
            if version is not None:
                trace_reviews = []
                for review in repository.reviews_of_version(version["id"]):
                    review["difference_ids"] = json.loads(review.pop("difference_ids_json"))
                    trace_reviews.append(review)
                entry["effective_version"] = {
                    "version_no": version["version_no"],
                    "published_at": version["published_at"],
                    "content": json.loads(version["content_json"]),
                    "content_digest": version["content_digest"],
                    "evidence": repository.version_evidence(version["id"]),
                    "reviews": trace_reviews,
                }
            results.append(entry)
        return {"as_of": date_text, "cutoff_at": cutoff_text, "items": results}

    # -- 内部辅助 ------------------------------------------------------------

    def _flag_pending(self, repository: LocalizationRepository, dossier: dict[str, Any], reason_type: str, actor: str, note: str, now: str) -> dict[str, Any]:
        if dossier["status"] == "closed":
            return dossier
        previous_status = dossier["status"]
        if previous_status == "review_pending":
            trigger = dossier["review_trigger"] or reason_type
            dossier = repository.update_dossier(dossier["id"], {"review_trigger": trigger}, now)
        else:
            dossier = repository.update_dossier(dossier["id"], {"status": "review_pending", "review_trigger": reason_type}, now)
        repository.add_event(dossier_id=dossier["id"], event_type="review_triggered",
                             detail={"reason_type": reason_type, "note": note, "previous_status": previous_status},
                             actor=actor, now=now)
        return dossier

    @staticmethod
    def _ensure_evidence_still_valid(repository: LocalizationRepository, version: dict[str, Any]) -> None:
        # 快照记录的是提交时状态，发布前重新核对来源证据的当前状态
        rows = repository.connection.execute(
            "SELECT e.id FROM localization_version_evidence ve JOIN evidence_documents e ON e.id=ve.evidence_id "
            "WHERE ve.version_id=? AND e.status!='accepted'",
            (version["id"],),
        ).fetchall()
        if rows:
            raise ConflictError(
                "来源证据状态已变化，档案须先完成待复核，再补交新版本",
                context={"evidence_ids": [int(row["id"]) for row in rows]},
            )

    def _status_as_of(self, connection: sqlite3.Connection, dossier_id: int, cutoff_text: str) -> str:
        status = "drafting"
        rows = connection.execute(
            "SELECT event_type,created_at FROM localization_events WHERE dossier_id=? AND created_at<=? ORDER BY id",
            (dossier_id, cutoff_text),
        ).fetchall()
        for row in rows:
            event_type = row["event_type"]
            if event_type in EVENT_STATUS:
                status = EVENT_STATUS[event_type]
            elif event_type == "review_cleared":
                published = connection.execute(
                    "SELECT 1 FROM localization_versions WHERE dossier_id=? AND status='published' AND published_at<=? LIMIT 1",
                    (dossier_id, row["created_at"]),
                ).fetchone()
                if published:
                    status = "published"
                else:
                    in_review = connection.execute(
                        "SELECT 1 FROM localization_versions WHERE dossier_id=? AND status='in_review' AND submitted_at<=? LIMIT 1",
                        (dossier_id, row["created_at"]),
                    ).fetchone()
                    status = "reviewing" if in_review else "drafting"
        return status

    @staticmethod
    def _day_cutoff(date_text: str) -> datetime:
        try:
            day = datetime.strptime(date_text, "%Y-%m-%d")
        except ValueError as exc:
            raise ValidationError("日期格式必须为 YYYY-MM-DD") from exc
        return day.replace(hour=23, minute=59, second=59, tzinfo=UTC)

    @staticmethod
    def _require_dossier(repository: LocalizationRepository, code: str) -> dict[str, Any]:
        dossier = repository.dossier_by_code(code)
        if dossier is None:
            raise NotFoundError("本地化档案不存在")
        return dossier

    def _version_detail(self, repository: LocalizationRepository, version: dict[str, Any]) -> dict[str, Any]:
        detail = dict(version)
        detail["content"] = json.loads(version["content_json"])
        del detail["content_json"]
        detail["evidence"] = repository.version_evidence(version["id"])
        reviews = []
        for review in repository.reviews_of_version(version["id"]):
            review["difference_ids"] = json.loads(review.pop("difference_ids_json"))
            reviews.append(review)
        detail["reviews"] = reviews
        return detail

    def _load_detail(self, connection: sqlite3.Connection, dossier: dict[str, Any]) -> dict[str, Any]:
        repository = LocalizationRepository(connection)
        result = dict(dossier)
        versions = []
        for version in repository.versions_of_dossier(dossier["id"]):
            versions.append(self._version_detail(repository, version))
        result["versions"] = versions
        result["differences"] = repository.list_differences(dossier["id"])
        result["cooperations"] = repository.list_cooperations(dossier["id"])
        current = repository.current_published_version(dossier["id"])
        result["current_published_version_no"] = current["version_no"] if current else None
        return result
