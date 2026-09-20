import asyncio
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from core.config import settings


def _send_sync(to: str, subject: str, html: str) -> None:
    from_addr = settings.from_email or settings.smtp_username or "noreply@anylist"

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = to
    msg.attach(MIMEText(html, "html"))

    enc = (settings.smtp_encryption or "tls").lower()

    if enc == "ssl":
        with smtplib.SMTP_SSL(settings.smtp_address, settings.smtp_port) as smtp:
            if settings.smtp_username and settings.smtp_password:
                smtp.login(settings.smtp_username, settings.smtp_password)
            smtp.sendmail(from_addr, to, msg.as_string())
    elif enc == "tls":
        with smtplib.SMTP(settings.smtp_address, settings.smtp_port) as smtp:
            smtp.starttls()
            if settings.smtp_username and settings.smtp_password:
                smtp.login(settings.smtp_username, settings.smtp_password)
            smtp.sendmail(from_addr, to, msg.as_string())
    else:  # none
        with smtplib.SMTP(settings.smtp_address, settings.smtp_port) as smtp:
            if settings.smtp_username and settings.smtp_password:
                smtp.login(settings.smtp_username, settings.smtp_password)
            smtp.sendmail(from_addr, to, msg.as_string())


async def send_email(to: str, subject: str, html: str) -> None:
    await asyncio.to_thread(_send_sync, to, subject, html)


async def send_activation_email(to: str, token: str) -> None:
    link = f"{settings.server_url}/auth/activate/{token}"
    html = f"""
    <!DOCTYPE html>
    <html>
    <body style="font-family: sans-serif; background: #18181b; color: #f4f4f5; padding: 40px;">
      <div style="max-width: 480px; margin: 0 auto; background: #27272a; border: 1px solid #3f3f46; border-radius: 12px; padding: 32px;">
        <h1 style="font-size: 24px; font-weight: 700; margin-bottom: 8px;">Confirm your email</h1>
        <p style="color: #a1a1aa; margin-bottom: 24px;">
          Thanks for registering on AnyList. Click the button below to activate your account.
          This link expires in <strong style="color: #f4f4f5;">24 hours</strong>.
        </p>
        <a href="{link}"
           style="display: inline-block; background: #f4f4f5; color: #09090b; font-weight: 700;
                  padding: 12px 24px; border-radius: 8px; text-decoration: none;">
          Activate account
        </a>
        <p style="color: #71717a; font-size: 12px; margin-top: 24px;">
          If you didn't create an account, you can safely ignore this email.
        </p>
      </div>
    </body>
    </html>
    """
    await send_email(to, "Activate your AnyList account", html)


async def send_password_reset_email(to: str, token: str) -> None:
    link = f"{settings.server_url}/reset-password/{token}"
    html = f"""
    <!DOCTYPE html>
    <html>
    <body style="font-family: sans-serif; background: #18181b; color: #f4f4f5; padding: 40px;">
      <div style="max-width: 480px; margin: 0 auto; background: #27272a; border: 1px solid #3f3f46; border-radius: 12px; padding: 32px;">
        <h1 style="font-size: 24px; font-weight: 700; margin-bottom: 8px;">Reset your password</h1>
        <p style="color: #a1a1aa; margin-bottom: 24px;">
          We received a request to reset your AnyList password. Click the button below to choose a new one.
          This link expires in <strong style="color: #f4f4f5;">1 hour</strong>.
        </p>
        <a href="{link}"
           style="display: inline-block; background: #f4f4f5; color: #09090b; font-weight: 700;
                  padding: 12px 24px; border-radius: 8px; text-decoration: none;">
          Reset password
        </a>
        <p style="color: #71717a; font-size: 12px; margin-top: 24px;">
          If you didn't request a password reset, you can safely ignore this email.
        </p>
      </div>
    </body>
    </html>
    """
    await send_email(to, "Reset your AnyList password", html)
