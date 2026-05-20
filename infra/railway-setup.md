# Railway Deployment — Step by Step

## Prerequisites

- GitHub account (free)
- Railway account at https://railway.app — sign in with GitHub
- Anthropic API key from console.anthropic.com
- Calendly developer account — create OAuth app (see `calendly-setup.md`)

## 1. Install Railway CLI

```bash
npm install -g @railway/cli
# or on Windows:
# winget install Railway.Railway
```

Verify:
```bash
railway --version
```

## 2. Login

```bash
railway login
```
Opens browser, authorize.

## 3. Initialize project from the api directory

```bash
cd services/api
railway init
```
Pick "Empty Project" — give it a name like `reachai-beta`.

## 4. Add a Postgres database

```bash
railway add
```
Pick PostgreSQL. Railway provisions it and sets `DATABASE_URL` automatically.

## 5. Set environment variables

In the Railway dashboard (https://railway.app/dashboard) → your project → Variables:

| Variable | Value |
|----------|-------|
| `ANTHROPIC_API_KEY` | Your Anthropic key |
| `CLAUDE_MODEL` | `claude-sonnet-4-6` |
| `CALENDLY_CLIENT_ID` | From Calendly developer portal |
| `CALENDLY_CLIENT_SECRET` | From Calendly developer portal |
| `CALENDLY_REDIRECT_URI` | `https://YOUR-RAILWAY-URL/v1/calendly/callback` |
| `ADMIN_API_KEY` | Generate: `openssl rand -hex 32` |
| `SESSION_SECRET_KEY` | Generate: `openssl rand -hex 32` |
| `ALLOWED_ORIGINS` | `https://moniatira.github.io,https://acmesalon.com` |
| `ENVIRONMENT` | `production` |
| `LOG_LEVEL` | `INFO` |

`DATABASE_URL` is set automatically by the Postgres plugin.

**IMPORTANT:** Postgres URL Railway provides starts with `postgresql://`. Our app uses async, so:
```
Add this Railway variable to convert it:
DATABASE_URL_OVERRIDE=postgresql+asyncpg://...

```
OR edit `app/core/config.py` to auto-convert:
```python
@property
def async_database_url(self) -> str:
    url = self.database_url
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+asyncpg://", 1)
    return url
```

## 6. Deploy

```bash
railway up
```

Railway builds the Dockerfile, runs migrations, starts uvicorn.

After deploy:
```bash
railway open
```
Opens your live URL. Note the URL — looks like `https://reachai-beta-production.up.railway.app`.

## 7. Update CALENDLY_REDIRECT_URI

Now that you have the real Railway URL, update:
```
CALENDLY_REDIRECT_URI=https://reachai-beta-production.up.railway.app/v1/calendly/callback
```

Also update this in your Calendly OAuth app settings at developers.calendly.com.

## 8. Verify

```bash
curl https://reachai-beta-production.up.railway.app/health
# {"status":"ok","service":"reachai-api",...}
```

## 9. Create your first workspace

```bash
python scripts/whitelist.py \
  --api-url https://reachai-beta-production.up.railway.app \
  --admin-key YOUR_ADMIN_KEY \
  --slug acme-salon \
  --name "Acme Salon" \
  --owner-email jordan@acmesalon.com
```

Output gives you:
- The Calendly connect URL to send the SMB
- The embed code to give them after they connect

## Costs

- Railway free tier: $5/month of usage included
- This service: ~$3-5/month with light traffic
- Postgres: included free
- Anthropic Claude API: pay-per-token, ~$0.01 per conversation
- Calendly: free for the SMB (their existing account)

## Troubleshooting

**Migrations fail on first deploy**
Run them manually:
```bash
railway run alembic upgrade head
```

**"Database connection refused"**
Check `DATABASE_URL` is set in Variables. Railway sets this automatically when you add Postgres.

**Async driver error**
The URL must use `postgresql+asyncpg://` prefix. See step 5 note.

**CORS errors from the GitHub Pages site**
Add `https://moniatira.github.io` to `ALLOWED_ORIGINS`.
