# Current Handoff

Last updated: 2026-07-15

## Current State

The project has a working Django foundation, domain models, a bounded local due-event dispatcher, a versioned SQS worker boundary, a real WeatherAPI.com adapter, a Twilio Verify boundary, and Twilio SMS and Voice senders behind `MessageSender`. Non-demo submissions map validated Twilio Message or Call SIDs into the existing `DeliveryResult` and delivery attempt. Provider SDK objects and raw responses remain inside their adapters.

Phase 9 adds a minimal authenticated DRF event API. Basic and session authentication protect every `/api/events/` operation. Users can create demo events for their own verified phone record, list and retrieve only their events, and cancel an owned `scheduled` event through the row-locking service. Cross-user identifiers return `404`; lifecycle conflicts return `409`. Creation requires an explicit ISO 8601 offset and normalizes to UTC. API representations expose a phone record ID rather than a full number and omit attempts, rendered messages, weather audit payloads, and provider identifiers.

Phase 10 adds deployment-ready AWS artifacts without creating live resources. `phase10-ecr.yaml` defines an immutable, scan-on-push ECR repository with bounded retention and a shared SNS alarm topic. `phase10-application.yaml` defines the two-AZ VPC layout, public TLS ALB, private Fargate web/worker/migration tasks, private encrypted RDS PostgreSQL, Secrets Manager injection, least-privilege task roles, retained log groups, basic alarms, and optional Route 53 alias. The Phase 8 queue template now accepts the shared alarm topic and exports the Scheduler name.

AWS bootstrap has begun in `us-east-1` using the non-root `wakeupcall` IAM user. All templates passed AWS-side validation. A DNS-validated ACM certificate for `wakeupcall.afam.app` has been requested and is pending its Cloudflare validation CNAME. No CloudFormation stack, image, queue, ECS task, RDS instance, or Scheduler has been created or enabled yet.

All ECS task definitions use one immutable image. Web runs Gunicorn, worker runs the existing SQS command, and migration is an explicit one-shot task. Web/worker desired counts default to zero, the Scheduler remains disabled, and real worker delivery defaults off. Production settings accept discrete database fields so ECS can inject only the RDS-managed password JSON key instead of resolving a credential into task-definition plaintext. `docs/deployment.md` sequences validation, image publication, queue deployment, zero-capacity application deployment, secret configuration, migration, demo verification, service start, and Scheduler enablement.

The ALB health check is handled by narrow first middleware only when the path is `/health/` and the documented ALB user agent is present. This permits the ALB's private-IP `Host` header without weakening the public-domain `ALLOWED_HOSTS` policy or treating redirects/errors as healthy.

The detail resource is read-only; there is no PUT, PATCH, or DELETE. Client-supplied `status` and `is_demo` cannot override server-owned values, so public creation remains demo-only. Lists are scheduled-time ordered and paginated at 50. Django Admin now has a controlled bulk-cancel action that calls the same locking service and leaves non-scheduled events unchanged.

Separate opt-in staging commands are the only current executable real-provider paths. They are disabled by default, require command-line confirmation and explicitly authorized destinations, reject demo and wrong-channel events before adapter construction, and use fake weather to isolate provider submission.

`dispatch_due_events` selects one oldest-first bounded batch and claims rows with PostgreSQL `SELECT ... FOR UPDATE SKIP LOCKED`. It is demo-only by default. Real batch delivery requires both `DELIVERY_REAL_DISPATCH_ENABLED=true` and `--allow-real-delivery`; its channel router constructs a Twilio adapter lazily. The default grace window is 15 minutes: older events become failed `MissedDeliveryWindow` attempts without weather or delivery provider calls. Each claimed delivery finalizes independently, and one failure does not stop the rest of the batch. Cancellation uses the same row lock, so cancellation and claiming have one legal winner.

`seed_scheduling_scenarios` replaces only a reserved seed user's records with an exact 30-event matrix spanning SMS/Voice, demo/real, due/future/missed, terminal, cancelled, and stale-processing states. Stale `processing` events are intentionally quarantined; automatic replay is unsafe because a crash after provider acceptance but before SID persistence has an ambiguous outcome.

Phase 8 adds strict version-1 queue envelopes for `dispatch_due_events` ticks and identifier-only `deliver_scheduled_event` work. The focused template configures EventBridge Scheduler to send one tick per minute to an SQS Standard queue when deployed and enabled. A long-polling worker expands each tick into one bounded oldest-first ID batch without changing event state, then reloads and row-locks each event before delivery. This deliberately permits duplicate publication while eliminating a claim-before-publish loss window.

The worker retries only explicitly retryable failures that occur before entering `MessageSender`. A retrying event remains `processing`, the failed attempt is immutable, and the original SQS message receives bounded exponential visibility delay. On the third receive the event becomes failed with `RetryExhausted:*` and the message remains for DLQ redrive. Permanent pre-send failures and all sender-boundary exceptions are audited and acknowledged; sender failures are never automatically replayed because provider acceptance may be ambiguous.

`infra/aws/phase8-queue.yaml` defines an encrypted Standard queue and 14-day DLQ, three-receive redrive, 20-second long polling, 120-second visibility, a disabled-by-default one-minute EventBridge Scheduler, least-privilege scheduler IAM, and CloudWatch alarms for DLQ depth and oldest-message age. The template is not deployed, and it does not include the Phase 10 ECS/RDS/ALB environment.

