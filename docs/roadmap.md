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

## Completed Twilio SMS Boundary

- Twilio SMS sender behind `MessageSender`
- Environment-driven sender number and bounded HTTP timeout
- Validated Twilio Message SID mapped into `DeliveryResult` and persisted by orchestration
- Project-owned, retry-classified configuration, request, rejection, rate-limit, timeout, availability, and malformed-response errors
- Safe masked success logging
- Disabled-by-default staging smoke command restricted to an explicitly authorized number
- Demo-event sender selection remains in application orchestration; smoke command rejects demo events before adapter construction

## Completed Twilio Voice and Callback Boundary

- Voice sender behind `MessageSender` with bounded Twilio client timeout
- Escaped inline TwiML and validated Call SID mapping
- All call-progress events submitted to the configured callback URL
- Authenticated, POST-only Twilio Voice status endpoint
- Normalized provider-status fields separate from local attempt/event status
- Sequence-based duplicate and out-of-order callback handling
- Terminal provider outcomes cannot regress
- Disabled-by-default staging voice command restricted to an authorized number
- Mocked adapter, service, callback, model, and staging-command tests

DTMF and speech interaction remain deferred.

## Completed Local Due-Event Dispatcher

Goal: prove scheduling behavior before introducing AWS.

- Bounded, oldest-first due-event selection with a hard batch maximum
- PostgreSQL `SKIP LOCKED` claiming and cancellation race coverage
- Configurable missed-event grace window that fails without provider calls
- Demo-only default with two explicit gates before real provider delivery
- Stale-processing quarantine design that avoids unsafe automatic replay
- Repeatable thirty-event seed command covering channels, times, statuses, and demo behavior
- Functional SQLite coverage and concurrency tests run against PostgreSQL

## Completed SQS Worker and EventBridge Tick

Goal: move the proven dispatcher and delivery invocation onto AWS-compatible asynchronous transport.

- Strict versioned tick and identifier-only delivery envelopes
- Bounded non-claiming publication that safely tolerates duplicates
- SQS adapter with bounded HTTP behavior and safe project-owned errors
- Long-polling worker with row-locked authoritative claims
- Three-receive retry policy limited to pre-sender transient failures
- Standard queue, encrypted DLQ, disabled-by-default one-minute Scheduler tick, and least-privilege scheduler role
- CloudWatch alarms for DLQ depth and oldest-message age
- Functional SQLite coverage and duplicate-worker concurrency coverage on PostgreSQL

Celery, Redis, per-event schedules, transactional outbox, and ambiguous provider replay remain excluded.

## Completed User/API Surface

Goal: expose only the workflows required by the exercise.

- DRF Basic and session authentication with authenticated-by-default permissions
- Paginated, ordered, user-owned event list and retrieval
- Demo-only event creation using an owned verified phone record ID
- Explicit-offset datetime input normalized to UTC
- Dedicated row-locked cancellation action with conflict responses for illegal states
- Cross-user objects consistently hidden with `404`
- Read-only lifecycle fields and no general update/delete endpoint
- Controlled Django Admin cancellation through the same service boundary
- Authentication, ownership, validation, state, privacy, and method tests

Registration, token issuance, and broad account APIs remain deferred. Phone verification and the bounded server-rendered workflow are completed in later slices below.

## Phase 10: AWS Deployment Artifacts — Staging Operational, Security Handoff Pending

Goal: deploy the existing image and commands without changing business behavior.

Scope:

- ECR
- Fargate web and worker services
- RDS PostgreSQL
- ALB, health check, and TLS
- Deploy and integrate the Phase 8 SQS, DLQ, alarms, and EventBridge Scheduler resources
- Secrets Manager or Parameter Store
- Explicit migration task
- CloudWatch log groups, retention, metrics, and basic alarms
- Least-privilege IAM and network rules

Implemented artifacts include an immutable/scanned ECR repository, shared SNS alarm topic, the Phase 8 queue stack with optional alarm actions, and an ECS/RDS/ALB application stack. The application stack supplies distinct web, worker, and migration task definitions from one image; private application and database subnets; TLS-only application traffic; RDS-managed credentials; JSON-key Secrets Manager injection; retained logs; and basic alarms. Web and worker desired counts default to zero, real worker delivery defaults off, and the Scheduler remains disabled for the initial rollout.

The operator runbook in `docs/deployment.md` was followed through image publication, queue creation, zero-capacity infrastructure deployment, secret configuration, migration, health verification, service startup, alarm-email confirmation, and both manual and automatic demo-only SQS exercises. The public TLS health endpoint and ALB target are healthy, web/worker services are steady, the one-minute Scheduler is enabled, and real worker delivery remains false. Bootstrap IAM permission narrowing and an explicit ongoing-cost/teardown decision remain operations handoff items.

