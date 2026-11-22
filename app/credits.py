"""Higgsfield credit vault.

Higgsfield bills in credits and the balance lives in the owner's account, so
the factory keeps its own ledger: a starting balance the owner sets, every
credit spent recorded per job and per series, a cap per series, and a panel
that shows what is left before a batch of reels is made.
"""
from sqlalchemy import func, select

from .db import ContentJob, ContentSeries, FactorySetting, ProviderUsage, SessionLocal

STARTING_BALANCE_KEY = "higgsfield_credits_starting"
SERIES_CAP_KEY = "higgsfield_credits_per_series"
DEFAULT_SERIES_CAP = 400
DEFAULT_CREDIT_USD = 0.0143


def _setting(key: str) -> str | None:
    with SessionLocal() as session:
        setting = session.get(FactorySetting, key)
        return setting.value if setting is not None else None


def _set_setting(key: str, value: str) -> None:
    with SessionLocal() as session:
        setting = session.get(FactorySetting, key)
        if setting is None:
            session.add(FactorySetting(key=key, value=value))
        else:
            setting.value = value
        session.commit()


def starting_balance() -> int:
    raw = _setting(STARTING_BALANCE_KEY)
    try:
        return max(0, int(float(raw))) if raw else 0
    except (TypeError, ValueError):
        return 0


def set_starting_balance(credits: int) -> int:
    value = max(0, int(credits))
    _set_setting(STARTING_BALANCE_KEY, str(value))
    return value


def series_cap() -> int:
    raw = _setting(SERIES_CAP_KEY)
    try:
        return max(0, int(float(raw))) if raw else DEFAULT_SERIES_CAP
    except (TypeError, ValueError):
        return DEFAULT_SERIES_CAP


def set_series_cap(credits: int) -> int:
    value = max(0, int(credits))
    _set_setting(SERIES_CAP_KEY, str(value))
    return value


def credits_spent() -> int:
    with SessionLocal() as session:
        total = session.scalar(
            select(func.coalesce(func.sum(ProviderUsage.units), 0.0)).where(
                ProviderUsage.provider == "higgsfield"
            )
        )
    return int(round(float(total or 0.0)))


def credits_spent_for_job(job_id: str) -> int:
    with SessionLocal() as session:
        total = session.scalar(
            select(func.coalesce(func.sum(ProviderUsage.units), 0.0)).where(
                ProviderUsage.provider == "higgsfield",
                ProviderUsage.job_id == job_id,
            )
        )
    return int(round(float(total or 0.0)))


def credits_spent_for_series(series_id: str) -> int:
    with SessionLocal() as session:
        job_ids = list(
            session.scalars(select(ContentJob.id).where(ContentJob.series_id == series_id))
        )
        if not job_ids:
            return 0
        total = session.scalar(
            select(func.coalesce(func.sum(ProviderUsage.units), 0.0)).where(
                ProviderUsage.provider == "higgsfield",
                ProviderUsage.job_id.in_(job_ids),
            )
        )
    return int(round(float(total or 0.0)))


def remaining_balance() -> int:
    return max(0, starting_balance() - credits_spent())


def credit_summary() -> dict[str, object]:
    """The credit vault as one dict, for the health endpoint and Telegram."""
    spent = credits_spent()
    start = starting_balance()
    return {
        "starting_balance": start,
        "spent": spent,
        "remaining": max(0, start - spent),
        "series_cap": series_cap(),
        "cost_usd": round(spent * DEFAULT_CREDIT_USD, 4),
    }


def enforce_series_credit_budget(series_id: str) -> dict[str, object]:
    """Stop a series from spending past its credit cap."""
    cap = series_cap()
    spent = credits_spent_for_series(series_id)
    if cap and spent >= cap:
        raise ValueError(
            f"This series has used its {cap} Higgsfield credits; "
            "raise the credit cap or pause the series."
        )
    return {"spent": spent, "cap": cap, "remaining": max(0, cap - spent)}


def enforce_credit_balance() -> dict[str, object]:
    """Stop the factory from spending when the pool is empty."""
    summary = credit_summary()
    if summary["starting_balance"] and summary["remaining"] <= 0:
        raise ValueError(
            "The Higgsfield credit pool is empty; add credits or switch the video provider."
        )
    return summary


def active_series_spend() -> list[dict[str, object]]:
    """Credit spend per series, for the operator."""
    with SessionLocal() as session:
        series_list = list(
            session.scalars(
                select(ContentSeries).order_by(ContentSeries.updated_at.desc()).limit(10)
            )
        )
    result = []
    for series in series_list:
        result.append(
            {
                "id": series.id[:8],
                "topic": series.topic,
                "status": series.status,
                "credits": credits_spent_for_series(series.id),
                "cap": series_cap(),
            }
        )
    return result
