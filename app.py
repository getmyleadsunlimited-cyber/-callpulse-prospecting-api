import os, sqlite3
from datetime import datetime, timezone
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel

DB = os.getenv("CALLPULSE_DB_PATH", "/tmp/callpulse.db")
API_KEY = os.getenv("CALLPULSE_ACTIONS_API_KEY", "")
app = FastAPI(title="CallPulse.org Prospecting Queue API", version="1.0.0")

def now(): return datetime.now(timezone.utc).isoformat()
def conn():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    return c
def auth(h):
    if API_KEY and h != f"Bearer {API_KEY}":
        raise HTTPException(401, "Unauthorized")
def init_db():
    c=conn()
    c.execute("""CREATE TABLE IF NOT EXISTS prospects(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      company_name TEXT NOT NULL, website TEXT NOT NULL, industry TEXT NOT NULL,
      score INTEGER NOT NULL, why_now TEXT NOT NULL, ai_recovery_opportunity TEXT NOT NULL,
      decision_maker_name TEXT, decision_maker_title TEXT, best_contact_channel TEXT,
      verified_contact TEXT, opening_message TEXT, status TEXT DEFAULT 'researched',
      draft_message TEXT, last_reply TEXT, intent TEXT, conversion_stage TEXT,
      created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""")
    c.commit(); c.close()
init_db()

class Prospect(BaseModel):
    company_name:str; website:str; industry:str; score:int; why_now:str; ai_recovery_opportunity:str
    decision_maker_name:str|None=None; decision_maker_title:str|None=None
    best_contact_channel:str|None=None; verified_contact:str|None=None; opening_message:str|None=None
class Approval(BaseModel):
    approved_by:str
class Draft(BaseModel):
    message:str
class Reply(BaseModel):
    reply_text:str; intent:str|None="unknown"
class Conversion(BaseModel):
    conversion_stage:str

@app.get("/")
def root(): return {"service":"CallPulse.org Prospecting Queue API","status":"online"}
@app.get("/health")
def health(): return {"ok":True}

@app.post("/prospects")
def create_prospect(p:Prospect, authorization:str|None=Header(default=None)):
    auth(authorization); c=conn(); t=now()
    cur=c.execute("""INSERT INTO prospects(company_name,website,industry,score,why_now,
      ai_recovery_opportunity,decision_maker_name,decision_maker_title,best_contact_channel,
      verified_contact,opening_message,created_at,updated_at)
      VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
      (p.company_name,p.website,p.industry,p.score,p.why_now,p.ai_recovery_opportunity,
       p.decision_maker_name,p.decision_maker_title,p.best_contact_channel,p.verified_contact,
       p.opening_message,t,t))
    c.commit(); r=c.execute("SELECT * FROM prospects WHERE id=?",(cur.lastrowid,)).fetchone(); c.close()
    return dict(r)

@app.get("/prospects")
def list_prospects(status:str|None=None, limit:int=25, authorization:str|None=Header(default=None)):
    auth(authorization); c=conn()
    if status:
        rows=c.execute("SELECT * FROM prospects WHERE status=? ORDER BY score DESC LIMIT ?",(status,limit)).fetchall()
    else:
        rows=c.execute("SELECT * FROM prospects ORDER BY score DESC LIMIT ?",(limit,)).fetchall()
    c.close(); return [dict(r) for r in rows]

@app.post("/prospects/{prospect_id}/approve")
def approve(prospect_id:int, a:Approval, authorization:str|None=Header(default=None)):
    auth(authorization); c=conn()
    c.execute("UPDATE prospects SET status='approved',updated_at=? WHERE id=?",(now(),prospect_id))
    c.commit(); r=c.execute("SELECT * FROM prospects WHERE id=?",(prospect_id,)).fetchone(); c.close()
    if not r: raise HTTPException(404,"Not found")
    return dict(r)

@app.post("/prospects/{prospect_id}/draft")
def draft(prospect_id:int, d:Draft, authorization:str|None=Header(default=None)):
    auth(authorization); c=conn()
    r=c.execute("SELECT status FROM prospects WHERE id=?",(prospect_id,)).fetchone()
    if not r: raise HTTPException(404,"Not found")
    if r["status"]!="approved": raise HTTPException(409,"Human approval required")
    c.execute("UPDATE prospects SET draft_message=?,updated_at=? WHERE id=?",(d.message,now(),prospect_id))
    c.commit(); r=c.execute("SELECT * FROM prospects WHERE id=?",(prospect_id,)).fetchone(); c.close()
    return dict(r)

@app.post("/prospects/{prospect_id}/reply")
def reply(prospect_id:int, rp:Reply, authorization:str|None=Header(default=None)):
    auth(authorization); c=conn()
    status="qualified" if rp.intent in ("interested","pricing","ready_to_start","trial_interest") else "replied"
    c.execute("UPDATE prospects SET status=?,last_reply=?,intent=?,updated_at=? WHERE id=?",
              (status,rp.reply_text,rp.intent,now(),prospect_id))
    c.commit(); r=c.execute("SELECT * FROM prospects WHERE id=?",(prospect_id,)).fetchone(); c.close()
    if not r: raise HTTPException(404,"Not found")
    return dict(r)

@app.post("/prospects/{prospect_id}/conversion")
def conversion(prospect_id:int, cv:Conversion, authorization:str|None=Header(default=None)):
    auth(authorization); c=conn()
    status="converted" if cv.conversion_stage in ("standard_start","three_day_trial","converted") else "qualified"
    c.execute("UPDATE prospects SET status=?,conversion_stage=?,updated_at=? WHERE id=?",
              (status,cv.conversion_stage,now(),prospect_id))
    c.commit(); r=c.execute("SELECT * FROM prospects WHERE id=?",(prospect_id,)).fetchone(); c.close()
    if not r: raise HTTPException(404,"Not found")
    return dict(r)
