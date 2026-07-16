# Architecture

## Document Status

This document separates the tested, as-built system from the planned production design. The **Current architecture** section describes code that exists. The **Target architecture** section is a proposal and must not be treated as implemented.

## Architectural Priorities

1. Deliver a clear coding exercise within a two-day implementation budget.
2. Keep business rules independent from Twilio, weather vendors, and AWS.
3. Make asynchronous delivery duplicate-aware and auditable.
4. Prefer Django and PostgreSQL capabilities over additional infrastructure.
5. Preserve a safe demo path through the same orchestration used by real delivery.

## Current Architecture

The repository is one Django project with four local applications:

| App | Current responsibility |
| --- | --- |
| `accounts` | Custom user, owned phone numbers, verification services, authenticated API/browser phone workflows, and Twilio Verify adapter |
| `scheduling` | One-time scheduled events, authenticated owner-scoped API/browser workflows, row-locked pending-event mutation services, admin action, lifecycle, and deterministic scenario seeding |
| `delivery` | Delivery attempts, inbound SMS command audits, due-event publication and claiming, SQS transport/worker, announcement rendering, Twilio adapters, callback handling, and orchestration |
| `weather` | Normalized weather value object, provider boundary, deterministic fake, and WeatherAPI.com REST adapter |

The current system supports both the direct bounded command and an SQS worker path. Queue processing is demo-only by default; real events require a separate environment gate and explicit worker flag. Single-event staging commands remain available for isolated provider smoke tests:

```text
dispatcher / staging SMS / staging Voice command
          |
          v
bounded due query --atomic SKIP LOCKED claim--> ScheduledEvent + DeliveryAttempt
          |
          +--> WeatherProvider --> FakeWeatherProvider
          |
          +--> announcement renderer
          |
          +--> DemoMessageSender --> masked metadata log
          |    or TwilioSmsSender --> Twilio Messages API
          |    or TwilioVoiceSender --> Twilio Calls API
          |
          +--atomic finalize--> suppressed or submitted attempt and event
```

The queue path is:

```text
EventBridge Scheduler (rate 1 minute)
          |
          v
SQS Standard: dispatch_due_events v1 envelope
          |
          v
long-polling worker --bounded due-ID query--> SQS delivery v1 envelopes
          |                                      |
          +<-------------------------------------+
          |
          +--atomic row claim--> authoritative ScheduledEvent + new attempt
          +--outside transaction--> weather, render, demo/Twilio sender
          +--atomic finalize--> suppressed / submitted / failed
```

Key files:

- `apps/scheduling/models.py`
- `apps/delivery/models.py`
- `apps/delivery/services.py`
- `apps/delivery/gateways.py`
- `apps/delivery/twilio_sms.py`
- `apps/delivery/twilio_voice.py`
- `apps/delivery/twilio_webhooks.py`
- `apps/weather/providers.py`

### Current transaction behavior

The delivery service locks the event row while claiming it, moves it from `scheduled` to `processing`, and creates an attempt. External work occurs after that transaction commits, so database locks are not held during provider calls. Final success or failure is recorded in a second transaction.

`dispatch_due_events` orders due rows by scheduled time and ID, locks at most the configured batch size with PostgreSQL `SKIP LOCKED`, and then executes each claimed delivery independently. The default batch size is 25 and the hard maximum is 100. Events strictly more than the default 15-minute grace window late are failed as missed inside the claim transaction and never call a provider. Cancellation and claiming lock the same event row, giving one legal winner under concurrency.

Terminal events return their latest attempt when delivery is requested again. This supplies basic idempotent behavior for duplicate invocations. A concurrent event already in `processing` is rejected rather than delivered twice.

### Current failure behavior

The direct dispatcher still makes all execution exceptions terminal. The SQS worker automatically retries only exceptions explicitly classified retryable before the sender boundary, currently transient WeatherAPI failures. Each failed receive creates an immutable failed attempt while the event remains `processing`; a later receive creates the next attempt under the same row lock. The third failed receive marks the event failed and leaves the message for DLQ redrive. Permanent pre-send failures are audited and acknowledged.

