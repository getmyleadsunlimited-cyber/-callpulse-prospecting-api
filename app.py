"""CallPulse autonomous seven-day prospecting campaign API."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, create_engine, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship, sessionmaker

from email_providers import configured_provider

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:////tmp/callpulse.db")
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+psycopg://", 1)
elif DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg://", 1)
API_KEY = os.getenv("CALLPULSE_ACTIONS_API_KEY", "")
DRY_RUN = os.getenv("CALLPULSE_DRY_RUN", "true").lower() != "false"
DELIVERY_WEBHOOK = os.getenv("CALLPULSE_DELIVERY_WEBHOOK", "")
EMAIL_PROVIDER_NAME = os.getenv("CALLPULSE_EMAIL_PROVIDER", "disabled").strip().lower()
EMAIL_FROM = os.getenv("CALLPULSE_EMAIL_FROM", "").strip()
TOUCH_DAYS = (0, 3, 6)
GENERAL_INDUSTRIES = (
    "eCommerce", "Roofing", "HVAC", "Dental", "Garage Door Repair", "Plumbing",
    "Emergency Towing", "Water Restoration", "Mold Remediation", "Pest Control",
    "Electrical", "Foundation Repair", "Tree Service", "Pool Service",
    "Landscaping / Lawn Care", "Med Spa",
)
INSURANCE_INDUSTRIES = ("Final Expense", "Auto Insurance")
INDUSTRIES = GENERAL_INDUSTRIES + INSURANCE_INDUSTRIES
MIN_QUALIFICATION_SCORE = 65
DEFAULT_LOCATION = "Houston, TX"

OPENING_MESSAGE_HELPERS = {
    "Roofing": "CallPulse AI Website Lead Recovery engages inspection and replacement visitors who leave your website without calling or requesting an estimate.",
    "HVAC": "CallPulse AI Website Lead Recovery engages AC repair and replacement visitors who leave your website without booking.",
    "Plumbing": "CallPulse AI Website Lead Recovery engages service and emergency visitors who leave your website without calling.",
    "Garage Door Repair": "CallPulse AI Website Lead Recovery engages repair and replacement visitors who leave your website without scheduling.",
    "Dental": "CallPulse AI Website Lead Recovery engages treatment visitors who leave your website without booking.",
    "Emergency Towing": "CallPulse AI Website Lead Recovery engages urgent visitors who leave your website without calling or requesting service.",
    "eCommerce": "CallPulse AI Website Lead Recovery engages shoppers who browse and leave without purchasing or entering the normal funnel.",
    "Final Expense": "CallPulse AI Website Lead Recovery engages quote and coverage visitors who leave without requesting information or speaking with an agent.",
    "Auto Insurance": "CallPulse AI Website Lead Recovery engages quote and coverage visitors who leave without requesting information or speaking with an agent.",
}
for _industry in INDUSTRIES:
    OPENING_MESSAGE_HELPERS.setdefault(
        _industry,
        "CallPulse AI Website Lead Recovery engages visitors who leave your website without calling, requesting a quote, or booking.",
    )
engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


class Prospect(Base):
    __tablename__ = "prospects"
    id: Mapped[int] = mapped_column(primary_key=True)
    company_name: Mapped[str] = mapped_column(String(200))
    website: Mapped[str] = mapped_column(String(500))
    industry: Mapped[str] = mapped_column(String(100), index=True)
    location: Mapped[str] = mapped_column(String(200), default=DEFAULT_LOCATION)
    score: Mapped[int] = mapped_column(Integer)
    why_now: Mapped[str] = mapped_column(Text)
    ai_recovery_opportunity: Mapped[str] = mapped_column(Text)
    decision_maker_name: Mapped[str | None] = mapped_column(String(200))
    decision_maker_title: Mapped[str | None] = mapped_column(String(200))
    verified_email: Mapped[str | None] = mapped_column(String(320), unique=True)
    email_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    opening_message: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(40), default="researched", index=True)
    last_reply: Mapped[str | None] = mapped_column(Text)
    intent: Mapped[str | None] = mapped_column(String(50))
    conversion_stage: Mapped[str | None] = mapped_column(String(50))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    campaigns: Mapped[list[Campaign]] = relationship(back_populates="prospect", cascade="all, delete-orphan")


class Campaign(Base):
    __tablename__ = "campaigns"
    __table_args__ = (UniqueConstraint("prospect_id", name="uq_active_campaign_prospect"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    prospect_id: Mapped[int] = mapped_column(ForeignKey("prospects.id", ondelete="CASCADE"))
    status: Mapped[str] = mapped_column(String(30), default="active", index=True)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    dry_run: Mapped[bool] = mapped_column(Boolean, default=True)
    live_authorized: Mapped[bool] = mapped_column(Boolean, default=False)
    live_authorized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    live_authorized_by: Mapped[str | None] = mapped_column(String(200))
    prospect: Mapped[Prospect] = relationship(back_populates="campaigns")
    touches: Mapped[list[Touch]] = relationship(back_populates="campaign", cascade="all, delete-orphan")


class Touch(Base):
    __tablename__ = "campaign_touches"
    __table_args__ = (UniqueConstraint("campaign_id", "day", name="uq_campaign_touch_day"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    campaign_id: Mapped[int] = mapped_column(ForeignKey("campaigns.id", ondelete="CASCADE"))
    day: Mapped[int] = mapped_column(Integer)
    scheduled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    status: Mapped[str] = mapped_column(String(30), default="scheduled", index=True)
    message: Mapped[str] = mapped_column(Text)
    idempotency_key: Mapped[str] = mapped_column(String(64), unique=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    dry_run: Mapped[bool] = mapped_column(Boolean, default=True)
    skipped: Mapped[bool] = mapped_column(Boolean, default=False)
    cancelled: Mapped[bool] = mapped_column(Boolean, default=False)
    cancellation_or_skip_reason: Mapped[str | None] = mapped_column(String(300))
    execution_status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    execution_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    execution_completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    provider_name: Mapped[str | None] = mapped_column(String(40))
    provider_message_id: Mapped[str | None] = mapped_column(String(300))
    last_execution_error: Mapped[str | None] = mapped_column(String(500))
    execution_attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    campaign: Mapped[Campaign] = relationship(back_populates="touches")


class Suppression(Base):
    __tablename__ = "suppressions"
    email: Mapped[str] = mapped_column(String(320), primary_key=True)
    reason: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class CanaryExecutionAudit(Base):
    __tablename__ = "canary_execution_audits"
    id: Mapped[int] = mapped_column(primary_key=True)
    campaign_id: Mapped[int] = mapped_column(ForeignKey("campaigns.id", ondelete="CASCADE"), index=True)
    delivery_id: Mapped[int] = mapped_column(ForeignKey("campaign_touches.id", ondelete="CASCADE"), index=True)
    prospect_id: Mapped[int] = mapped_column(ForeignKey("prospects.id", ondelete="CASCADE"), index=True)
    authorized_by: Mapped[str] = mapped_column(String(200))
    authorization_timestamp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    execution_requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    sender_identity: Mapped[str] = mapped_column(String(320))
    recipient_email: Mapped[str] = mapped_column(String(320))
    idempotency_key: Mapped[str] = mapped_column(String(64), index=True)
    provider_name: Mapped[str] = mapped_column(String(40))
    provider_message_id: Mapped[str | None] = mapped_column(String(300))
    result: Mapped[str] = mapped_column(String(30))
    failure_reason: Mapped[str | None] = mapped_column(String(1000))


Base.metadata.create_all(engine)
app = FastAPI(title="CallPulse Autonomous Campaign API", version="3.0.0")
bearer_scheme = HTTPBearer(auto_error=False)


def db_session():
    with SessionLocal() as db:
        yield db


def require_auth(credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme)):
    if (
        not API_KEY
        or credentials is None
        or credentials.scheme.lower() != "bearer"
        or not hmac.compare_digest(credentials.credentials, API_KEY)
    ):
        raise HTTPException(401, "Valid bearer authentication is required")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def normalize_email(email: str) -> str:
    email = email.strip().lower()
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        raise ValueError("A syntactically valid email is required")
    return email


# Backward-compatible name for integrations that imported the old adapter. Canary
# execution does not use this function; it always goes through EmailDeliveryProvider.
def deliver(email: str, message: str, idempotency_key: str) -> None:
    configured_provider("webhook", DELIVERY_WEBHOOK).send(
        sender=EMAIL_FROM, recipient=email, message=message, idempotency_key=idempotency_key)


class ProspectIn(BaseModel):
    company_name: str = Field(min_length=1, max_length=200)
    website: str = Field(pattern=r"^https?://")
    industry: str
    location: str = Field(default=DEFAULT_LOCATION, min_length=1, max_length=200)
    score: int = Field(ge=MIN_QUALIFICATION_SCORE, le=100)
    why_now: str = Field(min_length=1)
    ai_recovery_opportunity: str = Field(min_length=1)
    decision_maker_name: str | None = None
    decision_maker_title: str | None = None
    verified_email: str
    email_verified: bool
    opening_message: str | None = None

    @field_validator("industry")
    @classmethod
    def validate_industry(cls, value: str) -> str:
        if value not in INDUSTRIES:
            raise ValueError("Select a supported industry")
        return value

    @field_validator("verified_email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        return normalize_email(value)


class CampaignIn(BaseModel):
    start_at: datetime | None = None
    idempotency_key: str = Field(min_length=8, max_length=100)


class LiveAuthorizationIn(BaseModel):
    authorized_by: str = ""
    confirmation: str = ""


class CanaryExecutionIn(BaseModel):
    authorized_by: str = ""
    confirmation: str = ""
    delivery_id: int


class DeliveryEligibility(BaseModel):
    eligible: bool
    failures: list[str]


class SuppressionIn(BaseModel):
    email: str
    reason: str = Field(min_length=1, max_length=200)

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        return normalize_email(value)


class ReplyIn(BaseModel):
    reply_text: str = Field(min_length=1)
    intent: str = "unknown"


class ConversionIn(BaseModel):
    conversion_stage: Literal["signup_link_sent", "standard_start", "three_day_trial", "converted", "declined"]


class CampaignSequenceState(BaseModel):
    day: int
    status: str
    scheduled_at: datetime
    sent_at: datetime | None


class CampaignInspection(BaseModel):
    id: int
    prospect_id: int
    industry: str
    status: str
    starts_at: datetime
    ends_at: datetime
    dry_run: bool
    live_authorized: bool
    live_authorized_at: datetime | None
    live_authorized_by: str | None
    current_sequence_state: list[CampaignSequenceState]
    stopped: bool
    stop_reason: str | None


class DeliveryInspection(BaseModel):
    id: int
    campaign_id: int
    prospect_id: int
    sequence_day: int
    scheduled_at: datetime
    message: str
    status: str
    dry_run: bool
    skipped: bool
    sent_at: datetime | None
    cancelled: bool
    cancellation_or_skip_reason: str | None
    idempotency_key: str


def prospect_dict(p: Prospect) -> dict:
    return {c.name: getattr(p, c.name) for c in p.__table__.columns}


def campaign_dict(c: Campaign) -> dict:
    return {"id": c.id, "prospect_id": c.prospect_id, "status": c.status, "starts_at": c.starts_at,
            "ends_at": c.ends_at, "dry_run": c.dry_run, "live_authorized": c.live_authorized,
            "live_authorized_at": c.live_authorized_at, "live_authorized_by": c.live_authorized_by,
            "touches": [{x.name: getattr(t, x.name) for x in t.__table__.columns} for t in sorted(c.touches, key=lambda x: x.day)]}


def campaign_inspection(c: Campaign) -> dict:
    return {
        "id": c.id, "prospect_id": c.prospect_id, "industry": c.prospect.industry,
        "status": c.status, "starts_at": c.starts_at, "ends_at": c.ends_at,
        "dry_run": c.dry_run, "live_authorized": c.live_authorized,
        "live_authorized_at": c.live_authorized_at, "live_authorized_by": c.live_authorized_by,
        "current_sequence_state": [
            {"day": t.day, "status": t.status, "scheduled_at": t.scheduled_at, "sent_at": t.sent_at}
            for t in sorted(c.touches, key=lambda touch: touch.day)
        ],
        "stopped": c.status in {"stopped", "suppressed", "cancelled"},
        "stop_reason": None,  # The current schema has no campaign stop-reason column.
    }


def delivery_inspection(t: Touch) -> dict:
    return {
        "id": t.id, "campaign_id": t.campaign_id, "prospect_id": t.campaign.prospect_id,
        "sequence_day": t.day, "scheduled_at": t.scheduled_at, "message": t.message,
        "status": t.status, "dry_run": t.dry_run,
        "skipped": t.skipped, "sent_at": t.sent_at,
        "cancelled": t.cancelled,
        "cancellation_or_skip_reason": t.cancellation_or_skip_reason,
        "idempotency_key": t.idempotency_key,
    }


def prospect_suppressed(prospect: Prospect, db: Session) -> bool:
    return prospect.status == "suppressed" or bool(
        prospect.verified_email and db.get(Suppression, prospect.verified_email)
    )


def valid_outreach_destination(prospect: Prospect) -> bool:
    if not prospect.email_verified or not prospect.verified_email or not prospect.verified_email.strip():
        return False
    try:
        normalize_email(prospect.verified_email)
    except ValueError:
        return False
    return prospect.status not in {"invalid", "bounced", "hard_bounce", "opted_out", "suppressed"}


def can_execute_delivery(touch: Touch, campaign: Campaign, prospect: Prospect,
                         db: Session, now: datetime | None = None, *,
                         authorized_by: str = "inspection",
                         confirmation: str = "EXECUTE ONE CANARY DELIVERY",
                         check_provider: bool = False) -> DeliveryEligibility:
    """Return inspectable reasons rather than concealing safety decisions in a boolean."""
    failures: list[str] = []
    if not campaign.live_authorized: failures.append("campaign is not live authorized")
    if campaign.dry_run: failures.append("campaign is in dry-run mode")
    if campaign.status != "active": failures.append("campaign is not active")
    if touch.dry_run: failures.append("delivery is in dry-run mode")
    if touch.sent_at is not None: failures.append("delivery was already sent or simulated")
    if touch.skipped: failures.append("delivery is skipped")
    if touch.cancelled: failures.append("delivery is cancelled")
    if touch.execution_status == "sending": failures.append("another execution is already in flight")
    current, scheduled = now or utcnow(), touch.scheduled_at
    if scheduled.tzinfo is None: scheduled = scheduled.replace(tzinfo=timezone.utc)
    if current.tzinfo is None: current = current.replace(tzinfo=timezone.utc)
    if scheduled > current: failures.append("delivery is not due")
    if prospect_suppressed(prospect, db): failures.append("prospect is suppressed")
    if not valid_outreach_destination(prospect): failures.append("valid outreach destination is missing")
    if prospect.status in {"replied", "qualified", "converted"} or prospect.last_reply:
        failures.append("reply or conversion stop state prohibits outreach")
    if not touch.idempotency_key or not touch.idempotency_key.strip(): failures.append("delivery idempotency key is missing")
    if not touch.message or not touch.message.strip(): failures.append("persisted delivery message is empty")
    if not authorized_by.strip(): failures.append("authorized_by is required")
    if confirmation != "EXECUTE ONE CANARY DELIVERY": failures.append("confirmation phrase is invalid")
    if touch.idempotency_key and db.scalar(select(Touch.id).where(
        Touch.idempotency_key == touch.idempotency_key,
        (Touch.sent_at.is_not(None)) | (Touch.execution_status == "sent"),
    )) is not None:
        failures.append("a successful send already exists for the idempotency key")
    if touch.idempotency_key and db.scalar(select(Touch.id).where(
        Touch.idempotency_key == touch.idempotency_key,
        Touch.execution_status == "sending",
    )) is not None:
        failures.append("another execution is already in flight")
    if check_provider:
        if not EMAIL_FROM: failures.append("approved sender identity is not configured")
    failures = list(dict.fromkeys(failures))
    return DeliveryEligibility(eligible=not failures, failures=failures)


def add_canary_audit(db: Session, touch: Touch, authorized_by: str, result: str,
                     failure: str | None = None, provider_message_id: str | None = None) -> None:
    campaign, prospect = touch.campaign, touch.campaign.prospect
    db.add(CanaryExecutionAudit(
        campaign_id=campaign.id, delivery_id=touch.id, prospect_id=prospect.id,
        authorized_by=authorized_by.strip(), authorization_timestamp=campaign.live_authorized_at,
        execution_requested_at=utcnow(), sender_identity=EMAIL_FROM,
        recipient_email=prospect.verified_email or "", idempotency_key=touch.idempotency_key or "",
        provider_name=EMAIL_PROVIDER_NAME, provider_message_id=provider_message_id,
        result=result, failure_reason=failure,
    ))


@app.get("/health")
def health(db: Session = Depends(db_session)):
    db.execute(select(1))
    return {"ok": True, "database": "connected", "dry_run": DRY_RUN}


@app.get(
    "/launcher",
    response_class=HTMLResponse,
    operation_id="launcher",
    summary="Render the CallPulse campaign launcher operator UI.",
)
def launcher():
    """Render the operator UI; protected API endpoints still enforce bearer auth."""
    general_buttons = "".join(
        f'<button type="button" class="industry" data-industry="{name}" aria-pressed="false">{name}</button>'
        for name in GENERAL_INDUSTRIES
    )
    insurance_buttons = "".join(
        f'<button type="button" class="industry" data-industry="{name}" aria-pressed="false">{name}</button>'
        for name in INSURANCE_INDUSTRIES
    )
    opening_helpers = json.dumps(OPENING_MESSAGE_HELPERS).replace("</", "<\\/")
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>CallPulse Campaign Launcher</title>
  <style>
    :root {{ color-scheme: dark; font-family: Inter, system-ui, sans-serif; background: #07111f; color: #eaf2ff; }}
    body {{ margin: 0; min-height: 100vh; background: radial-gradient(circle at top, #173052, #07111f 55%); }}
    main {{ width: min(960px, calc(100% - 32px)); margin: 48px auto; }}
    .card {{ background: #0e1c30; border: 1px solid #294363; border-radius: 18px; padding: 28px; box-shadow: 0 24px 70px #0008; }}
    h1 {{ margin: 0 0 8px; }} .sub, .safety {{ color: #a9bdd7; line-height: 1.5; }}
    .vertical-section {{ margin: 22px 0; }} .vertical-section h2 {{ margin: 18px 0 9px; font-size: 1rem; color: #c8dbf2; }}
    .industries {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 10px; }}
    button {{ border: 1px solid #42658e; border-radius: 10px; padding: 11px 16px; color: #eaf2ff; background: #162b46; cursor: pointer; }}
    button.selected {{ background: #2f7cf6; border-color: #77aaff; }}
    .industry {{ min-height: 48px; text-align: center; }}
    form {{ display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }}
    label {{ display: grid; gap: 6px; color: #bcd0e8; font-size: .9rem; }} .wide {{ grid-column: 1 / -1; }}
    input, textarea {{ box-sizing: border-box; width: 100%; border: 1px solid #355273; border-radius: 9px; padding: 11px; background: #091526; color: white; }}
    textarea {{ min-height: 72px; resize: vertical; }} .check {{ display: flex; align-items: center; gap: 9px; }} .check input {{ width: auto; }}
    #launch {{ grid-column: 1 / -1; background: #18a66a; border-color: #5ad49c; font-weight: 700; }}
    #result {{ white-space: pre-wrap; min-height: 24px; padding-top: 12px; color: #b9d7ff; }}
    .summary {{ margin: 20px 0; padding: 18px; border: 1px solid #355273; border-radius: 12px; background: #091526; }}
    .summary h2 {{ margin: 0 0 12px; font-size: 1.05rem; }} .summary dl {{ display: grid; grid-template-columns: auto 1fr; gap: 8px 16px; margin: 0; }}
    .summary dt {{ color: #89a4c4; }} .summary dd {{ margin: 0; font-weight: 650; }}
    @media (max-width: 620px) {{ form {{ grid-template-columns: 1fr; }} .wide, #launch {{ grid-column: auto; }} }}
  </style>
</head>
<body><main><section class="card">
  <h1>CallPulse Campaign Launcher</h1>
  <p class="sub">Create a qualified prospect and schedule the safeguarded Day 0, 3, and 6 campaign.</p>
  <div class="vertical-section">
    <h2>Business vertical</h2>
    <div class="industries" aria-label="Business vertical">{general_buttons}</div>
    <h2>Insurance</h2>
    <div class="industries" aria-label="Insurance vertical">{insurance_buttons}</div>
  </div>
  <form id="campaign-form">
    <input id="industry" type="hidden" required>
    <label class="wide">Bearer API key<input id="api-key" type="password" autocomplete="off" required></label>
    <label>Company name<input id="company" required maxlength="200"></label>
    <label>Website<input id="website" type="url" placeholder="https://" required></label>
    <label>Location<input id="location" value="{DEFAULT_LOCATION}" required maxlength="200"></label>
    <label>Qualification score<input id="score" type="number" min="{MIN_QUALIFICATION_SCORE}" max="100" value="{MIN_QUALIFICATION_SCORE}" required></label>
    <label>Verified email<input id="email" type="email" required></label>
    <label class="wide">Why now<textarea id="why" required></textarea></label>
    <label class="wide">AI recovery opportunity<textarea id="opportunity" required></textarea></label>
    <label class="wide">Opening message<textarea id="message"></textarea></label>
    <label class="wide check"><input id="verified" type="checkbox" required> Email was independently verified</label>
    <section class="summary wide" aria-labelledby="summary-title">
      <h2 id="summary-title">Campaign summary</h2>
      <dl><dt>Vertical</dt><dd id="summary-industry">Not selected</dd><dt>Geography</dt><dd id="summary-location">{DEFAULT_LOCATION}</dd>
        <dt>Qualification</dt><dd>Score ≥ {MIN_QUALIFICATION_SCORE} + independently verified business email</dd>
        <dt>Dry-run</dt><dd>{str(DRY_RUN).lower()}</dd><dt>Sequence</dt><dd>Day 0 / Day 3 / Day 6 · stop on reply, opt-out, or hard bounce</dd></dl>
    </section>
    <button id="launch" type="submit">Create prospect &amp; launch campaign</button>
  </form>
  <p class="safety">Authentication, industry qualification, verified-email, suppression, idempotency, dry-run, and delivery checks are enforced by the API.</p>
  <output id="result" aria-live="polite"></output>
</section></main>
<script>
  const form = document.querySelector('#campaign-form');
  const result = document.querySelector('#result');
  const openingHelpers = {opening_helpers};
  const launcherState = {{ selectedIndustry: null }};
  const locationInput = document.querySelector('#location');
  locationInput.addEventListener('input', () => document.querySelector('#summary-location').textContent = locationInput.value || 'Not set');
  document.querySelectorAll('.industry').forEach(button => button.addEventListener('click', () => {{
    const selectedIndustry = button.dataset.industry;
    document.querySelectorAll('.industry').forEach(item => item.classList.remove('selected'));
    button.classList.add('selected');
    document.querySelectorAll('.industry').forEach(item => item.setAttribute('aria-pressed', String(item === button)));
    launcherState.selectedIndustry = selectedIndustry;
    document.querySelector('#industry').value = selectedIndustry;
    document.querySelector('#summary-industry').textContent = selectedIndustry;
    document.querySelector('#message').value = openingHelpers[selectedIndustry];
  }}));
  async function api(path, options) {{
    const response = await fetch(path, options);
    const body = await response.json();
    if (!response.ok) throw new Error(body.detail || `Request failed (${{response.status}})`);
    return body;
  }}
  form.addEventListener('submit', async event => {{
    event.preventDefault();
    if (!launcherState.selectedIndustry) {{ result.textContent = 'Select an industry.'; return; }}
    const headers = {{'Authorization': `Bearer ${{document.querySelector('#api-key').value}}`, 'Content-Type': 'application/json'}};
    result.textContent = 'Creating qualified prospect…';
    try {{
      const prospect = await api('/prospects', {{method: 'POST', headers, body: JSON.stringify({{
        company_name: document.querySelector('#company').value,
        website: document.querySelector('#website').value,
        industry: launcherState.selectedIndustry,
        location: locationInput.value,
        score: Number(document.querySelector('#score').value),
        why_now: document.querySelector('#why').value,
        ai_recovery_opportunity: document.querySelector('#opportunity').value,
        verified_email: document.querySelector('#email').value,
        email_verified: document.querySelector('#verified').checked,
        opening_message: document.querySelector('#message').value || null
      }})}});
      const campaign = await api(`/prospects/${{prospect.id}}/campaigns`, {{method: 'POST', headers, body: JSON.stringify({{
        idempotency_key: crypto.randomUUID()
      }})}});
      result.textContent = `Campaign #${{campaign.id}} scheduled safely for prospect #${{prospect.id}}.`;
    }} catch (error) {{ result.textContent = `Not launched: ${{error.message}}`; }}
  }});
</script></body></html>"""


