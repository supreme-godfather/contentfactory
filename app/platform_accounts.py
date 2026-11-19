"""Named social account slots for publishing destinations."""

from sqlalchemy import select

from .db import PlatformAccount, SessionLocal


LIMITS = {"youtube": 3, "instagram": 2}


def list_accounts(platform: str) -> list[dict[str, object]]:
    if platform not in LIMITS:
        raise ValueError("Unsupported platform")
    with SessionLocal() as session:
        rows = session.scalars(
            select(PlatformAccount)
            .where(PlatformAccount.platform == platform)
            .order_by(PlatformAccount.account_name.asc())
        )
        return [
            {
                "id": row.id,
                "name": row.account_name,
                "enabled": bool(row.enabled),
                "authorized": bool(row.authorized),
            }
            for row in rows
        ]


def add_account(platform: str, name: str) -> dict[str, object]:
    if platform not in LIMITS:
        raise ValueError("Unsupported platform")
    clean_name = " ".join(name.split()).strip()
    if not clean_name or len(clean_name) > 160:
        raise ValueError("Account name must contain 1-160 characters")
    with SessionLocal() as session:
        existing = session.scalar(
            select(PlatformAccount).where(
                PlatformAccount.platform == platform,
                PlatformAccount.account_name == clean_name,
            )
        )
        if existing:
            return {"id": existing.id, "name": existing.account_name}
        current = len(session.scalars(
            select(PlatformAccount).where(PlatformAccount.platform == platform)
        ).all())
        if current >= LIMITS[platform]:
            raise ValueError(f"Maximum {LIMITS[platform]} {platform.title()} accounts reached")
        account = PlatformAccount(
            id=f"{platform}:{clean_name.casefold()}",
            platform=platform,
            account_name=clean_name,
            enabled=0,
            authorized=0,
        )
        session.add(account)
        session.commit()
        return {"id": account.id, "name": account.account_name}


def set_account_enabled(platform: str, account_id: str, enabled: bool) -> None:
    with SessionLocal() as session:
        account = session.get(PlatformAccount, account_id)
        if account is None or account.platform != platform:
            raise ValueError("Account not found")
        account.enabled = int(enabled)
        session.commit()
