from __future__ import annotations


PRODUCT = {
    "code": "ablation-x",
    "name": "海外脉冲心脏消融设备",
    "organization": "海外消融科技",
    "origin_country": "美国",
    "category": "康复设备",
    "intended_use": "用于心律失常患者的导管消融治疗，具体适应人群以本地版本为准",
    "risk_level": "high",
    "regulatory_status": "研究",
}

HOSPITAL = {
    "code": "hospital-east",
    "name": "华东心血管医院",
    "site_type": "医院",
    "region": "上海",
    "capabilities": ["cardiac-ablation"],
    "max_concurrent": 2,
}

EXPO = {
    "code": "expo-booth-1",
    "name": "数贸会展位",
    "site_type": "展会体验点",
    "region": "杭州",
    "capabilities": [],
    "max_concurrent": 1,
}


def _setup_product_and_hospital(client, product=PRODUCT, site=HOSPITAL):
    assert client.post("/api/catalog/products", json=product).status_code == 201
    assert client.post("/api/catalog/sites", json=site).status_code == 201


def _accepted_evidence(client, *, version: str = "2025.1", digest_extra: str = "", replaces=None):
    payload = {
        "product_code": PRODUCT["code"],
        "evidence_type": "临床",
        "title": f"全球临床证据 {version}",
        "source_name": "海外多中心注册研究",
        "source_region": "美国",
        "version": version,
        "content_digest": ("e" + digest_extra).ljust(64, "0")[:64],
        "summary": {"patients": 480},
        "submitted_by": "global-evidence-team",
    }
    if replaces is not None:
        payload["replaces_evidence_id"] = replaces
    created = client.post("/api/catalog/evidence", json=payload)
    assert created.status_code == 201, created.text
    evidence = created.json()
    reviewed = client.post(f"/api/catalog/evidence/{evidence['id']}/review", json={
        "reviewer": "evidence-reviewer", "decision": "accepted", "note": "来源与统计口径可追溯",
    })
    assert reviewed.status_code == 200
    return evidence


def _create_profile(client, code="loc-ablation-east"):
    response = client.post("/api/localization/profiles", json={
        "code": code,
        "product_code": PRODUCT["code"],
        "target_region": "上海",
        "site_code": HOSPITAL["code"],
        "intended_use_local": "适用于华东心血管医院房颤患者的脉冲场消融，围术期流程按本地指南调整",
        "created_by": "bd-lead",
    })
    assert response.status_code == 201, response.text
    return response.json()


def _submit_version(client, profile_code, evidence_ids, *, differences=None, intended_use=None, submitted_by="localization-editor"):
    differences = differences if differences is not None else [
        {"difference_type": "翻译", "topic": "说明书警示语翻译", "source_text": "Do not reuse catheter",
         "adjusted_text": "一次性使用，禁止重复灭菌", "rationale": "按中文警示习惯重写"},
        {"difference_type": "术语校准", "topic": "脉冲场消融术语", "source_text": "PFA",
         "adjusted_text": "脉冲场消融（PFA）", "rationale": "与国内指南术语对齐"},
        {"difference_type": "临床场景", "topic": "适应人群", "source_text": "paroxysmal AF",
         "adjusted_text": "阵发性心房颤动，年龄按本院纳入标准", "rationale": "本院入排标准更严格"},
    ]
    payload = {"source_evidence_ids": evidence_ids, "differences": differences, "submitted_by": submitted_by}
    if intended_use:
        payload["intended_use_local"] = intended_use
    return client.post(f"/api/localization/profiles/{profile_code}/versions", json=payload)


def _approve_all(client, profile_code, *, until="operations", resolves=None):
    stages = ["medical", "compliance", "operations"]
    for stage in stages[: stages.index(until) + 1]:
        body = {"stage": stage, "reviewer": f"{stage}-reviewer", "decision": "approved", "comment": "内容齐备"}
        if stage == "operations" and resolves is not None:
            body["resolves_flag_ids"] = resolves
        response = client.post(f"/api/localization/profiles/{profile_code}/reviews", json=body)
        assert response.status_code == 200, response.text
    return response