@app.get("/industries", dependencies=[Depends(require_auth)])
def industry_buttons():
    return [{"label": name, "value": name, "section": "Insurance" if name in INSURANCE_INDUSTRIES else "Business verticals",
             "opening_message": OPENING_MESSAGE_HELPERS[name]} for name in INDUSTRIES]


@app.post("/prospects", status_code=201, dependencies=[Depends(require_auth)])
def create_prospect(body: ProspectIn, db: Session = Depends(db_session)):
    if not body.email_verified:
        raise HTTPException(422, "Only independently verified email addresses qualify")
    p = Prospect(**body.model_dump())
    db.add(p)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "Prospect email already exists")
    return prospect_dict(p)


@app.get("/prospects", dependencies=[Depends(require_auth)])
def list_prospects(status: str | None = None, industry: str | None = None,
                   limit: int = Query(25, ge=1, le=100), db: Session = Depends(db_session)):
    query = select(Prospect)
    if status:
        query = query.where(Prospect.status == status)
    if industry:
        query = query.where(Prospect.industry == industry)
    return [prospect_dict(p) for p in db.scalars(query.order_by(Prospect.score.desc()).limit(limit))]


@app.get("/prospects/{prospect_id}/campaigns", response_model=list[CampaignInspection],
         dependencies=[Depends(require_auth)], summary="Inspect a prospect's campaigns without changing state.")
