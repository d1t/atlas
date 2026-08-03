# Connecting Gmail (Google OAuth 2.0)

Atlas sends approved outreach from each user's **own** Gmail account and reads replies
to detect responses. This is the customer-facing integration. The SMTP/IMAP App Password
path still exists but authenticates a single shared mailbox, so it is a development and
administrator fallback only.

Until `GMAIL_OAUTH_ENABLED=true` **and** credentials are present, the flow is inert:
drafts are recorded, nothing is transmitted, and no Connect button is offered.

## 1. Create the Google Cloud project

1. https://console.cloud.google.com/projectcreate — name it e.g. `Atlas Trade OS`.
2. **APIs & Services → Library** → search **Gmail API** → **Enable**.

## 2. Configure the consent screen

3. **APIs & Services → OAuth consent screen** → User type **External** → Create.
4. Fill in app name, user support email, developer contact email.
5. Leave **Publishing status** as **Testing**. No Google verification is needed in this
   mode.
6. **Test users** → add every Google account that will connect a mailbox. Testing mode
   allows up to 100, and an account not on this list cannot connect.

## 3. Add exactly two scopes

7. **Scopes → Add or remove scopes** → add only:

   | Scope | Why Atlas needs it |
   | --- | --- |
   | `https://www.googleapis.com/auth/gmail.send` | Send the outreach you have approved, from your own address. |
   | `https://www.googleapis.com/auth/gmail.readonly` | Detect when a counterparty has replied. |

   `openid` and `email` are added automatically to identify which account was connected.

   Do **not** add `gmail.modify` or `https://mail.google.com/`. Nothing in Atlas
   modifies or deletes mail, and a broader grant is both harder to justify to a user and
   harder to get through verification later.

   Both Gmail scopes are **restricted**. That is fine in Testing mode. A public launch
   requires Google verification including a third-party security assessment — plan for
   weeks, and usually a fee.

## 4. Create the OAuth client

8. **Credentials → Create credentials → OAuth client ID → Web application.**
9. Under **Authorised redirect URIs**, add both:

   ```
   http://localhost:8000/api/v1/integrations/google/callback
   https://YOUR-BACKEND-DOMAIN/api/v1/integrations/google/callback
   ```

   These must match `GOOGLE_REDIRECT_URI` **character for character** — same scheme, no
   trailing slash, no path differences. A mismatch produces `redirect_uri_mismatch` and
   is the most common setup failure.

   Note the host is the **backend**, not the frontend. Google calls the API directly;
   the API then redirects the browser back to `/settings/integrations` on the frontend.

10. Copy the **Client ID** and **Client secret**.

## 5. Configure Atlas

```env
GMAIL_OAUTH_ENABLED=true
GOOGLE_CLIENT_ID=<client id>
GOOGLE_CLIENT_SECRET=<client secret>
GOOGLE_REDIRECT_URI=http://localhost:8000/api/v1/integrations/google/callback
TOKEN_ENCRYPTION_KEY=<generated below>
```

Generate the encryption key:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

This protects refresh tokens at rest. A refresh token is a long-lived key to somebody's
mailbox, so a database dump must not hand over every customer's mail. Connecting is
**refused** if this key is missing rather than storing tokens in plaintext.

Changing the key invalidates every stored connection and all users must reconnect. Treat
it like a database password: back it up, and rotate deliberately.

Never commit these values. `.env` is gitignored.

## 6. Verify

```bash
curl -H "Authorization: Bearer $TOKEN" localhost:8000/api/v1/integrations/google/status
```

| `mode` | Meaning |
| --- | --- |
| `unavailable` | Flag off or credentials missing — the flow is not offered. |
| `offline` | Configured, nobody connected yet. Drafts recorded, nothing sent. |
| `live` | Connected and healthy. |
| `needs_reconnect` | Access revoked, expired, or a required permission was declined. |

Then `POST /api/v1/integrations/google/connect`, open the returned
`authorization_url`, grant access, and confirm the status becomes `live`.

## Endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/integrations/google/permissions` | What will be requested and why, before leaving the app |
| `GET` | `/integrations/google/status` | Connection state |
| `POST` | `/integrations/google/connect` | Start authorisation; also used to reconnect |
| `GET` | `/integrations/google/callback` | Where Google returns the user |
| `DELETE` | `/integrations/google` | Revoke at Google and delete stored tokens |

## Troubleshooting

**`redirect_uri_mismatch`** — the URI in Google Cloud differs from
`GOOGLE_REDIRECT_URI`. Compare them character by character.

**Status stuck at `needs_reconnect` with `scope_changed`** — the user unticked a
permission on the consent screen. Reconnect and accept all requested permissions.

**Status flips to `needs_reconnect` with `revoked`** — access was withdrawn at
https://myaccount.google.com/permissions, or the account password changed. The refresh
token is permanently dead; reconnecting is the only fix.

**`403 access_denied` at the consent screen** — the Google account is not in the
**Test users** list.

**Connect returns 503** — either OAuth credentials or `TOKEN_ENCRYPTION_KEY` are
missing. Both are required before anyone can connect.
