# Consultation Billing — Django Backend

A backend system for wallet-based per-minute billing in 1:1 consultation sessions.

---

## Stack

- **Python 3.12** + **Django 4.2**
- **Django REST Framework** for the API layer
- **SimpleJWT** for authentication
- **SQLite** for local development (swap to PostgreSQL in production by changing `DATABASES` in settings)

---

## Project Layout

```
consultation_billing/    ← Django project config (settings, root URLs)
accounts/                ← Custom User model + JWT auth endpoints
wallet/                  ← Wallet balance, mock recharge flow
sessions_app/            ← Session state machine, billing logic, tests
```

---

## Setup

```bash
# 1. Clone the repo
git clone <repo-url>
cd consultation_billing

# 2. Install dependencies
pip install django djangorestframework djangorestframework-simplejwt

# 3. Run migrations
python manage.py migrate

# 4. (Optional) Create a superuser for the admin panel
python manage.py createsuperuser

# 5. Start the dev server
python manage.py runserver
```

---

## Running Tests

```bash
python manage.py test sessions_app --verbosity=2
```

---

## API Reference

### Authentication

| Method | URL | Description |
|--------|-----|-------------|
| POST | `/api/auth/register/` | Register a new user or provider |
| POST | `/api/auth/login/` | Get JWT access + refresh tokens |
| POST | `/api/auth/token/refresh/` | Refresh access token |
| GET | `/api/auth/me/` | Get current user info |

All protected endpoints require: `Authorization: Bearer <access_token>`

Role can be `"user"` or `"provider"`.

---

### Wallet

| Method | URL | Description |
|--------|-----|-------------|
| GET | `/api/wallet/` | View current balance |
| POST | `/api/wallet/recharge/initiate/` | Start a mock recharge |
| POST | `/api/wallet/recharge/callback/` | Mock gateway callback (no auth) |
| GET | `/api/wallet/recharge/transactions/` | View recharge history |

**Initiate recharge:**
```json
{ "amount": 500 }
```

**Callback (simulates payment gateway):**
```json
{ "reference": "<ref-from-initiate>", "status": "success" }
```
Status can be `"success"` or `"failed"`. Sending the same success callback multiple times credits the wallet only once.

---

### Sessions

| Method | URL | Who | Description |
|--------|-----|-----|-------------|
| POST | `/api/sessions/` | user | Create a session |
| GET | `/api/sessions/` | any | List your sessions |
| GET | `/api/sessions/<id>/` | any | Get session detail |
| POST | `/api/sessions/<id>/assign/` | user | Assign provider to session |
| POST | `/api/sessions/<id>/accept/` | provider | Accept session |
| POST | `/api/sessions/<id>/start/` | user | Start session (in_progress) |
| POST | `/api/sessions/<id>/end/` | user/provider | End session |
| POST | `/api/sessions/<id>/bill/` | any | Trigger billing for elapsed minutes |
| GET | `/api/sessions/<id>/billing/` | any | View billing records |
| POST | `/api/sessions/<id>/cancel/` | any | Cancel before in_progress |

---

## Full Walkthrough Example

```bash
# 1. Register user and provider
curl -X POST /api/auth/register/ -d '{"username":"alice","password":"pass","role":"user"}'
curl -X POST /api/auth/register/ -d '{"username":"drsmith","password":"pass","role":"provider"}'

# 2. Login as alice
curl -X POST /api/auth/login/ -d '{"username":"alice","password":"pass"}'
# → returns { "access": "...", "refresh": "..." }

# 3. Recharge wallet
curl -X POST /api/wallet/recharge/initiate/ -H "Authorization: Bearer <token>" -d '{"amount":500}'
# → returns { "transaction": { "reference": "abc-123" } }

curl -X POST /api/wallet/recharge/callback/ -d '{"reference":"abc-123","status":"success"}'

# 4. Create session with provider (use provider's user ID)
curl -X POST /api/sessions/ -H "Authorization: Bearer <token>" -d '{"provider_id":2}'

# 5. Provider accepts
curl -X POST /api/sessions/<session-id>/accept/ -H "Authorization: Bearer <provider-token>"

# 6. Alice starts the session
curl -X POST /api/sessions/<session-id>/start/ -H "Authorization: Bearer <token>"

# 7. Trigger billing (after some time passes)
curl -X POST /api/sessions/<session-id>/bill/ -H "Authorization: Bearer <token>"

# 8. End session
curl -X POST /api/sessions/<session-id>/end/ -H "Authorization: Bearer <token>"
```

---

## State Diagram

```
                  ┌─────────────┐
                  │ unassigned  │
                  └──────┬──────┘
              (assign)   │
                         ▼
                  ┌─────────────┐
                  │  assigned   │
                  └──────┬──────┘
         (provider       │
          accepts)       ▼
             ┌──────────────────────┐
             │ accepted_by_provider │
             └──────────┬───────────┘
          (user starts)  │
                         ▼
                  ┌─────────────┐
                  │ in_progress │
                  └──────┬──────┘
    (user/provider/       │
     system ends)         ▼
                  ┌─────────────┐
                  │  completed  │
                  └─────────────┘

  Any state before in_progress can also transition to:
                  ┌─────────────┐
                  │  cancelled  │
                  └─────────────┘
```

---

## ER Diagram

```
User (accounts_user)
 ├── id, username, email, password, role [user|provider]
 │
 ├──< Wallet (wallet_wallet)
 │     └── id, user_id, balance
 │
 ├──< RechargeTransaction (wallet_rechargetransaction)
 │     └── id (UUID), user_id, amount, reference, status, wallet_credited
 │
 ├──< ConsultationSession (sessions_app_consultationsession) [as user]
 │     └── id (UUID), user_id, provider_id, state, per_minute_cost,
 │         started_at, ended_at, billed_minutes, billed_amount,
 │         ended_by, end_reason
 │         │
 │         └──< BillingRecord (sessions_app_billingrecord)
 │               └── id, session_id, minute_number [UNIQUE per session],
 │                   amount_charged, charged_at
 │
 └──< ConsultationSession [as provider]
```

---

## Implementation Notes

See [IMPLEMENTATION_NOTES.md](./IMPLEMENTATION_NOTES.md) for the reasoning behind key decisions.