def test_profile_must_bind_hospital_and_published_version_immutable(client):
    _setup_product_and_hospital(client)
    client.post("/api/catalog/sites", json=EXPO)
    evidence = _accepted_evidence(client)

    # 合作机构必须是医院
    bad = client.post("/api/localization/profiles", json={
        "code": "loc-bad", "product_code": PRODUCT["code"], "target_region": "杭州",
        "site_code": EXPO["code"], "intended_use_local": "展会场景仅做科普演示，不涉及临床诊断与治疗建议",
        "created_by": "bd-lead",
    })
    assert bad.status_code == 422

    profile = _create_profile(client)
    assert profile["status"] == "draft"
    assert profile["product"]["code"] == PRODUCT["code"]

    # 来源证据必须先通过目录审阅
    unaccepted = client.post("/api/catalog/evidence", json={
        "product_code": PRODUCT["code"], "evidence_type": "安全", "title": "未审材料",
        "source_name": "厂家", "source_region": "美国", "version": "draft-1",
        "content_digest": "f" * 64, "summary": {}, "submitted_by": "x",
    }).json()
    rejected = _submit_version(client, profile["code"], [unaccepted["id"]])
    assert rejected.status_code == 409

    submitted = _submit_version(client, profile["code"], [evidence["id"]])
    assert submitted.status_code == 201, submitted.text
    v1 = submitted.json()
    assert v1["version_no"] == 1 and v1["status"] == "in_review" and v1["current_stage"] == "medical"
    assert len(v1["differences"]) == 3 and len(v1["sources"]) == 1

    # 审阅必须按医学→合规→运营顺序，合规不能抢在医学前
    out_of_order = client.post(f"/api/localization/profiles/{profile['code']}/reviews", json={
        "stage": "compliance", "reviewer": "c", "decision": "approved", "comment": "ok",
    })
    assert out_of_order.status_code == 409

    approved = _approve_all(client, profile["code"])
    assert approved.json()["status"] == "approved"
    detail = client.get(f"/api/localization/profiles/{profile['code']}").json()
    assert detail["status"] == "published" and detail["published_version_no"] == 1
    # 已发布版本不可再改：再次审阅找不到审阅中版本
    again = client.post(f"/api/localization/profiles/{profile['code']}/reviews", json={
        "stage": "medical", "reviewer": "m", "decision": "approved", "comment": "ok",
    })
    assert again.status_code == 409


def test_return_must_target_difference_and_resubmission_creates_new_version(client):
    _setup_product_and_hospital(client)
    evidence = _accepted_evidence(client)
    profile = _create_profile(client)
    v1 = _submit_version(client, profile["code"], [evidence["id"]]).json()
    diff_id = v1["differences"][0]["id"]

    # 退回不指向差异将被拒绝
    no_target = client.post(f"/api/localization/profiles/{profile['code']}/reviews", json={
        "stage": "medical", "reviewer": "m", "decision": "returned", "comment": "翻译还要改",
    })
    assert no_target.status_code == 422
    # 指向不存在的差异也被拒绝
    wrong = client.post(f"/api/localization/profiles/{profile['code']}/reviews", json={
        "stage": "medical", "reviewer": "m", "decision": "returned", "comment": "翻译需要重新校准",
        "difference_ids": [999999],
    })
    assert wrong.status_code == 422

    returned = client.post(f"/api/localization/profiles/{profile['code']}/reviews", json={
        "stage": "medical", "reviewer": "m", "decision": "returned",
        "comment": "警示语翻译不符合国内规范，请补充", "difference_ids": [diff_id],
    })
    assert returned.status_code == 200
    profile_after = client.get(f"/api/localization/profiles/{profile['code']}").json()
    assert profile_after["status"] == "draft"  # 从未发布过，退回后回草稿

    # 补交形成新版本 v2，而不是覆盖 v1；未解决差异可结转
    carry_diff = next(item for item in v1["differences"] if item["id"] == diff_id)
    v2 = _submit_version(client, profile["code"], [evidence["id"]], differences=[
        {"difference_type": "翻译", "topic": "说明书警示语翻译", "source_text": "Do not reuse catheter",
         "adjusted_text": "一次性使用导管，严禁重复灭菌或复用", "rationale": "按医疗器械中文标签规范重写",
         "carried_from": diff_id},
        {"difference_type": "临床场景", "topic": "抗凝方案", "source_text": "periprocedural anticoagulation",
         "adjusted_text": "围术期抗凝按本院房颤中心路径执行", "rationale": "本院路径与海外方案不同"},
    ]).json()
    assert v2["version_no"] == 2
    carried = next(item for item in v2["differences"] if item["carried_from_difference_id"] == diff_id)
    assert carried["resolution"] == "carried"

    # 不能结转一个已经解决的差异（v1 的其他差异此时仍 open，但伪造 id 不行）
    versions = client.get(f"/api/localization/profiles/{profile['code']}/versions").json()["items"]
    assert [item["version_no"] for item in versions] == [1, 2]

    _approve_all(client, profile["code"])
    detail = client.get(f"/api/localization/profiles/{profile['code']}").json()
    assert detail["published_version_no"] == 2
    # v1 仍是独立、可追溯的记录，状态保持 returned
    v1_record = client.get(f"/api/localization/versions/{v1['id']}").json()
    assert v1_record["status"] == "returned"


