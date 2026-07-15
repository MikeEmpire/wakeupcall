# Domain Model and Invariants

## Scope

The current domain supports authenticated creation and ownership-scoped access to one-time weather announcements for a verified US phone number by SMS or voice. Demo delivery is wired end to end, due events can move through a versioned SQS worker boundary, Twilio Verify is implemented behind an application gateway, and non-demo SMS and voice events can be submitted through explicitly gated commands. Voice call progress is recorded from authenticated Twilio callbacks.

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

Locally, verification codes must contain 4–10 digits before they reach the gateway. The authenticated API exposes owner-scoped enrollment, listing, verification-start, and verification-check actions. Missing and cross-owner phone IDs return `404`. Full numbers and codes are write-only; responses contain masked numbers and normalized verification status without provider SIDs.

Verification start and check actions use separate per-authenticated-user DRF throttle scopes. Defaults are three starts and ten checks per hour. Django's cache-backed throttles are intentionally a bounded abuse-control layer rather than a strict security or billing boundary; they can permit small race overruns and are process-local with the current default cache.

Demo mode applies to scheduled message/call delivery, not proof of phone ownership. The authenticated verification actions use real Verify configuration at runtime; tests inject a fake gateway rather than creating a runtime bypass.

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

### Authenticated event API

- All event endpoints require an authenticated custom `accounts.User` through DRF Basic or session authentication.
- List and retrieve queries are filtered by owner. Another user's identifier returns `404`, including at every mutation endpoint.
- Creation accepts a verified phone record ID owned by the authenticated user. Other users' and unverified phone records produce the same field-validation failure.
- `scheduled_for` must include an explicit ISO 8601 offset. Inputs are normalized to UTC; naive local times are rejected because user time zones are not modeled.
- API-created events are always demo events. Client-supplied `status` and `is_demo` values cannot override server-owned safety and lifecycle fields.
- Event detail is read-only. Rescheduling, channel switching, and cancellation use dedicated `POST` actions and succeed only from `scheduled`; lifecycle conflicts return `409`.
- Rescheduling requires a strictly future datetime with an explicit offset and stores the normalized UTC value. Channel switching accepts only `sms` or `voice`.
- Pending-event changes lock and reload the authoritative row before validation and saving. They change only `scheduled_for` or `channel`, do not create attempts, and do not add a state transition.
- API representations contain the phone record ID, not the full phone number, and never expose delivery message bodies, provider payloads, or credentials.
- Lists are ordered by scheduled time and paginated at 50 records.

### Authenticated browser application

- Existing users authenticate through Django sessions; registration and token issuance remain out of scope.
- Browser pages expose owner-scoped phone enrollment/verification and event list/create/detail workflows.
- Rescheduling, channel switching, and cancellation call the same row-locking services as the API. Mutation controls are POST-only and CSRF-protected.
- Browser scheduling input uses ISO 8601 text with a required explicit offset rather than silently interpreting `datetime-local` input. Stored and displayed scheduling semantics remain UTC.
- Phone numbers are masked outside enrollment input, and event pages omit attempts, rendered messages, weather snapshots, provider identifiers, and raw payloads.
- Ordinary users do not receive staff controls. Staff may follow the explicit Admin link, where operational lifecycle fields remain read-only and cancellation stays service-backed.

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

Attempts never move between terminal states. A queue retry creates a new attempt number rather than rewriting historical attempt data. Retryable pre-send weather failures leave the event in `processing` while the failed attempt records a `QueueRetryable:*` error. The original SQS message owns the retry and creates the next attempt under the event row lock. Exhaustion moves the event to `failed` with `RetryExhausted:*`; the message remains undeleted so SQS can redrive it to the DLQ.

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

A demo event follows the normal workflow through weather lookup, announcement rendering, and attempt creation. At the sender boundary it must use `DemoMessageSender`, which logs only the channel, masked destination, and message length. The complete rendered announcement remains in the delivery-attempt audit record rather than application logs. The event then becomes `suppressed`.

