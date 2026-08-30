from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Opinion(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    topic_id: str
    status: Literal["strong", "partial", "none"]
    rating: int | None = Field(default=None, ge=1, le=5)
    stance: str
    extended_stance: str = Field(default="", alias="extendedStance")
    sources: list[str]
    limitations: str

    @model_validator(mode="after")
    def sources_match_status(self) -> Opinion:
        if self.status == "none" and self.sources:
            raise ValueError("status none cannot have sources")
        if self.status == "none" and self.rating is not None:
            raise ValueError("status none cannot have a rating")
        if self.status != "none" and self.rating is None:
            raise ValueError(f"status {self.status} requires a rating")
        if self.status != "none" and not self.sources:
            raise ValueError(f"status {self.status} requires sources")
        if self.status == "strong" and len(self.sources) < 2:
            self.status = "partial"
        if len(self.sources) > 8:
            raise ValueError("an opinion cannot cite more than eight sources")
        if self.status == "none" and self.extended_stance:
            raise ValueError("status none cannot have an extended stance")
        return self


class PoliticianAnalysis(BaseModel):
    model_config = ConfigDict(extra="forbid")

    politician: str
    opinions: list[Opinion]


NONE_STANCE = "הנושא אינו מכוסה בציוצים שבמאגר."
