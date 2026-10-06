from core.account_bootstrap import lock_account_bootstrap
from core.account_defaults import initialize_account_defaults
import secrets
import base64
import hashlib
import hmac
from urllib.parse import urlencode, urlsplit
from datetime import datetime, timedelta, timezone
from typing import Annotated
from jose import jwt, JWTError

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from core.config import settings as app_settings
from core.security import (
    create_access_token,
    create_session_token,
    ALGORITHM,
    session_is_current,
)
from dependencies import get_optional_user
from models.account_security import OidcIdentity
from db import get_db
from models.base import UserRole
from models.users import User

router = APIRouter()


class OidcExchangeRequest(BaseModel):
    code: str
    state: str


def _require_oidc_configuration(*fields: str) -> None:
    if not app_settings.oidc_enabled:
        raise HTTPException(status_code=400, detail="OIDC not enabled")
    missing = [
        field.upper()
        for field in fields
        if not str(getattr(app_settings, field, "") or "").strip()
    ]
    if missing:
        raise HTTPException(
            status_code=503,
            detail=f"OIDC configuration incomplete: {', '.join(missing)}",
        )


@router.get("/config")
async def oidc_config():
    return {
        "enabled": app_settings.oidc_enabled,
        "provider_name": app_settings.oidc_provider_name,
        "disable_password_login": app_settings.oidc_disable_password_login,
    }


def _issuer() -> str | None:
    if app_settings.oidc_issuer_url:
        return app_settings.oidc_issuer_url.strip()
    if urlsplit(app_settings.oidc_auth_url or "").hostname == "accounts.google.com":
        return "https://accounts.google.com"
    return None


def _provider_key() -> str:
    # The configured token endpoint is a stable provider namespace for older
    # generic OIDC configurations that do not specify a discovery issuer.
    return _issuer() or app_settings.oidc_token_url


def _code_verifier(nonce: str) -> str:
    # Derive a private PKCE verifier without putting the secret into public state.
    digest = hmac.new(
        app_settings.secret_key.encode(), f"oidc-pkce:{nonce}".encode(), hashlib.sha256
    ).digest()
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")


def _decode_state(state: str) -> dict:
    try:
        claims = jwt.decode(state, app_settings.secret_key, algorithms=[ALGORITHM])
        if (
            claims.get("type") != "oidc_state"
            or claims.get("provider") != _provider_key()
        ):
            raise JWTError("Invalid authorization state")
        return claims
    except (JWTError, TypeError, ValueError):
        raise HTTPException(
            status_code=400,
            detail="Invalid or expired SSO authorization. Please try again",
        )


async def _verify_id_token(
    client, tokens: dict, state: dict, subject: str
) -> int | None:
    issuer = _issuer()
    if not issuer:
        if state.get("reauth_user") is not None:
            raise HTTPException(
                status_code=503,
                detail="Configure OIDC_ISSUER_URL to enable SSO account verification",
            )
        return None
    try:
        discovery = await client.get(
            issuer.rstrip("/") + "/.well-known/openid-configuration"
        )
        metadata = discovery.json()
        if not discovery.is_success or metadata.get("issuer") != issuer:
            raise ValueError("Invalid discovery issuer")
        jwks_url = metadata["jwks_uri"]
        if urlsplit(jwks_url).scheme != "https":
            raise ValueError("Insecure signing key endpoint")
        keys = await client.get(jwks_url)
        if not keys.is_success:
            raise ValueError("Signing keys unavailable")
        claims = jwt.decode(
            tokens["id_token"],
            keys.json(),
            algorithms=["RS256", "ES256"],
            audience=app_settings.oidc_client_id,
            issuer=[issuer, "accounts.google.com"]
            if issuer == "https://accounts.google.com"
            else issuer,
            access_token=tokens.get("access_token"),
            options={
                "require_exp": True,
                "require_iss": True,
                "require_aud": True,
                "require_sub": True,
                "require_iat": True,
            },
        )
        if claims.get("nonce") != state["nonce"] or claims["sub"] != subject:
            raise ValueError("Identity token does not match this login")
        if (
            claims.get("azp", app_settings.oidc_client_id)
            != app_settings.oidc_client_id
        ):
            raise ValueError("Invalid authorized party")
        if state.get("reauth_user") is not None:
            # Google's standard code flow does not expose auth_time. Require a
            # newly issued, nonce-bound ID token after explicit account selection.
            # Other issuers must attest actual authentication time (max_age=0).
            auth_time = (
                claims.get("iat")
                if issuer == "https://accounts.google.com"
                else claims.get("auth_time")
            )
            now = datetime.now(timezone.utc).timestamp()
            if (
                type(auth_time) is not int
                or auth_time < state["started_at"] - 30
                or not 0 <= now - auth_time <= 300
            ):
                raise ValueError("Fresh provider authentication required")
            return auth_time
        return None
    except (JWTError, KeyError, TypeError, ValueError):
        raise HTTPException(
            status_code=403,
            detail="Could not verify the SSO identity. Please sign in again",
        )