def test_product_suspension_and_site_exit_force_under_review(client):
    _setup_product_and_hospital(client)
    evidence = _accepted_evidence(client)
    profile = _create_profile(client)
    _submit_version(client, profile["code"], [evidence["id"]])
    _approve_all(client, profile["code"])

    # 产品暂停：已发布档案进入待复核，旧发布版本仍可追溯
    paused = client.patch(f"/api/catalog/products/{PRODUCT['code']}", json={"regulatory_status": "暂停"})
    assert paused.status_code == 200
    detail = client.get(f"/api/localization/profiles/{profile['code']}").json()
    assert detail["status"] == "under_review"
    flag = next(item for item in detail["review_flags"] if item["reason_type"] == "product_suspended")
    assert flag["status"] == "open"
    published_version = next(item for item in detail["versions"] if item["version_no"] == 1)
    assert published_version["status"] == "approved"

    # 待复核期间已签署合作引用的旧版本仍可追溯
    agreement = client.post(f"/api/localization/profiles/{profile['code']}/agreements", json={
        "agreement_code": "agr-2026-001", "signed_by": "hospital-procurement", "note": "数贸会合作意向",
    })
    assert agreement.status_code == 201, agreement.text
    assert agreement.json()["version_no"] == 1

    # 补交新版本并在运营终审时声明处理该标志，档案回到已发布
    _submit_version(client, profile["code"], [evidence["id"]], differences=[
        {"difference_type": "临床场景", "topic": "暂停期间使用范围",
         "source_text": "full clinical use", "adjusted_text": "暂停期间仅限研究性使用",
         "rationale": "产品合规状态变化后收窄用途"},
    ])
    _approve_all(client, profile["code"], resolves=[flag["id"]])
    detail = client.get(f"/api/localization/profiles/{profile['code']}").json()
    assert detail["status"] == "published" and detail["published_version_no"] == 2
    closed_flag = next(item for item in detail["review_flags"] if item["id"] == flag["id"])
    assert closed_flag["status"] == "resolved" and closed_flag["new_version_no"] == 2
    # 旧协议仍指向 v1
    old_agreement = next(item for item in detail["agreements"] if item["agreement_code"] == "agr-2026-001")
    assert old_agreement["version_no"] == 1

    # 医院退出：另一个已发布档案进入待复核
    profile_b = _create_profile(client, code="loc-ablation-east-2")
    _submit_version(client, profile_b["code"], [evidence["id"]])
    _approve_all(client, profile_b["code"])
    closed_site = client.patch(f"/api/catalog/sites/{HOSPITAL['code']}", json={"status": "closed"})
    assert closed_site.status_code == 200
    detail_b = client.get(f"/api/localization/profiles/{profile_b['code']}").json()
    assert detail_b["status"] == "under_review"
    assert any(item["reason_type"] == "site_exit" for item in detail_b["review_flags"])