def inspect_prospect_campaigns(prospect_id: int, db: Session = Depends(db_session)):
    prospect = db.get(Prospect, prospect_id)
    if not prospect:
        raise HTTPException(404, "Prospect not found")
    return [campaign_inspection(c) for c in sorted(prospect.campaigns, key=lambda campaign: campaign.id)]


@app.get("/campaigns/{campaign_id}/deliveries", response_model=list[DeliveryInspection],
         dependencies=[Depends(require_auth)], summary="Inspect campaign deliveries without running them.")
def inspect_campaign_deliveries(campaign_id: int, db: Session = Depends(db_session)):
    campaign = db.get(Campaign, campaign_id)
    if not campaign:
        raise HTTPException(404, "Campaign not found")
    return [delivery_inspection(t) for t in sorted(campaign.touches, key=lambda touch: touch.day)]


def authorization_failures(campaign: Campaign, body: LiveAuthorizationIn, db: Session) -> list[str]:
    prospect = campaign.prospect
    failures: list[str] = []
    if not body.authorized_by.strip(): failures.append("authorized_by is required")
    if body.confirmation != "AUTHORIZE LIVE OUTREACH": failures.append("confirmation phrase is invalid")
    if campaign.status != "active": failures.append("campaign is stopped or cancelled")
    if prospect_suppressed(prospect, db): failures.append("prospect is suppressed")
    if not valid_outreach_destination(prospect): failures.append("valid outreach destination is missing")
    if not campaign.touches: failures.append("campaign contains no deliveries")
    if any(not touch.idempotency_key or not touch.idempotency_key.strip() for touch in campaign.touches):
        failures.append("one or more deliveries lack an idempotency key")
    return failures


