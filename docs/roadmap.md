# Implementation Roadmap

## Guiding Rule

Complete one independently testable vertical slice at a time. External services and AWS infrastructure should transport or implement an already-working application workflow; they should not define the domain behavior.

## Completed Foundation

- Django 5.2 project with split settings
- Four local applications
- Custom user model
- PostgreSQL and SQLite development configuration
- Docker Compose web and database services
- Console logging
- Django Admin foundation
- Lightweight health endpoint
- Pytest and Ruff setup

## Completed Domain and Demo Workflow

- E.164 phone model with verification state
- One-time scheduled event model and state transitions
- Delivery-attempt audit model
- Normalized weather provider protocol and deterministic fake
- Announcement renderer
- Message-sender protocol and logging-only demo sender
- Transactional synchronous delivery orchestration
- Duplicate-aware terminal behavior
- Manual `deliver_demo_event` command

## Completed Real Weather Adapter

- WeatherAPI.com current-weather adapter behind `WeatherProvider`
- US ZIP query mapped into normalized `CurrentWeather`
- Environment-driven API key, base URL, and connect/read timeouts
- Project-owned errors for location, authentication, rate limit, timeout, availability, and malformed responses
- Retryability metadata for future queue policy
- Mocked HTTP tests that do not require credentials or network access
- Manual `check_weather ZIP_CODE` smoke command

## Completed Twilio Verify Boundary

- Project-owned verification gateway and result statuses
- Twilio Verify start and verification-check adapter
- Environment-driven account, token, service SID, and timeout
- Start and check application services
- Local verification-code format validation
- `verified_at` update only after provider approval
- Idempotence for already verified phones
- Protection against a number changing during verification
- Safe error mapping for invalid, blocked, expired, authentication, rate-limit, network, and provider failures
- Mocked service and adapter tests; no OTP persistence

Authenticated user endpoints and application-level request throttling remain part of the later user/API surface.

## Phase 5: Twilio SMS — Next

Goal: submit non-demo SMS events through the existing delivery service.

Scope:

- SMS sender adapter
- Provider configuration and secrets
- Provider SID persistence
- Timeout and error mapping
- Safe logging
- Staging smoke test using an authorized number

Keep demo-event selection inside the application orchestration so demo events cannot accidentally reach Twilio.

## Phase 6: Twilio Voice and Callbacks

Goal: add voice as a second delivery adapter and track asynchronous provider results.

Scope:

- Voice adapter and safe TwiML generation or endpoint
- Twilio signature validation
- Status callback endpoint
- Idempotent and out-of-order callback mapping
- Distinguish `submitted` from provider delivery outcome

DTMF or speech interaction is optional follow-up scope and should not block basic voice delivery.

## Phase 7: Local Due-Event Dispatcher

Goal: prove scheduling behavior before introducing AWS.

Scope:

- Management command to find due events in bounded batches
- PostgreSQL-safe row claiming
- Missed-event grace-window policy
- Cancellation race tests
- Stale-processing recovery design
- Thirty-event seed command covering channels, times, statuses, and demo behavior

Run concurrency tests against PostgreSQL.

## Phase 8: SQS Worker and EventBridge Tick

Goal: move the proven dispatcher and delivery invocation onto AWS-compatible asynchronous transport.

Scope:

- SQS Standard queue and DLQ
- Small versioned message envelope containing event IDs
- Long-polling worker command
- Visibility timeout and bounded retry behavior
- EventBridge minute tick
- Queue metrics and DLQ alerting plan

Do not add Celery, Redis, or one EventBridge schedule per user event.

## Phase 9: User/API Surface

Goal: expose only the workflows required by the exercise.

Scope:

- Authentication and user-owned event queries
- Create, list, retrieve, and cancel operations
- Django REST Framework only where an external REST API is actually required
- Administrator visibility and controlled actions
- Object ownership and authorization tests

Clarify whether “external REST API” means the weather dependency or a public application API before expanding this phase.

## Phase 10: AWS Deployment

Goal: deploy the existing image and commands without changing business behavior.

Scope:

- ECR
- Fargate web and worker services
- RDS PostgreSQL
- ALB, health check, and TLS
- SQS, DLQ, and EventBridge
- Secrets Manager or Parameter Store
- Explicit migration task
- CloudWatch log groups, retention, metrics, and basic alarms
- Least-privilege IAM and network rules

## Deferred Unless Time Remains

- Recurring schedules and DST policy
- DTMF or speech interaction
- Multiple phone numbers in the user interface
- Weather caching
- Transactional outbox
- Automatic provider reconciliation
- OpenTelemetry
- Advanced autoscaling
- Multi-provider failover
- Rich frontend

## Interview Review Topics

- At-least-once processing and duplicate resistance
- External-call/database ambiguous outcomes
- Row-lock and cancellation races
- UTC storage and future user timezone conversion
- Why SQS instead of Celery and Redis
- Why a periodic database dispatcher instead of per-event AWS schedules
- Why `submitted` does not mean delivered
- Demo safety and auditable suppression
- PostgreSQL versus SQLite test coverage