def test_superseded_evidence_flags_profiles_and_blocks_new_versions(client):
    _setup_product_and_hospital(client)
    old_evidence = _accepted_evidence(client, version="2024.2", digest_extra="old")
    profile = _create_profile(client)
    _submit_version(client, profile["code"], [old_evidence["id"]])
    _approve_all(client, profile["code"])

    # 新证据声明替代旧证据，接受后旧证据 superseded，档案进入待复核
    new_evidence = _accepted_evidence(client, version="2026.1", digest_extra="new", replaces=old_evidence["id"])
    detail = client.get(f"/api/localization/profiles/{profile['code']}").json()
    assert detail["status"] == "under_review"
    flag = next(item for item in detail["review_flags"] if item["reason_type"] == "evidence_superseded")
    assert flag["evidence_id"] == old_evidence["id"]

    # 新版本不能再引用已被替代的旧证据，必须改用新证据
    blocked = _submit_version(client, profile["code"], [old_evidence["id"]], differences=[
        {"difference_type": "翻译", "topic": "更新后的警示语", "source_text": "a", "adjusted_text": "b", "rationale": "x"},
    ])
    assert blocked.status_code == 409
    _submit_version(client, profile["code"], [new_evidence["id"]], differences=[
        {"difference_type": "临床场景", "topic": "新增随访终点",
         "source_text": "12-month follow-up", "adjusted_text": "按新证据补充24个月随访",
         "rationale": "新证据随访窗口更长"},
    ])
    _approve_all(client, profile["code"], resolves=[flag["id"]])
    detail = client.get(f"/api/localization/profiles/{profile['code']}").json()
    assert detail["status"] == "published" and detail["published_version_no"] == 2


def test_effective_at_returns_conclusion_and_open_items_at_a_point_in_time(client):
    _setup_product_and_hospital(client)
    evidence = _accepted_evidence(client)
    profile = _create_profile(client)

    before_profile = "2000-01-01"
    early = client.get(f"/api/localization/profiles/{profile['code']}/effective-at", params={"at": before_profile})
    assert early.status_code == 200
    assert early.json()["effective_version"] is None

    v1 = _submit_version(client, profile["code"], [evidence["id"]]).json()
    _approve_all(client, profile["code"])
    published = client.get(f"/api/localization/profiles/{profile['code']}").json()
    t1 = next(item for item in published["versions"] if item["version_no"] == 1)["published_at"]

    at_t1 = client.get(f"/api/localization/profiles/{profile['code']}/effective-at", params={"at": t1})
    assert at_t1.json()["effective_version"]["version_no"] == 1
    assert at_t1.json()["unresolved_differences"] == []

    # v1 有效期间签署的合作固定引用 v1，后续再发布新版本也不改变
    signed = client.post(f"/api/localization/profiles/{profile['code']}/agreements", json={
        "agreement_code": "agr-historic", "signed_by": "hospital",
    })
    assert signed.status_code == 201 and signed.json()["version_no"] == 1

    # 上游证据替代 → 挂起未解决事项；此时有效结论仍是 v1
    new_evidence = _accepted_evidence(client, version="2026.2", digest_extra="new2", replaces=evidence["id"])
    flagged = client.get(f"/api/localization/profiles/{profile['code']}/effective-at", params={"at": "2999-01-01"})
    body = flagged.json()
    assert body["effective_version"]["version_no"] == 1
    assert any(item["reason_type"] == "evidence_superseded" for item in body["open_review_flags"])

    # 补交 v2，被医学退回：出现未解决差异，有效版本仍为 v1
    v2_response = _submit_version(client, profile["code"], [new_evidence["id"]], differences=[
        {"difference_type": "术语校准", "topic": "新终点术语", "source_text": "MACE",
         "adjusted_text": "主要不良心血管事件（MACE）", "rationale": "待医学确认定义口径"},
    ])
    v2 = v2_response.json()
    open_diff_id = v2["differences"][0]["id"]
    client.post(f"/api/localization/profiles/{profile['code']}/reviews", json={
        "stage": "medical", "reviewer": "m", "decision": "returned",
        "comment": "MACE 定义口径需与本院共识对齐", "difference_ids": [open_diff_id],
    })
    during = client.get(f"/api/localization/profiles/{profile['code']}/effective-at", params={"at": "2999-01-01"})
    assert during.json()["effective_version"]["version_no"] == 1
    unresolved = during.json()["unresolved_differences"]
    assert any(item["id"] == open_diff_id for item in unresolved)
    # v1 发布时点这些事项尚不存在
    at_t1_again = client.get(f"/api/localization/profiles/{profile['code']}/effective-at", params={"at": t1})
    assert all(item["id"] != open_diff_id for item in at_t1_again.json()["unresolved_differences"])

    # 结转未决差异，新版本三段通过并关闭标志
    _submit_version(client, profile["code"], [new_evidence["id"]], differences=[
        {"difference_type": "术语校准", "topic": "新终点术语", "source_text": "MACE",
         "adjusted_text": "主要不良心血管事件（MACE），按本院共识定义", "rationale": "口径已对齐",
         "carried_from": open_diff_id},
    ])
    flags = client.get(f"/api/localization/profiles/{profile['code']}").json()["review_flags"]
    evidence_flag = next(item["id"] for item in flags if item["reason_type"] == "evidence_superseded")
    _approve_all(client, profile["code"], resolves=[evidence_flag])
    t3 = client.get(f"/api/localization/profiles/{profile['code']}/versions").json()["items"][-1]["published_at"]

    # 旧日期查到的仍是 v1（协议签署于 v1 之后，时点查询不会把它提前）
    old_date = client.get(f"/api/localization/profiles/{profile['code']}/effective-at", params={"at": t1})
    assert old_date.json()["effective_version"]["version_no"] == 1
    assert old_date.json()["agreements"] == []
    at_t3 = client.get(f"/api/localization/profiles/{profile['code']}/effective-at", params={"at": t3})
    assert at_t3.json()["effective_version"]["version_no"] == 3

    # 远期时点：当前结论为 v3，旧协议仍可追溯且固定引用 v1
    current = client.get(f"/api/localization/profiles/{profile['code']}/effective-at", params={"at": "2999-01-01"})
    assert current.json()["effective_version"]["version_no"] == 3
    agreements_now = current.json()["agreements"]
    assert any(item["agreement_code"] == "agr-historic" and item["version_no"] == 1 for item in agreements_now)
    assert current.json()["open_review_flags"] == []


