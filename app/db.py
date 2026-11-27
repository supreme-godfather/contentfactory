from datetime import datetime, timezone
from pathlib import Path
import logging
import time
from uuid import uuid4

from sqlalchemy import DateTime, Float, Integer, String, Text, UniqueConstraint, create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from .config import get_settings


class Base(DeclarativeBase):
    pass


class ContentJob(Base):
    __tablename__ = "content_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    brand: Mapped[str] = mapped_column(String(80), default="learn_with_elite_explainer")
    topic: Mapped[str] = mapped_column(String(240))
    platform: Mapped[str] = mapped_column(String(30))
    status: Mapped[str] = mapped_column(String(40), default="queued", index=True)
    stage: Mapped[str] = mapped_column(String(40), default="topic")
    content_version: Mapped[int] = mapped_column(Integer, default=1)
    approval_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(160), unique=True, index=True)
    script_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    render_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    schedule_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    claimed_by: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    series_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))


class ContentSeries(Base):
    __tablename__ = "content_series"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    topic: Mapped[str] = mapped_column(String(240))
    pack_id: Mapped[str] = mapped_column(String(60), index=True)
    part_count: Mapped[int] = mapped_column(Integer, default=6)
    status: Mapped[str] = mapped_column(String(40), default="planned", index=True)
    cadence: Mapped[str] = mapped_column(String(30), default="daily")
    budget_usd: Mapped[float] = mapped_column(Float, default=5.0)
    gap_hours: Mapped[int] = mapped_column(Integer, default=24)
    plan_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    character_asset: Mapped[str | None] = mapped_column(String(240), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))


class FactorySetting(Base):
    __tablename__ = "factory_settings"

    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[str] = mapped_column(String(240))


class WorkerLease(Base):
    __tablename__ = "worker_leases"

    worker_id: Mapped[str] = mapped_column(String(120), primary_key=True)
    status: Mapped[str] = mapped_column(String(40), default="online")
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class BrandProfile(Base):
    __tablename__ = "brand_profiles"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(120))
    language: Mapped[str] = mapped_column(String(40), default="hi")
    tone: Mapped[str] = mapped_column(String(120), default="simple conversational documentary")
    enabled: Mapped[int] = mapped_column(Integer, default=1)


class TrendCandidate(Base):
    __tablename__ = "trend_candidates"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    topic: Mapped[str] = mapped_column(String(240), index=True)
    source: Mapped[str] = mapped_column(String(80), default="manual")
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    region: Mapped[str] = mapped_column(String(20), default="IN")
    freshness_score: Mapped[int] = mapped_column(Integer, default=0)
    relevance_score: Mapped[int] = mapped_column(Integer, default=0)
    originality_score: Mapped[int] = mapped_column(Integer, default=100)
    risk_score: Mapped[int] = mapped_column(Integer, default=0)
    total_score: Mapped[int] = mapped_column(Integer, default=0, index=True)
    originality_required: Mapped[int] = mapped_column(Integer, default=75)
    transformation_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(30), default="candidate", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class AnalyticsSnapshot(Base):
    __tablename__ = "analytics_snapshots"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    platform: Mapped[str] = mapped_column(String(30), index=True)
    account: Mapped[str] = mapped_column(String(120))
    content_id: Mapped[str] = mapped_column(String(160), index=True)
    views: Mapped[int] = mapped_column(Integer, default=0)
    likes: Mapped[int] = mapped_column(Integer, default=0)
    comments: Mapped[int] = mapped_column(Integer, default=0)
    shares: Mapped[int] = mapped_column(Integer, default=0)
    watch_seconds: Mapped[int] = mapped_column(Integer, default=0)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class PlatformAccount(Base):
    __tablename__ = "platform_accounts"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    platform: Mapped[str] = mapped_column(String(30), index=True)
    account_name: Mapped[str] = mapped_column(String(160))
    enabled: Mapped[int] = mapped_column(Integer, default=0)
    authorized: Mapped[int] = mapped_column(Integer, default=0)
    daily_limit: Mapped[int] = mapped_column(Integer, default=3)


class OAuthCredential(Base):
    __tablename__ = "oauth_credentials"

    provider: Mapped[str] = mapped_column(String(40), primary_key=True)
    access_token_encrypted: Mapped[str] = mapped_column(Text)
    refresh_token_encrypted: Mapped[str] = mapped_column(Text)
    token_type: Mapped[str] = mapped_column(String(40), default="Bearer")
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    scope: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))


class ProviderCredential(Base):
    __tablename__ = "provider_credentials"

    provider: Mapped[str] = mapped_column(String(40), primary_key=True)
    secret_encrypted: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))


