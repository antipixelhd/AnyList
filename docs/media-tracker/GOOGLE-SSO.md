# Google sign-in setup

AnyList uses the standard OpenID Connect authorization-code flow. Accounts are invite-only: create the AnyList account first with the same email address the friend uses at Google. OIDC auto-creation stays disabled.

## Google Cloud configuration

1. In Google Cloud Console, create an OAuth 2.0 Client ID with application type **Web application**.
2. Add the exact public AnyList callback as an authorized redirect URI. Use your own deployment hostname; Google requires an exact match, including scheme, host, port, path, and trailing-slash choice.
3. Store the generated client ID and secret in the test instance environment. Never commit the secret.

Use these settings:

```dotenv
ENABLE_REGISTRATIONS=false
OIDC_ENABLED=true
OIDC_PROVIDER_NAME=Google
OIDC_CLIENT_ID=your-client-id.apps.googleusercontent.com
OIDC_CLIENT_SECRET=your-client-secret
OIDC_AUTH_URL=https://accounts.google.com/o/oauth2/v2/auth
OIDC_TOKEN_URL=https://oauth2.googleapis.com/token
OIDC_USERINFO_URL=https://openidconnect.googleapis.com/v1/userinfo
OIDC_REDIRECT_URL=https://your-anylist-host.example/oidc-callback
OIDC_IDENTIFIER_FIELD=email
OIDC_SCOPES=openid email
OIDC_AUTO_CREATE_USERS=false
OIDC_REQUIRE_VERIFIED_EMAIL=true
OIDC_DISABLE_PASSWORD_LOGIN=false
```

Keep password login enabled during isolated test deployment. It can be disabled after an administrator has successfully tested Google sign-in and retained a recovery path.

## Provisioning a friend

1. Open **Settings → Administration → Add a user**.
2. Enter a username and the friend's exact Google email address.
3. Leave both password fields empty for an SSO-only account, or set a password as a fallback.
4. Ask the friend to choose **Login with Google**.

Media Tracker lowercases the address for matching, requires Google's `email_verified` claim, and refuses unknown accounts. Self-registration and OIDC account creation remain disabled.

## Verification gate

Before enabling Google sign-in on the public instance, verify on the isolated test instance that:

- an administrator-provisioned email signs into the intended account;
- different email casing still matches;
- an unknown Google email receives `No account found for this identity`;
- an unverified email is rejected;
- direct registration remains disabled;
- the callback returns to the requested same-site page;
- password login still works for the administrator recovery account.

The endpoint values above come from Google's current OpenID Connect discovery document. Media Tracker requests only `openid email`; no Google profile, Drive, Calendar, or other account permissions are requested.

The isolated test host has public DNS and a dedicated Let's Encrypt certificate. Its Google authorization request contains the exact registered callback, and the deployed instance has registration and OIDC account creation disabled, verified-email enforcement enabled, password login enabled, and a password-backed administrator recovery account. The final verification item remains an interactive sign-in with that administrator's Google account, followed by confirmation that the callback returns to Media Tracker.

