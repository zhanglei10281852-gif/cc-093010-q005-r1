from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator


class ProfileCreate(BaseModel):
    code: str = Field(min_length=2, max_length=64, pattern=r"^[a-z0-9][a-z0-9._-]+$")
    product_code: str = Field(min_length=2, max_length=64)
    target_region: str = Field(min_length=2, max_length=120)
    site_code: str = Field(min_length=2, max_length=64)
    intended_use_local: str = Field(min_length=10, max_length=4000)
    created_by: str = Field(min_length=1, max_length=120)


class DifferenceItem(BaseModel):
    difference_type: Literal["翻译", "术语校准", "临床场景"]
    topic: str = Field(min_length=2, max_length=200)
    source_text: str = Field(default="", max_length=4000)
    adjusted_text: str = Field(default="", max_length=4000)
    rationale: str = Field(default="", max_length=2000)
    carried_from: int | None = Field(default=None, ge=1)


class VersionSubmit(BaseModel):
    intended_use_local: str | None = Field(default=None, min_length=10, max_length=4000)
    source_evidence_ids: list[int] = Field(min_length=1, max_length=200)
    differences: list[DifferenceItem] = Field(min_length=1, max_length=500)
    submitted_by: str = Field(min_length=1, max_length=120)


class VersionReview(BaseModel):
    stage: Literal["medical", "compliance", "operations"]
    reviewer: str = Field(min_length=1, max_length=120)
    decision: Literal["approved", "returned"]
    comment: str = Field(default="", max_length=2000)
    difference_ids: list[int] = Field(default_factory=list, max_length=500)
    resolves_flag_ids: list[int] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def approve_without_difference_targets(self) -> "VersionReview":
        if self.decision == "returned" and not self.difference_ids:
            raise ValueError("退回时必须在 difference_ids 中指向具体差异")
        if self.decision == "approved" and self.difference_ids:
            raise ValueError("通过审阅时不应指向差异；如需关闭待复核标志请使用 resolves_flag_ids")
        return self


class ManualFlag(BaseModel):
    actor: str = Field(min_length=1, max_length=120)
    detail: str = Field(min_length=4, max_length=1000)


class FlagResolve(BaseModel):
    actor: str = Field(min_length=1, max_length=120)
    action: Literal["resolved", "dismissed"]
    note: str = Field(min_length=4, max_length=1000)


class AgreementSign(BaseModel):
    agreement_code: str = Field(min_length=2, max_length=80, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]+$")
    signed_by: str = Field(min_length=1, max_length=120)
    version_no: int | None = Field(default=None, ge=1)
    note: str = Field(default="", max_length=1000)


class WithdrawRequest(BaseModel):
    actor: str = Field(min_length=1, max_length=120)
    reason: str = Field(min_length=4, max_length=1000)


class AgreementTerminate(BaseModel):
    actor: str = Field(min_length=1, max_length=120)
