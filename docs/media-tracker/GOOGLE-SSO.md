# Google sign-in setup

AnyList uses the OpenID Connect authorization-code flow. The isolated test instance remains invite-only. Production accepts new accounts from verified Google identities while keeping ordinary password registration closed after the first account.

## Google Cloud configuration

1. In Google Cloud Console, create an OAuth 2.0 Client ID with application type **Web application**.
2. Add the exact public AnyList callback as an authorized redirect URI. Use your own deployment hostname; Google requires an exact match, including scheme, host, port, path, and trailing-slash choice.
3. Store the generated client ID and secret in the VPS environment. Never commit the secret. The production callback is `https://anylist.don-cloud.dedyn.io/oidc-callback`; retain the existing test callback on the client.

Use these production settings:

```dotenv
ENABLE_REGISTRATIONS=false
OIDC_ENABLED=true
OIDC_PROVIDER_NAME=Google
OIDC_CLIENT_ID=your-client-id.apps.googleusercontent.com
OIDC_CLIENT_SECRET=your-client-secret
OIDC_AUTH_URL=https://accounts.google.com/o/oauth2/v2/auth
OIDC_TOKEN_URL=https://oauth2.googleapis.com/token
OIDC_USERINFO_URL=https://openidconnect.googleapis.com/v1/userinfo
OIDC_REDIRECT_URL=https://anylist.don-cloud.dedyn.io/oidc-callback
OIDC_IDENTIFIER_FIELD=email
OIDC_SCOPES=openid email
OIDC_AUTO_CREATE_USERS=true
OIDC_REQUIRE_VERIFIED_EMAIL=true
OIDC_DISABLE_PASSWORD_LOGIN=false
```

Keep password login enabled. With `ENABLE_REGISTRATIONS=false`, the empty database still permits exactly the first password registration to bootstrap an administrator. Once an account exists, normal registration closes. An administrator can create later password-backed accounts. A Google login can also create the first account; it receives the same administrator role. Restrict access to the hostname until the intended first administrator is established.

## Test-instance provisioning

1. Open **Settings → Administration → Add a user**.
2. Enter a username and the friend's exact Google email address.
3. Leave both password fields empty for an SSO-only account, or set a password as a fallback.
4. Ask the friend to choose **Login with Google**.

The test instance lowercases the address for matching, requires Google's `email_verified` claim, and refuses unknown accounts. Its OIDC auto-creation remains disabled. Production accepts a new verified Google identity without manual provisioning.

## Verification

The owner signed into production with Google after creating the password-backed administrator. The Google identity resolved to that administrator account; the database still contained one user. Before closing the remaining access gate, verify that:

- an administrator-provisioned email signs into the intended account on the test instance;
- different email casing still matches;
- an unknown Google email receives `No account found for this identity` on the test instance;
- a new verified Google email creates an ordinary, non-admin production account;
- an unverified email is rejected;
- direct registration closes after the first account;
- the callback returns to the requested same-site page;
- password login still works for the administrator recovery account.

The endpoint values above come from Google's current OpenID Connect discovery document. Media Tracker requests only `openid email`; no Google profile, Drive, Calendar, or other account permissions are requested.

The isolated test host uses its own callback and retains invite-only access. Production uses the callback above and permits verified Google self-registration.

