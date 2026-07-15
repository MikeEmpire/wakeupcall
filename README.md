# Wakeup Call

The project provides a production-minded Django application for scheduling weather-aware SMS and Voice wake-up events. It includes verified one-time events, ownership-scoped APIs, delivery auditing, a bounded dispatcher, an SQS worker boundary, WeatherAPI.com, Twilio Verify, SMS and Voice adapters, authenticated Voice callbacks, PostgreSQL, Docker development services, and an AWS Fargate staging deployment.

## Local virtual-environment setup

Python 3.12 is recommended.

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements/development.txt
cp .env.example .env
```

For the quickest non-Docker start, remove or comment out `DATABASE_URL` in `.env`; development settings then use SQLite. To use local PostgreSQL, keep `DATABASE_URL` and ensure that database is available.

Run migrations and start Django:

```bash
python manage.py migrate
python manage.py runserver
```

The application is available at <http://localhost:8000/> and its health endpoint is `GET /health/`.

## Docker Compose setup

Copy the example environment file, build the image, run migrations explicitly, and start the services:

```bash
cp .env.example .env
docker compose build
docker compose run --rm web python manage.py migrate
docker compose up
```

Compose runs Django at <http://localhost:8000/> and PostgreSQL in an internal service named `db`. Source code is mounted into the Django container for development reloads.

## Staging deployment

The staging environment is deployed in AWS `us-east-1` at <https://wakeupcall.afam.app>. Its public health check is:

```text
GET https://wakeupcall.afam.app/health/
```

The deployment uses an immutable `linux/amd64` image in ECR, an HTTPS ALB, private Fargate web and worker tasks, private RDS PostgreSQL, SQS with a DLQ, EventBridge Scheduler, Secrets Manager, CloudWatch logs and alarms, and a confirmed SNS alarm-email subscription. The one-minute Scheduler is enabled, but queued real-provider delivery remains disabled. Automatic staging ticks process demo events through the complete weather/render/audit path and suppress Twilio submission.

The ordered rollout, secret handling, migration task, safety gates, validation, rollback, and teardown process are documented in [`docs/deployment.md`](docs/deployment.md). The templates create billable resources; keep real delivery disabled unless destinations, provider compliance, and cost have been explicitly approved.

## Environment configuration

Settings default to `config.settings.development`. Production processes must use `config.settings.production` and provide `DJANGO_SECRET_KEY`, `DJANGO_ALLOWED_HOSTS`, and either `DATABASE_URL` or the discrete `DATABASE_HOST`, `DATABASE_NAME`, `DATABASE_USER`, and `DATABASE_PASSWORD` settings used by ECS secret injection.

`.env` is ignored by Git. Never commit real secrets. The values in `.env.example` are development placeholders only.

## Common commands

Create an administrator:

```bash
python manage.py createsuperuser
```

Run checks and tests:

```bash
python manage.py check
python manage.py makemigrations --check
pytest
```

## Authenticated event API

The minimal owner-scoped API supports:

```text
GET  /api/events/
POST /api/events/
GET  /api/events/{id}/
POST /api/events/{id}/reschedule/
POST /api/events/{id}/channel/
POST /api/events/{id}/cancel/
```

Use Django session authentication or, for exercise/testing clients, HTTP Basic authentication over TLS. Basic authentication is not the final production identity design. There is intentionally no registration or token-issuance endpoint. Django staff and superusers use the standard Admin at `/admin/`; ordinary authenticated users can access only their own API events. A create request references an existing verified phone record owned by the user:

```json
{
  "phone_number_id": 1,
  "zip_code": "94107",
  "scheduled_for": "2026-07-16T14:30:00Z",
  "channel": "sms"
}
```

API-created events are always demos. Datetimes require an explicit offset and are returned in UTC. Representations contain phone record IDs, not full phone numbers or delivery message bodies.

Pending events can be rescheduled or switched between SMS and Voice with dedicated payloads:

```json
{"scheduled_for": "2026-07-17T14:30:00-07:00"}
```

```json
{"channel": "voice"}
```

These actions work only while the event is `scheduled`; they do not change its destination, ZIP code, demo state, lifecycle state, or delivery audit.

## Authenticated phone API

Existing users can enroll and verify their own phone records:

```text
GET  /api/phones/
POST /api/phones/
POST /api/phones/{id}/verification/start/
POST /api/phones/{id}/verification/check/
```

Enrollment accepts an E.164 number, such as `{"number": "+14155552671"}`. Responses expose the record ID, masked number, and verification state; the full number is write-only. Verification checks accept `{"code": "123456"}` and never echo the code or provider identifiers. Start and check actions are throttled per authenticated user, with defaults of `3/hour` and `10/hour` configurable through `PHONE_VERIFICATION_START_RATE` and `PHONE_VERIFICATION_CHECK_RATE`.

Process a due demo event with deterministic fake weather:

```bash
python manage.py deliver_demo_event EVENT_ID
```

The command records the rendered announcement and suppressed delivery attempt. It does not contact Twilio or any weather service.

Create the deterministic 30-event scheduling matrix, then process one bounded due batch:

```bash
python manage.py seed_scheduling_scenarios
python manage.py dispatch_due_events
```

The dispatcher is demo-only by default, uses a 25-event batch and 15-minute grace window, and never contacts Twilio for demo events. Real batch delivery requires both `DELIVERY_REAL_DISPATCH_ENABLED=true` and the explicit `--allow-real-delivery` flag; use it only when every due real event is intentionally authorized for provider submission.

Publish one bounded batch to SQS or run the long-polling worker:

```bash
python manage.py publish_due_events
python manage.py run_delivery_worker
```

Use `run_delivery_worker --once` for one bounded poll. Configure `AWS_REGION`, `DELIVERY_QUEUE_URL`, and `WEATHER_API_KEY`. Queue processing is demo-only by default. Real queued SMS/Voice requires both `DELIVERY_REAL_WORKER_ENABLED=true` and `--allow-real-delivery` on the worker. The deployed queue resources are defined in `infra/aws/phase8-queue.yaml`; ECR and ECS/RDS/ALB resources are defined in `infra/aws/phase10-ecr.yaml` and `infra/aws/phase10-application.yaml`.

With `WEATHER_API_KEY` configured, smoke-test the real weather adapter without creating or delivering an event:

```bash
python manage.py check_weather 94107
```

The real adapter uses WeatherAPI.com and returns only the normalized location, Fahrenheit temperature, condition, and observation time used by the application.

To make an intentional staging SMS submission, configure Twilio credentials, `TWILIO_SMS_FROM_NUMBER`, `TWILIO_SMS_SMOKE_TO_NUMBER`, and `TWILIO_SMS_SMOKE_ENABLED=true`. Create a due, non-demo SMS event whose verified destination exactly matches the authorized smoke number, then run:

```bash
python manage.py send_staging_sms_event EVENT_ID --confirm-send
```

This command makes a real Twilio request. It rejects demo events, voice events, unconfirmed runs, and destinations other than the configured staging number. Its output and adapter logs omit full phone numbers and message bodies.

For a staging voice call, also configure `TWILIO_VOICE_FROM_NUMBER`, the public canonical `TWILIO_VOICE_STATUS_CALLBACK_URL`, `TWILIO_VOICE_SMOKE_TO_NUMBER`, and `TWILIO_VOICE_SMOKE_ENABLED=true`. Then create a due non-demo voice event for that authorized number and run:

```bash
python manage.py send_staging_voice_event EVENT_ID --confirm-call
```

Twilio signs callback requests to `POST /twilio/voice/status/`. The configured callback URL must exactly match the public HTTPS URL used by Twilio for signature validation.

Run the same commands in Docker by prefixing them with `docker compose run --rm web`.

## Design and agent workflow

- [`AGENTS.md`](AGENTS.md) defines the required workflow and constraints for AI agents and contributors.
- [`docs/handoff.md`](docs/handoff.md) records the current state and exact next recommended slice.
- [`docs/domain.md`](docs/domain.md) defines entities, invariants, and status transitions.
- [`docs/architecture.md`](docs/architecture.md) separates the as-built system from the planned production design.
- [`docs/roadmap.md`](docs/roadmap.md) sequences future work and documents phase boundaries.

## Current boundaries

Registration, token issuance, inbound SMS commands, DTMF/speech interaction, and frontend features are not implemented yet. The staging AWS environment is live with automatic demo processing, but real queued SMS and Voice remain explicitly gated off. The Voice callback route is provider-only, API-created events remain demo-only, and a Twilio provider acceptance result is never described as final carrier delivery.