@app.post("/campaigns/{campaign_id}/authorize-live", dependencies=[Depends(require_auth)],
          summary="Explicitly authorize a campaign for future live execution.")
def authorize_live(campaign_id: int, body: LiveAuthorizationIn, db: Session = Depends(db_session)):
    campaign = db.get(Campaign, campaign_id)
    if not campaign:
        raise HTTPException(404, "Campaign not found")
    failures = authorization_failures(campaign, body, db)
    if failures:
        return JSONResponse(status_code=409, content={
            "detail": "Campaign failed live authorization safety checks", "failures": failures,
        })
    # An identical retry is a read of the established authorization decision.
    if not campaign.live_authorized:
        campaign.live_authorized = True
        campaign.live_authorized_at = utcnow()
        campaign.live_authorized_by = body.authorized_by.strip()
        campaign.dry_run = False
        for touch in campaign.touches:
            if touch.sent_at is None and not touch.skipped and not touch.cancelled:
                touch.dry_run = False
        db.commit()
    return {
        "campaign_id": campaign.id, "dry_run": campaign.dry_run,
        "live_authorized": campaign.live_authorized,
        "live_authorized_at": campaign.live_authorized_at,
        "live_authorized_by": campaign.live_authorized_by,
    }


@app.get("/campaigns/{campaign_id}/safety", dependencies=[Depends(require_auth)],
         summary="Inspect live-execution safety without changing state.")