`POST /twilio/voice/status/` validates Twilio signatures against the configured canonical HTTPS callback URL. It maps signed Call SID, Call Status, and Sequence Number fields into normalized attempt-level provider status. Sequence numbers make duplicates and out-of-order callbacks no-ops, terminal provider outcomes cannot regress, and the local event remains `submitted`.

The most recent Phase 10 validation result is:

- `python manage.py check`: passed with a temporary SQLite override
- `python manage.py makemigrations --check`: passed; no model migration required
- `pytest`: 227 passed, 3 PostgreSQL-only tests skipped with a temporary SQLite override
- `docker compose run --rm web pytest`: 230 passed against PostgreSQL, including existing queue/cancellation races
- `ruff check .`: passed
- `docker compose config --quiet`: passed
- `docker compose build`: passed
- explicit `linux/amd64` production image build: passed
- all three CloudFormation templates parsed as YAML: passed
- `aws cloudformation validate-template`: all three templates passed in `us-east-1`; no resources were created

No registration, user-facing phone verification API, token issuance endpoint, SMS status callback, deployed queue, or live AWS environment exists. The user confirmed the credentialed weather smoke command succeeds. A live Twilio SMS smoke to a physical US handset reached the Messages API, returned a Message SID, and produced local `submitted` state; Twilio later reported `undelivered` with error `30034` because the US 10DLC sender is not attached to an approved A2P campaign. A second live smoke to Twilio's Virtual Phone also returned a valid Message SID and produced a fully audited local `submitted` attempt without error, providing a carrier-independent demonstration while A2P approval remains pending. Twilio Verify, Voice, and Voice callbacks have mocked coverage but have not been live-smoke-tested from this repository session.

## Next Recommended Slice

Validate and deploy the Phase 10 staging environment with explicit operator authorization.

Stop after:

- choose the AWS account, region, application domain, same-region ACM certificate, Route 53 choice, alarm destination, and RDS availability/deletion settings
- run AWS-side template validation
- deploy foundation, queue with Scheduler disabled, and zero-capacity application stacks
- publish one immutable production image and configure the generated application secret
- run and verify the migration task before starting web and worker services
- verify TLS health, logs, alarms, and one demo event end to end before enabling the Scheduler

Do not enable real worker delivery during initial deployment. Keep `EnableRealWorkerDelivery=false` until provider compliance, destinations, and cost are explicitly approved. Do not add frontend work, registration, broad account management, recurrence, provider replay, or new apps in this slice.

## Start Here

Read:

1. `docs/deployment.md`
2. `infra/aws/phase10-ecr.yaml`
3. `infra/aws/phase8-queue.yaml`
4. `infra/aws/phase10-application.yaml`
5. deployment tradeoffs and known ambiguity in `docs/architecture.md`

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
GET  /api/events/
POST /api/events/
GET  /api/events/{id}/
POST /api/events/{id}/cancel/
```

Use Basic authentication over TLS or an authenticated Django session. Creation requires `phone_number_id`, a five-digit ZIP, an explicit-offset future `scheduled_for`, and `sms` or `voice`. Publicly created events are always demos.

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

To intentionally place one due non-demo Voice event to its explicitly authorized staging number, configure `TWILIO_VOICE_FROM_NUMBER`, the public `TWILIO_VOICE_STATUS_CALLBACK_URL`, `TWILIO_VOICE_SMOKE_TO_NUMBER`, and `TWILIO_VOICE_SMOKE_ENABLED=true`, then run:

```bash
python manage.py send_staging_voice_event EVENT_ID --confirm-call
```

This places a real call and has not been run in this repository session. The callback URL must exactly match the public HTTPS URL Twilio signs.

## Known Gaps

- CloudFormation syntax, local contract tests, and AWS-side `validate-template` checks pass in `us-east-1`. Live stack deployment has not run. The `wakeupcall-staging` CLI profile resolves to the non-root `wakeupcall` IAM user in `us-east-1`; it has temporary directly attached `AdministratorAccess` for bootstrap and should be narrowed after deployment. The application will use externally managed Cloudflare DNS for `wakeupcall.afam.app`; its ACM certificate is pending DNS validation. Route 53 is intentionally not required.
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
- No authenticated verification endpoints or application-level verification throttles exist yet.
- The event API relies on existing users and phone records; registration, token issuance, phone management, and verification endpoints are not exposed.
- HTTP Basic authentication is suitable for this bounded exercise/testing surface only and requires TLS; production deployment should explicitly choose session-based browser access or a managed/token authentication design.
- `PhoneNumber.number` is globally unique as a current assumption.
- Direct model status assignment can bypass transition methods; application code must use services and transition methods.
- Real queue delivery is intentionally available only behind two explicit gates and has not been live-smoke-tested; only the single-event SMS staging path has made a real request.

## Environment Note

The active local environment has been seen as `venv/`; the README recommends `.venv/`. Either works when activated. The dependency is `django-environ`, imported as `environ`. Do not install the unrelated `environ==1.0` package; it shadows `django-environ` and fails under Python 3.

## Handoff Update Checklist

At the end of the next slice:

- replace the “Next Recommended Slice” section
- update validation counts and results
- add newly discovered gaps
- move completed roadmap items to the completed section
- update the date
