from __future__ import annotations

from datetime import date, timedelta


PRODUCT = {
    "code": "ablation-cath",
    "name": "海外心脏脉冲消融导管",
    "organization": "海外消融科技",
    "origin_country": "美国",
    "category": "康复设备",
    "intended_use": "用于阵发性房颤的脉冲电场消融，海外注册适用人群为成年患者",
    "risk_level": "high",
    "regulatory_status": "研究",
}


def make_site(code: str, *, site_type: str = "医院") -> dict:
    return {
        "code": code,
        "name": f"合作机构-{code}",
        "site_type": site_type,
        "region": "上海",
        "capabilities": ["cardiac-ablation"],
        "max_concurrent": 2,
    }


def _accepted_evidence(client, digest: str = "c" * 64) -> int:
    evidence = client.post("/api/catalog/evidence", json={
        "product_code": PRODUCT["code"],
        "evidence_type": "临床",
        "title": "海外多中心临床报告",
        "source_name": "Global Heart Registry",
        "source_region": "美国",
        "version": "2026.1",
        "content_digest": digest,
        "summary": {"patients": 480},
        "submitted_by": "evidence-owner",
    })
    assert evidence.status_code == 201, evidence.text
    evidence_id = evidence.json()["id"]
    reviewed = client.post(f"/api/catalog/evidence/{evidence_id}/review",
                           json={"reviewer": "ev-reviewer", "decision": "accepted", "note": "可追溯"})
    assert reviewed.status_code == 200, reviewed.text
    return evidence_id


def _prepare(client, site_code: str = "hospital-sh-1") -> int:
    product = client.post("/api/catalog/products", json=PRODUCT)
    assert product.status_code == 201, product.text
    site = client.post("/api/catalog/sites", json=make_site(site_code))
    assert site.status_code == 201, site.text
    return _accepted_evidence(client)


def _create_dossier(client, code: str = "loc-sh-1", site_code: str = "hospital-sh-1") -> dict:
    response = client.post("/api/localization/dossiers", json={
        "code": code,
        "product_code": PRODUCT["code"],
        "target_region": "上海",
        "site_code": site_code,
        "intended_use_local": "限定于心内科电生理手术中，由经过培训的术者用于成年房颤患者",
        "created_by": "biz-ops",
    })
    assert response.status_code == 201, response.text
    return response.json()


def _version_payload(evidence_id: int, **overrides) -> dict:
    payload = {
        "submitted_by": "biz-ops",
        "translation_note": "全文由医学翻译完成，保留器械英文原名并附中文注册名",
        "terminology_note": "ablation 统一译为消融，pulse field 统一译为脉冲电场",
        "clinical_scenario_note": "适用场景调整为国内三甲医院电生理导管室，术中使用三维标测",
        "evidence_ids": [evidence_id],
        "resolutions": [],
        "change_note": "",
    }
    payload.update(overrides)
    return payload


def _approve_all(client, code: str, version_no: int) -> None:
    for stage, reviewer in (("medical", "med-a"), ("compliance", "legal-a"), ("operations", "ops-a")):
        response = client.post(f"/api/localization/dossiers/{code}/versions/{version_no}/reviews/{stage}",
                               json={"reviewer": reviewer, "decision": "approved", "note": "通过"})
        assert response.status_code == 200, response.text


def test_dossier_must_bind_hospital(client):
    client.post("/api/catalog/products", json=PRODUCT)
    client.post("/api/catalog/sites", json=make_site("expo-1", site_type="展会体验点"))
    response = client.post("/api/localization/dossiers", json={
        "code": "loc-bad",
        "product_code": PRODUCT["code"],
        "target_region": "上海",
        "site_code": "expo-1",
        "intended_use_local": "不适用于展会体验点",
        "created_by": "biz-ops",
    })
    assert response.status_code == 422


