from datetime import datetime, timezone
import unittest
from unittest.mock import AsyncMock, patch
from urllib.parse import parse_qs, urlsplit

from cryptography.hazmat.primitives.asymmetric import rsa
from jose import jwt, jwk
from sqlalchemy import select
from fastapi import HTTPException

from account_security_helpers import AccountSecurityCase
from core.config import settings
from core.security import ALGORITHM
from models.account_security import OidcIdentity
from models.users import User
from routers import oidc


class ProviderClient:
    def __init__(
        self,
        userinfo=None,
        tokens=None,
        keys=None,
        issuer="https://accounts.google.com",
    ):
        self.userinfo = userinfo or {
            "sub": "subject",
            "email": "owner@example.com",
            "email_verified": True,
        }
        self.tokens = tokens or {"access_token": "provider-token"}
        self.keys = keys or {}
        self.issuer = issuer

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def post(self, *args, **kwargs):
        return self.response(self.tokens)

    def response(self, data):
        from types import SimpleNamespace

        return SimpleNamespace(is_success=True, json=lambda: data)

    async def get(self, url, **kwargs):
        if "well-known" in url:
            return self.response(
                {"issuer": self.issuer, "jwks_uri": "https://keys.example.com/jwks"}
            )
        if "keys.example.com" in url:
            return self.response(self.keys)
        return self.response(self.userinfo)


