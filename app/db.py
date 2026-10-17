from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from sqlalchemy import DateTime, Integer, String, Text, create_engine
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
    claimed_by: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
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


settings = get_settings()
if settings.database_url.startswith("sqlite:///"):
    Path("data").mkdir(exist_ok=True)
connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
engine = create_engine(settings.database_url, connect_args=connect_args, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def init_db() -> None:
    Base.metadata.create_all(engine)
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