class AutomationEvent(Base):
    __tablename__ = "automation_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    kind: Mapped[str] = mapped_column(String(40), index=True)
    job_id: Mapped[str] = mapped_column(String(36), index=True)
    detail: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class ProviderUsage(Base):
    __tablename__ = "provider_usage"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    job_id: Mapped[str] = mapped_column(String(36), index=True)
    provider: Mapped[str] = mapped_column(String(40), index=True)
    kind: Mapped[str] = mapped_column(String(40))
    units: Mapped[float] = mapped_column(Float, default=0.0)
    unit: Mapped[str] = mapped_column(String(40), default="count")
    unit_price_usd: Mapped[float] = mapped_column(Float, default=0.0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class ManagerAudit(Base):
    __tablename__ = "manager_audit"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    owner_id: Mapped[str] = mapped_column(String(80), index=True)
    channel: Mapped[str] = mapped_column(String(30), index=True)
    request: Mapped[str] = mapped_column(Text)
    decision: Mapped[str] = mapped_column(String(40), index=True)
    detail: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class ManagerConfirmation(Base):
    __tablename__ = "manager_confirmations"

    token: Mapped[str] = mapped_column(String(12), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(80), index=True)
    channel: Mapped[str] = mapped_column(String(30), index=True)
    action: Mapped[str] = mapped_column(String(40))
    args_json: Mapped[str] = mapped_column(Text)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    executed: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class PublicationRecord(Base):
    __tablename__ = "publication_records"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    job_id: Mapped[str] = mapped_column(String(36), index=True)
    platform: Mapped[str] = mapped_column(String(30), index=True)
    idempotency_key: Mapped[str] = mapped_column(String(180), unique=True, index=True)
    status: Mapped[str] = mapped_column(String(40), default="pending")
    remote_id: Mapped[str | None] = mapped_column(String(240), nullable=True)
    remote_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class ClipJob(Base):
    __tablename__ = "clip_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    source_url_encrypted: Mapped[str] = mapped_column(Text)
    source_display: Mapped[str] = mapped_column(String(500))
    source_kind: Mapped[str] = mapped_column(String(20), index=True)
    capture_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(40), default="queued", index=True)
    stage: Mapped[str] = mapped_column(String(40), default="ingest")
    idempotency_key: Mapped[str] = mapped_column(String(160), unique=True, index=True)
    claimed_by: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    lease_token_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    attempt: Mapped[int] = mapped_column(Integer, default=0)
    metadata_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))


class ClipCandidate(Base):
    __tablename__ = "clip_candidates"
    __table_args__ = (UniqueConstraint("job_id", "rank", name="uq_clip_candidate_job_rank"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    job_id: Mapped[str] = mapped_column(String(36), index=True)
    rank: Mapped[int] = mapped_column(Integer)
    start_ms: Mapped[int] = mapped_column(Integer)
    end_ms: Mapped[int] = mapped_column(Integer)
    score: Mapped[int] = mapped_column(Integer, default=0)
    title: Mapped[str] = mapped_column(String(200))
    reason: Mapped[str] = mapped_column(String(500), default="")
    transcript_excerpt: Mapped[str | None] = mapped_column(Text, nullable=True)
    render_filename: Mapped[str] = mapped_column(String(240))
    youtube_thumbnail_filename: Mapped[str] = mapped_column(String(240))
    instagram_thumbnail_filename: Mapped[str] = mapped_column(String(240))
    sha256: Mapped[str] = mapped_column(String(64))
    size_bytes: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(30), default="awaiting_review", index=True)
    reviewed_by: Mapped[str | None] = mapped_column(String(80), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))


settings = get_settings()
logger = logging.getLogger(__name__)
database_url = settings.database_url
if not database_url.strip():
    database_url = "sqlite:///./data/factory.db"
if database_url.startswith("postgresql://"):
    database_url = database_url.replace("postgresql://", "postgresql+psycopg://", 1)
elif database_url.startswith("postgres://"):
    database_url = database_url.replace("postgres://", "postgresql+psycopg://", 1)
is_sqlite = database_url.startswith("sqlite")
if is_sqlite:
    sqlite_path = make_url(database_url).database
    if sqlite_path and sqlite_path != ":memory:":
        Path(sqlite_path).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
    connect_args = {"check_same_thread": False, "timeout": 30}
else:
    connect_args = {"connect_timeout": 10}
engine = create_engine(database_url, connect_args=connect_args, pool_pre_ping=True)
if is_sqlite:
    @event.listens_for(engine, "connect")
    def _configure_sqlite_connection(connection, _record) -> None:
        cursor = connection.cursor()
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.close()

SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def init_db() -> None:
    for attempt in range(1, 13):
        try:
            Base.metadata.create_all(engine)
            break
        except OperationalError:
            if attempt == 12:
                raise
            logger.warning(
                "Database is not ready; retrying initialization (%d/12)",
                attempt,
            )
            time.sleep(5)
    # Keep the small initial deployment upgradeable without a migration runner.
    # Future schema changes should move to versioned migrations.
    from sqlalchemy import inspect, text

    inspector = inspect(engine)
    columns = {column["name"] for column in inspector.get_columns("content_jobs")}
    with engine.begin() as connection:
        if "claimed_by" not in columns:
            connection.execute(text("ALTER TABLE content_jobs ADD COLUMN claimed_by VARCHAR(120)"))
        if "lease_expires_at" not in columns:
            connection.execute(text("ALTER TABLE content_jobs ADD COLUMN lease_expires_at TIMESTAMP"))
        if "retry_count" not in columns:
            connection.execute(text("ALTER TABLE content_jobs ADD COLUMN retry_count INTEGER DEFAULT 0"))
        if "scheduled_at" not in columns:
            connection.execute(text("ALTER TABLE content_jobs ADD COLUMN scheduled_at TIMESTAMP"))
        if "schedule_notified_at" not in columns:
            connection.execute(text("ALTER TABLE content_jobs ADD COLUMN schedule_notified_at TIMESTAMP"))
        if "series_id" not in columns:
            connection.execute(text("ALTER TABLE content_jobs ADD COLUMN series_id VARCHAR(36)"))
            connection.execute(text("CREATE INDEX IF NOT EXISTS ix_content_jobs_series_id ON content_jobs (series_id)"))