def test_full_review_and_publish_flow(client):
    evidence_id = _prepare(client)
    dossier = _create_dossier(client)
    assert dossier["status"] == "drafting"

    submitted = client.post("/api/localization/dossiers/loc-sh-1/versions",
                            json=_version_payload(evidence_id))
    assert submitted.status_code == 201, submitted.text
    version = submitted.json()
    assert version["version_no"] == 1
    assert version["current_stage"] == "medical"
    assert version["evidence"][0]["evidence_version"] == "2026.1"

    # 必须依次审阅：合规不能抢在医学前面
    early = client.post("/api/localization/dossiers/loc-sh-1/versions/1/reviews/compliance",
                        json={"reviewer": "legal-a", "decision": "approved"})
    assert early.status_code == 409

    _approve_all(client, "loc-sh-1", 1)

    detail = client.get("/api/localization/dossiers/loc-sh-1").json()
    assert detail["status"] == "published"
    assert detail["current_published_version_no"] == 1

    # 未接受的证据不能作为依据
    other = client.post("/api/catalog/evidence", json={
        "product_code": PRODUCT["code"],
        "evidence_type": "性能",
        "title": "厂商内部台架报告",
        "source_name": "vendor lab",
        "source_region": "美国",
        "version": "draft-2",
        "content_digest": "d" * 64,
        "summary": {},
        "submitted_by": "owner",
    }).json()
    resubmit = client.post("/api/localization/dossiers/loc-sh-1/versions",
                           json=_version_payload(other["id"], change_note="补充台架数据"))
    assert resubmit.status_code == 409


def test_rejection_points_to_difference_and_resubmission_creates_new_version(client):
    evidence_id = _prepare(client)
    _create_dossier(client)
    client.post("/api/localization/dossiers/loc-sh-1/versions", json=_version_payload(evidence_id))

    # 医学退回：内联登记具体差异项
    rejected = client.post("/api/localization/dossiers/loc-sh-1/versions/1/reviews/medical", json={
        "reviewer": "med-a",
        "decision": "rejected",
        "note": "术语与场景调整不到位",
        "new_differences": [
            {"category": "术语校准", "title": "消融能量类型译名不统一", "detail": "pulse field 多处直译为脉冲场"},
            {"category": "临床场景", "title": "未说明国内术式差异", "detail": "缺少三维标测联用说明"},
        ],
    })
    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["version"]["status"] == "rejected"

    detail = client.get("/api/localization/dossiers/loc-sh-1").json()
    assert detail["status"] == "drafting"
    open_diffs = client.get("/api/localization/dossiers/loc-sh-1/differences?status=open").json()["items"]
    assert len(open_diffs) == 2

    # 旧决定不可覆盖
    again = client.post("/api/localization/dossiers/loc-sh-1/versions/1/reviews/medical",
                        json={"reviewer": "med-a", "decision": "approved"})
    assert again.status_code == 409

    # 补交不回应差异项则拒绝
    bad = client.post("/api/localization/dossiers/loc-sh-1/versions",
                      json=_version_payload(evidence_id, change_note="修改了译文"))
    assert bad.status_code == 409

    # 补交形成新版本 v2，逐项回应差异
    second = client.post("/api/localization/dossiers/loc-sh-1/versions", json=_version_payload(
        evidence_id,
        change_note="统一术语并补充国内术式说明",
        resolutions=[
            {"difference_id": open_diffs[0]["id"], "resolution": "全文统一为脉冲电场消融"},
            {"difference_id": open_diffs[1]["id"], "resolution": "新增三维标测联用章节"},
        ],
    ))
    assert second.status_code == 201, second.text
    assert second.json()["version_no"] == 2

    _approve_all(client, "loc-sh-1", 2)
    detail = client.get("/api/localization/dossiers/loc-sh-1").json()
    assert detail["current_published_version_no"] == 2
    # v1 及退回记录仍可追溯
    v1 = [v for v in detail["versions"] if v["version_no"] == 1][0]
    assert v1["status"] == "rejected"
    assert v1["reviews"][0]["decision"] == "rejected"
    assert v1["reviews"][0]["difference_ids"]
    resolved = client.get("/api/localization/dossiers/loc-sh-1/differences").json()["items"]
    assert {item["status"] for item in resolved} == {"resolved"}


