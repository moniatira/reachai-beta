# ReachAI Beta — Week 1

Custom-built voice + chat AI receptionist that books appointments on Calendly.

## What ships in Week 1

- ✅ FastAPI backend (deployable to Railway in 5 minutes)
- ✅ PostgreSQL database with workspaces, sessions, bookings tables
- ✅ Calendly OAuth — SMBs authorize their Calendly account
- ✅ `/v1/chat` endpoint with Claude-powered conversation
- ✅ Custom chat widget — one `<script>` tag SMBs paste on their site
- ✅ Whitelist gate — only approved SMBs can sign up
- ✅ Health check + observability
- ✅ Test scripts to validate end-to-end

## Architecture

```
SMB website  ──→  widget.js (chat bubble)
                       │
                       ▼
              POST /v1/chat (your widget calls this)
                       │
                       ▼
            ┌──────────────────────────┐
            │  FastAPI backend         │
            │  - Workspace lookup      │
            │  - Session state         │
            │  - Claude API call       │ ──→ Anthropic Claude
            │  - Calendly tool calls   │ ──→ Calendly API
            └──────────────────────────┘
                       │
                       ▼
              PostgreSQL (Railway)
              - workspaces
              - calendly_tokens
              - sessions
              - bookings
```

## File map

```
services/api/
  app/
    main.py                    FastAPI entry point + middleware
    core/
      config.py                Env vars + settings (pydantic-settings)
      db.py                    SQLAlchemy async engine + session
      security.py              Workspace lookup, whitelist gate
    models/
      __init__.py
      workspace.py             SQLAlchemy ORM models
    services/
      claude.py                Anthropic SDK wrapper with tool use
      calendly.py              Calendly OAuth + REST client
    prompts/
      booking.py               System prompt template
    api/
      __init__.py
      workspaces.py            POST /v1/workspaces (admin-gated)
      chat.py                  POST /v1/chat (main conversation endpoint)
      calendly_oauth.py        GET /v1/calendly/connect, /callback
      widget.py                GET /v1/widget/{workspace_id}.js
      health.py                GET /health
  migrations/
    env.py                     Alembic config
    versions/
      001_initial.py           Create all tables
  requirements.txt             Python dependencies
  alembic.ini                  Alembic config
  Dockerfile                   Production container for Railway
  railway.json                 Railway deploy config
  .env.example                 Template for all required vars

services/widget/
  widget.js                    Customer-facing chat widget (vanilla JS)
  widget.css                   Widget styles
  index.html                   Test page for local development

infra/
  railway-setup.md             Step-by-step Railway deployment guide
  calendly-setup.md            How to register Calendly OAuth app

scripts/
  whitelist.py                 CLI: add an SMB to the whitelist
  test_chat.py                 CLI: simulate a chat conversation locally
  test_e2e.py                  End-to-end test against deployed API
```

## Deploy in 30 minutes

### Step 1 — Create accounts (5 min)
- [Railway](https://railway.app) — sign in with GitHub
- [Calendly developer portal](https://developers.calendly.com) — create OAuth app

### Step 2 — Configure Calendly OAuth (5 min)
- Create OAuth app at developers.calendly.com
- Set redirect URI to: `https://YOUR-RAILWAY-URL.up.railway.app/v1/calendly/callback`
- Save Client ID and Client Secret for step 4

### Step 3 — Deploy backend (10 min)
```bash
cd services/api
railway login
railway init
railway add postgresql
railway up
```

Railway gives you a URL like `https://reachai-api.up.railway.app`.

### Step 4 — Set environment variables in Railway dashboard
```
ANTHROPIC_API_KEY=sk-ant-...
CALENDLY_CLIENT_ID=...
CALENDLY_CLIENT_SECRET=...
CALENDLY_REDIRECT_URI=https://YOUR-RAILWAY-URL.up.railway.app/v1/calendly/callback
ADMIN_API_KEY=<generate a long random string>
DATABASE_URL=<Railway provides this automatically>
ENVIRONMENT=production
ALLOWED_ORIGINS=https://moniatira.github.io,https://acmesalon.com
```

### Step 5 — Run database migrations (2 min)
```bash
railway run alembic upgrade head
```

### Step 6 — Whitelist your first SMB (1 min)
```bash
python scripts/whitelist.py \
  --api-url https://YOUR-RAILWAY-URL.up.railway.app \
  --admin-key YOUR_ADMIN_KEY \
  --name "Acme Salon" \
  --slug acme-salon \
  --owner-email jordan@acmesalon.com
```

Output gives you:
- Workspace ID
- Calendly connect URL (send to SMB)
- Embed code (give to SMB after they connect Calendly)

### Step 7 — Test end-to-end (5 min)
```bash
python scripts/test_e2e.py \
  --api-url https://YOUR-RAILWAY-URL.up.railway.app \
  --workspace-id acme-salon
```

## What the SMB experiences

1. You send them the Calendly connect URL
2. They click → authorize → redirected back with confirmation
3. You send them the one-line embed code
4. They paste it on their website
5. Customers visit their site, see the chat bubble, book appointments

## Customer experience

1. Visit SMB's site → see ReachAI chat bubble (branded to SMB)
2. Click → chat opens
3. "Hi! I'm Sarah from Acme Salon. How can I help?"
4. Customer types "I need a haircut tomorrow"
5. AI checks Calendly real-time → "I have 10am, 1pm, or 4pm"
6. Customer picks → AI books → confirmation email from Calendly

## Next steps (Week 2+)

- Add Vapi voice channel calling same `/v1/chat` backend logic
- Add SMS via Twilio
- Add email parsing via Postmark
- Build provider dashboard wired to real data
- Add Stripe billing
- Expand to Google Calendar, Outlook, Cal.com
