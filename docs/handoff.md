# Current Handoff

Last updated: 2026-07-15

## Current State

The architecture source of truth now includes an evidence-backed Mermaid suite covering the complete system, responsibility boundaries, six internal service views, primary and background workflows, four external-integration views, persistence, lifecycle, failures, and local/AWS runtime topology. The diagrams distinguish implemented, demo-only, external-limitation, and documented-but-not-implemented behavior and trace their claims to code, tests, design documents, infrastructure templates, and relevant commits. No application behavior changed in this documentation slice.

The project has a working Django foundation, domain models, a bounded local due-event dispatcher, a versioned SQS worker boundary, a real WeatherAPI.com adapter, a Twilio Verify boundary, and Twilio SMS and Voice senders behind `MessageSender`. Non-demo submissions map validated Twilio Message or Call SIDs into the existing `DeliveryResult` and delivery attempt. Provider SDK objects and raw responses remain inside their adapters.

Phase 9 adds a minimal authenticated DRF event API. Basic and session authentication protect every `/api/events/` operation. Users can create demo events for their own verified phone record, list and retrieve only their events, and cancel an owned `scheduled` event through the row-locking service. Cross-user identifiers return `404`; lifecycle conflicts return `409`. Creation requires an explicit ISO 8601 offset and normalizes to UTC. API representations expose a phone record ID rather than a full number and omit attempts, rendered messages, weather audit payloads, and provider identifiers.

Phase 11 adds dedicated owner-scoped actions to reschedule a pending event and switch it between SMS and Voice. Both services reload the authoritative event under a row lock, accept only `scheduled` events, and save only the requested field. Rescheduling requires a strictly future datetime with an explicit offset and normalizes it to UTC. The actions return `404` for missing or cross-owner IDs, `400` for invalid payloads, and `409` for lifecycle conflicts. They do not create attempts, change lifecycle state, or involve provider calls.

Phase 12 adds authenticated phone enrollment, listing, verification-start, and verification-check endpoints. Enrollment accepts an E.164 number as write-only input and returns masked phone metadata. Verification actions are owner-scoped, expose only normalized status, and never return codes or provider SIDs. Duplicate numbers use the same safe validation response across owners; approved checks set `verified_at`, rejected checks leave it unset, and checks are idempotent after approval. Separate per-user DRF throttle scopes default to three starts and ten checks per hour.

Phase 13 adds a responsive server-rendered Django application for existing users. Session-authenticated pages cover phone enrollment/verification and event list/create/detail. Pending events can be rescheduled, switched between SMS and Voice, or cancelled through the same locking services used by the API. All browser mutations are POST-only and CSRF-protected. Schedule forms require an explicit ISO 8601 offset, phone data is masked, delivery/provider internals remain absent, and only staff receive an Admin navigation link.

Phase 14 adds a signed one-digit Voice menu after real announcements. `1` cancels the call owner’s earliest still-`scheduled` event and `2` switches it to SMS. The signed action webhook derives ownership from the submitted attempt’s Call SID, locks the attempt and target event, delegates to Phase 11 services, and records the digit, normalized result, target ID, and completion time atomically. Duplicate and concurrent callbacks return the stored result without applying another action; invalid, stale, and no-pending-event cases return safe TwiML.

Phase 15 adds a signed inbound SMS webhook with the bounded commands `STOP`, `SMS`, and `TIME <ISO-8601-with-offset>`. Ownership comes only from an exact verified inbound sender match, and commands target that owner’s earliest still-`scheduled` event by scheduled time and ID. Changes delegate to the Phase 11 services. A unique Message SID audit row makes sequential and PostgreSQL-concurrent conflicting callbacks idempotent without storing sender numbers or message bodies. Responses are short, non-sensitive Messaging TwiML. Advanced Opt-Out `STOP` callbacks apply local cancellation but return empty TwiML so Twilio’s compliance response is not duplicated.

Phase 10 is live in the staging AWS account in `us-east-1`. The `wakeup-call-staging-foundation`, `wakeup-call-staging-queue`, and `wakeup-call-staging-application` CloudFormation stacks create the immutable ECR repository, shared SNS alarm topic, encrypted SQS/DLQ transport, two-AZ VPC, public TLS ALB, private Fargate tasks, private encrypted RDS PostgreSQL, Secrets Manager configuration, retained log groups, and basic alarms. Cloudflare DNS routes `wakeupcall.afam.app` to the ALB, and the ACM certificate is issued.

