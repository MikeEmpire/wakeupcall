# Wakeup Call

The project provides a clean Django foundation for a weather-aware wake-up call application. It includes a custom user model, verified phone and one-time event domain models, delivery-attempt auditing, a synchronous demo-delivery workflow, WeatherAPI.com, Twilio Verify, SMS, and Voice adapters, authenticated Voice status callbacks, PostgreSQL configuration, Docker development services, console logging, Django REST Framework, and a lightweight health endpoint. Asynchronous scheduling and processing are intentionally deferred.

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

Settings default to `config.settings.development`. Production processes must use `config.settings.production` and provide `DJANGO_SECRET_KEY`, `DJANGO_ALLOWED_HOSTS`, and `DATABASE_URL`.

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

Process a due demo event with deterministic fake weather:

```bash
python manage.py deliver_demo_event EVENT_ID
```

The command records the rendered announcement and suppressed delivery attempt. It does not contact Twilio or any weather service.

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

User-facing verification endpoints, schedulers, queues, AWS deployment resources, registration, and frontend features are deferred to later phases. Twilio Verify is available through application services, but no public workflow exposes it yet. Real SMS and Voice are exposed only through opt-in staging management commands; the callback route is provider-only, and there is no user-facing delivery API.
