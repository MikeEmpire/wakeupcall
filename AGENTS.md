# Wakeup Call Agent Guide

This file applies to the entire repository. It is the operating contract for AI agents and human contributors working on the project.

## Mission

Build a production-minded Django coding exercise for scheduling weather-aware SMS and voice wake-up events. The implementation must remain understandable and achievable in roughly two days. Prefer a small, complete vertical slice over speculative infrastructure or abstraction.

## Read Before Changing Code

Read these files in order:

1. `docs/handoff.md` — current implementation status and next recommended slice.
2. `docs/domain.md` — domain invariants and lifecycle rules.
3. `docs/architecture.md` — current boundaries and planned target architecture.
4. `docs/roadmap.md` — sequencing, exit criteria, and deferred work.
5. Relevant code and tests. Code and tests are the final source of truth if documentation has drifted.

Inspect `git status` before editing. The worktree may contain valid user changes. Preserve and build on them; do not replace or revert unrelated work.

## Working Method

1. State the narrow slice being implemented and its stopping point.
2. Inspect the existing implementation before designing replacements.
3. Implement through existing Django app boundaries.
4. Add focused tests for success, failure, and important state behavior.
5. Run the validation commands below.
6. Update `docs/handoff.md` and any design document affected by a changed decision.
7. Stop at the declared boundary. Do not begin the next roadmap phase implicitly.

When requirements are ambiguous, favor the smallest reversible choice and document the assumption. Ask before making a choice that materially expands product scope.

## Non-Negotiable Constraints

- Keep local Django apps limited to `accounts`, `scheduling`, `delivery`, and `weather` unless a new app has clear domain ownership and is approved.
- Use the custom `accounts.User`; never switch back to Django's default user model.
- Store server timestamps in UTC with `USE_TZ = True`.
- Events are one-time events until recurrence is explicitly designed.
- Current ZIP support is five-digit US ZIP codes.
- A scheduled event must use a verified phone number owned by its user.
- Preserve the event and delivery-attempt state machines in `docs/domain.md`.
- Do not call external providers from models, views, admin classes, or migrations.
- Provider-specific objects and raw payloads must not leak beyond adapter boundaries.
- Demo events must never reach a real SMS or voice provider. They must still render, audit, and log the intended delivery.
- Never log secrets, verification codes, raw request bodies, or full phone numbers.
- Queue processing will be at-least-once. All delivery work must remain duplicate-aware.
- Do not claim exactly-once phone delivery; the external-call/database boundary has an unavoidable ambiguous failure window.
- PostgreSQL is the production database. SQLite is only a convenience fallback for local development.

## Architecture Rules

- Models own local invariants and legal in-memory state transitions.
- Application services orchestrate use cases and transaction boundaries.
- Gateway protocols define external capabilities.
- Infrastructure adapters implement weather, Twilio, and queue details.
- Queue messages will contain identifiers, not serialized model state.
- Reload authoritative database state immediately before delivery.
- Keep network calls outside long-running database transactions.
- Use row locking for claims that must be safe under concurrent PostgreSQL workers.

Avoid microservices, Celery, Redis, Kubernetes, event sourcing, multi-provider failover, and generalized repository/unit-of-work frameworks unless the documented requirements change.

## Dependencies and Environment

- Python 3.12 is the supported development version.
- Install development dependencies with `pip install -r requirements/development.txt`.
- The environment-variable library is `django-environ`, imported as `environ`.
- Never install the unrelated package named `environ`; it is obsolete and contains Python 2 syntax.
- Add runtime dependencies to `requirements/base.txt`, development-only tools to `requirements/development.txt`, and keep production based on `requirements/production.txt`.
- Never commit `.env`, credentials, provider tokens, or real phone numbers.

The repository may contain either `venv/` or `.venv/`. Activate the environment before running commands, or prefix commands with the environment's Python executable.

## Required Validation

For every implementation slice, run:

```bash
python manage.py check
python manage.py makemigrations --check
pytest
ruff check .
```

When models change, create intentional migrations and run:

```bash
python manage.py migrate
```

When Docker files or dependencies change, also run:

```bash
docker compose config
docker compose build
```

Concurrency behavior that relies on row locks must eventually be tested against PostgreSQL, not only SQLite.

## Testing Expectations

- Test behavior through public service or model interfaces rather than private implementation details.
- Mock project gateway protocols, not internals of third-party SDKs.
- Cover duplicate processing, invalid transitions, cancellation, provider failures, and demo suppression where relevant.
- Use deterministic provider fakes and timezone-aware datetimes.
- Do not add broad placeholder tests.

## Documentation Maintenance

- `docs/architecture.md` describes as-built architecture first and future architecture second.
- `docs/domain.md` changes whenever an entity, invariant, status, or transition changes.
- `docs/roadmap.md` changes when scope or sequencing changes.
- `docs/handoff.md` is a living status file. Update its completed work, validation result, known gaps, and exact next recommended slice after meaningful work.
- Mark proposed features as **Planned**. Do not describe them in the present tense until they exist and are tested.

## Definition of Done for a Slice

A slice is done only when its behavior is implemented, focused tests pass, migrations are consistent, documentation reflects material decisions, secrets and PII are handled safely, and the stopping boundary is reported clearly.
