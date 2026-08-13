"""CallPulse autonomous seven-day prospecting campaign API."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Literal, Callable

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, case, create_engine, select, update
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship, sessionmaker

from email_providers import EmailProviderError, configured_provider

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:////tmp/callpulse.db")
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+psycopg://", 1)
elif DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg://", 1)
API_KEY = os.getenv("CALLPULSE_ACTIONS_API_KEY", "")
INTERNAL_ADMIN_API_KEY = os.getenv("CALLPULSE_INTERNAL_ADMIN_API_KEY", "")
SESSION_HOURS = int(os.getenv("CALLPULSE_SESSION_HOURS", "12"))
LOGIN_WINDOW_MINUTES = 15
LOGIN_MAX_FAILURES = 8
LOGIN_SOURCE_MAX_FAILURES = 40
PASSWORD_MAX_LENGTH = 256
CREDENTIALS_JSON = os.getenv("CALLPULSE_TENANT_CREDENTIALS", "")
DRY_RUN = os.getenv("CALLPULSE_DRY_RUN", "true").lower() != "false"
DELIVERY_WEBHOOK = os.getenv("CALLPULSE_DELIVERY_WEBHOOK", "")
EMAIL_PROVIDER_NAME = os.getenv("CALLPULSE_EMAIL_PROVIDER", "disabled").strip().lower()
EMAIL_FROM = os.getenv("CALLPULSE_EMAIL_FROM", "").strip()
MICROSOFT_TENANT_ID = os.getenv("MICROSOFT_TENANT_ID", "").strip()
MICROSOFT_CLIENT_ID = os.getenv("MICROSOFT_CLIENT_ID", "").strip()
MICROSOFT_CLIENT_SECRET = os.getenv("MICROSOFT_CLIENT_SECRET", "")
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
DEFAULT_WORKSPACE_ID = "callpulse-direct"
WORKSPACE_ID_MAX_LENGTH = 100
WORKSPACE_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]*")

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
    __table_args__ = (UniqueConstraint("workspace_id", "verified_email", name="uq_prospect_workspace_email"),)
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
    workspace_id: Mapped[str] = mapped_column(String(100), default=DEFAULT_WORKSPACE_ID, index=True)
    verified_email: Mapped[str | None] = mapped_column(String(320))
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
    authorized_recipient_email: Mapped[str | None] = mapped_column(String(320))
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
    subject: Mapped[str] = mapped_column(String(300), default="A practical lead recovery idea")
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
    provider_correlation_id: Mapped[str | None] = mapped_column(String(300))
    last_execution_error: Mapped[str | None] = mapped_column(String(500))
    execution_attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    campaign: Mapped[Campaign] = relationship(back_populates="touches")


class Suppression(Base):
    __tablename__ = "suppressions"
    email: Mapped[str] = mapped_column(String(320), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(100), primary_key=True, default=DEFAULT_WORKSPACE_ID)
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
    provider_correlation_id: Mapped[str | None] = mapped_column(String(300))
    result: Mapped[str] = mapped_column(String(30))
    failure_reason: Mapped[str | None] = mapped_column(String(1000))


class EmailVerificationAudit(Base):
    __tablename__ = "email_verification_audits"
    id: Mapped[int] = mapped_column(primary_key=True)
    prospect_id: Mapped[int] = mapped_column(ForeignKey("prospects.id", ondelete="CASCADE"), index=True)
    workspace_id: Mapped[str] = mapped_column(String(100), index=True)
    old_email: Mapped[str | None] = mapped_column(String(320))
    new_email: Mapped[str] = mapped_column(String(320))
    verifier_identity: Mapped[str] = mapped_column(String(200))
    verified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    invalidated_campaign_ids: Mapped[str] = mapped_column(Text, default="[]")

class Account(Base):
    __tablename__ = "accounts"
    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    account_type: Mapped[str] = mapped_column(String(20))


class Workspace(Base):
    __tablename__ = "workspaces"
    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    owner_account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"), index=True)
    workspace_type: Mapped[str] = mapped_column(String(20))


class AgencyWorkspaceAccess(Base):
    __tablename__ = "agency_workspace_access"
    __table_args__ = (UniqueConstraint("agency_account_id", "workspace_id", name="uq_agency_workspace"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    agency_account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"), index=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(500))
    security_version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class AccountMembership(Base):
    __tablename__ = "account_memberships"
    __table_args__ = (UniqueConstraint("user_id", "account_id", name="uq_user_account"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"), index=True)
    primary_workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"))
    role: Mapped[str] = mapped_column(String(20))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    user: Mapped[User] = relationship()
    workspace_grants: Mapped[list[MembershipWorkspaceAccess]] = relationship(cascade="all, delete-orphan")


class MembershipWorkspaceAccess(Base):
    __tablename__ = "membership_workspace_access"
    __table_args__ = (UniqueConstraint("membership_id", "workspace_id", name="uq_membership_workspace"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    membership_id: Mapped[int] = mapped_column(ForeignKey("account_memberships.id", ondelete="CASCADE"), index=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)


class UserSession(Base):
    __tablename__ = "user_sessions"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    membership_id: Mapped[int] = mapped_column(ForeignKey("account_memberships.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    security_version: Mapped[int] = mapped_column(Integer)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class UserAudit(Base):
    __tablename__ = "user_audits"
    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[str] = mapped_column(String(100), index=True)
    actor_user_id: Mapped[int | None] = mapped_column(Integer)
    target_user_id: Mapped[int] = mapped_column(Integer, index=True)
    action: Mapped[str] = mapped_column(String(50))
    details: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class LoginSecurityEvent(Base):
    __tablename__ = "login_security_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    account_key: Mapped[str] = mapped_column(String(64), index=True)
    source_key: Mapped[str] = mapped_column(String(64), index=True)
    succeeded: Mapped[bool] = mapped_column(Boolean, default=False)
    reason: Mapped[str] = mapped_column(String(40))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True)


class LoginRateLimit(Base):
    __tablename__ = "login_rate_limits"
    key: Mapped[str] = mapped_column(String(65), primary_key=True)
    window_started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer)


class PendingInvitation(Base):
    __tablename__ = "pending_invitations"
    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"), index=True)
    email: Mapped[str] = mapped_column(String(320), index=True)
    role: Mapped[str] = mapped_column(String(20))
    primary_workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"))
    workspace_ids_json: Mapped[str] = mapped_column(Text)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by_user_id: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class WorkspaceAudit(Base):
    __tablename__ = "workspace_audits"
    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(100), index=True)
    account_id: Mapped[str] = mapped_column(String(100), index=True)
    action: Mapped[str] = mapped_column(String(50))
    actor_user_id: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


Base.metadata.create_all(engine)
app = FastAPI(title="CallPulse Autonomous Campaign API", version="3.0.0")
bearer_scheme = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class AuthenticatedIdentity:
    """The tenant authorization grants bound to one bearer credential."""
    role: Literal["direct", "agency", "client", "internal_admin"]
    workspace_id: str
    client_workspace_ids: frozenset[str] = frozenset()
    access_role: Literal["owner", "admin", "member", "viewer"] = "owner"
    account_id: str | None = None
    user_id: int | None = None
    membership_id: int | None = None
    session_id: int | None = None
    email: str | None = None

    def permits(self, workspace_id: str) -> bool:
        if self.role == "internal_admin":
            return True
        if workspace_id == self.workspace_id:
            return True
        return self.role == "agency" and workspace_id in self.client_workspace_ids


def validate_workspace_id(workspace_id: object) -> str:
    """Return a workspace ID only when it exactly matches the persisted ID grammar."""
    if (not isinstance(workspace_id, str) or not workspace_id
            or len(workspace_id) > WORKSPACE_ID_MAX_LENGTH
            or WORKSPACE_ID_PATTERN.fullmatch(workspace_id) is None):
        raise ValueError("Workspace ID is invalid")
    return workspace_id


def load_tenant_credentials(raw: str) -> dict[str, AuthenticatedIdentity]:
    """Parse credential grants once at startup; malformed grants fail closed."""
    if not raw.strip():
        return {}
    try:
        configured = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError("CALLPULSE_TENANT_CREDENTIALS must be valid JSON") from exc
    if not isinstance(configured, dict):
        raise RuntimeError("CALLPULSE_TENANT_CREDENTIALS must be a JSON object")
    identities: dict[str, AuthenticatedIdentity] = {}
    for token, grant in configured.items():
        if not isinstance(token, str) or not token or not isinstance(grant, dict):
            raise RuntimeError("Every tenant credential must have a non-empty token and object grant")
        role, workspace_id = grant.get("role"), grant.get("workspace_id")
        clients = grant.get("client_workspace_ids", [])
        if role not in {"direct", "agency", "client"}:
            raise RuntimeError("Tenant credential role/workspace_id is invalid")
        try:
            workspace_id = validate_workspace_id(workspace_id)
        except ValueError as exc:
            raise RuntimeError("Tenant credential role/workspace_id is invalid") from exc
        if not isinstance(clients, list):
            raise RuntimeError("client_workspace_ids must be an array of valid workspace IDs")
        try:
            clients = [validate_workspace_id(item) for item in clients]
        except ValueError as exc:
            raise RuntimeError("client_workspace_ids must be an array of valid workspace IDs") from exc
        if role != "agency" and clients:
            raise RuntimeError("Only agency credentials may grant client workspaces")
        identities[token] = AuthenticatedIdentity(role, workspace_id, frozenset(clients))
    return identities


TENANT_CREDENTIALS = load_tenant_credentials(CREDENTIALS_JSON)


def db_session():
    with SessionLocal() as db:
        yield db


def require_auth(credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme)) -> AuthenticatedIdentity:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(401, "Valid bearer authentication is required")
    supplied = credentials.credentials
    # The legacy credential is deliberately a Direct-account credential, never a
    # global tenant selector. The separate admin secret is for internal operators only.
    if API_KEY and hmac.compare_digest(supplied, API_KEY):
        return AuthenticatedIdentity("direct", DEFAULT_WORKSPACE_ID)
    if INTERNAL_ADMIN_API_KEY and hmac.compare_digest(supplied, INTERNAL_ADMIN_API_KEY):
        return AuthenticatedIdentity("internal_admin", DEFAULT_WORKSPACE_ID)
    for token, identity in TENANT_CREDENTIALS.items():
        if hmac.compare_digest(supplied, token):
            return identity
    token_hash = hashlib.sha256(supplied.encode()).hexdigest()
    with SessionLocal() as db:
        session = db.scalar(select(UserSession).where(UserSession.token_hash == token_hash))
        user = db.get(User, session.user_id) if session else None
        membership = db.get(AccountMembership, session.membership_id) if session else None
        now = utcnow()
        expires = session.expires_at.replace(tzinfo=timezone.utc) if session and session.expires_at.tzinfo is None else (session.expires_at if session else now)
        if (user and membership and membership.active and session.revoked_at is None
                and expires > now and session.security_version == user.security_version):
            account = db.get(Account, membership.account_id)
            grants = frozenset(x.workspace_id for x in membership.workspace_grants)
            return AuthenticatedIdentity(
                account.account_type, membership.primary_workspace_id, grants,
                access_role=membership.role, account_id=membership.account_id, user_id=user.id,
                membership_id=membership.id, session_id=session.id, email=user.email,
            )
    raise HTTPException(401, "Valid bearer authentication is required")


def require_roles(*roles: str) -> Callable:
    def dependency(identity: AuthenticatedIdentity = Depends(require_auth)) -> AuthenticatedIdentity:
        if identity.role == "internal_admin" or identity.access_role in roles:
            return identity
        raise HTTPException(403, "Your role does not permit this operation")
    return dependency


def hash_password(password: str, salt: bytes | None = None) -> str:
    if len(password) < 12:
        raise ValueError("Password must contain at least 12 characters")
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 310_000)
    return f"pbkdf2_sha256$310000${salt.hex()}${digest.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, rounds, salt, expected = encoded.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        actual = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), int(rounds))
        return hmac.compare_digest(actual, bytes.fromhex(expected))
    except (ValueError, TypeError):
        return False


def workspace_context(x_workspace_id: str | None = Header(
    default=None, alias="X-Workspace-ID",
    description="Select a workspace already authorized by the bearer credential; this header grants no access.",
), identity: AuthenticatedIdentity = Depends(require_auth)) -> str:
    """Resolve and authorize tenant context before any tenant data is queried."""
    if x_workspace_id is None:
        return identity.workspace_id
    try:
        workspace_id = validate_workspace_id(x_workspace_id)
    except ValueError:
        raise HTTPException(400, "X-Workspace-ID is invalid")
    if not identity.permits(workspace_id):
        raise HTTPException(403, "Authenticated credential is not authorized for this workspace")
    return workspace_id


def scoped_prospect(db: Session, prospect_id: int, workspace_id: str) -> Prospect | None:
    return db.scalar(select(Prospect).where(Prospect.id == prospect_id, Prospect.workspace_id == workspace_id))


def scoped_campaign(db: Session, campaign_id: int, workspace_id: str) -> Campaign | None:
    return db.scalar(select(Campaign).where(
        Campaign.id == campaign_id, Campaign.prospect.has(Prospect.workspace_id == workspace_id)))


def scoped_touch(db: Session, delivery_id: int, workspace_id: str) -> Touch | None:
    return db.scalar(select(Touch).where(
        Touch.id == delivery_id,
        Touch.campaign.has(Campaign.prospect.has(Prospect.workspace_id == workspace_id))))


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
        sender=EMAIL_FROM, recipient=email, subject="CallPulse outreach",
        message=message, idempotency_key=idempotency_key)


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
    verified_email: str | None = None
    opening_message: str | None = None

    @field_validator("industry")
    @classmethod
    def validate_industry(cls, value: str) -> str:
        if value not in INDUSTRIES:
            raise ValueError("Select a supported industry")
        return value

    @field_validator("verified_email")
    @classmethod
    def validate_email(cls, value: str | None) -> str | None:
        return normalize_email(value) if value is not None else None



class TrustedEmailVerificationIn(BaseModel):
    verified_email: str

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
    provider: str | None = None
    provider_configured: bool = False
    sender: str | None = None


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


class LoginIn(BaseModel):
    email: str = Field(max_length=320)
    password: str = Field(max_length=PASSWORD_MAX_LENGTH)
    account_id: str = Field(min_length=1, max_length=100)


class UserCreateIn(BaseModel):
    email: str = Field(max_length=320)
    role: Literal["owner", "admin", "member", "viewer"]
    account_id: str | None = None
    account_type: Literal["direct", "agency", "client"] | None = None
    primary_workspace_id: str | None = None
    workspace_ids: list[str] = Field(default_factory=list)


class RoleChangeIn(BaseModel):
    role: Literal["owner", "admin", "member", "viewer"]


class WorkspaceAccessIn(BaseModel):
    workspace_ids: list[str]


class PasswordChangeIn(BaseModel):
    current_password: str = Field(max_length=PASSWORD_MAX_LENGTH)
    new_password: str = Field(min_length=12, max_length=PASSWORD_MAX_LENGTH)


class AgencyWorkspaceIn(BaseModel):
    agency_account_id: str
    workspace_id: str


class InvitationAcceptIn(BaseModel):
    token: str = Field(min_length=20, max_length=200)
    password: str | None = Field(default=None, min_length=12, max_length=PASSWORD_MAX_LENGTH)


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
        prospect.verified_email and db.get(Suppression, (prospect.verified_email, prospect.workspace_id))
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
    if (not campaign.authorized_recipient_email
            or campaign.authorized_recipient_email != prospect.verified_email):
        failures.append("campaign authorization recipient does not match the current verified email")
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
        failures.extend(provider_readiness_failures())
    failures = list(dict.fromkeys(failures))
    return DeliveryEligibility(eligible=not failures, failures=failures,
                               provider=EMAIL_PROVIDER_NAME,
                               provider_configured=not provider_readiness_failures(),
                               sender=EMAIL_FROM or None)


def provider_readiness_failures() -> list[str]:
    failures: list[str] = []
    if not EMAIL_FROM:
        failures.append("approved sender identity is not configured")
    if EMAIL_PROVIDER_NAME == "disabled":
        failures.append("email delivery provider is disabled")
    elif EMAIL_PROVIDER_NAME == "microsoft_graph":
        if not MICROSOFT_TENANT_ID: failures.append("microsoft tenant id is not configured")
        if not MICROSOFT_CLIENT_ID: failures.append("microsoft client id is not configured")
        if not MICROSOFT_CLIENT_SECRET: failures.append("microsoft client secret is not configured")
    elif EMAIL_PROVIDER_NAME != "mock":
        failures.append("email delivery provider is unsupported")
    return failures


def add_canary_audit(db: Session, touch: Touch, authorized_by: str, result: str,
                     failure: str | None = None, provider_message_id: str | None = None,
                     provider_correlation_id: str | None = None) -> None:
    campaign, prospect = touch.campaign, touch.campaign.prospect
    db.add(CanaryExecutionAudit(
        campaign_id=campaign.id, delivery_id=touch.id, prospect_id=prospect.id,
        authorized_by=authorized_by.strip(), authorization_timestamp=campaign.live_authorized_at,
        execution_requested_at=utcnow(), sender_identity=EMAIL_FROM,
        recipient_email=prospect.verified_email or "", idempotency_key=touch.idempotency_key or "",
        provider_name=EMAIL_PROVIDER_NAME, provider_message_id=provider_message_id,
        provider_correlation_id=provider_correlation_id,
        result=result, failure_reason=failure,
    ))


def membership_dict(membership: AccountMembership) -> dict:
    return {"id": membership.id, "user_id": membership.user_id, "email": membership.user.email,
            "account_id": membership.account_id, "role": membership.role, "active": membership.active,
            "primary_workspace_id": membership.primary_workspace_id,
            "workspace_ids": sorted(x.workspace_id for x in membership.workspace_grants)}


def managed_membership(db: Session, membership_id: int, identity: AuthenticatedIdentity) -> AccountMembership:
    membership = db.get(AccountMembership, membership_id)
    if not membership or (identity.role != "internal_admin" and membership.account_id != identity.account_id):
        raise HTTPException(404, "User not found")
    return membership


def audit_membership(db: Session, identity: AuthenticatedIdentity, membership: AccountMembership,
                     action: str, details: dict) -> None:
    db.add(UserAudit(account_id=membership.account_id, actor_user_id=identity.user_id,
                     target_user_id=membership.user_id, action=action,
                     details=json.dumps({"membership_id": membership.id, **details}, sort_keys=True)))


def login_key(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


# A stable, valid hash ensures unknown identities pay the same PBKDF2 cost.
DUMMY_PASSWORD_HASH = hash_password("callpulse-dummy-password-never-valid", bytes(16))


def record_login_event(db: Session, email: str, account_id: str, source: str,
                       succeeded: bool, reason: str) -> None:
    db.add(LoginSecurityEvent(account_key=login_key(f"{email}:{account_id}"),
                              source_key=login_key(source), succeeded=succeeded, reason=reason))


def reserve_login_attempt(db: Session, key: str, limit: int, now: datetime) -> bool:
    """Atomically reserve one attempt in a fixed window before password hashing."""
    cutoff = now - timedelta(minutes=LOGIN_WINDOW_MINUTES)
    dialect_insert = postgresql_insert if db.bind.dialect.name == "postgresql" else sqlite_insert
    incoming = {"key": key, "window_started_at": now, "attempts": 1}
    statement = dialect_insert(LoginRateLimit).values(**incoming)
    statement = statement.on_conflict_do_update(
        index_elements=[LoginRateLimit.key],
        set_={
            "window_started_at": case(
                (LoginRateLimit.window_started_at <= cutoff, now),
                else_=LoginRateLimit.window_started_at,
            ),
            "attempts": case(
                (LoginRateLimit.window_started_at <= cutoff, 1),
                else_=LoginRateLimit.attempts + 1,
            ),
        },
        where=((LoginRateLimit.window_started_at <= cutoff) | (LoginRateLimit.attempts < limit)),
    ).returning(LoginRateLimit.attempts)
    reserved = db.scalar(statement) is not None
    db.commit()  # Publish the reservation before expensive PBKDF2 work begins.
    return reserved


@app.post("/auth/login")
def login(body: LoginIn, request: Request, db: Session = Depends(db_session)):
    source = request.client.host if request.client else "unknown"
    try:
        email = normalize_email(body.email)
    except ValueError:
        email = body.email.strip().lower()[:320]
    account_key, source_key = login_key(f"{email}:{body.account_id}"), login_key(source)
    now = utcnow()
    account_reserved = reserve_login_attempt(db, f"a:{account_key}", LOGIN_MAX_FAILURES, now)
    source_reserved = reserve_login_attempt(db, f"s:{source_key}", LOGIN_SOURCE_MAX_FAILURES, now)
    if not account_reserved or not source_reserved:
        record_login_event(db, email, body.account_id, source, False, "throttled")
        db.commit()
        raise HTTPException(429, "Too many login attempts; try again later")
    user = db.scalar(select(User).where(User.email == email))
    membership = db.scalar(select(AccountMembership).where(
        AccountMembership.user_id == user.id, AccountMembership.account_id == body.account_id,
    )) if user else None
    password_ok = verify_password(body.password, user.password_hash if user else DUMMY_PASSWORD_HASH)
    if not user or not membership or not membership.active or not password_ok:
        record_login_event(db, email, body.account_id, source, False, "invalid_credentials")
        db.commit()
        raise HTTPException(401, "Invalid email, account, or password")
    token = secrets.token_urlsafe(32)
    session = UserSession(user_id=user.id, membership_id=membership.id,
                          token_hash=hashlib.sha256(token.encode()).hexdigest(),
                          security_version=user.security_version,
                          expires_at=utcnow() + timedelta(hours=SESSION_HOURS))
    db.add(session)
    record_login_event(db, email, body.account_id, source, True, "authenticated")
    db.commit()
    return {"access_token": token, "token_type": "bearer", "expires_in": SESSION_HOURS * 3600}


@app.post("/auth/logout", status_code=204, dependencies=[Depends(require_auth)])
def logout(identity: AuthenticatedIdentity = Depends(require_auth), db: Session = Depends(db_session)):
    if identity.session_id:
        db.execute(update(UserSession).where(UserSession.id == identity.session_id).values(revoked_at=utcnow()))
        db.commit()


@app.post("/auth/revoke-all", status_code=204, dependencies=[Depends(require_auth)])
def revoke_all_sessions(identity: AuthenticatedIdentity = Depends(require_auth), db: Session = Depends(db_session)):
    if identity.user_id:
        db.execute(update(UserSession).where(UserSession.user_id == identity.user_id,
                                             UserSession.revoked_at.is_(None)).values(revoked_at=utcnow()))
        db.commit()


@app.post("/auth/revoke-account", status_code=204, dependencies=[Depends(require_roles("owner"))])
def revoke_account_sessions(identity: AuthenticatedIdentity = Depends(require_auth), db: Session = Depends(db_session)):
    membership_ids = select(AccountMembership.id).where(AccountMembership.account_id == identity.account_id)
    db.execute(update(UserSession).where(UserSession.membership_id.in_(membership_ids),
                                         UserSession.revoked_at.is_(None)).values(revoked_at=utcnow()))
    if identity.membership_id:
        membership = db.get(AccountMembership, identity.membership_id)
        audit_membership(db, identity, membership, "account_sessions_revoked", {})
    db.commit()


@app.post("/auth/change-password", status_code=204, dependencies=[Depends(require_auth)])
def change_password(body: PasswordChangeIn, identity: AuthenticatedIdentity = Depends(require_auth),
                    db: Session = Depends(db_session)):
    user = db.get(User, identity.user_id)
    if not user or not verify_password(body.current_password, user.password_hash):
        raise HTTPException(401, "Current password is invalid")
    user.password_hash = hash_password(body.new_password)
    user.security_version += 1
    db.execute(update(UserSession).where(UserSession.user_id == user.id,
                                         UserSession.revoked_at.is_(None)).values(revoked_at=utcnow()))
    membership = db.get(AccountMembership, identity.membership_id)
    audit_membership(db, identity, membership, "password_changed", {})
    db.commit()


@app.get("/me", dependencies=[Depends(require_auth)])
def current_user(identity: AuthenticatedIdentity = Depends(require_auth)):
    return {"id": identity.user_id, "membership_id": identity.membership_id, "email": identity.email,
            "account_id": identity.account_id, "role": identity.access_role, "account_type": identity.role,
            "primary_workspace_id": identity.workspace_id,
            "workspace_ids": sorted({identity.workspace_id, *identity.client_workspace_ids})}


def authorized_workspace_ids(db: Session, account: Account) -> set[str]:
    owned = set(db.scalars(select(Workspace.id).where(Workspace.owner_account_id == account.id)))
    if account.account_type == "agency":
        owned.update(db.scalars(select(AgencyWorkspaceAccess.workspace_id).where(
            AgencyWorkspaceAccess.agency_account_id == account.id)))
    return owned


def validate_membership_workspaces(db: Session, account: Account, primary: str,
                                   requested: set[str]) -> set[str]:
    allowed = authorized_workspace_ids(db, account)
    workspaces = requested | {primary}
    if primary not in allowed or not workspaces <= allowed:
        raise HTTPException(403, "Workspace is not owned by or delegated to this account")
    if account.account_type != "agency" and workspaces != {primary}:
        raise HTTPException(422, "Client and direct users may access only their primary workspace")
    return workspaces


def deliver_invitation_token(email: str, token: str) -> None:
    """Provider seam: production deployments deliver this out-of-band to the invitee."""
    raise HTTPException(503, "Invitation delivery provider is not configured")


@app.post("/users", status_code=201, dependencies=[Depends(require_roles("owner"))])
def create_user(body: UserCreateIn, identity: AuthenticatedIdentity = Depends(require_auth),
                db: Session = Depends(db_session)):
    internal = identity.role == "internal_admin"
    account_id = body.account_id if internal else identity.account_id
    account_type = body.account_type if internal else identity.role
    primary = body.primary_workspace_id if internal else identity.workspace_id
    if not account_id or account_type not in {"direct", "agency", "client"} or not primary:
        raise HTTPException(422, "account_id, account_type, and primary_workspace_id are required")
    try:
        email, primary = normalize_email(body.email), validate_workspace_id(primary)
        requested = {validate_workspace_id(x) for x in body.workspace_ids}
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    account = db.get(Account, account_id)
    if not account:
        if not internal:
            raise HTTPException(404, "Account not found")
        if body.role != "owner":
            raise HTTPException(422, "The first active membership in an account must be an owner")
        account = Account(id=account_id, account_type=account_type)
        db.add(account)
        db.flush()
    elif account.account_type != account_type:
        raise HTTPException(409, "Account type does not match")
    workspace = db.get(Workspace, primary)
    if not workspace:
        if not internal:
            raise HTTPException(404, "Workspace not found")
        workspace = Workspace(id=primary, owner_account_id=account.id,
                              workspace_type="client" if account.account_type == "client" else account.account_type)
        db.add(workspace)
        db.flush()
    workspaces = validate_membership_workspaces(db, account, primary, requested)
    token = secrets.token_urlsafe(32)
    invitation = PendingInvitation(
        account_id=account.id, email=email, role=body.role, primary_workspace_id=primary,
        workspace_ids_json=json.dumps(sorted(workspaces)),
        token_hash=hashlib.sha256(token.encode()).hexdigest(),
        expires_at=utcnow() + timedelta(hours=24), created_by_user_id=identity.user_id,
    )
    db.add(invitation)
    db.add(UserAudit(account_id=account.id, actor_user_id=identity.user_id, target_user_id=0,
                     action="invitation_created", details=json.dumps({"role": body.role}, sort_keys=True)))
    deliver_invitation_token(email, token)
    db.commit()
    return {"status": "pending", "expires_in": 86400}


def optional_identity(credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme)) -> AuthenticatedIdentity | None:
    if credentials is None:
        return None
    return require_auth(credentials)


@app.post("/invitations/accept", status_code=201)
def accept_invitation(body: InvitationAcceptIn, identity: AuthenticatedIdentity | None = Depends(optional_identity),
                      db: Session = Depends(db_session)):
    token_hash = hashlib.sha256(body.token.encode()).hexdigest()
    invitation = db.scalar(select(PendingInvitation).where(
        PendingInvitation.token_hash == token_hash).with_for_update())
    expires = invitation.expires_at.replace(tzinfo=timezone.utc) if invitation and invitation.expires_at.tzinfo is None else (invitation.expires_at if invitation else utcnow())
    if not invitation or invitation.accepted_at is not None or expires <= utcnow():
        raise HTTPException(410, "Invitation is invalid or expired")
    user = db.scalar(select(User).where(User.email == invitation.email))
    if user:
        if not identity or identity.user_id != user.id:
            raise HTTPException(401, "Authenticate as the invited identity to accept this invitation")
    else:
        if identity is not None:
            raise HTTPException(403, "Invitation does not belong to the authenticated identity")
        if body.password is None:
            raise HTTPException(422, "A password is required to establish a new identity")
        user = User(email=invitation.email, password_hash=hash_password(body.password))
        db.add(user); db.flush()
    existing = db.scalar(select(AccountMembership).where(
        AccountMembership.user_id == user.id, AccountMembership.account_id == invitation.account_id))
    if existing:
        raise HTTPException(409, "An account membership already exists")
    db.scalar(select(Account).where(Account.id == invitation.account_id).with_for_update())
    has_active_membership = db.scalar(select(AccountMembership.id).where(
        AccountMembership.account_id == invitation.account_id,
        AccountMembership.active.is_(True)).limit(1)) is not None
    if not has_active_membership and invitation.role != "owner":
        raise HTTPException(409, "The first active account membership must be an owner")
    membership = AccountMembership(
        user_id=user.id, account_id=invitation.account_id,
        primary_workspace_id=invitation.primary_workspace_id, role=invitation.role,
    )
    membership.workspace_grants = [MembershipWorkspaceAccess(workspace_id=x)
                                   for x in json.loads(invitation.workspace_ids_json)]
    db.add(membership); db.flush()
    invitation.accepted_at = utcnow()
    audit_membership(db, identity or AuthenticatedIdentity("direct", invitation.primary_workspace_id),
                     membership, "invitation_accepted", {"role": membership.role})
    db.commit()
    return membership_dict(membership)


@app.get("/users", dependencies=[Depends(require_roles("owner"))])
def list_users(identity: AuthenticatedIdentity = Depends(require_auth), db: Session = Depends(db_session)):
    query = select(AccountMembership) if identity.role == "internal_admin" else select(AccountMembership).where(
        AccountMembership.account_id == identity.account_id)
    return [membership_dict(m) for m in db.scalars(query.order_by(AccountMembership.id))]


def last_active_owner(db: Session, membership: AccountMembership) -> bool:
    if membership.role != "owner" or not membership.active:
        return False
    count = db.scalar(select(AccountMembership.id).where(
        AccountMembership.account_id == membership.account_id,
        AccountMembership.active.is_(True), AccountMembership.role == "owner",
        AccountMembership.id != membership.id).limit(1))
    return count is None


@app.patch("/users/{membership_id}/role", dependencies=[Depends(require_roles("owner"))])
def change_user_role(membership_id: int, body: RoleChangeIn,
                     identity: AuthenticatedIdentity = Depends(require_auth), db: Session = Depends(db_session)):
    membership = managed_membership(db, membership_id, identity)
    db.scalar(select(Account).where(Account.id == membership.account_id).with_for_update())
    if body.role != "owner" and last_active_owner(db, membership):
        raise HTTPException(409, "Every account must retain at least one active owner")
    old = membership.role; membership.role = body.role
    audit_membership(db, identity, membership, "role_changed", {"from": old, "to": body.role})
    db.commit()
    return membership_dict(membership)


@app.post("/users/{membership_id}/deactivate", dependencies=[Depends(require_roles("owner"))])
def deactivate_user(membership_id: int, identity: AuthenticatedIdentity = Depends(require_auth),
                    db: Session = Depends(db_session)):
    membership = managed_membership(db, membership_id, identity)
    db.scalar(select(Account).where(Account.id == membership.account_id).with_for_update())
    if last_active_owner(db, membership):
        raise HTTPException(409, "Every account must retain at least one active owner")
    membership.active = False
    db.execute(update(UserSession).where(UserSession.membership_id == membership.id,
                                         UserSession.revoked_at.is_(None)).values(revoked_at=utcnow()))
    audit_membership(db, identity, membership, "user_deactivated", {})
    db.commit()
    return membership_dict(membership)


@app.post("/users/{membership_id}/revoke-sessions", status_code=204,
          dependencies=[Depends(require_roles("owner"))])
def revoke_membership_sessions(membership_id: int, identity: AuthenticatedIdentity = Depends(require_auth),
                               db: Session = Depends(db_session)):
    membership = managed_membership(db, membership_id, identity)
    db.execute(update(UserSession).where(UserSession.membership_id == membership.id,
                                         UserSession.revoked_at.is_(None)).values(revoked_at=utcnow()))
    audit_membership(db, identity, membership, "sessions_revoked", {})
    db.commit()


@app.put("/users/{membership_id}/workspace-access", dependencies=[Depends(require_roles("owner"))])
def change_workspace_access(membership_id: int, body: WorkspaceAccessIn,
                            identity: AuthenticatedIdentity = Depends(require_auth), db: Session = Depends(db_session)):
    membership = managed_membership(db, membership_id, identity)
    try: requested = {validate_workspace_id(x) for x in body.workspace_ids}
    except ValueError as exc: raise HTTPException(422, str(exc))
    account = db.get(Account, membership.account_id)
    workspaces = validate_membership_workspaces(db, account, membership.primary_workspace_id, requested)
    old = sorted(x.workspace_id for x in membership.workspace_grants)
    membership.workspace_grants = [MembershipWorkspaceAccess(workspace_id=x) for x in sorted(workspaces)]
    audit_membership(db, identity, membership, "workspace_access_changed", {"from": old, "to": sorted(workspaces)})
    db.commit()
    return membership_dict(membership)


@app.post("/internal/agency-workspaces", status_code=201,
          dependencies=[Depends(require_roles("owner"))])
def delegate_agency_workspace(body: AgencyWorkspaceIn,
                              identity: AuthenticatedIdentity = Depends(require_auth), db: Session = Depends(db_session)):
    if identity.role != "internal_admin":
        raise HTTPException(403, "Internal administrator authentication is required")
    agency, workspace = db.get(Account, body.agency_account_id), db.get(Workspace, body.workspace_id)
    if not agency or agency.account_type != "agency" or not workspace or workspace.workspace_type != "client":
        raise HTTPException(404, "Agency or client workspace not found")
    item = db.scalar(select(AgencyWorkspaceAccess).where(
        AgencyWorkspaceAccess.agency_account_id == agency.id,
        AgencyWorkspaceAccess.workspace_id == workspace.id))
    if not item:
        db.add(AgencyWorkspaceAccess(agency_account_id=agency.id, workspace_id=workspace.id))
        db.add(WorkspaceAudit(workspace_id=workspace.id, account_id=agency.id,
                              action="agency_workspace_delegated", actor_user_id=identity.user_id))
        db.commit()
    return {"agency_account_id": agency.id, "workspace_id": workspace.id}


@app.get("/health")
def health(db: Session = Depends(db_session)):
    db.execute(select(1))
    return {"ok": True, "database": "connected", "dry_run": DRY_RUN}


@app.get("/email-provider/status", dependencies=[Depends(require_auth)],
         summary="Inspect email provider readiness without sending.")
def email_provider_status():
    return {"provider": EMAIL_PROVIDER_NAME, "configured": not provider_readiness_failures(),
            "sender": EMAIL_FROM or None, "live_send_enabled": False}


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


@app.post("/prospects", status_code=201, dependencies=[Depends(require_roles("owner", "admin", "member"))])
def create_prospect(body: ProspectIn, workspace_id: str = Depends(workspace_context), db: Session = Depends(db_session)):
    # Trusted verification is never accepted from this customer-facing request.
    p = Prospect(workspace_id=workspace_id, email_verified=False, **body.model_dump())
    db.add(p)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "Prospect email already exists")
    return prospect_dict(p)


@app.post("/internal/prospects/{prospect_id}/verify-email", dependencies=[Depends(require_auth)])
def verify_prospect_email(prospect_id: int, body: TrustedEmailVerificationIn,
                          identity: AuthenticatedIdentity = Depends(require_auth),
                          workspace_id: str = Depends(workspace_context), db: Session = Depends(db_session)):
    if identity.role != "internal_admin":
        raise HTTPException(403, "Internal administrator authentication is required")
    prospect = db.scalar(select(Prospect).where(
        Prospect.id == prospect_id, Prospect.workspace_id == workspace_id).with_for_update())
    if not prospect:
        raise HTTPException(404, "Prospect not found")
    duplicate = db.scalar(select(Prospect.id).where(
        Prospect.workspace_id == workspace_id,
        Prospect.verified_email == body.verified_email,
        Prospect.id != prospect.id,
    ).limit(1))
    if duplicate is not None:
        raise HTTPException(409, "Verified email is already assigned in this workspace")
    old_email = prospect.verified_email
    if old_email is not None:
        try:
            old_email = normalize_email(old_email)
        except ValueError:
            # Preserve malformed legacy data in the audit; it can never compare
            # equal to the newly validated address or pass an outreach gate.
            pass
    changed = old_email != body.verified_email
    invalidated_campaign_ids: list[int] = []
    if changed:
        for campaign in prospect.campaigns:
            invalidated_campaign_ids.append(campaign.id)
            campaign.live_authorized = False
            campaign.live_authorized_at = None
            campaign.live_authorized_by = None
            campaign.authorized_recipient_email = None
            campaign.dry_run = True
            for touch in campaign.touches:
                if touch.sent_at is None:
                    touch.dry_run = True
                    if touch.execution_status != "sending":
                        touch.execution_status = "pending"
                    touch.last_execution_error = "recipient changed; campaign authorization invalidated"
    prospect.verified_email = body.verified_email
    prospect.email_verified = True
    verified_at = utcnow()
    prospect.updated_at = verified_at
    db.add(EmailVerificationAudit(
        prospect_id=prospect.id, workspace_id=workspace_id,
        old_email=old_email, new_email=body.verified_email,
        verifier_identity=identity.role, verified_at=verified_at,
        invalidated_campaign_ids=json.dumps(invalidated_campaign_ids),
    ))
    try:
        db.commit()
    except IntegrityError:
        # The unique constraint is authoritative: another request may assign the
        # address after the pre-check. Roll back every in-transaction mutation,
        # including campaign invalidation, touch state, and the audit insert.
        db.rollback()
        raise HTTPException(409, "Verified email is already assigned in this workspace")
    return prospect_dict(prospect)


@app.get("/prospects", dependencies=[Depends(require_auth)])
def list_prospects(status: str | None = None, industry: str | None = None,
                   limit: int = Query(25, ge=1, le=100), workspace_id: str = Depends(workspace_context),
                   db: Session = Depends(db_session)):
    query = select(Prospect).where(Prospect.workspace_id == workspace_id)
    if status:
        query = query.where(Prospect.status == status)
    if industry:
        query = query.where(Prospect.industry == industry)
    return [prospect_dict(p) for p in db.scalars(query.order_by(Prospect.score.desc()).limit(limit))]


@app.get("/prospects/{prospect_id}/campaigns", response_model=list[CampaignInspection],
         dependencies=[Depends(require_auth)], summary="Inspect a prospect's campaigns without changing state.")
def inspect_prospect_campaigns(prospect_id: int, workspace_id: str = Depends(workspace_context), db: Session = Depends(db_session)):
    prospect = scoped_prospect(db, prospect_id, workspace_id)
    if not prospect:
        raise HTTPException(404, "Prospect not found")
    return [campaign_inspection(c) for c in sorted(prospect.campaigns, key=lambda campaign: campaign.id)]


@app.get("/campaigns/{campaign_id}/deliveries", response_model=list[DeliveryInspection],
         dependencies=[Depends(require_auth)], summary="Inspect campaign deliveries without running them.")
def inspect_campaign_deliveries(campaign_id: int, workspace_id: str = Depends(workspace_context), db: Session = Depends(db_session)):
    campaign = scoped_campaign(db, campaign_id, workspace_id)
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


@app.post("/campaigns/{campaign_id}/authorize-live", dependencies=[Depends(require_roles("owner", "admin"))],
          summary="Explicitly authorize a campaign for future live execution.")
def authorize_live(campaign_id: int, body: LiveAuthorizationIn, workspace_id: str = Depends(workspace_context), db: Session = Depends(db_session)):
    campaign = scoped_campaign(db, campaign_id, workspace_id)
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
        campaign.authorized_recipient_email = campaign.prospect.verified_email
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
def inspect_campaign_safety(campaign_id: int, workspace_id: str = Depends(workspace_context), db: Session = Depends(db_session)):
    campaign = scoped_campaign(db, campaign_id, workspace_id)
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
def canary_preflight(delivery_id: int, workspace_id: str = Depends(workspace_context), db: Session = Depends(db_session)):
    touch = scoped_touch(db, delivery_id, workspace_id)
    if not touch:
        raise HTTPException(404, "Delivery not found")
    return can_execute_delivery(touch, touch.campaign, touch.campaign.prospect, db,
                                check_provider=True)


@app.get("/deliveries/{delivery_id}/execution", dependencies=[Depends(require_auth)],
         summary="Inspect persisted canary execution state without executing.")
def inspect_delivery_execution(delivery_id: int, workspace_id: str = Depends(workspace_context), db: Session = Depends(db_session)):
    touch = scoped_touch(db, delivery_id, workspace_id)
    if not touch:
        raise HTTPException(404, "Delivery not found")
    prospect = touch.campaign.prospect
    return {
        "delivery_id": touch.id, "campaign_id": touch.campaign_id,
        "prospect_id": prospect.id, "execution_status": touch.execution_status,
        "attempt_count": touch.execution_attempt_count, "sent_at": touch.sent_at,
        "provider": touch.provider_name, "provider_message_id": touch.provider_message_id,
        "provider_correlation_id": touch.provider_correlation_id,
        "idempotency_key": touch.idempotency_key, "sender": EMAIL_FROM or None,
        "recipient": prospect.verified_email, "last_execution_error": touch.last_execution_error,
    }


@app.get("/campaigns/{campaign_id}/canary-audits", dependencies=[Depends(require_auth)],
         summary="Inspect non-secret canary audit records without executing.")
def inspect_canary_audits(campaign_id: int, workspace_id: str = Depends(workspace_context),
                          db: Session = Depends(db_session)):
    campaign = scoped_campaign(db, campaign_id, workspace_id)
    if not campaign:
        raise HTTPException(404, "Campaign not found")
    audits = db.scalars(select(CanaryExecutionAudit).where(
        CanaryExecutionAudit.campaign_id == campaign.id).order_by(CanaryExecutionAudit.id))
    return [{column.name: getattr(audit, column.name)
             for column in audit.__table__.columns} for audit in audits]


@app.post("/campaigns/{campaign_id}/canary-execute", dependencies=[Depends(require_roles("owner", "admin"))],
          summary="Explicitly attempt no more than one persisted email delivery.")
def canary_execute(campaign_id: int, body: CanaryExecutionIn, workspace_id: str = Depends(workspace_context), db: Session = Depends(db_session)):
    """Claim exactly one explicitly named delivery; never enumerate campaign deliveries."""
    campaign = scoped_campaign(db, campaign_id, workspace_id)
    if not campaign:
        return JSONResponse(status_code=409, content={
            "detail": "Canary execution blocked by safety checks", "failures": ["campaign does not exist"],
        })
    touch = scoped_touch(db, body.delivery_id, workspace_id)
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

    if provider_readiness_failures():
        add_canary_audit(db, touch, body.authorized_by, "failed", "email delivery provider is not configured")
        db.commit()
        raise HTTPException(503, "No email delivery provider is configured; no email was sent")
    try:
        if EMAIL_PROVIDER_NAME == "microsoft_graph":
            provider = configured_provider(EMAIL_PROVIDER_NAME, tenant_id=MICROSOFT_TENANT_ID,
                                           client_id=MICROSOFT_CLIENT_ID,
                                           client_secret=MICROSOFT_CLIENT_SECRET)
        else:
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
    touch = scoped_touch(db, body.delivery_id, workspace_id)
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
            sender=EMAIL_FROM, recipient=touch.campaign.authorized_recipient_email,
            subject=touch.subject, message=touch.message, idempotency_key=touch.idempotency_key,
        )
    except Exception as exc:
        safe_error = (exc.reason if isinstance(exc, EmailProviderError)
                      else f"{type(exc).__name__}: email provider call failed")[:500]
        touch.execution_status = "failed"
        touch.execution_completed_at = utcnow()
        touch.last_execution_error = safe_error
        add_canary_audit(db, touch, body.authorized_by, "failed", safe_error)
        db.commit()
        content = {"detail": "Email provider call failed; no send was confirmed"}
        if isinstance(exc, EmailProviderError) and exc.retry_after:
            content["retry_after"] = exc.retry_after
        return JSONResponse(status_code=502, content=content)

    completed = utcnow()
    touch.status, touch.sent_at, touch.execution_status = "sent", completed, "sent"
    touch.execution_completed_at = completed
    touch.provider_message_id = result.message_id
    touch.provider_correlation_id = result.correlation_id
    touch.last_execution_error = None
    add_canary_audit(db, touch, body.authorized_by, "sent", provider_message_id=result.message_id,
                     provider_correlation_id=result.correlation_id)
    db.commit()
    return {"delivery_id": touch.id, "execution_status": "sent", "already_executed": False,
            "sent_at": touch.sent_at, "provider": provider.name,
            "provider_message_id": touch.provider_message_id}


@app.post("/prospects/{prospect_id}/campaigns", status_code=201, dependencies=[Depends(require_roles("owner", "admin", "member"))])
def launch_campaign(prospect_id: int, body: CampaignIn, workspace_id: str = Depends(workspace_context), db: Session = Depends(db_session)):
    p = scoped_prospect(db, prospect_id, workspace_id)
    if not p:
        raise HTTPException(404, "Prospect not found")
    if not p.email_verified or not p.verified_email:
        raise HTTPException(422, "A verified email is required")
    if p.score < MIN_QUALIFICATION_SCORE:
        raise HTTPException(422, f"Qualification score must be at least {MIN_QUALIFICATION_SCORE}")
    if db.get(Suppression, (p.verified_email, p.workspace_id)):
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
        campaign.touches.append(Touch(day=day, scheduled_at=start + timedelta(days=day),
                                      subject=f"Lead recovery for {p.company_name}",
                                      message=templates[day], idempotency_key=key))
    p.status, p.updated_at = "campaign_active", utcnow()
    db.add(campaign)
    db.commit()
    return campaign_dict(campaign)


@app.post("/launcher/run", dependencies=[Depends(require_roles("owner", "admin"))])
def run_launcher(now_at: datetime | None = None, workspace_id: str = Depends(workspace_context), db: Session = Depends(db_session)):
    current = now_at or utcnow()
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    due = list(db.scalars(select(Touch).where(Touch.status == "scheduled", Touch.scheduled_at <= current, Touch.campaign.has(Campaign.prospect.has(Prospect.workspace_id == workspace_id))).order_by(Touch.scheduled_at)))
    sent = skipped = failed = 0
    for touch in due:
        p = touch.campaign.prospect
        if touch.campaign.status != "active" or p.status in {"replied", "qualified", "converted", "suppressed"} or db.get(Suppression, (p.verified_email, p.workspace_id)):
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


@app.post("/suppressions", status_code=201, dependencies=[Depends(require_roles("owner", "admin"))])
def suppress(body: SuppressionIn, workspace_id: str = Depends(workspace_context), db: Session = Depends(db_session)):
    item = db.get(Suppression, (body.email, workspace_id))
    if not item:
        item = Suppression(email=body.email, workspace_id=workspace_id, reason=body.reason)
        db.add(item)
    for p in db.scalars(select(Prospect).where(Prospect.verified_email == body.email, Prospect.workspace_id == workspace_id)):
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


@app.post("/prospects/{prospect_id}/reply", dependencies=[Depends(require_roles("owner", "admin"))])
def record_reply(prospect_id: int, body: ReplyIn, workspace_id: str = Depends(workspace_context), db: Session = Depends(db_session)):
    p = scoped_prospect(db, prospect_id, workspace_id)
    if not p:
        raise HTTPException(404, "Prospect not found")
    p.last_reply, p.intent = body.reply_text, body.intent
    p.status = "qualified" if body.intent in {"interested", "pricing", "ready_to_start", "trial_interest"} else "replied"
    for campaign in p.campaigns:
        campaign.status = "stopped"
    db.commit()
    return prospect_dict(p)


@app.post("/prospects/{prospect_id}/conversion", dependencies=[Depends(require_roles("owner", "admin"))])
def conversion(prospect_id: int, body: ConversionIn, workspace_id: str = Depends(workspace_context), db: Session = Depends(db_session)):
    p = scoped_prospect(db, prospect_id, workspace_id)
    if not p:
        raise HTTPException(404, "Prospect not found")
    p.conversion_stage = body.conversion_stage
    p.status = "converted" if body.conversion_stage in {"standard_start", "three_day_trial", "converted"} else "qualified"
    db.commit()
    return prospect_dict(p)