def inspect_campaign_safety(campaign_id: int, db: Session = Depends(db_session)):
    campaign = db.get(Campaign, campaign_id)
    if not campaign:
        raise HTTPException(404, "Campaign not found")
    prospect = campaign.prospect
    eligible = [can_execute_delivery(t, campaign, prospect, db) for t in campaign.touches]
    failures = authorization_failures(
        campaign, LiveAuthorizationIn(authorized_by=campaign.live_authorized_by or "inspection",
                                      confirmation="AUTHORIZE LIVE OUTREACH"), db
    )
    return {
        "campaign_id": campaign.id, "dry_run": campaign.dry_run,
        "live_authorized": campaign.live_authorized,
        "live_authorized_at": campaign.live_authorized_at,
        "live_authorized_by": campaign.live_authorized_by,
        "suppressed": prospect_suppressed(prospect, db),
        "outreach_destination_present": valid_outreach_destination(prospect),
        "delivery_count": len(campaign.touches),
        "eligible_delivery_count": sum(item.eligible for item in eligible),
        "safety_failures": failures,
    }


@app.get("/deliveries/{delivery_id}/canary-preflight", response_model=DeliveryEligibility,
         dependencies=[Depends(require_auth)], summary="Read canary eligibility without changing state.")
def canary_preflight(delivery_id: int, db: Session = Depends(db_session)):
    touch = db.get(Touch, delivery_id)
    if not touch:
        raise HTTPException(404, "Delivery not found")
    return can_execute_delivery(touch, touch.campaign, touch.campaign.prospect, db,
                                check_provider=True)


