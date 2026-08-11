"""CallPulse autonomous seven-day prospecting campaign API."""
from __future__ import annotations

import hashlib
import json
import os
import re
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, create_engine, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship, sessionmaker

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:////tmp/callpulse.db")
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+psycopg://", 1)
elif DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg://", 1)
API_KEY = os.getenv("CALLPULSE_ACTIONS_API_KEY", "")
DRY_RUN = os.getenv("CALLPULSE_DRY_RUN", "true").lower() != "false"
DELIVERY_WEBHOOK = os.getenv("CALLPULSE_DELIVERY_WEBHOOK", "")
TOUCH_DAYS = (0, 3, 6)
INDUSTRIES = ("Final Expense", "Auto Insurance")
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
    campaign: Mapped[Campaign] = relationship(back_populates="touches")


class Suppression(Base):
    __tablename__ = "suppressions"
    email: Mapped[str] = mapped_column(String(320), primary_key=True)
    reason: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


Base.metadata.create_all(engine)
app = FastAPI(title="CallPulse Autonomous Campaign API", version="3.0.0")


def db_session():
    with SessionLocal() as db:
        yield db


def require_auth(authorization: str | None = Header(default=None)):
    if not API_KEY or authorization != f"Bearer {API_KEY}":
        raise HTTPException(401, "Valid bearer authentication is required")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def normalize_email(email: str) -> str:
    email = email.strip().lower()
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        raise ValueError("A syntactically valid email is required")
    return email


def deliver(email: str, message: str, idempotency_key: str) -> None:
    """Send through the operator's HTTPS adapter; non-2xx responses fail the touch."""
    if not DELIVERY_WEBHOOK.startswith("https://"):
        raise RuntimeError("CALLPULSE_DELIVERY_WEBHOOK must be an HTTPS URL")
    payload = json.dumps({"to": email, "message": message, "idempotency_key": idempotency_key}).encode()
    request = urllib.request.Request(DELIVERY_WEBHOOK, data=payload, method="POST", headers={
        "Content-Type": "application/json", "Idempotency-Key": idempotency_key})
    with urllib.request.urlopen(request, timeout=20) as response:
        if not 200 <= response.status < 300:
            raise RuntimeError(f"Delivery adapter returned HTTP {response.status}")