Once execution enters `MessageSender`, every exception is terminal and acknowledged even if its exception class otherwise advertises retryability. A timeout may hide provider acceptance, so replay could duplicate an SMS or call. A crash with a processing attempt remains quarantined and eventually surfaces through the DLQ alarm rather than being automatically replayed.

### Current configuration

- Split development and production settings
- PostgreSQL through `DATABASE_URL`
- SQLite fallback only when development has no database URL
- Docker Compose with Django and PostgreSQL
- Console logging suitable for later ECS forwarding
- Django Admin registration for current entities
- `GET /health/` performs a lightweight process health check
- Production accepts either `DATABASE_URL` or discrete PostgreSQL settings so ECS can inject the RDS-managed password as a Secrets Manager JSON key without putting a resolved password in a task definition

### Deployment artifacts

Phase 10 CloudFormation is deployed as a bounded staging environment in `us-east-1`:

- `phase10-ecr.yaml` creates an encrypted immutable ECR repository with scan-on-push, bounded image retention, and an SNS alarm topic with an optional email subscription.
- `phase8-queue.yaml` retains the queue/DLQ/Scheduler boundary and can route both queue alarms to the shared topic.
- `phase10-application.yaml` creates a two-AZ VPC layout, public ALB, private Fargate web/worker/migration tasks, private RDS PostgreSQL, Secrets Manager configuration, retained CloudWatch logs, basic alarms, and optional Route 53 alias.

The same immutable image is used for all task definitions. The web command runs Gunicorn, the worker command runs the existing queue worker, and migration is an explicit one-shot task. Task IAM roles separate web access from the worker's queue permissions. The ECS execution role can read only the generated application secret and the RDS-managed database secret.

ALB target checks use a task private IP in the HTTP `Host` header. Narrow first middleware answers only `/health/` requests carrying ALB's documented `ELB-HealthChecker/2.0` user agent, before Django host and HTTPS enforcement. Normal application traffic still uses the configured public-domain allowlist and TLS redirect; the target group accepts only HTTP 200.

The initial zero-capacity rollout completed after secret configuration and a successful one-off migration. Web and worker now run at one task each. After the SNS email subscription was confirmed and test delivery succeeded, the one-minute Scheduler was enabled while real worker delivery remained false. An automatic tick verified that due demo events traverse SQS and become suppressed without provider SIDs; due real events remained untouched. One NAT Gateway is an explicit staging cost tradeoff and a single-AZ outbound dependency; RDS Multi-AZ and deletion protection remain off for this staging deployment.

### Authenticated event API

The minimal DRF surface is mounted under `/api/`:

| Method | Path | Behavior |
| --- | --- | --- |
| `GET` | `/api/events/` | Paginated, scheduled-time-ordered events owned by the authenticated user |
| `POST` | `/api/events/` | Create one future demo event for an owned verified phone record |
| `GET` | `/api/events/{id}/` | Retrieve one owned event |
| `POST` | `/api/events/{id}/reschedule/` | Row-locked future-time change from `scheduled` only |
| `POST` | `/api/events/{id}/channel/` | Row-locked SMS/Voice change from `scheduled` only |
| `POST` | `/api/events/{id}/cancel/` | Row-locked cancellation from `scheduled` only |

Basic authentication is first so unauthenticated API requests receive `401`; session authentication also supports the Django-admin/browser context. Basic authentication is limited to this exercise/testing surface and requires TLS. A production deployment must explicitly choose session-based browser access or a managed/token authentication design. There is no registration or login-token issuance endpoint.

Serializers expose lifecycle timestamps, status, channel, ZIP code, demo state, and phone record ID. Full phone numbers, rendered announcements, weather audit payloads, delivery attempts, and provider identifiers are outside this user-facing representation. Creation is delegated to a transactional application service that re-locks and revalidates the verified phone immediately before saving. Dedicated reschedule, channel-change, and cancellation services reload the owned event under a row lock and reject any state other than `scheduled`. Only the requested time or channel is saved; no provider call, attempt, or lifecycle transition occurs.

Django Admin retains operational visibility with lifecycle fields read-only. Its controlled bulk-cancel action calls the cancellation service and skips events that are no longer scheduled rather than assigning statuses directly.

### Authenticated phone API

