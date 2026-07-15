# Domain Model and Invariants

## Scope

The current domain supports one-time weather announcements to a verified US phone number by SMS or voice. Demo delivery is wired end to end, Twilio Verify is implemented behind an application gateway, and non-demo SMS and voice events can be submitted to Twilio through opt-in staging commands. Voice call progress is recorded from authenticated Twilio callbacks.

## User

`accounts.User` extends Django's `AbstractUser` without business-specific fields.

Rules:

- All user foreign keys must reference the configured custom user model.
- Administrator access uses Django's `is_staff`, `is_superuser`, and standard permissions.
- Phone verification and scheduling data do not belong directly on the user row.

## PhoneNumber

Represents a delivery destination owned by one user.

Current fields of architectural significance:

- `user`
- `number`
- `verified_at`
- audit timestamps

Invariants:

- The number uses E.164 format: a leading `+` followed by at most 15 digits.
- The number is currently globally unique.
- `verified_at is not None` means verified.
- Changing a number should require verification again. Treat verified phone records as effectively immutable once real verification exists.
- Verification codes and challenge lifecycle are owned by Twilio Verify and are never stored locally.
- Only an `approved` verification check sets `verified_at`.
- A rejected or pending check leaves the phone unverified.
- Verification checks are idempotent after the phone is verified and do not call the provider again.
- A phone number changed during an in-flight check is not marked verified.

The global uniqueness rule is a current product assumption, not an unavoidable technical requirement. Revisit it if shared household numbers must be supported.

### Verification workflow

```text
unverified phone --start--> Twilio-managed pending challenge
       |
       +--check rejected/pending--> remains unverified
       |
       +--check approved----------> verified_at set in UTC
```

Locally, verification codes must contain 4–10 digits before they reach the gateway. Public endpoint throttling is not implemented because user-facing verification endpoints do not exist yet. It is required before those endpoints are exposed.

Demo mode currently applies to scheduled message/call delivery, not proof of phone ownership. Production and future user-facing flows should use real Verify; tests inject a fake gateway rather than creating a runtime bypass.

## ScheduledEvent

Represents one requested weather announcement.

Important fields:

- owner (`user`)
- verified destination (`phone_number`)
- five-digit `zip_code`
- UTC `scheduled_for`
- `channel`: `sms` or `voice`
- `status`
- `is_demo`
- processing and completion timestamps

Invariants:

- The phone number belongs to the same user.
- The phone number is verified when the event is validated.
- New events are scheduled in the future.
- Events are one-time; recurrence is not represented.
- New events default to demo mode as a safety measure.
- User-local time zones are not represented yet. Input conversion will happen at a future boundary; storage remains UTC.

### Event state machine

```text
              +------------> cancelled
              |
scheduled ----+----> processing ----> submitted
                                |----> failed
                                +----> suppressed
```

Allowed transitions:

| From | To |
| --- | --- |
| `scheduled` | `processing`, `cancelled` |
| `processing` | `submitted`, `failed`, `suppressed` |

All other transitions are invalid. Current terminal states are `submitted`, `failed`, `suppressed`, and `cancelled`.

Status meanings:

- `scheduled`: waiting to become due
- `processing`: exclusively claimed for delivery work
- `submitted`: accepted by a delivery provider; not proof of final delivery
- `failed`: the current workflow ended with an error
- `suppressed`: fully rendered and audited but intentionally not sent
- `cancelled`: stopped before processing

Application services must use the model transition method and save the associated timestamps. Direct status assignment bypasses transition validation and is not an approved application path. Admin exposes operational status fields as read-only.

## DeliveryAttempt

Represents one auditable execution attempt for an event.

Important fields:

- event
- attempt number, unique within the event
- status
- rendered announcement
- normalized weather snapshot
- optional provider SID
- optional normalized provider status, callback sequence, and update time
- sanitized error code and message
- start and completion timestamps

### Attempt state machine

```text
processing ----> submitted
           |---> failed
           +---> suppressed
```

Attempts do not currently retry or move between terminal states. A retry will create a new attempt number rather than rewrite historical attempt data.

The attempt's local `submitted` status remains terminal and means only that Twilio accepted the create request. Voice callbacks update a separate provider-status lifecycle:

```text
queued / initiated --> ringing --> in_progress --> completed
                         |  |             |
                         |  +--> busy     +--> failed / canceled
                         +-----> no_answer / failed / canceled
```

Callbacks carry a provider sequence number. An update is applied only when its sequence is newer than the stored sequence, and provider-terminal outcomes never regress. Exact duplicates and late older callbacks are successful no-ops. This provider outcome never rewrites the scheduled event's local `submitted` status. A `completed` voice call means audio was connected; it does not prove that a person, rather than voicemail or another system, heard the announcement.

The weather snapshot is JSON because it is historical evidence with a small provider-normalized shape, not relational data used for filtering.

## Demo Delivery Invariant

A demo event follows the normal workflow through weather lookup, announcement rendering, and attempt creation. At the sender boundary it must use `DemoMessageSender`, which logs the intended message with a masked destination. It then becomes `suppressed`.

Demo mode must never be implemented as an early return that skips auditing, and a demo event must never be passed to a real SMS or voice adapter.

The Twilio SMS adapter returns only a validated Message SID. A successful create call moves a non-demo event to `submitted`; it does not prove handset delivery. Provider objects and raw responses stay inside the adapter. The staging smoke command additionally requires a disabled-by-default feature flag, command-line confirmation, and an event destination matching the explicitly configured authorized staging number.

The Twilio Voice adapter follows the same demo restriction and returns only a validated Call SID. It generates escaped inline TwiML from the project-rendered announcement and subscribes to initiated, ringing, answered, and completed callbacks. The voice staging command uses separate disabled-by-default configuration, confirmation, and authorized-destination checks.

## Idempotency and Concurrency

- Claiming uses a database row lock and permits only `scheduled` events to enter `processing`.
- Reprocessing a terminal delivered/failed/suppressed event returns the latest attempt without another send.
- A `processing` or `cancelled` event is not deliverable.
- PostgreSQL is required to validate true row-lock behavior. SQLite tests validate functional flow only.

Before asynchronous processing, define:

- recovery policy for stale `processing` events
- transient versus permanent failure categories
- retry limits and delay policy
- unknown-outcome policy after an ambiguous external call
- missed-event grace window

## Current Product Assumptions

- United States only
- Five-digit ZIP codes; ZIP+4 is not accepted
- Fahrenheit announcements
- One phone destination per event
- One-time schedules only
- Minute-level scheduling precision is acceptable in the planned scheduler
- `submitted` and eventual provider delivery are distinct concepts

Any change to these assumptions requires a model and migration review rather than an incidental adapter change.
