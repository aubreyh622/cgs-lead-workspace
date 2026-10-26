from __future__ import annotations

import re
from datetime import date, datetime, timezone
from typing import Literal
from uuid import NAMESPACE_URL, uuid5
from urllib.parse import urlsplit, urlunsplit

import tldextract
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_domain = tldextract.TLDExtract(suffix_list_urls=(), cache_dir=None)
DIMENSIONS = ("problem", "capability_fit", "timing", "buyer_access")
Status = Literal["Needs research", "Ready for review", "Approved for handoff", "Deferred", "Rejected"]


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def public_url(value: str) -> str:
    p = urlsplit(value.strip() if "://" in value else "https://" + value.strip())
    host = (p.hostname or "").lower().rstrip(".")
    if (p.scheme not in ("https", "http") or p.username or p.password
            or p.port not in (None, 80, 443)
            or not re.fullmatch(r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z][a-z0-9-]{1,62}", host)
            or host.endswith((".local", ".localhost", ".internal"))):
        raise ValueError("Use a public website domain and a standard HTTP(S) port.")
    return urlunsplit((p.scheme, host, p.path.rstrip("/") or "/", p.query, ""))


def domain(value: str) -> str:
    host = urlsplit(public_url(value)).hostname
    return _domain(host).top_domain_under_public_suffix or host.removeprefix("www.")


def stable_id(company_domain: str, mode: str = "live") -> str:
    return str(uuid5(NAMESPACE_URL, f"cgs-identification:{mode}:{domain(company_domain)}"))


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, hide_input_in_errors=True)


class Seed(Model):
    company: str = Field(min_length=2, max_length=200)
    domain: str

    @field_validator("domain")
    @classmethod
    def normalize(cls, v):
        return domain(v)


class Brief(Model):
    schema_version: Literal["1.0"] = "1.0"
    brief_id: str
    version: str
    capability: str = Field(min_length=10)
    problem: str = Field(min_length=20)
    industry: str
    geography: str
    signals: list[str] = Field(min_length=1)
    exclusions: list[str]
    excluded_domains: list[str] = Field(default_factory=list)
    buyer_roles: list[str] = Field(min_length=1)
    acceptable_sources: list[str] = Field(min_length=1)
    signal_max_age_days: int = Field(default=365, ge=1, le=1825)
    role_max_age_days: int = Field(default=180, ge=1, le=730)
    approved_by: str | None = None
    approved_at: datetime | None = None
    approval_basis: Literal["pending", "mock", "cgs"] = "pending"
    pipeline_owner: str | None = None
    missing_contact_owner: Literal["identification", "downstream", "unconfirmed"] = "unconfirmed"

    @field_validator("excluded_domains")
    @classmethod
    def normalize(cls, values):
        return sorted({domain(v) for v in values})


class Dimension(Model):
    weight: float = Field(gt=0, le=1)
    anchors: dict[str, str]

    @model_validator(mode="after")
    def anchors_valid(self):
        if set(self.anchors) != {"low", "medium", "high"}:
            raise ValueError("Define low, medium and high anchors.")
        return self


class Rubric(Model):
    version: str
    dimensions: dict[str, Dimension]
    high_threshold: float = Field(ge=0, le=100)
    medium_threshold: float = Field(ge=0, le=100)
    approved_by: str | None = None
    approved_at: datetime | None = None
    approval_basis: Literal["pending", "mock", "cgs"] = "pending"

    @model_validator(mode="after")
    def weights_valid(self):
        if set(self.dimensions) != set(DIMENSIONS):
            raise ValueError("The rubric must contain the four identification dimensions.")
        if abs(sum(d.weight for d in self.dimensions.values()) - 1) > 0.00001:
            raise ValueError("Weights must sum to one.")
        if self.high_threshold <= self.medium_threshold:
            raise ValueError("High threshold must exceed medium threshold.")
        return self


class Source(Model):
    source_id: str
    url: str
    retrieved_at: datetime
    content: str = Field(min_length=1)

    _url = field_validator("url")(public_url)


class Observation(Model):
    evidence_id: str
    category: Literal["identity", "problem", "signal", "industry", "geography", "timing", "person", "account", "other"]
    observed_fact: str = Field(min_length=10, max_length=2000)
    source_id: str
    excerpt: str = Field(min_length=15, max_length=2000)
    publication_date: date | None = None
    date_excerpt: str | None = None


class Person(Model):
    name: str
    role: str
    company: str
    evidence_ids: list[str] = Field(min_length=1)
    role_as_of: date | None = None
    role_date_excerpt: str | None = None
    verified_at: date


class Component(Model):
    assessment: Literal["low", "medium", "high"] | None
    reason: str = Field(min_length=10, max_length=2000)
    evidence_ids: list[str]


class Research(Model):
    company: str
    domain: str
    identity: Literal["confirmed", "ambiguous", "unconfirmed"]
    eligibility: Literal["eligible", "ineligible", "unknown"]
    eligibility_reason: str
    evidence: list[Observation] = Field(max_length=30)
    people: list[Person] | None = None
    problem_hypothesis: str = Field(min_length=20)
    cgs_relevance: str = Field(min_length=20)
    alternative_explanation: str = Field(min_length=20)
    confidence: Literal["low", "medium", "high"]
    unanswered_questions: list[str] = Field(min_length=1)
    conflicts: list[str]
    components: dict[str, Component]
    suggested_next_investigation: str = Field(min_length=10)

    _domain = field_validator("domain")(domain)

    @model_validator(mode="after")
    def dimensions_valid(self):
        if set(self.components) != set(DIMENSIONS):
            raise ValueError("Supply all four component assessments; use null for unknowns.")
        return self


class Priority(Model):
    rubric_version: str
    score: float | None
    coverage: float
    possible_range: list[float]
    band: Literal["high", "medium", "low", "Needs research"]
    provisional: bool
    components: dict[str, Component]


class Review(Model):
    decision: Status
    reviewer: str | None = None
    reviewed_at: datetime | None = None
    notes: str = ""
    minutes: float = Field(default=0, ge=0)
    source_support_checked: bool = False
    role_accuracy_checked: bool | None = None


class Lead(Model):
    schema_version: Literal["1.0"] = "1.0"
    run_id: str
    lead_id: str
    mode: Literal["live", "synthetic"]
    approval_basis: Literal["pending", "mock", "cgs"] = "pending"
    research_method: Literal["agent", "manual_public_web", "synthetic_fixture"] = "agent"
    problem_brief_id: str
    problem_brief_version: str
    research: Research
    # The same evidence-backed record powers cards, CSV and the JSON handoff.
    sources: list[Source]
    priority: Priority
    validation_issues: list[str]
    research_gaps: list[str] = Field(default_factory=list)
    review: Review


class Limits(Model):
    searches: int = Field(default=90, ge=1, le=150)
    extractions: int = Field(default=150, ge=1, le=250)
    model_calls: int = Field(default=200, ge=1, le=300)
    tavily_credits: float = Field(default=150, gt=0, le=500)
    active_seconds: float = Field(default=3600, gt=0, le=7200)
    model_requests_per_minute: int | None = Field(default=None, ge=1, le=60)


class Action(Model):
    tool: Literal["search", "read_page", "record_lead", "finish"]
    arguments_json: str
    reason: str
