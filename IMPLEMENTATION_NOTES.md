# Implementation Notes

### 1. State machine as a dict lookup, not a library

I chose to keep the state machine logic simple and self-contained inside the model
rather than pulling in a third-party library like `django-fsm`.

The `VALID_TRANSITIONS` dict on `ConsultationSession` makes the allowed paths
immediately readable. If the transition isn't in the dict, it simply doesn't happen —
no hidden magic. Anyone reading the model for the first time can understand the
full lifecycle in under a minute.

---

### 2. Decimal, not float, for all money values

Floating point arithmetic is not safe for money. `Decimal("50.00")` is used
consistently across models, billing logic, and tests. The model fields are all
`DecimalField`. This avoids the classic `50.0 * 3 = 149.99999...` category of bugs.

---

### 3. Idempotent recharge callbacks via `wallet_credited` flag

Payment gateways often replay webhooks. The guard against double-crediting is the
`wallet_credited` boolean on `RechargeTransaction`, updated atomically inside a
`select_for_update` transaction.

The flow is:
1. Lock the transaction row.
2. If already `SUCCESS` → return 200 without crediting.
3. Otherwise update status, credit wallet, set `wallet_credited = True` in the same
   atomic block.

Even if two identical callbacks arrive simultaneously, only one thread will win the
row lock; the other will see `wallet_credited = True` and skip.

---

### 4. Idempotent per-minute billing via `BillingRecord.unique_together`

The `(session, minute_number)` unique constraint at the database level is the source
of truth for "has this minute been billed?". The billing service catches `IntegrityError`
on duplicate inserts and skips them. This makes it safe to call `run_billing()` multiple
times for the same session without overcharging.

The session's `billed_minutes` counter tracks the high-water mark so we only iterate
over new minutes.

---

### 5. Per-minute cost locked at session start

`per_minute_cost` is written to the session record the moment the user calls
`/start/`. Subsequent changes to `settings.SESSION_PER_MINUTE_COST` don't affect
already-running sessions. This matches how real consultation platforms work — the
rate you agreed to at booking is the rate you pay.

---

### 6. Automatic session end on insufficient balance

Inside `run_billing()`, when the wallet balance is less than `per_minute_cost` before
billing a new minute:
- The session transitions to `completed` inside the same atomic transaction.
- `ended_by` is set to `system`, `end_reason` to `"Insufficient Balance"`.
- `ended_at` is stamped at that moment.
- The wallet is NOT touched — it stays at whatever partial balance it had.

This guarantees the wallet never goes negative and the audit trail is complete.

---

### 7. One active session per user enforced in the view

Before transitioning a session to `in_progress`, `StartSessionView` queries for any
other session with `state=in_progress` for the same user. If one exists, the request
returns `HTTP 400`. This is a business rule, not a DB constraint, because it needs to
return a meaningful error — not an opaque IntegrityError.

---

### 8. Role-based permissions are lightweight custom classes

`IsProviderUser` and `IsRegularUser` are thin `BasePermission` subclasses that check
`user.role`. This keeps the view code clean — the permission intent is clear from the
class name without reading the implementation.

---

### 9. JWT with role embedded in the token

The custom `TokenObtainPairSerializer` adds `role` and `username` to the JWT payload.
This means a frontend client can read the role from the token itself without making a
separate API call, which is useful for routing users to the right UI.

---

### 10. SQLite for development, PostgreSQL-ready

The `select_for_update()` calls are safe on PostgreSQL. SQLite supports them in WAL
mode but has no real row-level locking; for tests and local dev this is fine. Switching
to PostgreSQL only requires changing the `DATABASES` setting — no code changes needed.