def test_manual_flags_and_withdrawn_profile(client):
    _setup_product_and_hospital(client)
    evidence = _accepted_evidence(client)
    profile = _create_profile(client)
    _submit_version(client, profile["code"], [evidence["id"]])
    _approve_all(client, profile["code"])

    # 人工挂旗：待复核，不能直接无视；手工关闭需写结论
    raised = client.post(f"/api/localization/profiles/{profile['code']}/flags", json={
        "actor": "ops", "detail": "医院反馈手术室接线条件与原方案不符",
    })
    assert raised.status_code == 201
    detail = client.get(f"/api/localization/profiles/{profile['code']}").json()
    assert detail["status"] == "under_review"
    flag_id = raised.json()["id"]

    resolve = client.post(f"/api/localization/profiles/{profile['code']}/flags/{flag_id}/resolve", json={
        "actor": "ops", "action": "dismissed", "note": "现场复核后确认接线条件满足，关闭该事项",
    })
    assert resolve.status_code == 200
    detail = client.get(f"/api/localization/profiles/{profile['code']}").json()
    assert detail["status"] == "published"

    # 已处理标志不能重复处理
    duplicate = client.post(f"/api/localization/profiles/{profile['code']}/flags/{flag_id}/resolve", json={
        "actor": "ops", "action": "resolved", "note": "再次确认没有问题",
    })
    assert duplicate.status_code == 409

    # 撤回后不能再提交版本，历史版本仍可追溯
    withdrawn = client.post(f"/api/localization/profiles/{profile['code']}/withdraw", json={
        "actor": "bd-lead", "reason": "医院战略调整，合作终止",
    })
    assert withdrawn.status_code == 200
    blocked = _submit_version(client, profile["code"], [evidence["id"]])
    assert blocked.status_code == 409
    history = client.get(f"/api/localization/versions/1")
    assert history.status_code == 200
    assert history.json()["status"] == "approved"


def test_only_published_versions_can_be_signed(client):
    _setup_product_and_hospital(client)
    evidence = _accepted_evidence(client)
    profile = _create_profile(client)

    # 尚未发布，不能签署
    early = client.post(f"/api/localization/profiles/{profile['code']}/agreements", json={
        "agreement_code": "agr-early", "signed_by": "h",
    })
    assert early.status_code == 409

    _submit_version(client, profile["code"], [evidence["id"]])
    # 审阅中也不能签署
    in_review = client.post(f"/api/localization/profiles/{profile['code']}/agreements", json={
        "agreement_code": "agr-review", "signed_by": "h",
    })
    assert in_review.status_code == 409

    _approve_all(client, profile["code"])
    signed = client.post(f"/api/localization/profiles/{profile['code']}/agreements", json={
        "agreement_code": "agr-final", "signed_by": "h",
    })
    assert signed.status_code == 201 and signed.json()["version_no"] == 1

    # 终止合作后重复终止被拒绝
    terminated = client.post(f"/api/localization/profiles/{profile['code']}/agreements/agr-final/terminate", json={"actor": "ops"})
    assert terminated.status_code == 200
    again = client.post(f"/api/localization/profiles/{profile['code']}/agreements/agr-final/terminate", json={"actor": "ops"})
    assert again.status_code == 409