class OidcAccountTests(AccountSecurityCase):
    async def exchange(self, info=None, headers=None, reauth=False, auth_time=None):
        response = await self.client.get(
            f"/auth/oidc/authorize?reauth={str(reauth).lower()}", headers=headers or {}
        )
        if response.status_code != 200:
            return response
        with (
            patch.object(oidc.httpx, "AsyncClient", return_value=ProviderClient(info)),
            patch.object(oidc, "_verify_id_token", AsyncMock(return_value=auth_time)),
        ):
            return await self.client.post(
                "/auth/oidc/exchange",
                headers=headers or {},
                json={"code": "code", "state": response.json()["state"]},
            )

    async def test_verified_invitation_links_subject_and_preserves_account(self):
        user = await self.user(password=None)
        result = await self.exchange(
            {
                "sub": "google-subject",
                "email": "OWNER@Example.COM",
                "email_verified": True,
            }
        )
        self.assertEqual(result.status_code, 200, result.text)
        link = (await self.db.execute(select(OidcIdentity))).scalar_one()
        self.assertEqual((link.user_id, link.subject), (user.id, "google-subject"))
        self.assertEqual(
            jwt.decode(
                result.json()["access_token"],
                settings.secret_key,
                algorithms=[ALGORITHM],
            )["sub"],
            str(user.id),
        )

    async def test_login_after_account_or_provider_email_changes_uses_stable_subject(
        self,
    ):
        user = await self.user(password=None)
        self.db.add(
            OidcIdentity(
                user_id=user.id,
                provider="https://accounts.google.com",
                subject="stable",
            )
        )
        user.email = "changed@example.com"
        await self.db.commit()
        result = await self.exchange(
            {
                "sub": "stable",
                "email": "provider-changed@example.com",
                "email_verified": True,
            }
        )
        self.assertEqual(result.status_code, 200, result.text)
        claims = jwt.decode(
            result.json()["access_token"], settings.secret_key, algorithms=[ALGORITHM]
        )
        self.assertEqual(claims["sub"], str(user.id))
        self.assertEqual((await self.db.execute(select(User))).scalars().all(), [user])

    async def test_another_subject_with_same_email_cannot_take_over_linked_account(
        self,
    ):
        user = await self.user(password=None)
        self.db.add(
            OidcIdentity(
                user_id=user.id,
                provider="https://accounts.google.com",
                subject="stable",
            )
        )
        await self.db.commit()
        result = await self.exchange(
            {"sub": "another", "email": user.email, "email_verified": True}
        )
        self.assertEqual(result.status_code, 403, result.text)

    async def test_edited_legacy_email_does_not_rebind_google_identity(self):
        user = await self.user(password=None)
        user.email = "new@example.com"
        await self.db.commit()
        result = await self.exchange(
            {"sub": "attacker", "email": "new@example.com", "email_verified": True}
        )
        self.assertEqual(result.status_code, 403, result.text)
        result = await self.exchange()
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(
            (await self.db.execute(select(OidcIdentity))).scalar_one().user_id, user.id
        )

    async def test_unverified_email_unknown_identity_and_missing_subject_fail_closed(
        self,
    ):
        await self.user()
        for info, status in (
            (
                {
                    "sub": "subject",
                    "email": "owner@example.com",
                    "email_verified": False,
                },
                403,
            ),
            (
                {
                    "sub": "unknown",
                    "email": "unknown@example.com",
                    "email_verified": True,
                },
                403,
            ),
            ({"email": "owner@example.com", "email_verified": True}, 403),
        ):
            response = await self.exchange(info)
            self.assertEqual(response.status_code, status, response.text)
        self.assertEqual(
            (await self.db.execute(select(OidcIdentity))).scalars().all(), []
        )

    async def test_first_and_subsequent_auto_created_users_get_correct_admin_fields(
        self,
    ):
        with patch.object(settings, "oidc_auto_create_users", True):
            for index in range(2):
                response = await self.exchange(
                    {
                        "sub": str(index),
                        "email": f"user{index}@example.com",
                        "email_verified": True,
                    }
                )
                self.assertEqual(response.status_code, 200, response.text)
        users = (await self.db.execute(select(User).order_by(User.id))).scalars().all()
        self.assertTrue(users[0].is_admin)
        self.assertEqual(users[0].role.value, "admin")
        self.assertFalse(users[1].is_admin)
        self.assertEqual(users[1].role.value, "user")

    async def test_repeated_callback_reuses_identity(self):
        user = await self.user()
        for _ in range(2):
            self.assertEqual((await self.exchange()).status_code, 200)
        self.assertEqual(
            len((await self.db.execute(select(OidcIdentity))).scalars().all()), 1
        )
        self.assertEqual((await self.db.execute(select(User))).scalars().all(), [user])

    async def test_reauth_matches_current_account_and_provides_short_lived_proof(self):
        user = await self.user(password=None)
        now = int(datetime.now(timezone.utc).timestamp())
        response = await self.exchange(
            headers=self.headers(user), reauth=True, auth_time=now
        )
        self.assertEqual(response.status_code, 200, response.text)
        token = response.json()["access_token"]
        claims = jwt.decode(token, settings.secret_key, algorithms=[ALGORITHM])
        self.assertEqual(claims["oidc_auth_time"], now)
        # Reauthentication also binds a legacy account's original verified identity.
        self.assertEqual(
            (await self.db.execute(select(OidcIdentity))).scalar_one().user_id, user.id
        )
        response = await self.client.post(
            "/auth/change-password",
            headers={"Authorization": f"Bearer {token}"},
            json={"new_password": "new"},
        )
        self.assertEqual(response.status_code, 200, response.text)

    async def test_wrong_google_account_cannot_reauthenticate(self):
        user = await self.user(password=None)
        response = await self.exchange(
            {"sub": "wrong", "email": "other@example.com", "email_verified": True},
            headers=self.headers(user),
            reauth=True,
            auth_time=int(datetime.now(timezone.utc).timestamp()),
        )
        self.assertEqual(response.status_code, 403, response.text)
        self.assertEqual(
            (await self.db.execute(select(OidcIdentity))).scalars().all(), []
        )

    async def test_authorization_requests_nonce_and_google_account_chooser(self):
        user = await self.user()
        result = await self.client.get(
            "/auth/oidc/authorize?reauth=true", headers=self.headers(user)
        )
        self.assertEqual(result.status_code, 200, result.text)
        data = result.json()
        params = parse_qs(urlsplit(data["auth_url"]).query)
        self.assertEqual(params["prompt"], ["select_account"])
        state = jwt.decode(data["state"], settings.secret_key, algorithms=[ALGORITHM])
        self.assertEqual(state["reauth_user"], user.id)
        self.assertEqual(params["nonce"], [state["nonce"]])
        self.assertEqual(
            (await self.client.get("/auth/oidc/authorize?reauth=true")).status_code, 401
        )

    async def test_expired_or_tampered_state_and_revoked_reauth_session_fail(self):
        user = await self.user()
        headers = self.headers(user)
        state = (
            await self.client.get("/auth/oidc/authorize?reauth=true", headers=headers)
        ).json()["state"]
        for candidate in (
            state + "tampered",
            jwt.encode(
                {"sub": "oidc", "type": "oidc_state", "exp": 1},
                settings.secret_key,
                algorithm=ALGORITHM,
            ),
        ):
            response = await self.client.post(
                "/auth/oidc/exchange",
                headers=headers,
                json={"code": "code", "state": candidate},
            )
            self.assertEqual(response.status_code, 400, response.text)
        user.session_version += 1
        await self.db.commit()
        response = await self.client.post(
            "/auth/oidc/exchange",
            headers=headers,
            json={"code": "code", "state": state},
        )
        self.assertEqual(response.status_code, 401, response.text)

    async def test_incomplete_configuration_and_generic_reauth_without_issuer_fail_closed(
        self,
    ):
        with patch.object(settings, "oidc_auth_url", None):
            result = await self.client.get("/auth/oidc/authorize")
            self.assertEqual(result.status_code, 503, result.text)
        user = await self.user()
        with patch.object(
            settings, "oidc_auth_url", "https://provider.example/authorize"
        ):
            result = await self.client.get(
                "/auth/oidc/authorize?reauth=true", headers=self.headers(user)
            )
            self.assertEqual(result.status_code, 503, result.text)


class OidcSignatureTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cls.keys = {
            "keys": [jwk.construct(cls.private_key.public_key(), "RS256").to_dict()]
        }

    async def verify(self, **overrides):
        now = int(datetime.now(timezone.utc).timestamp())
        claims = {
            "iss": "https://accounts.google.com",
            "aud": "client",
            "sub": "subject",
            "exp": now + 600,
            "iat": now,
            "nonce": "nonce",
        }
        claims.update(overrides)
        signed = jwt.encode(claims, self.private_key, algorithm="RS256")
        client = ProviderClient(tokens={"id_token": signed}, keys=self.keys)
        state = {"nonce": "nonce", "reauth_user": 1, "started_at": now}
        with patch.multiple(
            settings,
            oidc_issuer_url="https://accounts.google.com",
            oidc_client_id="client",
        ):
            return await oidc._verify_id_token(client, client.tokens, state, "subject")

    async def test_valid_signed_google_token_proves_recent_interactive_authorization(
        self,
    ):
        self.assertIsInstance(await self.verify(), int)

    async def test_wrong_issuer_audience_nonce_subject_and_stale_token_rejected(self):
        now = int(datetime.now(timezone.utc).timestamp())
        for changes in (
            {"iss": "https://attacker.example"},
            {"aud": "another-client"},
            {"nonce": "wrong"},
            {"sub": "wrong"},
            {"azp": "wrong"},
            {"exp": now - 60},
            {"iat": now - 600},
            {"iat": now + 60},
        ):
            with (
                self.subTest(changes=changes),
                self.assertRaises(HTTPException) as raised,
            ):
                await self.verify(**changes)
            self.assertEqual(raised.exception.status_code, 403)

    async def test_untrusted_signature_rejected(self):
        other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        now = int(datetime.now(timezone.utc).timestamp())
        signed = jwt.encode(
            {
                "iss": "https://accounts.google.com",
                "aud": "client",
                "sub": "subject",
                "exp": now + 600,
                "iat": now,
                "nonce": "nonce",
            },
            other,
            algorithm="RS256",
        )
        client = ProviderClient(tokens={"id_token": signed}, keys=self.keys)
        with (
            patch.multiple(
                settings,
                oidc_issuer_url="https://accounts.google.com",
                oidc_client_id="client",
            ),
            self.assertRaises(HTTPException),
        ):
            await oidc._verify_id_token(
                client, client.tokens, {"nonce": "nonce"}, "subject"
            )

    async def test_generic_provider_requires_actual_recent_authentication_time(self):
        now = int(datetime.now(timezone.utc).timestamp())
        for auth_time in (None, now - 600, now):
            claims = {
                "iss": "https://idp.example",
                "aud": "client",
                "sub": "subject",
                "exp": now + 600,
                "iat": now,
                "nonce": "nonce",
            }
            if auth_time is not None:
                claims["auth_time"] = auth_time
            signed = jwt.encode(claims, self.private_key, algorithm="RS256")
            client = ProviderClient(
                tokens={"id_token": signed},
                keys=self.keys,
                issuer="https://idp.example",
            )
            with patch.multiple(
                settings, oidc_issuer_url="https://idp.example", oidc_client_id="client"
            ):
                if auth_time == now:
                    self.assertEqual(
                        await oidc._verify_id_token(
                            client,
                            client.tokens,
                            {"nonce": "nonce", "reauth_user": 1, "started_at": now},
                            "subject",
                        ),
                        now,
                    )
                else:
                    with self.assertRaises(HTTPException):
                        await oidc._verify_id_token(
                            client,
                            client.tokens,
                            {"nonce": "nonce", "reauth_user": 1, "started_at": now},
                            "subject",
                        )