@app.get("/deliveries/{delivery_id}/execution", dependencies=[Depends(require_auth)],
         summary="Inspect persisted canary execution state without executing.")
def inspect_delivery_execution(delivery_id: int, db: Session = Depends(db_session)):
    touch = db.get(Touch, delivery_id)
    if not touch:
        raise HTTPException(404, "Delivery not found")
    prospect = touch.campaign.prospect
    return {
        "delivery_id": touch.id, "campaign_id": touch.campaign_id,
        "prospect_id": prospect.id, "execution_status": touch.execution_status,
        "attempt_count": touch.execution_attempt_count, "sent_at": touch.sent_at,
        "provider": touch.provider_name, "provider_message_id": touch.provider_message_id,
        "idempotency_key": touch.idempotency_key, "sender": EMAIL_FROM or None,
        "recipient": prospect.verified_email, "last_execution_error": touch.last_execution_error,
    }


@app.post("/campaigns/{campaign_id}/canary-execute", dependencies=[Depends(require_auth)],
          summary="Explicitly attempt no more than one persisted email delivery.")
def canary_execute(campaign_id: int, body: CanaryExecutionIn, db: Session = Depends(db_session)):
    """Claim exactly one explicitly named delivery; never enumerate campaign deliveries."""
    campaign = db.get(Campaign, campaign_id)
    if not campaign:
        return JSONResponse(status_code=409, content={
            "detail": "Canary execution blocked by safety checks", "failures": ["campaign does not exist"],
        })
    touch = db.get(Touch, body.delivery_id)
    if not touch:
        return JSONResponse(status_code=409, content={
            "detail": "Canary execution blocked by safety checks", "failures": ["delivery does not exist"],
        })
    if touch.campaign_id != campaign.id:
        return JSONResponse(status_code=409, content={
            "detail": "Canary execution blocked by safety checks",
            "failures": ["delivery does not belong to the specified campaign"],
        })

    # A retry after confirmed success is an idempotent read and never reaches a provider.
    if touch.sent_at is not None or touch.execution_status == "sent":
        return {"delivery_id": touch.id, "execution_status": "sent", "already_executed": True,
                "provider_message_id": touch.provider_message_id}

    eligibility = can_execute_delivery(
        touch, campaign, campaign.prospect, db, authorized_by=body.authorized_by,
        confirmation=body.confirmation, check_provider=True,
    )
    if not eligibility.eligible:
        add_canary_audit(db, touch, body.authorized_by, "blocked", "; ".join(eligibility.failures))
        db.commit()
        return JSONResponse(status_code=409, content={
            "detail": "Canary execution blocked by safety checks", "failures": eligibility.failures,
        })

    if EMAIL_PROVIDER_NAME not in {"mock", "webhook"}:
        add_canary_audit(db, touch, body.authorized_by, "failed", "email delivery provider is not configured")
        db.commit()
        raise HTTPException(503, "No email delivery provider is configured; no email was sent")
    try:
        provider = configured_provider(EMAIL_PROVIDER_NAME, DELIVERY_WEBHOOK)
    except ValueError as exc:
        add_canary_audit(db, touch, body.authorized_by, "failed", str(exc))
        db.commit()
        raise HTTPException(503, f"Email provider configuration is incomplete: {exc}")

    started = utcnow()
    # Atomic compare-and-set is PostgreSQL safe and also makes concurrent test requests safe.
    claimed = db.execute(update(Touch).where(
        Touch.id == touch.id, Touch.sent_at.is_(None),
        Touch.execution_status.not_in(("sending", "sent")),
    ).values(
        execution_status="sending", execution_started_at=started,
        execution_completed_at=None, last_execution_error=None,
        execution_attempt_count=Touch.execution_attempt_count + 1,
        provider_name=provider.name,
    )).rowcount
    db.commit()
    if claimed != 1:
        db.refresh(touch)
        if touch.sent_at is not None or touch.execution_status == "sent":
            return {"delivery_id": touch.id, "execution_status": "sent", "already_executed": True,
                    "provider_message_id": touch.provider_message_id}
        return JSONResponse(status_code=409, content={
            "detail": "Canary execution blocked by safety checks",
            "failures": ["another execution is already in flight"],
        })

    # Re-load and re-check all mutable stop state immediately before the sole provider call.
    db.expire_all()
    touch = db.get(Touch, body.delivery_id)
    final_check = can_execute_delivery(
        touch, touch.campaign, touch.campaign.prospect, db,
        authorized_by=body.authorized_by, confirmation=body.confirmation,
    )
    final_failures = [reason for reason in final_check.failures
                      if reason != "another execution is already in flight"]
    if final_failures:
        touch.execution_status = "blocked"
        touch.execution_completed_at = utcnow()
        touch.last_execution_error = "; ".join(final_failures)
        add_canary_audit(db, touch, body.authorized_by, "blocked", touch.last_execution_error)
        db.commit()
        return JSONResponse(status_code=409, content={
            "detail": "Canary execution blocked by safety checks", "failures": final_failures,
        })

    try:
        result = provider.send(
            sender=EMAIL_FROM, recipient=touch.campaign.prospect.verified_email,
            message=touch.message, idempotency_key=touch.idempotency_key,
        )
    except Exception as exc:
        safe_error = f"{type(exc).__name__}: email provider call failed"[:500]
        touch.execution_status = "failed"
        touch.execution_completed_at = utcnow()
        touch.last_execution_error = safe_error
        add_canary_audit(db, touch, body.authorized_by, "failed", safe_error)
        db.commit()
        raise HTTPException(502, "Email provider call failed; no send was confirmed")

    completed = utcnow()
    touch.status, touch.sent_at, touch.execution_status = "sent", completed, "sent"
    touch.execution_completed_at = completed
    touch.provider_message_id = result.message_id
    touch.last_execution_error = None
    add_canary_audit(db, touch, body.authorized_by, "sent", provider_message_id=result.message_id)
    db.commit()
    return {"delivery_id": touch.id, "execution_status": "sent", "already_executed": False,
            "sent_at": touch.sent_at, "provider": provider.name,
            "provider_message_id": touch.provider_message_id}


