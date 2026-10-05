from datetime import datetime, timezone

from fastapi import HTTPException
from jose import JWTError, jwt
from sqlalchemy import delete

from core.config import settings
from core.security import ALGORITHM, session_is_current, verify_password
from models.account_security import EmailChangeToken
from models.email_activation import EmailActivation
from models.password_reset import PasswordResetToken


async def invalidate_account_links(db, user_id: int) -> None:
    for model in (PasswordResetToken, EmailActivation, EmailChangeToken):
        await db.execute(delete(model).where(model.user_id == user_id))


def revoke_sessions(user) -> None:
    user.session_version = (user.session_version or 0) + 1


def require_account_proof(
    user, current_password: str | None, token: str | None
) -> None:
    try:
        claims = jwt.decode(token or "", settings.secret_key, algorithms=[ALGORITHM])
        if (
            claims.get("type") is not None
            or claims.get("sub") != str(user.id)
            or not session_is_current(user, claims)
        ):
            raise JWTError("Session revoked")
        if (
            user.password_hash
            and current_password
            and verify_password(current_password, user.password_hash)
        ):
            return
        age = datetime.now(timezone.utc).timestamp() - claims["oidc_auth_time"]
        if 0 <= age <= 300:
            return
    except (JWTError, KeyError, TypeError, ValueError):
        pass
    raise HTTPException(
        status_code=403,
        detail="Enter your current password or verify your SSO sign-in again",
    )
