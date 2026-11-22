"""Higgsfield money vault.

The Higgsfield API is a separate product from the higgsfield.ai subscription:
the API runs on its own **dollar balance** and is billed per generation, while
the website plan's credits belong to the studio, the MCP server and the CLI.

So the factory tracks dollars, not platform credits:

- the owner sets the API balance the account has,
- every generation is written to the cost ledger with its real price,
- a per-series cap stops one story from draining the balance,
- the factory refuses to spend a balance that is empty, and it estimates a
  request before it is made so a shot is never paid for twice.
"""
from sqlalchemy import func, select

from .db import ContentJob, ContentSeries, FactorySetting, ProviderUsage, SessionLocal

STARTING_BALANCE_KEY = "higgsfield_balance_usd"
SERIES_CAP_KEY = "higgsfield_usd_per_series"
DEFAULT_SERIES_CAP_USD = 5.0
FALLBACK_CREDIT_USD = 0.0143


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


def starting_balance() -> float:
    raw = _setting(STARTING_BALANCE_KEY)
    try:
        return max(0.0, float(raw)) if raw else 0.0
    except (TypeError, ValueError):
        return 0.0


def set_starting_balance(usd: float) -> float:
    value = round(max(0.0, float(usd)), 2)
    _set_setting(STARTING_BALANCE_KEY, f"{value:.2f}")
    return value


def series_cap() -> float:
    raw = _setting(SERIES_CAP_KEY)
    try:
        return max(0.0, float(raw)) if raw else DEFAULT_SERIES_CAP_USD
    except (TypeError, ValueError):
        return DEFAULT_SERIES_CAP_USD


def set_series_cap(usd: float) -> float:
    value = round(max(0.0, float(usd)), 2)
    _set_setting(SERIES_CAP_KEY, f"{value:.2f}")
    return value


def spent_usd() -> float:
    with SessionLocal() as session:
        total = session.scalar(
            select(func.coalesce(func.sum(ProviderUsage.cost_usd), 0.0)).where(
                ProviderUsage.provider == "higgsfield"
            )
        )
    return round(float(total or 0.0), 4)


def spent_usd_for_job(job_id: str) -> float:
    with SessionLocal() as session:
        total = session.scalar(
            select(func.coalesce(func.sum(ProviderUsage.cost_usd), 0.0)).where(
                ProviderUsage.provider == "higgsfield",
                ProviderUsage.job_id == job_id,
            )
        )
    return round(float(total or 0.0), 4)


def spent_usd_for_series(series_id: str) -> float:
    with SessionLocal() as session:
        job_ids = list(
            session.scalars(select(ContentJob.id).where(ContentJob.series_id == series_id))
        )
        if not job_ids:
            return 0.0
        total = session.scalar(
            select(func.coalesce(func.sum(ProviderUsage.cost_usd), 0.0)).where(
                ProviderUsage.provider == "higgsfield",
                ProviderUsage.job_id.in_(job_ids),
            )
        )
    return round(float(total or 0.0), 4)


def remaining_balance() -> float:
    return round(max(0.0, starting_balance() - spent_usd()), 4)


def money_summary() -> dict[str, object]:
    """The money vault as one dict, for the health endpoint and Telegram."""
    start = starting_balance()
    spent = spent_usd()
    return {
        "balance_usd": start,
        "spent_usd": spent,
        "remaining_usd": round(max(0.0, start - spent), 4),
        "series_cap_usd": series_cap(),
    }


def enforce_series_money_budget(series_id: str) -> dict[str, object]:
    """Stop a series from spending past its dollar cap."""
    cap = series_cap()
    spent = spent_usd_for_series(series_id)
    if cap and spent >= cap:
        raise ValueError(
            f"This series has used its ${cap:.2f} Higgsfield budget "
            f"(${spent:.2f} spent); raise the cap or pause the series."
        )
    return {"spent_usd": spent, "cap_usd": cap, "remaining_usd": round(max(0.0, cap - spent), 4)}


def enforce_balance(needed_usd: float = 0.0) -> dict[str, object]:
    """Refuse to spend more than the balance holds."""
    summary = money_summary()
    remaining = float(summary["remaining_usd"])
    if summary["balance_usd"] and remaining <= 0:
        raise ValueError(
            "The Higgsfield API balance is empty; top it up or switch the video provider."
        )
    if needed_usd and summary["balance_usd"] and needed_usd > remaining:
        raise ValueError(
            f"This shot costs ${needed_usd:.2f} but only ${remaining:.2f} is left on the "
            "Higgsfield API balance."
        )
    return summary


def affordable_shots(
    remaining_usd: float,
    price_per_shot: float,
    desired: int,
) -> int:
    """How many shots the remaining balance can pay for."""
    if price_per_shot <= 0:
        return desired
    return max(0, min(desired, int(remaining_usd // price_per_shot)))


def active_series_spend() -> list[dict[str, object]]:
    """Dollar spend per series, for the operator."""
    with SessionLocal() as session:
        series_list = list(
            session.scalars(
                select(ContentSeries).order_by(ContentSeries.updated_at.desc()).limit(10)
            )
        )
    return [
        {
            "id": series.id[:8],
            "topic": series.topic,
            "status": series.status,
            "spent_usd": spent_usd_for_series(series.id),
            "cap_usd": series_cap(),
        }
        for series in series_list
    ]