def test_three_hospitals_get_independent_profiles_for_one_product(client):
    client.post("/api/catalog/products", json=PRODUCT)
    hospitals = [
        ("hospital-north", "华北心血管中心", "北京"),
        ("hospital-south", "华南心脏医院", "广州"),
        ("hospital-west", "西部心律失常诊疗中心", "成都"),
    ]
    for code, name, region in hospitals:
        site = dict(HOSPITAL, code=code, name=name, region=region)
        assert client.post("/api/catalog/sites", json=site).status_code == 201
    evidence = _accepted_evidence(client)

    profile_codes = []
    for index, (code, _name, region) in enumerate(hospitals, start=1):
        response = client.post("/api/localization/profiles", json={
            "code": f"loc-ablation-{index}",
            "product_code": PRODUCT["code"],
            "target_region": region,
            "site_code": code,
            "intended_use_local": f"面向{region}合作医院的脉冲场消融本地用途，围术期流程按本院路径调整",
            "created_by": "bd-lead",
        })
        assert response.status_code == 201, response.text
        profile_codes.append(response.json()["code"])

    # 三家档案彼此独立：医学只退回第一家，其他两家不受影响
    first = profile_codes[0]
    submitted = _submit_version(client, first, [evidence["id"]]).json()
    diff = submitted["differences"][0]["id"]
    client.post(f"/api/localization/profiles/{first}/reviews", json={
        "stage": "medical", "reviewer": "m", "decision": "returned",
        "comment": "第一家医院的警示语翻译需重译", "difference_ids": [diff],
    })
    for code in profile_codes[1:]:
        _submit_version(client, code, [evidence["id"]])
        _approve_all(client, code)

    first_detail = client.get(f"/api/localization/profiles/{first}").json()
    assert first_detail["status"] == "draft"
    for code in profile_codes[1:]:
        detail = client.get(f"/api/localization/profiles/{code}").json()
        assert detail["status"] == "published" and detail["published_version_no"] == 1

    # 按产品可一次列出全部三家档案
    listing = client.get("/api/localization/profiles", params={"product_code": PRODUCT["code"]})
    assert len(listing.json()["items"]) == 3


def test_localization_permissions_seeded(client, admin):
    response = client.get("/api/roles/permissions", headers=admin["headers"])
    assert response.status_code == 200
    codes = {item["code"] for item in response.json()}
    assert {"localization.read", "localization.write",
            "localization.review.medical", "localization.review.compliance",
            "localization.review.operations"} <= codes
    roles = client.get("/api/roles", headers=admin["headers"]).json()
    by_code = {item["code"]: {perm["code"] for perm in item["permissions"]} for item in roles}
    assert "localization.review.medical" in by_code["medical_reviewer"]
    assert "localization.review.compliance" in by_code["compliance_reviewer"]
    assert "localization.review.operations" in by_code["operations_reviewer"]
    assert "localization.review.medical" not in by_code["operations_reviewer"]


def test_unrelated_evidence_rejected_and_listing_filters(client):
    _setup_product_and_hospital(client)
    # 第二个产品的证据不能挂到本档案
    other_product = {**PRODUCT, "code": "ablation-y", "name": "其他消融设备"}
    client.post("/api/catalog/products", json=other_product)
    other_evidence = client.post("/api/catalog/evidence", json={
        "product_code": "ablation-y", "evidence_type": "临床", "title": "其他产品证据",
        "source_name": "欧洲注册研究", "source_region": "欧洲", "version": "v1",
        "content_digest": "a1" * 32, "summary": {}, "submitted_by": "other-team",
    }).json()
    client.post(f"/api/catalog/evidence/{other_evidence['id']}/review", json={
        "reviewer": "r", "decision": "accepted", "note": "ok ok",
    })
    profile = _create_profile(client)
    response = _submit_version(client, profile["code"], [other_evidence["id"]])
    assert response.status_code == 422

    # 列表过滤
    listing = client.get("/api/localization/profiles", params={"product_code": PRODUCT["code"], "region": "上海"})
    assert listing.status_code == 200
    assert {item["code"] for item in listing.json()["items"]} == {profile["code"]}
    none = client.get("/api/localization/profiles", params={"region": "北京"})
    assert none.json()["items"] == []
