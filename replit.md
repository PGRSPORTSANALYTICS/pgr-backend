# PGR Backend

## Overview
Production-ready FastAPI backend with PostgreSQL database, Stripe integration, and async architecture.

## Project Structure
```
app/
├── __init__.py
├── main.py          # FastAPI application entry point
├── config.py        # Configuration and settings
├── database.py      # Async SQLAlchemy setup
├── models/
│   ├── __init__.py
│   ├── user.py      # User model with access levels
│   ├── subscription.py  # Stripe subscription tracking
│   └── audit_log.py     # Audit logging
├── routers/
│   ├── __init__.py
│   ├── health.py    # Health and version endpoints
│   ├── auth.py      # Authentication endpoints
│   ├── stripe_routes.py  # Stripe checkout and webhooks
│   └── access.py    # Access control endpoints
└── services/
    ├── __init__.py
    ├── discord.py   # Discord role assignment (ready for bot token)
    ├── stripe_service.py  # Stripe API integration
    └── audit.py     # Audit logging service
```

## Tech Stack
- **Backend**: Python 3.11, FastAPI, Uvicorn
- **Database**: PostgreSQL with async SQLAlchemy 2.0
- **Auth**: JWT tokens (email-based, no passwords)
- **Payments**: Stripe (via Replit integration)
- **Port**: 5000 (Replit), configurable via PORT env var (use 8000 for Railway)

## API Endpoints

### Core
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/` | API info |
| GET | `/health` | Health check with DB status |
| GET | `/version` | App version and environment |

### Auth
| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/auth/login` | Login/register with email |
| GET | `/auth/me` | Get current user |

### Stripe
| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/stripe/create-checkout-session` | Create Stripe checkout |
| POST | `/stripe/webhook` | Stripe webhook handler |

### Access
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/access/status` | Get user access level and subscription |

## Database Models
- **users**: id, email, stripe_customer_id, discord_user_id, access_level, timestamps
- **subscriptions**: id, user_id, stripe_subscription_id, plan, status, timestamps
- **audit_logs**: id, user_id, event_type, source, request_id, status, timestamp

## Access Levels
- `free`: Default access level
- `premium`: Upgraded via Stripe subscription

## Environment Variables
- `DATABASE_URL`: PostgreSQL connection string
- `SESSION_SECRET`: JWT signing secret
- `STRIPE_WEBHOOK_SECRET`: Stripe webhook signature secret (optional)
- `DISCORD_BOT_TOKEN`: Discord bot token (when ready)
- `DISCORD_GUILD_ID`: Discord server ID (when ready)

## Running
```bash
python -m app.main
```

## Railway Deployment
- Set deployment target to port 8000
- Use uvicorn command: `uvicorn app.main:app --host 0.0.0.0 --port 8000`

## Recent Changes
- 2026-01-08: Replaced Flask with FastAPI
- 2026-01-08: Added async SQLAlchemy 2.0
- 2026-01-08: Implemented auth, Stripe, access control endpoints
- 2026-01-08: Added Discord service layer (ready for bot token)
- 2026-01-08: Added audit logging