def test_site_withdrawal_flags_dossier_and_freezes_review(client):
    evidence_id = _prepare(client)
    _create_dossier(client)
    client.post("/api/localization/dossiers/loc-sh-1/versions", json=_version_payload(evidence_id))
    _approve_all(client, "loc-sh-1", 1)
    signed = client.post("/api/localization/dossiers/loc-sh-1/cooperations", json={
        "reference": "MOU-2026-001",
        "signed_by": "hospital-sh-1",
        "note": "试点合作",
    })
    assert signed.status_code == 201, signed.text
    assert signed.json()["version_no"] == 1

    event = client.post("/api/localization/events/site-withdrawal?site_code=hospital-sh-1", json={
        "reason_type": "site_withdrawal",
        "actor": "biz-ops",
        "note": "医院战略调整退出合作",
    })
    assert event.status_code == 200, event.text
    assert event.json()["affected"] == ["loc-sh-1"]

    detail = client.get("/api/localization/dossiers/loc-sh-1").json()
    assert detail["status"] == "review_pending"
    assert detail["review_trigger"] == "site_withdrawal"

    # 待复核期间不能签署新合作，也不能推进审阅或补交新版本
    blocked_sign = client.post("/api/localization/dossiers/loc-sh-1/cooperations", json={
        "reference": "MOU-2026-002",
        "signed_by": "hospital-sh-1",
    })
    assert blocked_sign.status_code == 409
    blocked_submit = client.post("/api/localization/dossiers/loc-sh-1/versions",
                                 json=_version_payload(evidence_id, change_note="机构退出后不应直接补交"))
    assert blocked_submit.status_code == 409

    # 已签署合作引用的旧版本仍可追溯
    cooperations = detail["cooperations"]
    assert cooperations[0]["reference"] == "MOU-2026-001"
    assert cooperations[0]["version_no"] == 1

    # 完成复核后档案恢复已发布
    cleared = client.post("/api/localization/dossiers/loc-sh-1/clear-review", json={
        "reason_type": "manual",
        "actor": "biz-ops",
        "note": "已与接替机构确认安排",
    })
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["status"] == "published"


def test_superseded_evidence_blocks_publish_and_flags_published_dossier(client):
    evidence_id = _prepare(client)
    _create_dossier(client)
    client.post("/api/localization/dossiers/loc-sh-1/versions", json=_version_payload(evidence_id))
    _approve_all(client, "loc-sh-1", 1)

    notified = client.post(f"/api/localization/events/evidence/{evidence_id}/superseded", json={
        "reason_type": "evidence_superseded",
        "actor": "evidence-owner",
        "note": "上游发布 2027.1 版临床报告",
    })
    assert notified.status_code == 200, notified.text
    assert notified.json()["affected"] == ["loc-sh-1"]
    detail = client.get("/api/localization/dossiers/loc-sh-1").json()
    assert detail["status"] == "review_pending"
    assert detail["review_trigger"] == "evidence_superseded"

    # 用新证据补交新版本，审阅通过后重新发布
    new_evidence_id = _accepted_evidence(client, digest="e" * 64)
    submitted = client.post("/api/localization/dossiers/loc-sh-1/versions", json=_version_payload(
        new_evidence_id, change_note="替换为 2027.1 版上游临床报告"))
    assert submitted.status_code == 201, submitted.text
    _approve_all(client, "loc-sh-1", 2)
    detail = client.get("/api/localization/dossiers/loc-sh-1").json()
    assert detail["status"] == "published"
    assert detail["current_published_version_no"] == 2


def test_publish_guard_rechecks_evidence_status(client):
    evidence_id = _prepare(client)
    _create_dossier(client)
    client.post("/api/localization/dossiers/loc-sh-1/versions", json=_version_payload(evidence_id))
    client.post("/api/localization/dossiers/loc-sh-1/versions/1/reviews/medical",
                json={"reviewer": "med-a", "decision": "approved"})
    client.post("/api/localization/dossiers/loc-sh-1/versions/1/reviews/compliance",
                json={"reviewer": "legal-a", "decision": "approved"})
    # 运营签发前证据被替代（档案尚未进入待复核也不能放过）
    client.post(f"/api/localization/events/evidence/{evidence_id}/superseded", json={
        "reason_type": "evidence_superseded",
        "actor": "evidence-owner",
        "note": "上游更新",
    })
    blocked = client.post("/api/localization/dossiers/loc-sh-1/versions/1/reviews/operations",
                          json={"reviewer": "ops-a", "decision": "approved"})
    assert blocked.status_code == 409


