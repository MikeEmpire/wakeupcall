# Wakeup Call

The project provides a production-minded Django foundation for a weather-aware wake-up call application. It includes verified one-time events, delivery auditing, a bounded local dispatcher, a versioned SQS worker boundary, WeatherAPI.com, Twilio Verify, SMS and Voice adapters, authenticated Voice callbacks, PostgreSQL, Docker development services, and a lightweight health endpoint.

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
POST /api/events/{id}/cancel/
```

Use Django session authentication or, for exercise/testing clients, HTTP Basic authentication over TLS. Basic authentication is not the final production identity design. There is intentionally no registration or token-issuance endpoint. A create request references an existing verified phone record owned by the user:

```json
{
  "phone_number_id": 1,
  "zip_code": "94107",
  "scheduled_for": "2026-07-16T14:30:00Z",
  "channel": "sms"
}
```

API-created events are always demos. Datetimes require an explicit offset and are returned in UTC. Representations contain phone record IDs, not full phone numbers or delivery message bodies.

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

Use `run_delivery_worker --once` for one bounded poll. Configure `AWS_REGION`, `DELIVERY_QUEUE_URL`, and `WEATHER_API_KEY`. Queue processing is demo-only by default. Real queued SMS/Voice requires both `DELIVERY_REAL_WORKER_ENABLED=true` and `--allow-real-delivery` on the worker. The queue resources are defined in `infra/aws/phase8-queue.yaml`; deployment-ready ECR and ECS/RDS/ALB templates are in `infra/aws/phase10-ecr.yaml` and `infra/aws/phase10-application.yaml`.

The ordered AWS rollout, secret handling, migration task, safety gates, validation, and teardown process are documented in [`docs/deployment.md`](docs/deployment.md). The templates create billable resources when deployed; no live AWS deployment is performed by local validation.

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

User-facing phone verification endpoints, registration, token issuance, and frontend features are deferred. AWS deployment artifacts exist but have not been deployed by this repository session. Real SMS and Voice require explicit gates; the callback route is provider-only, and API-created events remain demo-only.