Image commit `003844a3cbd4eee2b7a57c38d66055cd6ecc88b5` was built for `linux/amd64`, pushed under its full commit tag, and deployed to task-definition revisions web `3`, worker `3`, and migration `3`. The migration task completed with exit code 0 on image digest `sha256:027e639f71d38d2b1178eccd64fa79b040cc9cacd4c6d5d16e455639b5883070`. Web and worker are each steady at one running task, the ALB target is healthy, `https://wakeupcall.afam.app/health/` returns HTTP 200, and the production stylesheet returns HTTP 200 through WhiteNoise. Provider configuration is stored in the generated application secret; the SMS-capable Twilio number is also configured as the Voice sender. Separate active superuser and ordinary-user credentials are provisioned for the reviewer demo.

The SNS email subscription is confirmed and received an intentional non-sensitive test notification. The one-minute EventBridge Scheduler is enabled. An automatic tick processed the deterministic staging scenarios with the real-delivery gate still false: six due demo events became `suppressed`, four missed demo events failed locally, four due real events remained `scheduled`, and no demo attempt had a provider SID. The queue drained afterward and all five alarms remained `OK`.

All ECS task definitions use one immutable image. Web runs Gunicorn, worker runs the existing SQS command, and migration is an explicit one-shot task. The templates default web/worker capacity to zero, Scheduler to disabled, and real worker delivery to false; the verified staging rollout now runs web/worker at one task each with Scheduler enabled and real delivery still false. Production settings accept discrete database fields so ECS can inject only the RDS-managed password JSON key instead of resolving a credential into task-definition plaintext. `docs/deployment.md` sequences validation, image publication, queue deployment, zero-capacity application deployment, secret configuration, migration, demo verification, service start, and Scheduler enablement.

The ALB health check is handled by narrow first middleware only when the path is `/health/` and the documented ALB user agent is present. This permits the ALB's private-IP `Host` header without weakening the public-domain `ALLOWED_HOSTS` policy or treating redirects/errors as healthy.

The detail resource is read-only; there is no PUT, PATCH, or DELETE. Client-supplied `status` and `is_demo` cannot override server-owned values, so public creation remains demo-only. Lists are scheduled-time ordered and paginated at 50. Django Admin now has a controlled bulk-cancel action that calls the same locking service and leaves non-scheduled events unchanged.

Separate opt-in staging commands are the only current executable real-provider paths. They are disabled by default, require command-line confirmation and explicitly authorized destinations, reject demo and wrong-channel events before adapter construction, and use fake weather to isolate provider submission.

`dispatch_due_events` selects one oldest-first bounded batch and claims rows with PostgreSQL `SELECT ... FOR UPDATE SKIP LOCKED`. It is demo-only by default. Real batch delivery requires both `DELIVERY_REAL_DISPATCH_ENABLED=true` and `--allow-real-delivery`; its channel router constructs a Twilio adapter lazily. The default grace window is 15 minutes: older events become failed `MissedDeliveryWindow` attempts without weather or delivery provider calls. Each claimed delivery finalizes independently, and one failure does not stop the rest of the batch. Cancellation uses the same row lock, so cancellation and claiming have one legal winner.

`seed_scheduling_scenarios` replaces only a reserved seed user's records with an exact 30-event matrix spanning SMS/Voice, demo/real, due/future/missed, terminal, cancelled, and stale-processing states. Stale `processing` events are intentionally quarantined; automatic replay is unsafe because a crash after provider acceptance but before SID persistence has an ambiguous outcome.

Phase 8 adds strict version-1 queue envelopes for `dispatch_due_events` ticks and identifier-only `deliver_scheduled_event` work. The focused template configures EventBridge Scheduler to send one tick per minute to an SQS Standard queue when deployed and enabled. A long-polling worker expands each tick into one bounded oldest-first ID batch without changing event state, then reloads and row-locks each event before delivery. This deliberately permits duplicate publication while eliminating a claim-before-publish loss window.

The worker retries only explicitly retryable failures that occur before entering `MessageSender`. A retrying event remains `processing`, the failed attempt is immutable, and the original SQS message receives bounded exponential visibility delay. On the third receive the event becomes failed with `RetryExhausted:*` and the message remains for DLQ redrive. Permanent pre-send failures and all sender-boundary exceptions are audited and acknowledged; sender failures are never automatically replayed because provider acceptance may be ambiguous.