class ProspectIn(BaseModel):
    company_name: str = Field(min_length=1, max_length=200)
    website: str = Field(pattern=r"^https?://")
    industry: Literal["Final Expense", "Auto Insurance"]
    score: int = Field(ge=0, le=100)
    why_now: str = Field(min_length=1)
    ai_recovery_opportunity: str = Field(min_length=1)
    decision_maker_name: str | None = None
    decision_maker_title: str | None = None
    verified_email: str
    email_verified: bool
    opening_message: str | None = None

    @field_validator("verified_email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        return normalize_email(value)


class CampaignIn(BaseModel):
    start_at: datetime | None = None
    idempotency_key: str = Field(min_length=8, max_length=100)


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


def prospect_dict(p: Prospect) -> dict:
    return {c.name: getattr(p, c.name) for c in p.__table__.columns}


def campaign_dict(c: Campaign) -> dict:
    return {"id": c.id, "prospect_id": c.prospect_id, "status": c.status, "starts_at": c.starts_at,
            "ends_at": c.ends_at, "touches": [{x.name: getattr(t, x.name) for x in t.__table__.columns} for t in sorted(c.touches, key=lambda x: x.day)]}


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
    industry_buttons = "".join(
        f'<button type="button" class="industry" data-industry="{name}">{name}</button>'
        for name in INDUSTRIES
    )
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>CallPulse Campaign Launcher</title>
  <style>
    :root {{ color-scheme: dark; font-family: Inter, system-ui, sans-serif; background: #07111f; color: #eaf2ff; }}
    body {{ margin: 0; min-height: 100vh; background: radial-gradient(circle at top, #173052, #07111f 55%); }}
    main {{ width: min(760px, calc(100% - 32px)); margin: 48px auto; }}
    .card {{ background: #0e1c30; border: 1px solid #294363; border-radius: 18px; padding: 28px; box-shadow: 0 24px 70px #0008; }}
    h1 {{ margin: 0 0 8px; }} .sub, .safety {{ color: #a9bdd7; line-height: 1.5; }}
    .industries {{ display: flex; gap: 10px; margin: 22px 0; }}
    button {{ border: 1px solid #42658e; border-radius: 10px; padding: 11px 16px; color: #eaf2ff; background: #162b46; cursor: pointer; }}
    button.selected {{ background: #2f7cf6; border-color: #77aaff; }}
    form {{ display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }}
    label {{ display: grid; gap: 6px; color: #bcd0e8; font-size: .9rem; }} .wide {{ grid-column: 1 / -1; }}
    input, textarea {{ box-sizing: border-box; width: 100%; border: 1px solid #355273; border-radius: 9px; padding: 11px; background: #091526; color: white; }}
    textarea {{ min-height: 72px; resize: vertical; }} .check {{ display: flex; align-items: center; gap: 9px; }} .check input {{ width: auto; }}
    #launch {{ grid-column: 1 / -1; background: #18a66a; border-color: #5ad49c; font-weight: 700; }}
    #result {{ white-space: pre-wrap; min-height: 24px; padding-top: 12px; color: #b9d7ff; }}
    @media (max-width: 620px) {{ form {{ grid-template-columns: 1fr; }} .wide, #launch {{ grid-column: auto; }} }}
  </style>
</head>
<body><main><section class="card">
  <h1>CallPulse Campaign Launcher</h1>
  <p class="sub">Create a qualified prospect and schedule the safeguarded Day 0, 3, and 6 campaign.</p>
  <div class="industries" aria-label="Industry">{industry_buttons}</div>
  <form id="campaign-form">
    <input id="industry" type="hidden" required>
    <label class="wide">Bearer API key<input id="api-key" type="password" autocomplete="off" required></label>
    <label>Company name<input id="company" required maxlength="200"></label>
    <label>Website<input id="website" type="url" placeholder="https://" required></label>
    <label>Qualification score<input id="score" type="number" min="0" max="100" required></label>
    <label>Verified email<input id="email" type="email" required></label>
    <label class="wide">Why now<textarea id="why" required></textarea></label>
    <label class="wide">AI recovery opportunity<textarea id="opportunity" required></textarea></label>
    <label class="wide">Opening message<textarea id="message"></textarea></label>
    <label class="wide check"><input id="verified" type="checkbox" required> Email was independently verified</label>
    <button id="launch" type="submit">Create prospect &amp; launch campaign</button>
  </form>
  <p class="safety">Authentication, industry qualification, verified-email, suppression, idempotency, dry-run, and delivery checks are enforced by the API.</p>
  <output id="result" aria-live="polite"></output>
</section></main>
<script>
  const form = document.querySelector('#campaign-form');
  const result = document.querySelector('#result');
  document.querySelectorAll('.industry').forEach(button => button.addEventListener('click', () => {{
    document.querySelectorAll('.industry').forEach(item => item.classList.remove('selected'));
    button.classList.add('selected');
    document.querySelector('#industry').value = button.dataset.industry;
  }}));
  async function api(path, options) {{
    const response = await fetch(path, options);
    const body = await response.json();
    if (!response.ok) throw new Error(body.detail || `Request failed (${{response.status}})`);
    return body;
  }}
  form.addEventListener('submit', async event => {{
    event.preventDefault();
    if (!document.querySelector('#industry').value) {{ result.textContent = 'Select an industry.'; return; }}
    const headers = {{'Authorization': `Bearer ${{document.querySelector('#api-key').value}}`, 'Content-Type': 'application/json'}};
    result.textContent = 'Creating qualified prospect…';
    try {{
      const prospect = await api('/prospects', {{method: 'POST', headers, body: JSON.stringify({{
        company_name: document.querySelector('#company').value,
        website: document.querySelector('#website').value,
        industry: document.querySelector('#industry').value,
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
    return [{"label": name, "value": name} for name in INDUSTRIES]


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


@app.post("/prospects/{prospect_id}/campaigns", status_code=201, dependencies=[Depends(require_auth)])
def launch_campaign(prospect_id: int, body: CampaignIn, db: Session = Depends(db_session)):
    p = db.get(Prospect, prospect_id)
    if not p:
        raise HTTPException(404, "Prospect not found")
    if not p.email_verified or not p.verified_email:
        raise HTTPException(422, "A verified email is required")
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
        0: p.opening_message or f"A quick idea for {p.company_name}'s missed-call recovery.",
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
            skipped += 1
        else:
            try:
                if not DRY_RUN:
                    deliver(p.verified_email, touch.message, touch.idempotency_key)
                touch.status = "simulated" if DRY_RUN else "sent"
                touch.sent_at = current
                sent += 1
            except Exception:
                # Leave the touch scheduled for a safe retry; never claim an unconfirmed send.
                failed += 1
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