## Completed Pending-Event Changes

Goal: let an authenticated owner change the next pending wake-up time or contact method without weakening delivery concurrency guarantees.

- Dedicated owner-scoped, row-locking services reschedule a `scheduled` event or switch its channel between `sms` and `voice`.
- Dedicated API actions preserve the read-only detail resource and return `404` for hidden identifiers, `400` for invalid input, and `409` for lifecycle conflicts.
- Rescheduled datetimes require an explicit offset and a strictly future value, then normalize to UTC.
- Destination, ZIP code, demo state, lifecycle fields, and delivery attempts remain unchanged.
- Functional tests cover validation, ownership, and terminal states; PostgreSQL tests prove that races with cancellation and delivery claiming serialize into legal authoritative outcomes.

## Completed Phone Enrollment and Verification API

Goal: expose the existing Twilio Verify boundary as a safe authenticated ownership workflow.

- Owner-scoped phone enrollment and paginated listing expose masked metadata only.
- Dedicated verification-start and verification-check actions use the existing services and Twilio Verify adapter boundary.
- Separate per-user throttle scopes bound challenge starts and code checks.
- Duplicate numbers, already-verified phones, rejected or expired challenges, provider errors, and ownership boundaries have safe, tested responses.
- Full phone numbers and codes are write-only; provider SIDs, credentials, and raw payloads remain outside API representations.

## Completed Minimal User Application

Goal: satisfy scheduling "using the app" with a small server-rendered Django workflow rather than a separate frontend stack.

- Existing users can sign in and out through Django sessions; registration remains absent.
- Responsive server-rendered pages cover phone enrollment/verification and event list/create/detail.
- Reschedule, cancel, and SMS/Voice controls call the same application services as the APIs.
- CSRF, owner scoping, accessible errors, explicit-offset/UTC guidance, masking, and omission of delivery/provider internals have focused coverage.
- Ordinary users receive only user workflows; staff receive an explicit path to the existing controlled Admin.

## Phase 14: Voice DTMF Interaction — Next

Goal: satisfy the call-interaction requirement with one bounded DTMF flow; speech recognition remains optional.

Before implementation, document the exact meaning of "next scheduled time" for the one-time-event model. Prefer a small menu with fixed, auditable behavior rather than open-ended speech parsing. Candidate actions are a fixed snooze that creates a new future demo event, cancellation of the owner's next pending event, and switching that next event to SMS.

Scope after that decision:

- Generate TwiML using `<Gather>` with a bounded timeout and digit count.
- Add a dedicated HTTPS action webhook with Twilio signature validation.
- Resolve actions through an attempt/event identifier and row-locked application services; never trust caller-supplied ownership data.
- Make duplicate webhook deliveries idempotent and return safe TwiML for invalid or stale actions.
- Keep demo events away from real provider adapters and add mocked callback tests before any live smoke.

Exit criteria: one authorized live or Twilio-provided test call can exercise the documented DTMF menu, and retries cannot apply an action twice. Do not add speech recognition or inbound call scheduling.

## Phase 15: Inbound SMS Controls — Planned

Goal: give the text-message path equivalent bounded controls if the assignment is interpreted to require replies from both communication methods.

Scope:

- Add a Twilio-signed inbound SMS webhook.
- Define a deliberately small command grammar for stop, channel switching, and changing the next pending time.
- Resolve the user from the verified destination safely, apply changes through the Phase 11 services, and make duplicate provider requests idempotent.
- Return short, non-sensitive TwiML responses and avoid logging message bodies or full phone numbers.

Exit criteria: mocked signed requests cover every command, invalid input, unknown/unverified senders, duplicates, and lifecycle conflicts. A real inbound SMS smoke remains optional and compliance-gated.

## Phase 16: Submission and Operations Polish — Planned

Goal: make the implemented behavior easy to review, demonstrate, and safely operate.

Scope:

- Add a concise demo script covering ordinary-user ownership, Admin behavior, verification, scheduling changes, weather auditing, demo suppression, SQS processing, and CloudWatch evidence.
- Correct all as-built documentation and clearly identify provider/compliance limitations.
- Decide whether an authorized Voice live smoke adds useful evidence; do not make it a prerequisite for deterministic automated tests.
- Replace the bootstrap IAM user's direct `AdministratorAccess` with bounded deployment/operator permissions.
- Document scale-to-zero and teardown choices for NAT, ALB, ECS, and RDS costs.
- Re-run the complete SQLite, PostgreSQL, Docker, template, and deployed health validation set.

Exit criteria: a reviewer can build locally, understand the architecture and tradeoffs, run the deterministic demo, inspect the live demo environment, and distinguish provider submission from final delivery.

## Deferred Unless Time Remains

- Recurring schedules and DST policy
- Speech recognition
- Optional inbound-call scheduling
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
