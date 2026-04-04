# SPDX-License-Identifier: MIT
# Copyright (c) 2026 FairyClaw contributors, PKU DS Lab
"""FastAPI dependency injection module."""

from fastapi import Depends, Header, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from fairyclaw.config.settings import settings
from fairyclaw.infrastructure.database.session import get_db_session
from fairyclaw.infrastructure.database.repository import EventRepository
from fairyclaw.core.agent.session.memory import PersistentMemory

AUTH_SCHEME = "bearer"
MISSING_AUTH_DETAIL = "Missing Authorization header"
INVALID_TOKEN_DETAIL = "Invalid token"


def _parse_bearer(auth_value: str) -> str | None:
    """Parse Authorization header and extract bearer token.

    Args:
        auth_value (str): Raw Authorization header value.

    Returns:
        str | None: Parsed token or None for invalid format.
    """
    parts = auth_value.split(" ", 1)
    if len(parts) != 2:
        return None
    if parts[0].lower() != AUTH_SCHEME:
        return None
    return parts[1].strip()


async def require_auth(authorization: str | None = Header(default=None)) -> None:
    """Validate API token from Authorization header.

    Args:
        authorization (str | None): Raw Authorization header value.

    Returns:
        None

    Raises:
        HTTPException: Raised with 401 when token is missing or invalid.
    """
    if not authorization:
        raise HTTPException(status_code=401, detail=MISSING_AUTH_DETAIL)
    token = _parse_bearer(authorization)
    if token != settings.api_token:
        raise HTTPException(status_code=401, detail=INVALID_TOKEN_DETAIL)


async def db_session_dep(session: AsyncSession = Depends(get_db_session)) -> AsyncSession:
    """Provide request-scoped async database session.

    Args:
        session (AsyncSession): Injected database session.

    Returns:
        AsyncSession: Same injected session object.
    """
    return session

get_db = db_session_dep


async def get_memory(db: AsyncSession = Depends(get_db)) -> PersistentMemory:
    """Provide memory service bound to current DB session.

    Args:
        db (AsyncSession): Request-scoped database session.

    Returns:
        PersistentMemory: Memory service instance.
    """
    repo = EventRepository(db)
    return PersistentMemory(repo)

