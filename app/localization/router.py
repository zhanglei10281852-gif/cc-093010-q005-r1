from __future__ import annotations

from fastapi import APIRouter, Query

from app.localization.schemas import (
    CloseDossier,
    CooperationSign,
    CooperationWithdraw,
    DifferenceCreate,
    DossierCreate,
    DossierUpdate,
    ReviewAction,
    TriggerReview,
    VersionSubmit,
)
from app.localization.service import LocalizationService


router = APIRouter(prefix="/api/localization", tags=["全球资料本地化档案"])


def service() -> LocalizationService:
    return LocalizationService()


@router.post("/dossiers", status_code=201)
def create_dossier(payload: DossierCreate):
    return service().create_dossier(payload.model_dump())


@router.patch("/dossiers/{code}")
def update_dossier(code: str, payload: DossierUpdate):
    return service().update_dossier(code, payload.intended_use_local)


@router.get("/dossiers")
def list_dossiers(status: str | None = None, product_code: str | None = None,
                  target_region: str | None = None, limit: int = Query(default=100, ge=1, le=500)):
    return {"items": service().list_dossiers(status, product_code, target_region, limit)}


@router.get("/dossiers/{code}")
def get_dossier(code: str):
    return service().get_dossier(code)


@router.post("/dossiers/{code}/close")
def close_dossier(code: str, payload: CloseDossier):
    return service().close_dossier(code, payload.actor, payload.reason)


@router.post("/dossiers/{code}/differences", status_code=201)
def add_difference(code: str, payload: DifferenceCreate):
    return service().add_difference(code, payload.model_dump())


@router.get("/dossiers/{code}/differences")
def list_differences(code: str, status: str | None = Query(default=None, pattern="^(open|resolved)$")):
    return {"items": service().list_differences(code, status)}


@router.post("/dossiers/{code}/versions", status_code=201)
def submit_version(code: str, payload: VersionSubmit):
    return service().submit_version(code, payload.model_dump())


@router.post("/dossiers/{code}/versions/{version_no}/reviews/{stage}")
def review_version(code: str, version_no: int, stage: str, payload: ReviewAction):
    return service().review_version(code, version_no, stage, payload.model_dump())


@router.post("/dossiers/{code}/trigger-review")
def trigger_review(code: str, payload: TriggerReview):
    return service().trigger_review(code, payload.reason_type, payload.actor, payload.note)


@router.post("/dossiers/{code}/clear-review")
def clear_review_pending(code: str, payload: TriggerReview):
    return service().clear_review_pending(code, payload.actor, payload.note)


@router.post("/events/site-withdrawal")
def notify_site_withdrawal(payload: TriggerReview, site_code: str = Query(..., min_length=2)):
    return service().notify_site_withdrawal(site_code, payload.actor, payload.note)


@router.post("/events/product-suspension")
def notify_product_suspension(payload: TriggerReview, product_code: str = Query(..., min_length=2)):
    return service().notify_product_suspension(product_code, payload.actor, payload.note)


@router.post("/events/evidence/{evidence_id}/superseded")
def notify_evidence_superseded(evidence_id: int, payload: TriggerReview):
    return service().notify_evidence_superseded(evidence_id, payload.actor, payload.note)


@router.post("/events/impact-scan")
def scan_impacts(actor: str = Query(..., min_length=1)):
    return service().scan_impacts(actor)


@router.post("/dossiers/{code}/cooperations", status_code=201)
def sign_cooperation(code: str, payload: CooperationSign):
    return service().sign_cooperation(code, payload.model_dump())


@router.post("/cooperations/{reference}/withdraw")
def withdraw_cooperation(reference: str, payload: CooperationWithdraw):
    return service().withdraw_cooperation(reference, payload.actor, payload.reason)


@router.get("/trace")
def trace_as_of(date: str = Query(..., pattern=r"^\d{4}-\d{2}-\d{2}$"),
                product_code: str | None = None, site_code: str | None = None):
    return service().as_of(date, product_code, site_code)