`infra/aws/phase8-queue.yaml` defines the deployed encrypted Standard queue and 14-day DLQ, three-receive redrive, 20-second long polling, 120-second visibility, a disabled-by-default one-minute EventBridge Scheduler, least-privilege scheduler IAM, and CloudWatch alarms for DLQ depth and oldest-message age. The queue remains a separate transport stack from the Phase 10 ECS/RDS/ALB environment.

`POST /twilio/voice/status/` validates Twilio signatures against the configured canonical HTTPS callback URL. It maps signed Call SID, Call Status, and Sequence Number fields into normalized attempt-level provider status. `POST /twilio/voice/action/` independently validates its canonical URL and maps Call SID plus one digit into the bounded action service. `POST /twilio/sms/inbound/` validates its own canonical URL and resolves ownership from the verified inbound sender. No callback trusts caller-supplied ownership or target identifiers.

The Phase 15 validation result is:

- `python manage.py check`: passed with a temporary SQLite override
- `python manage.py makemigrations --check`: passed after generating the intentional `delivery.0004` inbound-command audit migration
- `python manage.py migrate`: passed through `delivery.0004`
- `pytest`: 350 passed, 7 PostgreSQL-only tests skipped with a temporary SQLite override
- `docker compose run --rm web pytest`: 357 passed against PostgreSQL, including concurrent conflicting inbound-SMS idempotency
- `ruff check .`: passed
- `git diff --check`: passed

The earlier Phase 14 Docker, CloudFormation, staging health, alarm, and demo-only SQS validation remains unchanged; Phase 15 did not alter those artifacts or enable provider delivery.

Phase 16 wires and deploys the callback configuration. Docker Compose passes through the Voice action and inbound SMS callback URLs while retaining the SMS sender number. The deployed Phase 10 application task definitions derive both web callback URLs and the worker's Voice action URL from `ApplicationDomain`, and inject the web task's SMS sender number from the existing application secret. The Twilio SMS-capable number now posts inbound messages to the deployed HTTPS callback. No live inbound SMS or Voice callback smoke has occurred.

The local callback-wiring validation result is:

- `python manage.py check`: passed with a temporary SQLite override
- `python manage.py makemigrations --check`: passed with a temporary SQLite override
- `pytest tests/test_aws_templates.py`: 8 passed
- `pytest`: 353 passed, 7 PostgreSQL-only tests skipped with a temporary SQLite override
- `ruff check .`: passed
- `docker compose config --quiet`: passed
- all three CloudFormation templates parsed as YAML and passed read-only AWS validation in `us-east-1`
- `git diff --check`: passed

The staging rollout result is:

- immutable image tag `003844a3cbd4eee2b7a57c38d66055cd6ecc88b5` was pushed to ECR
- the Scheduler was disabled during rollout and restored to `ENABLED` at one-minute cadence
- application and queue stacks finished `UPDATE_COMPLETE`; real worker delivery remains `false`
- migration task exited 0; web and worker each reached one stable running task
- public health returned HTTP 200 and all three callback routes returned the expected HTTP 405 for GET
- the delivery queue and DLQ drained to zero and all five alarms remained `OK`
- a fresh deterministic seed tick published ten identifiers, suppressed six due demo deliveries, and left zero demo provider SIDs
- final synthetic matrix: nine demo `suppressed`, seven demo `failed`, four demo `scheduled`, two demo `cancelled`, one demo `processing`, four real `scheduled`, and three real `submitted`
- Twilio inbound SMS webhook configuration was updated successfully; live inbound SMS and Voice callback smokes remain unperformed
- `TWILIO_VOICE_FROM_NUMBER` was populated with the verified Voice-capable Twilio sender and the worker replacement deployment reached steady state; real delivery remains disabled
- production static assets were collected into the image and served successfully through WhiteNoise; both reviewer roles authenticated with the expected Admin navigation boundary

No public registration, token issuance endpoint, or SMS delivery-status callback exists. Staff can provision existing users through Django Admin. The operator has manually exercised sign-in, phone enrollment/verification, and event scheduling, and confirmed the credentialed weather smoke command succeeds. A live Twilio SMS smoke to a physical US handset reached the Messages API, returned a Message SID, and produced local `submitted` state; Twilio later reported `undelivered` with error `30034` because the US 10DLC sender is not attached to an approved A2P campaign. A second live smoke to Twilio's Virtual Phone also returned a valid Message SID and produced a fully audited local `submitted` attempt without error, providing a carrier-independent demonstration while A2P approval remains pending. Voice and interaction callbacks retain deterministic mocked coverage but have not been live-smoke-tested.