@app.post("/prospects/{prospect_id}/campaigns", status_code=201, dependencies=[Depends(require_auth)])
def launch_campaign(prospect_id: int, body: CampaignIn, db: Session = Depends(db_session)):
    p = db.get(Prospect, prospect_id)
    if not p:
        raise HTTPException(404, "Prospect not found")
    if not p.email_verified or not p.verified_email:
        raise HTTPException(422, "A verified email is required")
    if p.score < MIN_QUALIFICATION_SCORE:
        raise HTTPException(422, f"Qualification score must be at least {MIN_QUALIFICATION_SCORE}")
    if db.get(Suppression, p.verified_email):
        raise HTTPException(409, "Recipient is suppressed")
    existing = db.scalar(select(Campaign).where(Campaign.prospect_id == p.id))
    if existing:
        return campaign_dict(existing)
    start = body.start_at or utcnow()
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    campaign = Campaign(prospect=p, starts_at=start, ends_at=start + timedelta(days=7))
    templates = {
        0: p.opening_message or OPENING_MESSAGE_HELPERS[p.industry],
        3: "Following up with a practical way to recover missed opportunities without adding staff.",
        6: "Last note from me—should I close the loop, or is a short recovery demo useful?",
    }
    for day in TOUCH_DAYS:
        key = hashlib.sha256(f"{body.idempotency_key}:{p.id}:{day}".encode()).hexdigest()
        campaign.touches.append(Touch(day=day, scheduled_at=start + timedelta(days=day), message=templates[day], idempotency_key=key))
    p.status, p.updated_at = "campaign_active", utcnow()
    db.add(campaign)
    db.commit()
    return campaign_dict(campaign)


