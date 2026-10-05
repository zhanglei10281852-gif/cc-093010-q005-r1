from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class DossierCreate(BaseModel):
    code: str = Field(min_length=2, max_length=64, pattern=r"^[a-z0-9][a-z0-9._-]+$")
    product_code: str = Field(min_length=2, max_length=64)
    target_region: str = Field(min_length=2, max_length=120)
    site_code: str = Field(min_length=2, max_length=64)
    intended_use_local: str = Field(min_length=10, max_length=2000)
    created_by: str = Field(min_length=1, max_length=120)


class DossierUpdate(BaseModel):
    intended_use_local: str = Field(min_length=10, max_length=2000)


class DifferenceResolution(BaseModel):
    difference_id: int
    resolution: str = Field(min_length=2, max_length=2000)


class VersionSubmit(BaseModel):
    submitted_by: str = Field(min_length=1, max_length=120)
    translation_note: str = Field(min_length=2, max_length=4000)
    terminology_note: str = Field(min_length=2, max_length=4000)
    clinical_scenario_note: str = Field(min_length=2, max_length=4000)
    extra: dict = Field(default_factory=dict)
    evidence_ids: list[int] = Field(min_length=1, max_length=200)
    resolutions: list[DifferenceResolution] = Field(default_factory=list, max_length=200)
    change_note: str = Field(default="", max_length=2000)


class NewDifference(BaseModel):
    category: Literal["翻译", "术语校准", "临床场景", "合规依据", "其他"]
    title: str = Field(min_length=2, max_length=200)
    detail: str = Field(default="", max_length=4000)


class ReviewAction(BaseModel):
    reviewer: str = Field(min_length=1, max_length=120)
    decision: Literal["approved", "rejected"]
    note: str = Field(default="", max_length=2000)
    difference_ids: list[int] = Field(default_factory=list, max_length=200)
    new_differences: list[NewDifference] = Field(default_factory=list, max_length=50)


class DifferenceCreate(BaseModel):
    category: Literal["翻译", "术语校准", "临床场景", "合规依据", "其他"]
    title: str = Field(min_length=2, max_length=200)
    detail: str = Field(default="", max_length=4000)
    evidence_id: int | None = None
    opened_by: str = Field(min_length=1, max_length=120)


class TriggerReview(BaseModel):
    reason_type: Literal["manual", "site_withdrawal", "product_suspension", "evidence_superseded"] = "manual"
    actor: str = Field(min_length=1, max_length=120)
    note: str = Field(min_length=2, max_length=2000)


class CloseDossier(BaseModel):
    actor: str = Field(min_length=1, max_length=120)
    reason: str = Field(min_length=2, max_length=2000)


class CooperationSign(BaseModel):
    reference: str = Field(min_length=2, max_length=120)
    signed_by: str = Field(min_length=1, max_length=120)
    note: str = Field(default="", max_length=2000)
    version_no: int | None = Field(default=None, ge=1)


class CooperationWithdraw(BaseModel):
    actor: str = Field(min_length=1, max_length=120)
    reason: str = Field(min_length=2, max_length=2000)