## Next Recommended Slice

Finish the remaining Phase 16 operations work: replace the bootstrap IAM user's direct `AdministratorAccess` with a reviewed bounded deployment/operator policy, decide and document the post-review scale-to-zero or teardown choice, and optionally perform an authorized live inbound SMS or Voice smoke. Use `docs/demo.md` for the deterministic reviewer walkthrough.

Do not add speech recognition, inbound-call scheduling, recurrence, registration, new apps, or enable real worker delivery.

## Start Here

Read:

1. Phase 16 in `docs/roadmap.md`
2. the current operational gaps below
3. `docs/deployment.md`
4. the as-built staging and provider limitations

Run the baseline before editing:

```bash
python manage.py check
python manage.py makemigrations --check
pytest
ruff check .
```

## Authenticated API Workflow

The owner-scoped endpoints are:

```text
GET  /api/phones/
POST /api/phones/
POST /api/phones/{id}/verification/start/
POST /api/phones/{id}/verification/check/
GET  /api/events/
POST /api/events/
GET  /api/events/{id}/
POST /api/events/{id}/reschedule/
POST /api/events/{id}/channel/
POST /api/events/{id}/cancel/
```

Use Basic authentication over TLS or an authenticated Django session. Creation requires `phone_number_id`, a five-digit ZIP, an explicit-offset future `scheduled_for`, and `sms` or `voice`. Publicly created events are always demos.

Rescheduling accepts only `scheduled_for`; channel switching accepts only `channel`. Both actions apply only while the event remains `scheduled` and leave all other event and attempt data unchanged.

Phone enrollment accepts a full E.164 number as write-only input. Phone responses expose a masked number and verification state. Verification start/check responses expose normalized status only; start and check rates default to `3/hour` and `10/hour` per authenticated user.

## Browser Workflow

Existing users sign in at `/login/`, manage phones at `/phones/`, and manage demo events at `/events/`. The browser uses Django sessions, CSRF-protected POST mutations, masked phone data, explicit-offset schedule input, and the same application services as the APIs. Registration is not exposed.

## Current Manual Workflow

After creating a verified phone and a due demo event, run:

```bash
python manage.py deliver_demo_event EVENT_ID
```

Expected result: the command reports `suppressed`, the event becomes `suppressed`, and one attempt contains the rendered message and weather snapshot.

To create the repeatable scenario matrix and process one safe local batch:

```bash
python manage.py seed_scheduling_scenarios
python manage.py dispatch_due_events
```

The dispatcher processes demo events only unless both the environment gate and explicit real-delivery flag are set. Its default batch size is 25 and default grace window is 15 minutes.

To publish due identifiers to configured SQS and run the worker:

```bash
python manage.py publish_due_events
python manage.py run_delivery_worker
```

`run_delivery_worker --once` performs one bounded poll. Configure `AWS_REGION`, `DELIVERY_QUEUE_URL`, and `WEATHER_API_KEY`. Real queued delivery additionally requires `DELIVERY_REAL_WORKER_ENABLED=true` and the worker's `--allow-real-delivery` flag.

To smoke-test real normalized weather with an API key configured:

```bash
python manage.py check_weather 94107
```

To intentionally submit one due non-demo SMS event to the explicitly authorized staging number, configure `TWILIO_SMS_FROM_NUMBER`, `TWILIO_SMS_SMOKE_TO_NUMBER`, and `TWILIO_SMS_SMOKE_ENABLED=true`, then run:

```bash
python manage.py send_staging_sms_event EVENT_ID --confirm-send
```

This makes a real Twilio request. It has been run successfully through API acceptance; final carrier delivery remains blocked by Twilio A2P registration.

To intentionally place one due non-demo Voice event to its explicitly authorized staging number, configure `TWILIO_VOICE_FROM_NUMBER`, the public `TWILIO_VOICE_STATUS_CALLBACK_URL`, `TWILIO_VOICE_ACTION_CALLBACK_URL`, `TWILIO_VOICE_SMOKE_TO_NUMBER`, and `TWILIO_VOICE_SMOKE_ENABLED=true`, then run:

