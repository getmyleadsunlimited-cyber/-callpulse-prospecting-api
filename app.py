import hashlib
import os
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, create_engine, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship, sessionmaker

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:////tmp/callpulse.db")
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+psycopg://", 1)
elif DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg://", 1)
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {})
SessionLocal = sessionmaker(engine, expire_on_commit=False)


class Base(DeclarativeBase): pass
class Campaign(Base):
    __tablename__ = "campaigns"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    industry: Mapped[str] = mapped_column(String(100))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    prospects: Mapped[list["Prospect"]] = relationship(cascade="all, delete-orphan")
class Prospect(Base):
    __tablename__ = "prospects"
    __table_args__ = (UniqueConstraint("campaign_id", "email", name="uq_campaign_email"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    campaign_id: Mapped[int] = mapped_column(ForeignKey("campaigns.id", ondelete="CASCADE"), index=True)
    company_name: Mapped[str] = mapped_column(String(200))
    email: Mapped[str] = mapped_column(String(320))
    email_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    score: Mapped[int] = mapped_column(Integer)
    replied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    opted_out_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    hard_bounced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    deliveries: Mapped[list["Delivery"]] = relationship(cascade="all, delete-orphan")
class Delivery(Base):
    __tablename__ = "deliveries"
    __table_args__ = (UniqueConstraint("prospect_id", "day", name="uq_prospect_day"), UniqueConstraint("idempotency_key", name="uq_delivery_key"))
    id: Mapped[int] = mapped_column(primary_key=True)
    prospect_id: Mapped[int] = mapped_column(ForeignKey("prospects.id", ondelete="CASCADE"), index=True)
    day: Mapped[int] = mapped_column(Integer)
    scheduled_for: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    status: Mapped[str] = mapped_column(String(30), default="scheduled")
    idempotency_key: Mapped[str] = mapped_column(String(64))
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

Base.metadata.create_all(engine)
app = FastAPI(title="CallPulse Prospecting API", version="2.0.0")
INDUSTRIES = ["Roofing", "HVAC", "Plumbing", "Dental", "Med Spa", "Legal", "Real Estate", "Final Expense", "Auto Insurance"]

def db():
    with SessionLocal() as session: yield session
def auth(authorization: str | None = Header(None)):
    key = os.getenv("CALLPULSE_ACTIONS_API_KEY", "")
    if key and authorization != f"Bearer {key}": raise HTTPException(401, "Unauthorized")
def get_campaign(cid: int, session: Session) -> Campaign:
    obj = session.get(Campaign, cid)
    if not obj: raise HTTPException(404, "Campaign not found")
    return obj

class CampaignIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    industry: str
class ProspectIn(BaseModel):
    company_name: str
    email: EmailStr
    email_verified: bool = False
    score: int = Field(ge=0, le=100)
class EventIn(BaseModel):
    event: Literal["reply", "opt_out", "hard_bounce"]

@app.get("/")
def root(): return {"service": "CallPulse Prospecting API", "status": "online"}
@app.get("/health")
def health(session: Session = Depends(db)):
    session.execute(select(1))
    return {"ok": True}
@app.get("/industries")
def industries(_: None = Depends(auth)): return {"industries": INDUSTRIES}
@app.get("/launcher", response_class=HTMLResponse)
def launcher():
    buttons = "".join(f'<button type="button" data-industry="{x}">{x}</button>' for x in INDUSTRIES)
    return f"<!doctype html><html><head><title>Campaign Launcher</title></head><body><main><h1>Launch a campaign</h1>{buttons}</main></body></html>"
@app.post("/campaigns", status_code=201)
def create_campaign(body: CampaignIn, session: Session = Depends(db), _: None = Depends(auth)):
    if body.industry not in INDUSTRIES: raise HTTPException(422, "Unsupported industry")
    obj = Campaign(name=body.name, industry=body.industry); session.add(obj); session.commit(); session.refresh(obj)
    return campaign_json(obj)
@app.get("/campaigns")
def campaigns(session: Session = Depends(db), _: None = Depends(auth)):
    return [campaign_json(x) for x in session.scalars(select(Campaign).order_by(Campaign.id.desc()))]
@app.get("/campaigns/{campaign_id}")
def campaign(campaign_id: int, session: Session = Depends(db), _: None = Depends(auth)): return campaign_json(get_campaign(campaign_id, session))
@app.delete("/campaigns/{campaign_id}", status_code=204)
def delete_campaign(campaign_id: int, session: Session = Depends(db), _: None = Depends(auth)):
    session.delete(get_campaign(campaign_id, session)); session.commit()
@app.post("/campaigns/{campaign_id}/prospects", status_code=201)
def add_prospect(campaign_id: int, body: ProspectIn, session: Session = Depends(db), _: None = Depends(auth)):
    get_campaign(campaign_id, session)
    email = str(body.email).lower()
    existing = session.scalar(select(Prospect).where(Prospect.campaign_id == campaign_id, Prospect.email == email))
    if existing: return prospect_json(existing)
    obj = Prospect(campaign_id=campaign_id, company_name=body.company_name, email=email, email_verified=body.email_verified, score=body.score)
    session.add(obj); session.flush()
    for day in (0, 3, 6):
        key = hashlib.sha256(f"{obj.id}:{day}".encode()).hexdigest()
        session.add(Delivery(prospect_id=obj.id, day=day, scheduled_for=obj.created_at + timedelta(days=day), idempotency_key=key))
    session.commit(); session.refresh(obj); return prospect_json(obj)
@app.get("/campaigns/{campaign_id}/prospects")
def prospects(campaign_id: int, session: Session = Depends(db), _: None = Depends(auth)):
    get_campaign(campaign_id, session)
    return [prospect_json(x) for x in session.scalars(select(Prospect).where(Prospect.campaign_id == campaign_id))]
@app.post("/prospects/{prospect_id}/events")
def event(prospect_id: int, body: EventIn, session: Session = Depends(db), _: None = Depends(auth)):
    p = session.get(Prospect, prospect_id)
    if not p: raise HTTPException(404, "Prospect not found")
    stamp = datetime.now(timezone.utc)
    setattr(p, {"reply": "replied_at", "opt_out": "opted_out_at", "hard_bounce": "hard_bounced_at"}[body.event], stamp)
    for d in p.deliveries:
        if d.status == "scheduled": d.status = "cancelled"
    session.commit(); return prospect_json(p)
@app.post("/deliveries/run")
def run_deliveries(session: Session = Depends(db), _: None = Depends(auth)):
    now = datetime.now(timezone.utc); sent = 0
    due = session.scalars(select(Delivery).where(Delivery.status == "scheduled", Delivery.scheduled_for <= now)).all()
    for delivery in due:
        p = session.get(Prospect, delivery.prospect_id)
        if p.score < 65 or not p.email_verified or p.replied_at or p.opted_out_at or p.hard_bounced_at:
            delivery.status = "cancelled"
        else:
            # Intentionally records a delivery only. A separate, explicitly configured worker may send email.
            delivery.status = "delivered"; delivery.delivered_at = now; sent += 1
    session.commit(); return {"processed": len(due), "delivered": sent}

def campaign_json(x): return {"id": x.id, "name": x.name, "industry": x.industry, "active": x.active, "created_at": x.created_at}
def prospect_json(x):
    return {"id": x.id, "campaign_id": x.campaign_id, "company_name": x.company_name, "email": x.email, "email_verified": x.email_verified, "score": x.score,
            "replied_at": x.replied_at, "opted_out_at": x.opted_out_at, "hard_bounced_at": x.hard_bounced_at,
            "deliveries": [{"day": d.day, "status": d.status, "scheduled_for": d.scheduled_for, "idempotency_key": d.idempotency_key} for d in sorted(x.deliveries, key=lambda d: d.day)]}
