from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

from jose import jwt
from sqlalchemy import select

from account_security_helpers import AccountSecurityCase
from core.config import settings
from core.security import (
    ALGORITHM,
    create_access_token,
    hash_opaque_token,
    verify_password,
)
from models.account_security import EmailChangeToken, OidcIdentity
from models.email_activation import EmailActivation
from models.password_reset import PasswordResetToken
from routers import auth


class AccountSecurityTests(AccountSecurityCase):
    async def test_password_change_checks_old_password_and_revokes_all_browser_tokens(
        self,
    ):
        user = await self.user()
        headers = self.headers(user)
        legacy = {"Authorization": f"Bearer {create_access_token(user.id)}"}
        for password, status in (("wrong", 403), ("old-password", 200)):
            result = await self.client.post(
                "/auth/change-password",
                headers=headers,
                json={"current_password": password, "new_password": "new-password"},
            )
            self.assertEqual(result.status_code, status, result.text)
        self.assertTrue(verify_password("new-password", user.password_hash))
        for old in (headers, legacy):
            self.assertEqual(
                (await self.client.get("/auth/me", headers=old)).status_code, 401
            )
        self.assertEqual(
            (await self.client.get("/auth/me", headers=self.headers(user))).status_code,
            200,
        )
        self.assertEqual(user.api_key, "owner@example.com")

    async def test_sso_only_password_requires_recent_account_verification(self):
        user = await self.user(password=None)
        now = int(datetime.now(timezone.utc).timestamp())
        for age, expected in ((None, 403), (301, 403), (-60, 403), (0, 200)):
            headers = self.headers(
                user, **({"oidc_auth_time": now - age} if age is not None else {})
            )
            response = await self.client.post(
                "/auth/change-password", headers=headers, json={"new_password": "new"}
            )
            self.assertEqual(response.status_code, expected, response.text)

    async def test_email_stays_active_until_confirmation_and_token_is_single_use(self):
        user = await self.user()
        headers = self.headers(user)
        self.db.add(PasswordResetToken(user_id=user.id, token="reset"))
        self.db.add(
            EmailActivation(user_id=user.id, token="activate", email=user.email)
        )
        self.db.add(OidcIdentity(user_id=user.id, provider="google", subject="stable"))
        await self.db.commit()
        with patch.object(auth, "send_email_change_email", AsyncMock()) as send:
            response = await self.client.post(
                "/auth/change-email",
                headers=headers,
                json={"email": "New@Example.com", "current_password": "old-password"},
            )
        self.assertEqual(response.status_code, 200, response.text)
        token = send.call_args.args[1]
        self.assertEqual(user.email, "owner@example.com")
        record = (await self.db.execute(select(EmailChangeToken))).scalar_one()
        self.assertEqual(record.token_hash, hash_opaque_token(token))
        response = await self.client.post(
            "/auth/confirm-email-change", json={"token": token}
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(user.email, "new@example.com")
        self.assertEqual(user.oidc_login_email, "owner@example.com")
        self.assertTrue(user.email_confirmed)
        self.assertEqual(
            (await self.client.get("/auth/me", headers=headers)).status_code, 401
        )
        self.assertEqual(
            (
                await self.client.post(
                    "/auth/confirm-email-change", json={"token": token}
                )
            ).status_code,
            400,
        )
        for model in (PasswordResetToken, EmailActivation, EmailChangeToken):
            self.assertEqual((await self.db.execute(select(model))).scalars().all(), [])
        self.assertEqual(
            (await self.db.execute(select(OidcIdentity))).scalar_one().subject, "stable"
        )

    async def test_email_validation_duplicate_and_proof_fail_before_sending(self):
        user = await self.user()
        await self.user("taken@example.com")
        for email, password, status in (
            ("invalid", "old-password", 422),
            ("taken@example.com", "old-password", 409),
            (user.email, "old-password", 400),
            ("new@example.com", "wrong", 403),
        ):
            with patch.object(auth, "send_email_change_email", AsyncMock()) as send:
                response = await self.client.post(
                    "/auth/change-email",
                    headers=self.headers(user),
                    json={"email": email, "current_password": password},
                )
            self.assertEqual(response.status_code, status, response.text)
            send.assert_not_awaited()

    async def test_mail_failure_does_not_leave_pending_change(self):
        user = await self.user()
        with patch.object(
            auth,
            "send_email_change_email",
            AsyncMock(side_effect=RuntimeError("mail failure")),
        ):
            response = await self.client.post(
                "/auth/change-email",
                headers=self.headers(user),
                json={"email": "new@example.com", "current_password": "old-password"},
            )
        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            (await self.db.execute(select(EmailChangeToken))).scalars().all(), []
        )

    async def test_no_smtp_requires_admin(self):
        user = await self.user()
        with patch.object(settings, "smtp_address", None):
            response = await self.client.post(
                "/auth/change-email",
                headers=self.headers(user),
                json={"email": "new@example.com", "current_password": "old-password"},
            )
        self.assertEqual(response.status_code, 503)

    async def pending(self, user, email="new@example.com", **kwargs):
        token = EmailChangeToken(
            user_id=user.id,
            email=email,
            previous_email=user.email,
            session_version=user.session_version,
            token_hash=hash_opaque_token("confirm"),
            created_at=datetime.now(timezone.utc).replace(tzinfo=None),
            **kwargs,
        )
        self.db.add(token)
        await self.db.commit()
        return token

    async def test_expired_changed_version_and_changed_source_email_are_rejected(self):
        for cause in ("expired", "version", "source"):
            user = await self.user(f"{cause}@example.com")
            token = await self.pending(user)
            if cause == "expired":
                token.created_at -= timedelta(hours=2)
            elif cause == "version":
                user.session_version += 1
            else:
                user.email = "changed@example.com"
            await self.db.commit()
            result = await self.client.post(
                "/auth/confirm-email-change", json={"token": "confirm"}
            )
            self.assertEqual(result.status_code, 400, result.text)
            await self.db.delete(token)
            await self.db.commit()

    async def test_destination_claimed_before_confirmation_is_rejected(self):
        user = await self.user()
        await self.pending(user, "taken@example.com")
        await self.user("taken@example.com")
        response = await self.client.post(
            "/auth/confirm-email-change", json={"token": "confirm"}
        )
        self.assertEqual(response.status_code, 409)
        await self.db.refresh(user)
        self.assertEqual(user.email, "owner@example.com")
        self.assertEqual(user.session_version, 0)

    async def test_replacement_request_invalidates_previous_confirmation(self):
        user = await self.user()
        tokens = []
        for email in ("first@example.com", "second@example.com"):
            with patch.object(auth, "send_email_change_email", AsyncMock()) as send:
                result = await self.client.post(
                    "/auth/change-email",
                    headers=self.headers(user),
                    json={"email": email, "current_password": "old-password"},
                )
            self.assertEqual(result.status_code, 200, result.text)
            tokens.append(send.call_args.args[1])
        self.assertEqual(
            (
                await self.client.post(
                    "/auth/confirm-email-change", json={"token": tokens[0]}
                )
            ).status_code,
            400,
        )
        self.assertEqual(
            (
                await self.client.post(
                    "/auth/confirm-email-change", json={"token": tokens[1]}
                )
            ).status_code,
            200,
        )

    async def test_admin_email_change_confirms_preserves_identity_and_cancels_pending_links(
        self,
    ):
        admin = await self.user("admin@example.com", admin=True)
        user = await self.user(password=None)
        user.totp_enabled = True
        user.totp_secret = "keep"
        self.db.add(OidcIdentity(user_id=user.id, provider="google", subject="stable"))
        self.db.add(PasswordResetToken(user_id=user.id, token="reset"))
        await self.pending(user)
        old = self.headers(user)
        result = await self.client.patch(
            f"/admin/users/{user.id}/email",
            headers=self.headers(admin),
            json={"email": "New@Example.com"},
        )
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(user.email, "new@example.com")
        self.assertEqual(user.oidc_login_email, "owner@example.com")
        self.assertTrue(user.email_confirmed)
        self.assertEqual(user.totp_secret, "keep")
        self.assertEqual(
            (await self.client.get("/auth/me", headers=old)).status_code, 401
        )
        self.assertEqual(
            (await self.db.execute(select(OidcIdentity))).scalar_one().subject, "stable"
        )
        self.assertEqual(
            (await self.db.execute(select(PasswordResetToken))).scalars().all(), []
        )
        self.assertEqual(
            (await self.db.execute(select(EmailChangeToken))).scalars().all(), []
        )

    async def test_admin_email_rejects_unauthorized_duplicates_and_missing_accounts(
        self,
    ):
        admin = await self.user("admin@example.com", admin=True)
        user = await self.user()
        for headers, uid, email, status in (
            ({}, user.id, "new@example.com", 401),
            (self.headers(user), user.id, "new@example.com", 403),
            (self.headers(admin), user.id, admin.email, 409),
            (self.headers(admin), 999, "new@example.com", 404),
        ):
            response = await self.client.patch(
                f"/admin/users/{uid}/email", headers=headers, json={"email": email}
            )
            self.assertEqual(response.status_code, status, response.text)

    async def test_password_recovery_revokes_sessions_and_pending_email_changes(self):
        user = await self.user()
        old = self.headers(user)
        self.db.add(PasswordResetToken(user_id=user.id, token="reset"))
        self.db.add(PasswordResetToken(user_id=user.id, token="another-reset"))
        await self.pending(user)
        result = await self.client.post(
            "/auth/reset-password/reset", json={"new_password": "new-password"}
        )
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(
            (await self.client.get("/auth/me", headers=old)).status_code, 401
        )
        self.assertEqual(
            (await self.db.execute(select(PasswordResetToken))).scalars().all(), []
        )
        self.assertEqual(
            (await self.db.execute(select(EmailChangeToken))).scalars().all(), []
        )

    async def test_admin_password_reset_invalidates_pending_two_factor_login(self):
        admin = await self.user("admin@example.com", admin=True)
        user = await self.user()
        user.totp_enabled = True
        user.totp_secret = "JBSWY3DPEHPK3PXP"
        await self.db.commit()
        pending = create_access_token(
            user.id, extra_claims={"type": "2fa_pending", "session_version": 0}
        )
        response = await self.client.post(
            f"/admin/users/{user.id}/reset-password",
            headers=self.headers(admin),
            json={"password": "new"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        response = await self.client.post(
            "/auth/2fa/verify-login", json={"temp_token": pending, "code": "123456"}
        )
        self.assertEqual(response.status_code, 401)

    async def test_state_token_cannot_be_used_as_browser_session(self):
        user = await self.user()
        token = create_access_token(user.id, extra_claims={"type": "oidc_state"})
        response = await self.client.get(
            "/auth/me", headers={"Authorization": f"Bearer {token}"}
        )
        self.assertEqual(response.status_code, 401)

    async def test_session_token_contains_version_but_no_password(self):
        user = await self.user()
        token = self.headers(user)["Authorization"].split()[1]
        claims = jwt.decode(token, settings.secret_key, algorithms=[ALGORITHM])
        self.assertEqual(claims["session_version"], 0)
        self.assertNotIn("password_hash", claims)

    async def test_revoked_session_rejected_by_tracking_and_optional_auth(self):
        from dependencies import get_optional_user, get_tracking_write_user
        from fastapi import HTTPException

        user = await self.user()
        token = self.headers(user)["Authorization"].split()[1]
        self.assertIs(await get_optional_user(self.db, token), user)
        self.assertIs(await get_tracking_write_user(self.db, token), user)
        user.session_version += 1
        await self.db.commit()
        self.assertIsNone(await get_optional_user(self.db, token))
        with self.assertRaises(HTTPException) as raised:
            await get_tracking_write_user(self.db, token)
        self.assertEqual(raised.exception.status_code, 401)

    async def test_revoked_session_cannot_load_authenticated_images(self):
        from routers.media import verify_image_token
        from starlette.requests import Request
        from fastapi import HTTPException

        user = await self.user()
        token = self.headers(user)["Authorization"].split()[1]
        request = Request(
            {
                "type": "http",
                "headers": [(b"authorization", f"Bearer {token}".encode())],
            }
        )
        self.assertEqual(await verify_image_token(request, self.db), user.id)
        user.session_version += 1
        await self.db.commit()
        with self.assertRaises(HTTPException) as raised:
            await verify_image_token(request, self.db)
        self.assertEqual(raised.exception.status_code, 401)
