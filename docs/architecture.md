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
| `accounts` | Custom user, owned phone numbers, verification services, and Twilio Verify adapter |
| `scheduling` | One-time scheduled events and their lifecycle |
| `delivery` | Delivery attempts, announcement rendering, sender boundary, Twilio SMS/Voice adapters, authenticated callback handling, and synchronous orchestration |
| `weather` | Normalized weather value object, provider boundary, deterministic fake, and WeatherAPI.com REST adapter |

The current executable flow is synchronous. Demo events use the suppression sender; explicitly authorized staging commands can use Twilio senders for due non-demo events:

```text
demo / staging SMS / staging Voice command
          |
          v
delivery service --atomic claim--> ScheduledEvent + DeliveryAttempt
          |
          +--> WeatherProvider --> FakeWeatherProvider
          |
          +--> announcement renderer
          |
          +--> DemoMessageSender --> masked console log
          |    or TwilioSmsSender --> Twilio Messages API
          |    or TwilioVoiceSender --> Twilio Calls API
          |
          +--atomic finalize--> suppressed or submitted attempt and event
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

Terminal events return their latest attempt when delivery is requested again. This supplies basic idempotent behavior for duplicate invocations. A concurrent event already in `processing` is rejected rather than delivered twice.

### Current failure behavior

All provider or rendering exceptions currently mark the event and attempt as `failed` and re-raise the exception. Retry classification and stuck-processing recovery do not exist yet. This is adequate for the fake synchronous slice but must be revisited before queue processing.

### Current configuration

- Split development and production settings
- PostgreSQL through `DATABASE_URL`
- SQLite fallback only when development has no database URL
- Docker Compose with Django and PostgreSQL
- Console logging suitable for later ECS forwarding
- Django Admin registration for current entities
- `GET /health/` performs a lightweight process health check

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

Application services call the gateway outside database transactions. After approval, the service locks the phone row and verifies that its number has not changed before setting `verified_at`.

### Message sender

Input: channel, E.164 destination, and rendered message.

Output: a small project-owned result containing an optional provider identifier.

The current `TwilioSmsSender` and `TwilioVoiceSender` support their respective channels. Both build a Twilio client with a bounded HTTP timeout, use environment-configured sender numbers, validate the returned provider SID, and map it into `DeliveryResult`. The Voice adapter generates escaped inline `<Say>` TwiML, so Twilio does not need a separate public endpoint to retrieve announcement text. Twilio SDK response objects remain inside adapters.

Adapter failures become project-owned errors for invalid destinations or requests, authentication or configuration problems, rate limiting, provider rejection, timeout/network failures, temporary unavailability, and malformed responses. Safe success logs contain only a masked destination and provider SID; message bodies, credentials, full phone numbers, and raw provider responses are excluded.

Twilio SDK logging is pinned to `WARNING` so its request/response diagnostics cannot emit account identifiers, request parameters, or raw provider responses through the project console logger.

`send_staging_sms_event` is the only current real-SMS executable path. It is disabled by default and requires `TWILIO_SMS_SMOKE_ENABLED=true`, an authorized `TWILIO_SMS_SMOKE_TO_NUMBER` matching the event, and `--confirm-send`. It rejects demo and voice events before constructing the Twilio adapter and uses deterministic fake weather so the smoke test isolates SMS submission.

`send_staging_voice_event` applies equivalent separate controls with `TWILIO_VOICE_SMOKE_ENABLED`, `TWILIO_VOICE_SMOKE_TO_NUMBER`, and `--confirm-call`. It rejects demo and SMS events before adapter construction.

### Voice status callbacks

`POST /twilio/voice/status/` is a narrow provider endpoint, not a user-facing API. CSRF is replaced by Twilio signature validation using the configured canonical HTTPS callback URL and auth token. Invalid signatures fail before payload processing. Accepted form fields are limited to Call SID, Call Status, and Sequence Number; raw bodies and phone-number callback fields are neither stored nor logged.

The callback service locks the submitted voice attempt by Call SID. Newer sequence numbers advance its normalized provider status; duplicates, older callbacks, and changes after a terminal provider outcome are no-ops. An unknown SID returns `404` so Twilio can retry if a callback raced the database commit that stores the Call SID. Callback processing does not alter the event's local `submitted` state.

### Delivery service

Responsible for claiming an event, retrieving weather, rendering the announcement, selecting the demo or real sender, and recording an attempt. It is not responsible for scanning due events, polling queues, or configuring vendor clients.

## Target Architecture — Planned

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
Twilio status callback ---> Django web <------------+

Container stdout/stderr --------------------> CloudWatch Logs
```

Planned components:

- Django web Fargate service behind an Application Load Balancer
- Worker Fargate service using the same image with a different command
- RDS PostgreSQL as the source of truth
- SQS Standard queue with a dead-letter queue
- EventBridge minute tick to initiate due-event dispatch
- Real weather REST adapter with bounded timeouts (implemented locally; deployment configuration remains planned)
- Twilio Verify, SMS, and Voice adapters (implemented locally)
- Authenticated Twilio Voice status callbacks (implemented locally)
- CloudWatch logs, basic metrics, and alarms
- Secrets Manager or Parameter Store for secrets

## Planned Scheduling and Queue Design

PostgreSQL remains authoritative for schedules. EventBridge should emit a periodic tick rather than create one AWS schedule per event. A dispatcher will atomically claim due rows in bounded batches and publish event IDs to SQS.

SQS Standard is at-least-once. Messages should contain an event ID and message type only. Workers must reload and claim the event before provider calls.

A database-to-SQS dual-write gap will remain in the simple design. Duplicate publication is acceptable because delivery is idempotent. A transactional outbox is deferred unless the exercise explicitly demands stronger publication guarantees.

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
