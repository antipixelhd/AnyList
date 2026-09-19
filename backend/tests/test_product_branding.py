import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from urllib.parse import parse_qs, unquote, urlparse

from core import email
from routers import auth


class ProductBrandingTests(unittest.IsolatedAsyncioTestCase):
    async def test_activation_email_uses_media_tracker_identity(self) -> None:
        send = AsyncMock()

        with patch.object(email, "send_email", send):
            await email.send_activation_email("friend@example.com", "activation-token")

        send.assert_awaited_once()
        _, subject, body = send.await_args.args
        self.assertEqual(subject, "Activate your Media Tracker account")
        self.assertIn("registering on Media Tracker", body)
        self.assertNotIn("Scrob", subject + body)

    async def test_password_reset_email_uses_media_tracker_identity(self) -> None:
        send = AsyncMock()

        with patch.object(email, "send_email", send):
            await email.send_password_reset_email("friend@example.com", "reset-token")

        send.assert_awaited_once()
        _, subject, body = send.await_args.args
        self.assertEqual(subject, "Reset your Media Tracker password")
        self.assertIn("reset your Media Tracker password", body)
        self.assertNotIn("Scrob", subject + body)

    async def test_totp_setup_uses_media_tracker_issuer(self) -> None:
        current_user = SimpleNamespace(email="friend@example.com", totp_enabled=False)

        result = await auth.totp_setup(current_user=current_user)

        query = parse_qs(urlparse(result["provisioning_uri"]).query)
        self.assertEqual(query["issuer"], ["Media Tracker"])
        self.assertEqual(
            unquote(urlparse(result["provisioning_uri"]).path),
            "/Media Tracker:friend@example.com",
        )


if __name__ == "__main__":
    unittest.main()