Demo mode must never be implemented as an early return that skips auditing, and a demo event must never be passed to a real SMS or voice adapter.

The deployment template preserves the same invariant. Worker tasks default to `DELIVERY_REAL_WORKER_ENABLED=false` and omit `--allow-real-delivery`; enabling real queue delivery changes both gates together through an explicit stack parameter. The Scheduler and both ECS services also default off/zero until secrets and migrations are verified. None of these infrastructure gates replaces the application-level demo sender selection.

The Twilio SMS adapter returns only a validated Message SID. A successful create call moves a non-demo event to `submitted`; it does not prove handset delivery. Provider objects and raw responses stay inside the adapter. The staging smoke command additionally requires a disabled-by-default feature flag, command-line confirmation, and an event destination matching the explicitly configured authorized staging number.

The Twilio Voice adapter follows the same demo restriction and returns only a validated Call SID. It generates escaped inline TwiML from the project-rendered announcement and subscribes to initiated, ringing, answered, and completed callbacks. The voice staging command uses separate disabled-by-default configuration, confirmation, and authorized-destination checks.

## Idempotency and Concurrency

- The local dispatcher selects due `scheduled` events oldest-first in a bounded batch and claims them with PostgreSQL `SELECT ... FOR UPDATE SKIP LOCKED`.
- Dispatcher runs are demo-only by default. Including real events requires both the disabled-by-default environment gate and an explicit command flag.
- Claiming permits only `scheduled` events to enter `processing`; cancellation, rescheduling, and channel switching use the same row-lock boundary, so concurrent operations serialize against authoritative state. Claiming first rejects a later pending-event change. A time or channel change that commits first may be followed legally by cancellation or claiming. A `SKIP LOCKED` batch may safely defer a row being changed until the next scheduler tick.
- An event whose scheduled time is strictly more than the configured grace period in the past transitions through `processing` to `failed`, with a `MissedDeliveryWindow` attempt. It does not call weather or message providers. The default grace period is 15 minutes.
- Reprocessing a terminal delivered/failed/suppressed event returns the latest attempt without another send.
- A `processing` or `cancelled` event is not deliverable.
- PostgreSQL tests validate duplicate-claim, cancellation, rescheduling, and channel-switch races. SQLite tests validate the functional flow only.

Queue invariants:

- The focused AWS template configures EventBridge Scheduler to emit one `dispatch_due_events` tick envelope per minute when deployed and enabled; it does not create one schedule per event.
- The tick selects a bounded oldest-first set of still-`scheduled` event IDs and publishes identifier-only delivery envelopes without changing database state.
- Publication may duplicate an ID. The worker reloads authoritative state and row-locks the event immediately before claiming, so duplicates cannot create a second provider call after a legal claim.
- Demo events are the default queue scope. Real events require the worker environment gate and explicit worker command flag.
- Only retryable failures before entering the message-sender boundary are automatically retried. Any sender exception is terminal because provider acceptance may be ambiguous.
- Malformed messages and retry-exhausted transient failures remain undeleted for bounded SQS redrive. Permanent local failures are audited and acknowledged.
- A retrying event remains `processing`, preventing the next scheduler tick from publishing a fresh message with a reset receive count.

Stale `processing` events are deliberately quarantined in the current slice. There is no automatic replay because the process may have died after Twilio accepted a request but before its SID was committed. A future worker recovery workflow must inspect and reconcile that ambiguous outcome; elapsed time alone is not permission to send again.

The current bounded policy uses three SQS receives, visibility-based exponential delay from 30 to 300 seconds, and no automatic replay after the sender boundary. Queue resource values and worker settings must remain aligned.

## Current Product Assumptions

- United States only
- Five-digit ZIP codes; ZIP+4 is not accepted
- Fahrenheit announcements
- One phone destination per event
- One-time schedules only
- Minute-level scheduling precision is acceptable in the planned scheduler
- `submitted` and eventual provider delivery are distinct concepts

Any change to these assumptions requires a model and migration review rather than an incidental adapter change.