```bash
python manage.py send_staging_voice_event EVENT_ID --confirm-call
```

This places a real call and has not been run in this repository session. Both callback URLs must exactly match the public HTTPS URLs Twilio signs.

## Known Gaps

- The three staging CloudFormation stacks are deployed in `us-east-1`. Cloudflare DNS routes `wakeupcall.afam.app`, the ACM certificate is issued, public TLS health returns HTTP 200, web and worker Fargate services are steady at one task each, Scheduler is enabled, and all five alarms are `OK`. The non-root `wakeupcall` deployment user still has directly attached `AdministratorAccess`; replace it with bounded deployment/operator permissions after verifying the replacement policy.
- The templates create billable resources. The single NAT Gateway is a staging cost tradeoff and a single-AZ outbound dependency; production availability should use per-AZ NAT or appropriate VPC endpoints.
- The shared SNS topic supports an optional email subscription, but the endpoint must be supplied and confirmed before alarms have a human destination.
- RDS Multi-AZ and deletion protection default off for staging and must be consciously selected for a longer-lived environment.
- Worker retry settings and the SQS redrive policy are configured separately and must remain aligned at three receives.
- Only pre-sender retryable failures are automatic; sender-boundary errors remain terminal even when their exception class is retryable.
- Stale `processing` events are quarantined with no reconciliation command; they must not be automatically replayed because provider acceptance may be ambiguous.
- DLQ redrive is defined, but operator inspection/redrive tooling is not implemented.
- SMS provider callbacks and final SMS delivery outcomes are not modeled.
- The fake weather observation time is current rather than fixed, though its content is deterministic.
- The user confirmed the real WeatherAPI.com smoke command succeeds with local credentials.
- Twilio Verify has mocked coverage but has not been live-smoke-tested with a service SID and test number.
- Twilio SMS API submission is live-smoke-tested to both a physical destination and Twilio's Virtual Phone. The Virtual Phone request returned a valid Message SID and a fully audited local `submitted` attempt; inbox visibility still requires operator confirmation in Twilio Console. Successful physical carrier delivery remains pending Sole Proprietor A2P 10DLC registration and campaign association for the purchased sender.
- Twilio Voice submission and signed callbacks have mocked coverage but have not been live-smoke-tested with a public HTTPS callback URL and authorized staging number.
- Twilio inbound SMS has deterministic signed-request and PostgreSQL concurrency coverage and its provider-side webhook is configured, but it has not been live-smoke-tested. Advanced Opt-Out behavior and carrier compliance require separate manual verification.
- `TWILIO_VOICE_ACTION_CALLBACK_URL` and `TWILIO_SMS_INBOUND_CALLBACK_URL` are constructed from the public application domain; they are not issued by Twilio. `TWILIO_SMS_FROM_NUMBER` is the SMS-capable E.164 number listed in Twilio Console. Compose and deployed ECS task-definition wiring are complete.
- The API relies on existing users; registration and token issuance are not exposed.
- DRF's cache-backed verification throttles are approximate and process-local with the current default cache. A multi-process deployment needing a strict shared abuse or billing boundary requires a shared cache or database-backed policy.
- HTTP Basic authentication is suitable for this bounded exercise/testing surface only and requires TLS; production deployment should explicitly choose session-based browser access or a managed/token authentication design.
- `PhoneNumber.number` is globally unique as a current assumption.
- Direct model status assignment can bypass transition methods; application code must use services and transition methods.
- Real queue delivery is intentionally available only behind two explicit gates and has not been live-smoke-tested; only the single-event SMS staging path has made a real request.
- PostgreSQL `SKIP LOCKED` may defer a due event for one scheduler cycle while a pending-event mutation holds its row lock; the next tick reloads the resulting authoritative state.
- The operator manually verified sign-in, phone enrollment/verification, event scheduling, AWS resources, and the public health endpoint. Automated Django form/view coverage remains the repeatable regression evidence.

## Environment Note

The active local environment has been seen as `venv/`; the README recommends `.venv/`. Either works when activated. The dependency is `django-environ`, imported as `environ`. Do not install the unrelated `environ==1.0` package; it shadows `django-environ` and fails under Python 3.

## Handoff Update Checklist

At the end of the next slice:

- replace the “Next Recommended Slice” section
- update validation counts and results
- add newly discovered gaps
- move completed roadmap items to the completed section
- update the date
