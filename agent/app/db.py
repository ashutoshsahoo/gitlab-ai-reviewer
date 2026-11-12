"""Persistence of review history (PostgreSQL in compose, SQLite in tests)."""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker
from sqlalchemy.pool import StaticPool

from .config import Settings

log = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Review(Base):
    __tablename__ = "reviews"
    __table_args__ = (Index("ix_reviews_mr_sha", "project_id", "mr_iid", "head_sha"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(Integer)
    mr_iid: Mapped[int] = mapped_column(Integer)
    head_sha: Mapped[str] = mapped_column(String(64))
    trigger: Mapped[str] = mapped_column(String(16), default="webhook")
    status: Mapped[str] = mapped_column(String(16), default="running")  # running|done|error
    gate_state: Mapped[str | None] = mapped_column(String(16), nullable=True)
    gate_passed: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    model: Mapped[str] = mapped_column(String(128), default="")
    files_reviewed: Mapped[int] = mapped_column(Integer, default=0)
    files_skipped: Mapped[int] = mapped_column(Integer, default=0)
    findings_total: Mapped[int] = mapped_column(Integer, default=0)
    counts_json: Mapped[str] = mapped_column(Text, default="{}")
    summary_md: Mapped[str] = mapped_column(Text, default="")
    error: Mapped[str] = mapped_column(Text, default="")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_s: Mapped[float | None] = mapped_column(Float, nullable=True)

    findings: Mapped[list["FindingRow"]] = relationship(back_populates="review", cascade="all, delete-orphan")


class FindingRow(Base):
    __tablename__ = "findings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    review_id: Mapped[int] = mapped_column(ForeignKey("reviews.id", ondelete="CASCADE"), index=True)
    fingerprint: Mapped[str] = mapped_column(String(16), index=True)
    file: Mapped[str] = mapped_column(Text)
    line: Mapped[int] = mapped_column(Integer)
    severity: Mapped[str] = mapped_column(String(16))
    category: Mapped[str] = mapped_column(String(32))
    title: Mapped[str] = mapped_column(Text)
    message: Mapped[str] = mapped_column(Text)
    suggestion: Mapped[str | None] = mapped_column(Text, nullable=True)
    confidence: Mapped[float] = mapped_column(Float)
    source: Mapped[str] = mapped_column(String(16))
    disposition: Mapped[str] = mapped_column(String(16))

    review: Mapped[Review] = relationship(back_populates="findings")


def make_session_factory(settings: Settings | str):
    url = settings if isinstance(settings, str) else settings.database_url
    if url.startswith("sqlite"):
        engine = create_engine(url, connect_args={"check_same_thread": False}, poolclass=StaticPool)
    else:
        engine = create_engine(url, pool_pre_ping=True)
    return sessionmaker(engine, expire_on_commit=False)


def init_db(session_factory, attempts: int = 20) -> None:
    engine = session_factory.kw["bind"]
    for i in range(attempts):
        try:
            Base.metadata.create_all(engine)
            return
        except Exception as exc:  # noqa: BLE001 - DB may still be starting / concurrent create
            log.warning("init_db attempt %d failed: %s", i + 1, exc)
            time.sleep(2)
    raise RuntimeError("database not reachable")