| Method | Path | Behavior |
| --- | --- | --- |
| `GET` | `/api/phones/` | Paginated phone records owned by the authenticated user |
| `POST` | `/api/phones/` | Enroll one unverified E.164 phone record |
| `POST` | `/api/phones/{id}/verification/start/` | Start an SMS Verify challenge for one owned unverified phone |
| `POST` | `/api/phones/{id}/verification/check/` | Check a 4–10 digit code and mark the phone verified only on approval |

Phone enrollment accepts the full number as a write-only value. Representations return only a masked number, local verification state, and audit timestamps. The verification actions return normalized status and safe phone metadata without codes, provider SIDs, raw payloads, or credentials. Duplicate enrollment uses the same generic validation error whether the existing globally unique number belongs to the caller or another user.

The views delegate ownership and verification behavior to application services and construct the Twilio adapter only after an owned unverified record is established. Provider calls remain outside database transactions; an approved check then row-locks the phone before setting `verified_at`. Separate cache-backed DRF throttle scopes default to `3/hour` for starts and `10/hour` for checks. These throttles are approximate and process-local with the current cache configuration.

### Server-rendered user application

The minimal Django UI is mounted at `/`, `/phones/`, and `/events/`, with built-in session login/logout at `/login/` and `/logout/`. App-owned forms and views remain thin: they validate browser input, enforce owner-scoped lookup, and delegate creation, verification, rescheduling, channel changes, and cancellation to the same application services used by the APIs.

All mutations are POST-only and protected by Django CSRF middleware. Event forms deliberately use explicit-offset ISO 8601 text instead of browser-local `datetime-local` interpretation. Templates render masked phone metadata and event lifecycle fields but omit delivery attempts, rendered announcements, weather snapshots, provider SIDs, and raw payloads. A shared responsive stylesheet supplies the small accessible interface without a JavaScript application or frontend build chain. Ordinary users receive Events and Phones navigation; `is_staff` users additionally receive an explicit Admin link.

## Service Boundaries

### Weather provider

Input: five-digit ZIP code.

Output: normalized `CurrentWeather` containing location, Fahrenheit temperature, condition, and observation time.

The application must not depend on a vendor's raw JSON schema.

