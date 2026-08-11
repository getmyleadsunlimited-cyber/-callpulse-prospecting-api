"""CallPulse autonomous seven-day prospecting campaigns."""
import os
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, JSON, String, Text, UniqueConstraint, create_engine, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql+psycopg://callpulse:callpulse@localhost/callpulse")
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+psycopg://", 1)
API_KEY = os.getenv("CALLPULSE_ACTIONS_API_KEY", "")
engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(engine, expire_on_commit=False)

INDUSTRIES = ["eCommerce", "Roofing", "HVAC", "Dental", "Garage Door Repair", "Plumbing", "Emergency Towing", "Water Restoration", "Mold Remediation", "Pest Control", "Electrical", "Foundation Repair", "Tree Service", "Pool Service", "Landscaping / Lawn Care", "Med Spa", "Final Expense", "Auto Insurance"]
OPENING_ANGLES = {
    "eCommerce": "What happens to shoppers who visit your store, consider a product, and leave without purchasing?",
    "Roofing": "What happens to homeowners who visit for an inspection or replacement and leave without calling or requesting an estimate?",
    "HVAC": "What happens to homeowners who visit for a repair or replacement and leave without booking service?",
    "Dental": "What happens to patients who visit for treatment and leave without scheduling an appointment?",
    "Garage Door Repair": "What happens to homeowners who visit for garage door repair and leave without calling or booking service?",
    "Plumbing": "What happens to homeowners who visit with a plumbing need and leave without calling or booking service?",
    "Emergency Towing": "What happens to stranded drivers who visit for towing and leave without calling for help?",
    "Water Restoration": "What happens to property owners who visit after water damage and leave without requesting an assessment?",
    "Mold Remediation": "What happens to property owners who visit about mold and leave without requesting an inspection?",
    "Pest Control": "What happens to homeowners who visit about a pest problem and leave without booking an inspection?",
    "Electrical": "What happens to homeowners who visit for electrical work and leave without requesting service?",
    "Foundation Repair": "What happens to homeowners who visit about foundation concerns and leave without requesting an inspection?",
    "Tree Service": "What happens to property owners who visit for tree work and leave without requesting an estimate?",
    "Pool Service": "What happens to pool owners who visit for service and leave without booking an appointment?",
    "Landscaping / Lawn Care": "What happens to property owners who visit for lawn care and leave without requesting an estimate?",
    "Med Spa": "What happens to prospective clients who visit for a treatment and leave without booking a consultation?",
    "Final Expense": "What happens to families who visit to explore final-expense coverage and leave without requesting a quote?",
    "Auto Insurance": "What happens to drivers who visit for auto coverage and leave without requesting a quote?",
}
LOCAL_SERVICES = set(INDUSTRIES) - {"eCommerce", "Roofing", "Final Expense", "Auto Insurance"}

def proof_for(industry: str) -> str:
    if industry == "eCommerce": return "TrendChasers proof"
    if industry == "Roofing": return "Roofing proof"
    return "Closest local-service proof" if industry in LOCAL_SERVICES else "Relevant documented insurance proof"

def utcnow() -> datetime: return datetime.now(timezone.utc)

class Base(DeclarativeBase): pass
class Campaign(Base):
    __tablename__ = "campaigns"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    industry: Mapped[str] = mapped_column(String(80))
    geography: Mapped[str] = mapped_column(String(200))
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date] = mapped_column(Date)
    daily_first_touch_limit: Mapped[int] = mapped_column(Integer, default=25)
    sending_window: Mapped[dict] = mapped_column(JSON, default=lambda: {"start":"09:00", "end":"17:00"})
    timezone: Mapped[str] = mapped_column(String(80), default="America/Chicago")
    minimum_score: Mapped[int] = mapped_column(Integer, default=65)
    allowed_priority_levels: Mapped[list] = mapped_column(JSON, default=lambda: ["A", "B"])
    auto_approve_qualified_prospects: Mapped[bool] = mapped_column(Boolean, default=True)
    verified_business_email_required: Mapped[bool] = mapped_column(Boolean, default=True)
    duplicate_suppression: Mapped[bool] = mapped_column(Boolean, default=True)
    opt_out_suppression: Mapped[bool] = mapped_column(Boolean, default=True)
    hard_bounce_suppression: Mapped[bool] = mapped_column(Boolean, default=True)
    stop_on_reply: Mapped[bool] = mapped_column(Boolean, default=True)
    automatic_prospect_replenishment: Mapped[bool] = mapped_column(Boolean, default=True)
    status: Mapped[str] = mapped_column(String(20), default="draft")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