@app.post("/launcher/run", dependencies=[Depends(require_auth)])
def run_launcher(now_at: datetime | None = None, db: Session = Depends(db_session)):
    current = now_at or utcnow()
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    due = list(db.scalars(select(Touch).where(Touch.status == "scheduled", Touch.scheduled_at <= current).order_by(Touch.scheduled_at)))
    sent = skipped = failed = 0
    for touch in due:
        p = touch.campaign.prospect
        if touch.campaign.status != "active" or p.status in {"replied", "qualified", "converted", "suppressed"} or db.get(Suppression, p.verified_email):
            touch.status = "suppressed"
            touch.skipped = True
            touch.cancellation_or_skip_reason = "prospect suppression or campaign stop"
            skipped += 1
        elif touch.campaign.dry_run:
            try:
                touch.status = "simulated"
                touch.sent_at = current
                sent += 1
            except Exception:
                # Leave the touch scheduled for a safe retry; never claim an unconfirmed send.
                failed += 1
        else:
            # Live-authorized records are only made eligible here. Provider execution is
            # deliberately outside this API and this safety-gate implementation.
            can_execute_delivery(touch, touch.campaign, p, db, current)
    db.commit()
    return {"processed": len(due), "sent_or_simulated": sent, "suppressed": skipped, "failed": failed, "dry_run": DRY_RUN}


@app.post("/suppressions", status_code=201, dependencies=[Depends(require_auth)])
def suppress(body: SuppressionIn, db: Session = Depends(db_session)):
    item = db.get(Suppression, body.email)
    if not item:
        item = Suppression(email=body.email, reason=body.reason)
        db.add(item)
    for p in db.scalars(select(Prospect).where(Prospect.verified_email == body.email)):
        p.status = "suppressed"
        for campaign in p.campaigns:
            campaign.status = "suppressed"
            for touch in campaign.touches:
                if touch.sent_at is None and not touch.skipped and not touch.cancelled:
                    touch.status = "suppressed"
                    touch.skipped = True
                    touch.cancellation_or_skip_reason = f"suppression: {body.reason}"
    db.commit()
    return {"email": item.email, "reason": item.reason}


@app.post("/prospects/{prospect_id}/reply", dependencies=[Depends(require_auth)])
def record_reply(prospect_id: int, body: ReplyIn, db: Session = Depends(db_session)):
    p = db.get(Prospect, prospect_id)
    if not p:
        raise HTTPException(404, "Prospect not found")
    p.last_reply, p.intent = body.reply_text, body.intent
    p.status = "qualified" if body.intent in {"interested", "pricing", "ready_to_start", "trial_interest"} else "replied"
    for campaign in p.campaigns:
        campaign.status = "stopped"
    db.commit()
    return prospect_dict(p)


@app.post("/prospects/{prospect_id}/conversion", dependencies=[Depends(require_auth)])
def conversion(prospect_id: int, body: ConversionIn, db: Session = Depends(db_session)):
    p = db.get(Prospect, prospect_id)
    if not p:
        raise HTTPException(404, "Prospect not found")
    p.conversion_stage = body.conversion_stage
    p.status = "converted" if body.conversion_stage in {"standard_start", "three_day_trial", "converted"} else "qualified"
    db.commit()
    return prospect_dict(p)