@router.get("/authorize")
async def oidc_authorize(
    current_user: Annotated[User | None, Depends(get_optional_user)],
    reauth: bool = False,
) -> dict[str, str]:
    _require_oidc_configuration(
        "oidc_client_id", "oidc_auth_url", "oidc_redirect_url", "oidc_token_url"
    )
    if reauth and current_user is None:
        raise HTTPException(
            status_code=401, detail="Sign in before verifying your account"
        )
    if reauth and not _issuer():
        raise HTTPException(
            status_code=503,
            detail="Configure OIDC_ISSUER_URL to enable SSO account verification",
        )
    nonce = secrets.token_urlsafe(32)
    state_claims = {
        "type": "oidc_state",
        "nonce": nonce,
        "provider": _provider_key(),
        "started_at": int(datetime.now(timezone.utc).timestamp()),
    }
    if reauth:
        state_claims.update(
            reauth_user=current_user.id,
            session_version=current_user.session_version or 0,
        )
    state = create_access_token(
        "oidc", expires_delta=timedelta(minutes=10), extra_claims=state_claims
    )
    params = {
        "client_id": app_settings.oidc_client_id,
        "redirect_uri": app_settings.oidc_redirect_url,
        "response_type": "code",
        "scope": app_settings.oidc_scopes,
        "state": state,
        "nonce": nonce,
    }
    if _issuer():
        params.update(
            code_challenge=base64.urlsafe_b64encode(
                hashlib.sha256(_code_verifier(nonce).encode()).digest()
            )
            .decode()
            .rstrip("="),
            code_challenge_method="S256",
        )
    if reauth:
        if _issuer() == "https://accounts.google.com":
            params.update(prompt="select_account")
        else:
            params.update(prompt="login", max_age="0")
    auth_url = f"{app_settings.oidc_auth_url}?{urlencode(params)}"
    return {"auth_url": auth_url, "state": state}


