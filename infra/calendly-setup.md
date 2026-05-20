# Calendly OAuth App Setup

ReachAI needs OAuth credentials from Calendly so SMBs can authorize us to read their calendar and create bookings.

## 1. Get a Calendly developer account

Go to https://developers.calendly.com and sign in with your Calendly account. (Free, doesn't need a paid Calendly plan to create OAuth apps for development.)

## 2. Create an OAuth application

Dashboard → My Apps → Create New App

Fill in:

| Field | Value |
|-------|-------|
| App name | ReachAI |
| Description | AI front desk that books appointments via voice + chat |
| Website | https://moniatira.github.io/bookring/ |
| Redirect URI | `https://YOUR-RAILWAY-URL.up.railway.app/v1/calendly/callback` |
| Scopes | `default` (Calendly's standard read/write) |

Save. Calendly gives you:
- **Client ID** — public identifier
- **Client Secret** — keep this secret (it's like a password)

## 3. Add credentials to Railway

In Railway dashboard → Variables:
```
CALENDLY_CLIENT_ID=<your client id>
CALENDLY_CLIENT_SECRET=<your client secret>
CALENDLY_REDIRECT_URI=https://YOUR-RAILWAY-URL.up.railway.app/v1/calendly/callback
```

## 4. Test the OAuth flow

After deploying, test the flow yourself:

1. Whitelist a test workspace with your own email
2. Click the Calendly connect URL the script gives you
3. Sign in with Calendly → authorize ReachAI
4. You should get redirected to a success page

If you see an error, check:
- Redirect URI in Calendly app exactly matches `CALENDLY_REDIRECT_URI` env var
- Client ID and Secret are correct (no extra spaces)
- The workspace slug exists and is whitelisted

## Production checklist (before going beyond beta)

- [ ] Submit OAuth app for Calendly review (required for >5 users on their platform)
- [ ] Add privacy policy URL
- [ ] Add terms of service URL
- [ ] Upload an app icon (256x256 PNG)
- [ ] Test on real Calendly accounts at different paid tiers

For the 2-SMB beta, you don't need Calendly app review yet.

## What Calendly scopes give us

The `default` scope provides:

- **Read user info** — name, email, scheduling URL
- **Read event types** — what services they offer with durations
- **Read available times** — open slots in a date range
- **Read scheduled events** — confirm bookings created

We do NOT need write permissions because Calendly handles the actual booking creation when the customer clicks the scheduling URL. This is simpler and safer for the beta.
