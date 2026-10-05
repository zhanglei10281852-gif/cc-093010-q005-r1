from __future__ import annotations

from fastapi import APIRouter, Query

from app.localization.schemas import (
    AgreementSign, AgreementTerminate, FlagResolve, ManualFlag, ProfileCreate,
    VersionReview, VersionSubmit, WithdrawRequest,
)
from app.localization.service import LocalizationService


router = APIRouter(prefix="/api/localization", tags=["全球资料本地化档案"])


def service() -> LocalizationService:
    return LocalizationService()


@router.post("/profiles", status_code=201)
def create_profile(payload: ProfileCreate):
    return service().create_profile(payload.model_dump())


@router.get("/profiles")
def list_profiles(
    status: str | None = None,
    product_code: str | None = None,
    site_code: str | None = None,
    region: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
):
    return {"items": service().list_profiles(status=status, product_code=product_code, site_code=site_code, region=region, limit=limit)}


@router.get("/profiles/{code}")
def get_profile(code: str):
    return service().get_profile(code)


@router.post("/profiles/{code}/withdraw")
def withdraw_profile(code: str, payload: WithdrawRequest):
    return service().withdraw_profile(code, payload.actor, payload.reason)


@router.post("/profiles/{code}/versions", status_code=201)
def submit_version(code: str, payload: VersionSubmit):
    return service().submit_version(code, payload.model_dump())


@router.get("/profiles/{code}/versions")
def list_versions(code: str):
    return {"items": service().list_versions(code)}


@router.get("/versions/{version_id}")
def get_version(version_id: int):
    return service().get_version(version_id)


@router.post("/profiles/{code}/reviews")
def review_version(code: str, payload: VersionReview):
    return service().review(code, payload.model_dump())


@router.post("/profiles/{code}/flags", status_code=201)
def raise_flag(code: str, payload: ManualFlag):
    return service().raise_manual_flag(code, payload.actor, payload.detail)


@router.post("/profiles/{code}/flags/{flag_id}/resolve")
def resolve_flag(code: str, flag_id: int, payload: FlagResolve):
    return service().resolve_flag(code, flag_id, payload.actor, payload.action, payload.note)


@router.post("/profiles/{code}/agreements", status_code=201)
def sign_agreement(code: str, payload: AgreementSign):
    return service().sign_agreement(code, payload.model_dump())


@router.post("/profiles/{code}/agreements/{agreement_code}/terminate")
def terminate_agreement(code: str, agreement_code: str, payload: AgreementTerminate):
    return service().terminate_agreement(code, agreement_code, payload.actor)


@router.get("/profiles/{code}/effective-at")
def effective_at(code: str, at: str = Query(..., description="查询时点：YYYY-MM-DD 或完整 UTC 时间")):
    return service().effective_at(code, at)