def test_product_suspension_flags_dossier(client):
    evidence_id = _prepare(client)
    _create_dossier(client)
    client.post("/api/localization/dossiers/loc-sh-1/versions", json=_version_payload(evidence_id))
    _approve_all(client, "loc-sh-1", 1)
    response = client.post(f"/api/localization/events/product-suspension?product_code={PRODUCT['code']}", json={
        "reason_type": "product_suspension",
        "actor": "biz-ops",
        "note": "产品全球暂停供货",
    })
    assert response.status_code == 200
    assert response.json()["affected"] == ["loc-sh-1"]
    detail = client.get("/api/localization/dossiers/loc-sh-1").json()
    assert detail["status"] == "review_pending"
    assert detail["product_status"] == "暂停"


def test_trace_as_of_date(client):
    evidence_id = _prepare(client)
    _create_dossier(client)
    client.post("/api/localization/dossiers/loc-sh-1/versions", json=_version_payload(evidence_id))
    # 医学退回，产生一个未解决差异
    client.post("/api/localization/dossiers/loc-sh-1/versions/1/reviews/medical", json={
        "reviewer": "med-a",
        "decision": "rejected",
        "note": "术语需要统一",
        "new_differences": [{"category": "翻译", "title": "译名不统一", "detail": "见正文"}],
    })

    # 建档之前的日期查不到该档案；发布前的当天状态为起草、无有效版本
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    before = client.get(f"/api/localization/trace?date={yesterday}").json()
    assert [item for item in before["items"] if item["code"] == "loc-sh-1"] == []
    drafting_today = client.get(f"/api/localization/trace?date={date.today().isoformat()}").json()
    entry = [item for item in drafting_today["items"] if item["code"] == "loc-sh-1"][0]
    assert entry["status_as_of"] == "drafting"
    assert entry["effective_version"] is None
    assert len(entry["open_differences"]) == 1

    open_diffs = client.get("/api/localization/dossiers/loc-sh-1/differences?status=open").json()["items"]
    client.post("/api/localization/dossiers/loc-sh-1/versions", json=_version_payload(
        evidence_id,
        change_note="统一译名",
        resolutions=[{"difference_id": open_diffs[0]["id"], "resolution": "全文统一"}],
    ))
    _approve_all(client, "loc-sh-1", 2)
    client.post("/api/localization/dossiers/loc-sh-1/cooperations", json={
        "reference": "MOU-TRACE-1",
        "signed_by": "hospital-sh-1",
    })

    today = client.get(f"/api/localization/trace?date={date.today().isoformat()}").json()
    entry = [item for item in today["items"] if item["code"] == "loc-sh-1"][0]
    assert entry["status_as_of"] == "published"
    assert entry["effective_version"]["version_no"] == 2
    assert entry["effective_version"]["reviews"][0]["stage"] == "medical"
    assert entry["open_differences"] == []
    assert entry["cooperations"][0]["reference"] == "MOU-TRACE-1"
    assert entry["cooperations"][0]["status"] == "active"

    # 触发待复核后，按今天查询应反映 review_pending，但有效版本仍是 v2
    client.post("/api/localization/events/site-withdrawal?site_code=hospital-sh-1", json={
        "reason_type": "site_withdrawal",
        "actor": "biz-ops",
        "note": "医院退出",
    })
    pending = client.get(f"/api/localization/trace?date={date.today().isoformat()}").json()
    entry = [item for item in pending["items"] if item["code"] == "loc-sh-1"][0]
    assert entry["status_as_of"] == "review_pending"
    assert entry["effective_version"]["version_no"] == 2