@router.post("/exchange")
async def oidc_exchange(
    payload: OidcExchangeRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User | None, Depends(get_optional_user)],
) -> dict[str, str]:
    """
    Exchanges an authorization code (already validated by the frontend) for a JWT.
    Called server-to-server by the frontend's oidc-callback SSR page.
    """
    _require_oidc_configuration(
        "oidc_client_id",
        "oidc_client_secret",
        "oidc_token_url",
        "oidc_userinfo_url",
        "oidc_redirect_url",
        "oidc_identifier_field",
    )

    state = _decode_state(payload.state)
    if state.get("reauth_user") is not None and (
        current_user is None
        or current_user.id != state["reauth_user"]
        or not session_is_current(current_user, state)
    ):
        raise HTTPException(
            status_code=401, detail="This account verification session has ended"
        )

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            token_resp = await client.post(
                app_settings.oidc_token_url,
                data={
                    "grant_type": "authorization_code",
                    "code": payload.code,
                    "redirect_uri": app_settings.oidc_redirect_url,
                    "client_id": app_settings.oidc_client_id,
                    "client_secret": app_settings.oidc_client_secret,
                    **(
                        {"code_verifier": _code_verifier(state["nonce"])}
                        if _issuer()
                        else {}
                    ),
                },
                headers={"Accept": "application/json"},
            )
            if not token_resp.is_success:
                raise HTTPException(status_code=400, detail="Token exchange failed")

            tokens = token_resp.json()
            oidc_access_token = tokens.get("access_token")
            if not oidc_access_token:
                raise HTTPException(
                    status_code=400, detail="No access token in response"
                )

            userinfo_resp = await client.get(
                app_settings.oidc_userinfo_url,
                headers={"Authorization": f"Bearer {oidc_access_token}"},
            )
            if not userinfo_resp.is_success:
                raise HTTPException(status_code=400, detail="Failed to fetch user info")

            userinfo: dict = userinfo_resp.json()
            subject = userinfo.get("sub")
            if not isinstance(subject, str) or not subject or len(subject) > 255:
                raise HTTPException(
                    status_code=403,
                    detail="The provider did not return a stable subject identity",
                )
            auth_time = await _verify_id_token(client, tokens, state, subject)
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=502, detail="Provider connection failed")

    # Serialize first linking and first-account provisioning, including invitation
    # matching. Neither changing a local email nor changing a provider email rebinds a link.
    await lock_account_bootstrap(db)
    provider = _provider_key()
    user = (
        await db.execute(
            select(User)
            .join(OidcIdentity, OidcIdentity.user_id == User.id)
            .where(OidcIdentity.provider == provider, OidcIdentity.subject == subject)
            .with_for_update(of=User)
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if state.get("reauth_user") is not None:
        if user is None:
            # A session issued before identity migration can bind its own original
            # verified invitation during reauthentication, without an extra logout.
            user = (
                await db.execute(
                    select(User)
                    .where(User.id == current_user.id)
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
            ).scalar_one()
            linked = (
                await db.execute(
                    select(OidcIdentity.id).where(
                        OidcIdentity.user_id == user.id,
                        OidcIdentity.provider == provider,
                    )
                )
            ).first()
            original = user.oidc_login_email or user.email
            if (
                linked
                or userinfo.get("email_verified") is not True
                or str(userinfo.get("email", "")).strip().lower() != original
            ):
                raise HTTPException(
                    status_code=403,
                    detail="Sign in with the SSO identity linked to this account",
                )
            db.add(OidcIdentity(user_id=user.id, provider=provider, subject=subject))
        if user.id != current_user.id or not session_is_current(user, state):
            raise HTTPException(
                status_code=403,
                detail="Sign in with the SSO identity linked to this account",
            )
        await db.commit()
        return {"access_token": create_session_token(user, oidc_auth_time=auth_time)}
    if user is not None:
        await db.commit()
        return {"access_token": create_session_token(user)}

    identifier = userinfo.get(app_settings.oidc_identifier_field)
    if not isinstance(identifier, str) or not identifier.strip():
        raise HTTPException(
            status_code=400,
            detail=f"Field '{app_settings.oidc_identifier_field}' not found in user info",
        )
    if app_settings.oidc_require_verified_email:
        if app_settings.oidc_identifier_field != "email":
            raise HTTPException(
                status_code=503,
                detail="Verified-email OIDC login requires OIDC_IDENTIFIER_FIELD=email",
            )
        if userinfo.get("email_verified") is not True:
            raise HTTPException(
                status_code=403,
                detail="The identity provider has not verified this email address",
            )
    identifier = identifier.strip().lower()
    candidates = (
        (
            await db.execute(
                select(User)
                .where(
                    func.lower(
                        func.trim(func.coalesce(User.oidc_login_email, User.email))
                    )
                    == identifier
                )
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        )
        .scalars()
        .all()
    )
    if len(candidates) > 1:
        raise HTTPException(
            status_code=409,
            detail="This SSO email matches multiple accounts. Contact an administrator",
        )
    user = candidates[0] if candidates else None
    if user is not None:
        # Email matching is only a one-time bridge for verified legacy accounts/invitations.
        if (
            app_settings.oidc_identifier_field != "email"
            or userinfo.get("email_verified") is not True
        ):
            raise HTTPException(
                status_code=403,
                detail="A verified email is required to link an existing account",
            )
        linked = (
            await db.execute(
                select(OidcIdentity.id).where(
                    OidcIdentity.user_id == user.id, OidcIdentity.provider == provider
                )
            )
        ).first()
        if linked:
            raise HTTPException(
                status_code=403,
                detail="This account is already linked to a different SSO identity",
            )
    else:
        if not app_settings.oidc_auto_create_users:
            raise HTTPException(
                status_code=403, detail="No account found for this identity"
            )
        # A changed address must not claim a different account's current email.
        occupied = (
            await db.execute(
                select(User.id).where(func.lower(func.trim(User.email)) == identifier)
            )
        ).first()
        if occupied:
            raise HTTPException(
                status_code=409,
                detail="This email belongs to an existing account. Contact an administrator",
            )
        raw_username = str(
            userinfo.get("preferred_username")
            or userinfo.get("name")
            or identifier.split("@")[0]
        )
        base = raw_username[:90] or "user"
        username = base
        counter = 1
        while (
            await db.execute(select(User.id).where(User.username == username))
        ).first():
            username = f"{base}{counter}"
            counter += 1
        count = (await db.execute(select(func.count()).select_from(User))).scalar_one()
        user = User(
            email=identifier,
            oidc_login_email=identifier,
            username=username,
            password_hash=None,
            api_key=secrets.token_urlsafe(32),
            is_admin=count == 0,
            role=UserRole.admin if count == 0 else UserRole.user,
            session_version=0,
        )
        db.add(user)
        await db.flush()
        initialize_account_defaults(db, user)
    db.add(OidcIdentity(user_id=user.id, provider=provider, subject=subject))
    await db.commit()
    return {"access_token": create_session_token(user)}