class Prospect(Base):
    __tablename__ = "prospects"
    __table_args__ = (UniqueConstraint("campaign_id", "normalized_email", name="uq_campaign_prospect_email"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    company_name: Mapped[str] = mapped_column(String(200)); website: Mapped[str] = mapped_column(String(500)); industry: Mapped[str] = mapped_column(String(80))
    score: Mapped[int] = mapped_column(Integer); priority: Mapped[str] = mapped_column(String(2), default="B")
    why_now: Mapped[str] = mapped_column(Text); ai_recovery_opportunity: Mapped[str] = mapped_column(Text)
    verified_facts: Mapped[list] = mapped_column(JSON, default=list); decision_maker_name: Mapped[str|None] = mapped_column(String(200)); decision_maker_title: Mapped[str|None] = mapped_column(String(200))
    best_contact_channel: Mapped[str|None] = mapped_column(String(40)); verified_contact: Mapped[str|None] = mapped_column(String(320)); normalized_email: Mapped[str|None] = mapped_column(String(320))
    opening_message: Mapped[str|None] = mapped_column(Text); status: Mapped[str] = mapped_column(String(30), default="researched"); draft_message: Mapped[str|None] = mapped_column(Text); last_reply: Mapped[str|None] = mapped_column(Text); intent: Mapped[str|None] = mapped_column(String(40)); conversion_stage: Mapped[str|None] = mapped_column(String(40))
    campaign_id: Mapped[int|None] = mapped_column(ForeignKey("campaigns.id")); campaign_approved: Mapped[bool] = mapped_column(Boolean, default=False); email_verified: Mapped[bool] = mapped_column(Boolean, default=False); suppression_status: Mapped[str] = mapped_column(String(30), default="clear")
    sequence_step: Mapped[int] = mapped_column(Integer, default=0); next_send_at: Mapped[datetime|None] = mapped_column(DateTime(timezone=True)); last_sent_at: Mapped[datetime|None] = mapped_column(DateTime(timezone=True)); sent_count: Mapped[int] = mapped_column(Integer, default=0); reply_detected: Mapped[bool] = mapped_column(Boolean, default=False); bounced: Mapped[bool] = mapped_column(Boolean, default=False); opted_out: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow); updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
class Delivery(Base):
    __tablename__ = "deliveries"
    __table_args__ = (UniqueConstraint("prospect_id", "sequence_step", name="uq_delivery_step"), UniqueConstraint("idempotency_key", name="uq_delivery_idempotency"))
    id: Mapped[int] = mapped_column(primary_key=True); prospect_id: Mapped[int] = mapped_column(ForeignKey("prospects.id")); campaign_id: Mapped[int] = mapped_column(ForeignKey("campaigns.id")); sequence_step: Mapped[int] = mapped_column(Integer); idempotency_key: Mapped[str] = mapped_column(String(200)); provider_message_id: Mapped[str] = mapped_column(String(300)); delivered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

def init_db(): Base.metadata.create_all(engine)
app = FastAPI(title="CallPulse Autonomous Prospecting API", version="2.0.0")
@app.on_event("startup")
def startup(): init_db()
def db():
    with SessionLocal() as session: yield session
def auth(authorization: str|None=Header(default=None)):
    if API_KEY and authorization != f"Bearer {API_KEY}": raise HTTPException(401, "Unauthorized")
def row(obj): return {c.name: getattr(obj, c.name) for c in obj.__table__.columns}
def get_campaign_or_404(s, campaign_id):
    item=s.get(Campaign,campaign_id)
    if not item: raise HTTPException(404,"Campaign not found")
    return item
def get_prospect_or_404(s, prospect_id):
    item=s.get(Prospect,prospect_id)
    if not item: raise HTTPException(404,"Prospect not found")
    return item

class CampaignIn(BaseModel):
    name:str; industry:str; geography:str; start_date:date; end_date:date|None=None; daily_first_touch_limit:int=Field(25,gt=0); sending_window:dict[str,str]=Field(default_factory=lambda:{"start":"09:00","end":"17:00"}); timezone:str="America/Chicago"; minimum_score:int=Field(65,ge=0,le=100); allowed_priority_levels:list[str]=Field(default_factory=lambda:["A","B"]); auto_approve_qualified_prospects:bool=True; verified_business_email_required:bool=True; duplicate_suppression:bool=True; opt_out_suppression:bool=True; hard_bounce_suppression:bool=True; stop_on_reply:bool=True; automatic_prospect_replenishment:bool=True
    @model_validator(mode="after")
    def validate_campaign(self):
        if self.industry not in INDUSTRIES: raise ValueError("Unsupported industry")
        self.end_date = self.end_date or self.start_date + timedelta(days=6)
        if self.end_date != self.start_date + timedelta(days=6): raise ValueError("Campaign must span exactly 7 calendar days")
        for key in ("start","end"): time.fromisoformat(self.sending_window[key])
        return self
class ProspectIn(BaseModel):
    company_name:str; website:str; industry:str; score:int=Field(ge=0,le=100); priority:str="B"; why_now:str; ai_recovery_opportunity:str; verified_facts:list[str]=Field(default_factory=list); decision_maker_name:str|None=None; decision_maker_title:str|None=None; best_contact_channel:str|None=None; verified_contact:str|None=None; email_verified:bool=False; opening_message:str|None=None
class DeliveryIn(BaseModel): prospect_id:int; sequence_step:int=Field(ge=0,le=2); idempotency_key:str; provider_message_id:str
class EventIn(BaseModel): prospect_id:int; hard:bool=True; detail:str|None=None
class ReplyEvent(BaseModel): prospect_id:int; reply_text:str; intent:str="unknown"

@app.get("/")
def root(): return {"service":app.title,"status":"online","launcher":"/launcher"}
@app.get("/health")
def health(): return {"ok":True,"database":"postgresql" if engine.dialect.name=="postgresql" else engine.dialect.name}
@app.post("/campaigns", operation_id="createCampaign")
def create_campaign(body:CampaignIn, _:Any=Depends(auth), s:Session=Depends(db)):
    item=Campaign(**body.model_dump()); s.add(item); s.commit(); return row(item)
@app.post("/campaigns/{campaign_id}/start", operation_id="startCampaign")
def start_campaign(campaign_id:int,_:Any=Depends(auth),s:Session=Depends(db)):
    c=get_campaign_or_404(s,campaign_id)
    if c.status in ("stopped","completed"): raise HTTPException(409,"Stopped/completed campaigns cannot restart")
    c.status="active"; s.commit(); return row(c)
@app.post("/campaigns/{campaign_id}/pause", operation_id="pauseCampaign")
def pause_campaign(campaign_id:int,_:Any=Depends(auth),s:Session=Depends(db)):
    c=get_campaign_or_404(s,campaign_id); c.status="paused"; s.commit(); return row(c)
@app.post("/campaigns/{campaign_id}/stop", operation_id="stopCampaign")
def stop_campaign(campaign_id:int,_:Any=Depends(auth),s:Session=Depends(db)):
    c=get_campaign_or_404(s,campaign_id); c.status="stopped"; s.query(Prospect).filter(Prospect.campaign_id==campaign_id).update({Prospect.next_send_at:None,Prospect.status:"stopped"}); s.commit(); return row(c)
@app.get("/campaigns/{campaign_id}", operation_id="getCampaign")
def get_campaign(campaign_id:int,_:Any=Depends(auth),s:Session=Depends(db)): return row(get_campaign_or_404(s,campaign_id))
@app.get("/campaigns/{campaign_id}/stats", operation_id="getCampaignStats")
def stats(campaign_id:int,_:Any=Depends(auth),s:Session=Depends(db)):
    c=get_campaign_or_404(s,campaign_id); q=s.query(Prospect).filter_by(campaign_id=campaign_id)
    return {"campaign_id":campaign_id,"status":c.status,"prospects":q.count(),"queued":q.filter(Prospect.next_send_at.is_not(None)).count(),"messages_sent":s.query(Delivery).filter_by(campaign_id=campaign_id).count(),"replies":q.filter_by(reply_detected=True).count(),"opt_outs":q.filter_by(opted_out=True).count(),"bounces":q.filter_by(bounced=True).count()}
@app.post("/campaigns/{campaign_id}/prospects", operation_id="queueProspect")
def queue_prospect(campaign_id:int,p:ProspectIn,_:Any=Depends(auth),s:Session=Depends(db)):
    c=get_campaign_or_404(s,campaign_id); email=(p.verified_contact or "").strip().lower() or None
    reasons=[]
    if p.score<c.minimum_score: reasons.append("score_below_minimum")
    if p.priority not in c.allowed_priority_levels: reasons.append("priority_not_allowed")
    if c.verified_business_email_required and (not p.email_verified or not email): reasons.append("verified_business_email_required")
    if email and c.duplicate_suppression and s.scalar(select(Prospect.id).where(Prospect.campaign_id==campaign_id,Prospect.normalized_email==email)): reasons.append("duplicate")
    existing=s.scalar(select(Prospect).where(Prospect.normalized_email==email, Prospect.suppression_status!="clear")) if email else None
    if existing: reasons.append(existing.suppression_status)
    if reasons: raise HTTPException(409,{"qualified":False,"reasons":reasons})
    values=p.model_dump(); personalized = p.opening_message or OPENING_ANGLES[c.industry]
    if not p.opening_message and p.verified_facts: personalized += f" I noticed {p.verified_facts[0]}."
    values.update(campaign_id=campaign_id,normalized_email=email,campaign_approved=c.auto_approve_qualified_prospects,status="queued",next_send_at=utcnow(),opening_message=personalized)
    item=Prospect(**values); s.add(item); s.commit(); result=row(item); result["proof_selection"]=proof_for(c.industry); return result
@app.get("/campaigns/{campaign_id}/followups", operation_id="getDueFollowups")
def due_followups(campaign_id:int,at:datetime|None=None,_:Any=Depends(auth),s:Session=Depends(db)):
    c=get_campaign_or_404(s,campaign_id); instant=at or utcnow()
    if c.status!="active": return []
    items=s.scalars(select(Prospect).where(Prospect.campaign_id==campaign_id,Prospect.next_send_at<=instant,Prospect.sent_count<3,Prospect.reply_detected.is_(False),Prospect.opted_out.is_(False),Prospect.bounced.is_(False),Prospect.suppression_status=="clear")).all()
    return [row(x)|{"idempotency_key":f"campaign:{campaign_id}:prospect:{x.id}:step:{x.sequence_step}"} for x in items]
@app.post("/campaigns/{campaign_id}/deliveries", operation_id="recordDelivery")
def record_delivery(campaign_id:int,e:DeliveryIn,_:Any=Depends(auth),s:Session=Depends(db)):
    c=get_campaign_or_404(s,campaign_id)
    p=s.scalar(select(Prospect).where(Prospect.id==e.prospect_id).with_for_update())
    if not p: raise HTTPException(404,"Prospect not found")
    existing=s.scalar(select(Delivery).where((Delivery.idempotency_key==e.idempotency_key)|((Delivery.prospect_id==p.id)&(Delivery.sequence_step==e.sequence_step))))
    if existing: return row(existing)|{"idempotent_replay":True}
    if p.campaign_id!=campaign_id or p.sequence_step!=e.sequence_step: raise HTTPException(409,"Prospect or sequence step mismatch")
    if c.status!="active" or p.reply_detected or p.opted_out or p.bounced or p.suppression_status!="clear": raise HTTPException(409,"Sending is stopped")
    sent=utcnow(); d=Delivery(campaign_id=campaign_id,**e.model_dump()); s.add(d); p.last_sent_at=sent; p.sent_count+=1; p.sequence_step+=1; p.next_send_at=sent+timedelta(days=3) if p.sent_count<3 else None; p.status="sent" if p.sent_count<3 else "sequence_complete"; s.commit(); return row(d)|{"idempotent_replay":False,"next_send_at":p.next_send_at}
def suppress(s:Session,e:EventIn,status:str,field:str):
    p=get_prospect_or_404(s,e.prospect_id); setattr(p,field,True); p.suppression_status=status; p.next_send_at=None; p.status=status; s.commit(); return row(p)
@app.post("/events/bounce", operation_id="recordBounce")
def bounce(e:EventIn,_:Any=Depends(auth),s:Session=Depends(db)):
    if not e.hard: return row(get_prospect_or_404(s,e.prospect_id))
    return suppress(s,e,"hard_bounce","bounced")
@app.post("/events/opt-out", operation_id="recordOptOut")
def optout(e:EventIn,_:Any=Depends(auth),s:Session=Depends(db)): return suppress(s,e,"opt_out","opted_out")
@app.post("/events/replies", operation_id="processReplies")
def process_reply(e:ReplyEvent,_:Any=Depends(auth),s:Session=Depends(db)):
    p=get_prospect_or_404(s,e.prospect_id); p.reply_detected=True; p.last_reply=e.reply_text; p.intent=e.intent; p.next_send_at=None; p.status="replied"; s.commit(); return row(p)

@app.get("/launcher",response_class=HTMLResponse,include_in_schema=False)
def launcher():
    buttons="".join(f'<button type="button" data-industry="{x}">{x}</button>' for x in INDUSTRIES)
    return HTMLResponse(f'''<!doctype html><html><head><title>CallPulse Campaign Launcher</title><style>body{{font:16px system-ui;max-width:900px;margin:40px auto;padding:20px}}.industries{{display:flex;flex-wrap:wrap;gap:8px}}button{{padding:10px}}button.selected{{background:#174ea6;color:white}}input{{padding:10px;width:100%;box-sizing:border-box;margin:12px 0}}#status{{margin-top:20px;padding:16px;background:#eef}}</style></head><body><h1>Launch a 7-Day Campaign</h1><label>Location<input id="location" value="Houston, TX"></label><div class="industries">{buttons}</div><button id="start">Start 7-Day Campaign</button><div id="status">Select an industry to view campaign status.</div><script>let industry='';document.querySelectorAll('[data-industry]').forEach(b=>b.onclick=()=>{{document.querySelectorAll('[data-industry]').forEach(x=>x.classList.remove('selected'));b.classList.add('selected');industry=b.dataset.industry;status.textContent=`Ready: ${{industry}} in ${{location.value}} · 7 days · minimum score 65`;}});start.onclick=()=>{{status.textContent=industry?`Campaign configuration ready for ${{industry}} in ${{location.value}}. Submit through authenticated API to start.`:'Select an industry first.'}}</script></body></html>''')