The current real adapter uses [WeatherAPI.com's documented `/current.json` endpoint](https://www.weatherapi.com/docs/) because it accepts a US ZIP directly. It sends bounded connect and read timeouts, maps successful responses into `CurrentWeather`, and translates vendor/network failures into project-owned exceptions:

- location not found
- authentication or access failure
- rate limit or quota exhaustion
- timeout
- provider unavailable
- malformed response

Exceptions expose a `retryable` classification for future worker policy. The current delivery service records all errors as terminal failures; it does not consume that classification yet. A separate `check_weather` command provides an opt-in credentialed smoke test without coupling the real adapter to demo delivery.

### Phone verification gateway

Input: an E.164 phone number for starting a challenge, or an E.164 phone number and user-supplied code for checking one.

Output: a project-owned result with `pending`, `approved`, or `rejected` status and an optional provider SID.

The current adapter uses [Twilio Verify's Verification and Verification Check resources](https://www.twilio.com/docs/verify/api/verification). Twilio owns OTP generation, challenge state, expiry, and attempt counters. The application stores no OTP and marks `verified_at` only after an `approved` check. Provider errors are translated into safe project exceptions for invalid input, blocked attempts, expiry, authentication, rate limits, timeouts, availability, and malformed responses.

Application services call the gateway outside database transactions. After approval, the service locks the phone row and verifies that its number has not changed before setting `verified_at`. Owner-scoped service entry points hide cross-user records before adapter calls and make checks idempotent after verification.

### Message sender

Input: channel, E.164 destination, and rendered message.

Output: a small project-owned result containing an optional provider identifier.

The current `TwilioSmsSender` and `TwilioVoiceSender` support their respective channels. Both build a Twilio client with a bounded HTTP timeout, use environment-configured sender numbers, validate the returned provider SID, and map it into `DeliveryResult`. The Voice adapter generates escaped inline `<Say>` and one-digit `<Gather>` TwiML, so Twilio does not need a separate public endpoint to retrieve announcement text. Twilio SDK response objects remain inside adapters.

Adapter failures become project-owned errors for invalid destinations or requests, authentication or configuration problems, rate limiting, provider rejection, timeout/network failures, temporary unavailability, and malformed responses. Safe success logs contain only a masked destination and provider SID; message bodies, credentials, full phone numbers, and raw provider responses are excluded.

Twilio SDK logging is pinned to `WARNING` so its request/response diagnostics cannot emit account identifiers, request parameters, or raw provider responses through the project console logger.

`send_staging_sms_event` is the only current real-SMS executable path. It is disabled by default and requires `TWILIO_SMS_SMOKE_ENABLED=true`, an authorized `TWILIO_SMS_SMOKE_TO_NUMBER` matching the event, and `--confirm-send`. It rejects demo and voice events before constructing the Twilio adapter and uses deterministic fake weather so the smoke test isolates SMS submission.

`send_staging_voice_event` applies equivalent separate controls with `TWILIO_VOICE_SMOKE_ENABLED`, `TWILIO_VOICE_SMOKE_TO_NUMBER`, and `--confirm-call`. It rejects demo and SMS events before adapter construction.

### Local due-event dispatcher

`dispatch_due_events` processes one bounded batch. It uses deterministic fake weather and is demo-only unless `DELIVERY_REAL_DISPATCH_ENABLED=true` and `--allow-real-delivery` are both supplied. The real sender is lazy and channel-aware, so demo-only runs cannot construct or call a Twilio adapter. Output and failure logs contain event identifiers, counts, and exception classes only.

`seed_scheduling_scenarios` safely replaces only a reserved seed user's data with exactly 30 events spanning due, future, missed, terminal, cancelled, and stale-processing states; both channels; and demo versus real safety behavior. Its phone number and provider identifiers are synthetic.

### SQS and scheduler boundary

`QueueEnvelope` version 1 supports a scheduler tick and an identifier-only event delivery message. Strict parsing rejects unknown versions, types, extra fields, and invalid identifiers. Message bodies and receipt handles are never logged.

`SqsDeliveryQueue` keeps boto3 objects and AWS error details inside the adapter. It applies bounded connect/read timeouts, receives at most ten messages with up to 20 seconds of long polling, maps AWS failures to safe project errors, deletes acknowledged messages, and changes visibility for retries.

`run_delivery_worker` long-polls continuously or once with `--once`. A scheduler tick publishes one bounded oldest-first set of IDs. Publication does not claim or mutate events, eliminating a claimed-before-publish loss window; a failed or repeated tick can publish duplicates, which worker row locking handles. Real queue delivery requires `DELIVERY_REAL_WORKER_ENABLED=true` and `--allow-real-delivery`.

`infra/aws/phase8-queue.yaml` defines the deployed Standard queue, encrypted 14-day DLQ, three-receive redrive policy, 120-second visibility timeout, 20-second long polling, disabled one-minute EventBridge Scheduler, least-privilege scheduler role, and CloudWatch alarms for DLQ depth and queue age. It remains a focused transport stack separate from ECS/RDS.

### Voice status callbacks

`POST /twilio/voice/status/` is a narrow provider endpoint, not a user-facing API. CSRF is replaced by Twilio signature validation using the configured canonical HTTPS callback URL and auth token. Invalid signatures fail before payload processing. Accepted form fields are limited to Call SID, Call Status, and Sequence Number; raw bodies and phone-number callback fields are neither stored nor logged.

The callback service locks the submitted voice attempt by Call SID. Newer sequence numbers advance its normalized provider status; duplicates, older callbacks, and changes after a terminal provider outcome are no-ops. An unknown SID returns `404` so Twilio can retry if a callback raced the database commit that stores the Call SID. Callback processing does not alter the event's local `submitted` state.

### Voice DTMF actions

`POST /twilio/voice/action/` is a separate Twilio-signed provider endpoint with its own canonical HTTPS URL. It accepts only Call SID and one gathered digit. The Call SID resolves a submitted Voice attempt and therefore its trusted owner; no ownership or target identifier is accepted from the request.

The action service locks the attempt, returns any previously recorded result, then locks the owner’s earliest `scheduled` event. Digit `1` delegates to the existing cancellation service and digit `2` delegates to the existing channel-change service. The event mutation and attempt audit marker commit atomically. Concurrent duplicates serialize on the attempt row, so one action applies and later callbacks return its result. Invalid input receives bounded TwiML without mutation; unknown or stale calls receive a non-sensitive terminal prompt.

### Inbound SMS controls

`POST /twilio/sms/inbound/` is a Twilio-signed provider endpoint with a configured canonical HTTPS URL. It accepts only the provider Message SID, inbound sender, configured Twilio recipient, message body, and optional Advanced Opt-Out classification needed for processing. The verified inbound sender resolves the owner; request-supplied user and event identifiers are neither accepted nor trusted.

The command grammar is limited to `STOP`, `SMS`, and `TIME <ISO-8601-with-offset>`. The application locks the provider-SID audit row and the owner’s earliest still-`scheduled` event, then delegates cancellation, channel change, or rescheduling to the Phase 11 services. The provider SID has a database uniqueness constraint, so sequential and concurrent conflicting retries return the first normalized result without a second mutation.

Responses are short Messaging TwiML and do not echo event data, provider identifiers, phone numbers, or message bodies. Unknown and unverified senders are indistinguishable. Message bodies, full phone numbers, raw requests, and secrets are not stored or logged. If Twilio reports `OptOutType=STOP`, the response contains no `<Message>` because Twilio has already generated the compliance reply; the local event cancellation remains independent of Twilio opt-out state.

### Delivery service

Responsible for scanning and claiming one bounded due-event batch, retrieving weather, rendering announcements, selecting the demo or real sender, and recording attempts. It is not responsible for polling queues or configuring vendor clients.

## Target Deployment Architecture — Artifacts Implemented, Not Deployed

```text
Browser / API client
        |
       ALB
        |
Django web tasks ----------------------- RDS PostgreSQL
        |                                      |
        | create/cancel/verify                 | source of truth
        v                                      |
EventBridge minute tick ---> SQS ---> Fargate worker
                                      |       |
                                      |       +--> Weather REST API
                                      |
                                      +----------> Twilio SMS / Voice
                                                    |
Twilio status/action callbacks ---> Django web <---- Twilio inbound SMS

Container stdout/stderr --------------------> CloudWatch Logs
```

Deployment artifacts cover:

- Django web Fargate service behind an Application Load Balancer
- Worker Fargate service using the same image with a different command
- RDS PostgreSQL as the source of truth
- Deployment of the implemented SQS Standard queue, dead-letter queue, alarms, and Scheduler tick
- Real weather REST adapter with bounded timeouts (implemented locally; deployment configuration remains planned)
- Twilio Verify, SMS, and Voice adapters (implemented locally)
- Authenticated Twilio Voice status callbacks (implemented locally)
- Authenticated Twilio inbound SMS controls (implemented locally)
- CloudWatch logs, basic metrics, and alarms
- Secrets Manager for application and RDS credentials

## Implemented Scheduling and Queue Design

PostgreSQL remains authoritative for schedules. EventBridge Scheduler emits a periodic tick rather than creating one schedule per event. The worker expands that tick into a bounded batch of event IDs without changing database state, so there is no database claim that can be stranded by a failed SQS publish.

SQS Standard is at-least-once. Delivery messages contain an event ID, message type, and schema version only. Workers reload and claim the event before provider calls. Duplicate publication is expected and safe at the database claim boundary.

A failed tick publication leaves events `scheduled`, so the next minute can try again. Partial publication may duplicate earlier IDs on the next tick. A transactional outbox remains deferred because this design favors safe duplication over a state-changing dual write.

## Known Delivery Ambiguity

A worker can crash after Twilio accepts a request but before the provider SID is committed. No local transaction can make that external side effect exactly once. The safe future policy is to mark or reconcile an unknown outcome rather than automatically place another voice call.

This limitation must be explained honestly in interviews and production documentation.

## Deployment Tradeoffs

- One repository and image; separate web and worker task commands
- Explicit migration task during deployment
- Private RDS
- Only the load balancer may reach web task ports
- Workers accept no inbound traffic
- Private Fargate tasks require outbound internet through NAT; a lower-cost demo deployment may use public IPs with strict security groups if documented

## Explicit Non-Goals

- Microservices
- Kubernetes
- Celery or Redis
- Recurring schedules
- Multi-region operation
- Multiple weather-provider failover
- Event sourcing
- User-authored voice markup or arbitrary callback URLs
