from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile

from fastapi.testclient import TestClient

from app.database import close_connection, database_path, get_connection, init_db
from app.main import app


def _print(value: object) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))


def init_database() -> int:
    init_db()
    _print({"database": str(database_path()), "initialized": True})
    return 0


def check_database() -> int:
    init_db()
    connection = get_connection()
    _print({
        "database": str(database_path()),
        "integrity": connection.execute("PRAGMA integrity_check").fetchone()[0],
        "foreign_keys": connection.execute("PRAGMA foreign_keys").fetchone()[0],
        "journal_mode": connection.execute("PRAGMA journal_mode").fetchone()[0],
        "schema_version": connection.execute("PRAGMA user_version").fetchone()[0],
    })
    return 0


def smoke() -> int:
    with tempfile.TemporaryDirectory(prefix="health-smoke-") as directory:
        os.environ["HEALTH_INNOVATION_DATABASE_PATH"] = os.path.join(directory, "smoke.db")
        close_connection()
        with TestClient(app) as client:
            root = client.get("/")
            health = client.get("/api/system/health")
            if root.status_code != 200 or health.status_code != 200:
                _print({"root": root.text, "health": health.text})
                return 1
            _print({"root": root.json(), "health": health.json()})
        close_connection()
    return 0


def pilot_demo() -> int:
    with tempfile.TemporaryDirectory(prefix="health-demo-") as directory:
        os.environ["HEALTH_INNOVATION_DATABASE_PATH"] = os.path.join(directory, "demo.db")
        close_connection()
        with TestClient(app) as client:
            product = client.post("/api/catalog/products", json={
                "code": "exoskeleton-a",
                "name": "轻量助力外骨骼",
                "organization": "示例康复科技",
                "origin_country": "中国",
                "category": "康复设备",
                "intended_use": "用于展会和康复机构的步态助力体验与运行数据观察",
                "risk_level": "medium",
                "regulatory_status": "展示",
            })
            site = client.post("/api/catalog/sites", json={
                "code": "expo-hall-a",
                "name": "数智医疗体验点",
                "site_type": "展会体验点",
                "region": "杭州",
                "capabilities": ["gait-assist"],
                "max_concurrent": 2,
            })
            protocol = client.post("/api/pilots/protocols?actor=demo", json={
                "code": "gait-assist",
                "name": "外骨骼步态体验方案",
                "capability": "gait-assist",
                "parameter_schema": {"minutes": {"type": "integer", "required": True, "minimum": 1, "maximum": 30}},
                "default_parameters": {},
                "max_runtime_seconds": 1800,
                "max_attempts": 2,
            })
            submitted = client.post("/api/pilots/sessions", json={
                "protocol_code": "gait-assist",
                "project_code": "expo-2026",
                "requested_by": "operator-demo",
                "parameters": {"minutes": 8},
                "priority": 70,
                "idempotency_key": "demo-session-001",
            })
            claimed = client.post("/api/pilots/sessions/claim", json={"site_code": "expo-hall-a", "capabilities": ["gait-assist"], "lease_seconds": 60})
            values = [product, site, protocol, submitted, claimed]
            if any(response.status_code >= 400 for response in values):
                _print({"errors": [response.text for response in values]})
                return 1
            _print({"product": product.json()["code"], "site": site.json()["code"], "session": claimed.json()["session"]})
        close_connection()
    return 0


def localization_demo() -> int:
    with tempfile.TemporaryDirectory(prefix="health-loc-") as directory:
        os.environ["HEALTH_INNOVATION_DATABASE_PATH"] = os.path.join(directory, "loc.db")
        close_connection()
        with TestClient(app) as client:
            product = client.post("/api/catalog/products", json={
                "code": "pfa-catheter",
                "name": "海外心脏脉冲消融导管",
                "organization": "海外消融科技",
                "origin_country": "美国",
                "category": "康复设备",
                "intended_use": "用于成年房颤患者的脉冲电场消融",
                "risk_level": "high",
                "regulatory_status": "研究",
            })
            site = client.post("/api/catalog/sites", json={
                "code": "hospital-bj",
                "name": "北京合作医院",
                "site_type": "医院",
                "region": "北京",
                "capabilities": ["cardiac-pfa"],
                "max_concurrent": 1,
            })
            evidence = client.post("/api/catalog/evidence", json={
                "product_code": "pfa-catheter",
                "evidence_type": "临床",
                "title": "全球多中心临床报告",
                "source_name": "Global PFA Registry",
                "source_region": "美国",
                "version": "2026.1",
                "content_digest": "f" * 64,
                "summary": {"patients": 480},
                "submitted_by": "evidence-owner",
            })
            client.post(f"/api/catalog/evidence/{evidence.json()['id']}/review",
                        json={"reviewer": "ev-a", "decision": "accepted", "note": "可追溯"})
            dossier = client.post("/api/localization/dossiers", json={
                "code": "pfa-bj-001",
                "product_code": "pfa-catheter",
                "target_region": "北京",
                "site_code": "hospital-bj",
                "intended_use_local": "用于北京合作医院电生理导管室内成年房颤患者的脉冲电场消融",
                "created_by": "biz-ops",
            })
            version = client.post("/api/localization/dossiers/pfa-bj-001/versions", json={
                "submitted_by": "biz-ops",
                "translation_note": "医学翻译并保留英文原名",
                "terminology_note": "pulse field ablation 统一译为脉冲电场消融",
                "clinical_scenario_note": "调整为国内电生理导管室并由培训术者操作",
                "evidence_ids": [evidence.json()["id"]],
            })
            reviews = []
            for stage, reviewer in (("medical", "med-a"), ("compliance", "legal-a"), ("operations", "ops-a")):
                reviews.append(client.post(
                    f"/api/localization/dossiers/pfa-bj-001/versions/1/reviews/{stage}",
                    json={"reviewer": reviewer, "decision": "approved", "note": "通过"},
                ))
            cooperation = client.post("/api/localization/dossiers/pfa-bj-001/cooperations", json={
                "reference": "MOU-BJ-2026-001",
                "signed_by": "hospital-bj",
                "note": "试点合作引用 v1",
            })
            values = [product, site, evidence, dossier, version, *reviews, cooperation]
            if any(response.status_code >= 400 for response in values):
                _print({"errors": [response.text for response in values]})
                return 1
            trace = client.get("/api/localization/trace", params={"date": "2026-10-05"})
            _print({
                "dossier": dossier.json()["code"],
                "published_version": version.json()["version_no"],
                "cooperation": cooperation.json()["reference"],
                "trace_status": trace.json()["items"][0]["status_as_of"],
            })
        close_connection()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="全球健康创新试点运营服务命令行")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init-db", help="初始化 SQLite 数据库")
    sub.add_parser("check-db", help="检查数据库完整性")
    sub.add_parser("smoke", help="进程内检查根路径和健康接口")
    sub.add_parser("pilot-demo", help="运行产品、场地、方案和场次演示")
    sub.add_parser("localization-demo", help="运行本地化档案三级审阅与合作引用演示")
    return parser


def main(argv: list[str] | None = None) -> int:
    command = build_parser().parse_args(argv).command
    actions = {"init-db": init_database, "check-db": check_database, "smoke": smoke,
               "pilot-demo": pilot_demo, "localization-demo": localization_demo}
    try:
        return actions[command]()
    finally:
        close_connection()


if __name__ == "__main__":
    sys.exit(main())

