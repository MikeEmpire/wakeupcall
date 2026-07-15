# Current Handoff

Last updated: 2026-07-14

## Current State

The project has a working Django foundation, domain models, a synchronous demo-delivery vertical slice, a real WeatherAPI.com adapter, and a Twilio Verify boundary. Verification services can start an SMS challenge and mark a phone verified only after Twilio returns `approved`; codes and challenge state are not persisted locally.

The most recent validation result is:

- `python manage.py check`: passed
- `python manage.py makemigrations --check`: passed
- `pytest`: 64 passed
- `ruff check .`: passed
- `docker compose config`: passed
- `docker compose build`: passed

No Twilio SMS/Voice delivery, user-facing verification endpoint, queue, periodic dispatcher, or AWS resource is implemented. The user confirmed the credentialed weather smoke command succeeds. Twilio Verify has mocked coverage but has not been live-smoke-tested from this repository session.

## Next Recommended Slice

Implement Twilio SMS behind the existing `MessageSender` boundary.

Stop after:

- Twilio SMS sender adapter and configuration
- provider SID mapping into `DeliveryResult`
- timeout and safe provider-error mapping
- focused adapter tests with a mocked Twilio client boundary
- an opt-in staging smoke path that cannot send demo events
- documentation update

Do not add Voice, callbacks, SQS, EventBridge, user-facing APIs, or deployment infrastructure in the same slice. Preserve the rule that demo events can never reach the Twilio sender.

## Start Here

Read:

1. `apps/delivery/gateways.py`
2. `apps/delivery/services.py`
3. `apps/delivery/tests/test_services.py`
4. Phase 5 in `docs/roadmap.md`
5. Twilio's official Message resource documentation

Run the baseline before editing:

```bash
python manage.py check
python manage.py makemigrations --check
pytest
ruff check .
```

## Current Manual Workflow

After creating a verified phone and a due demo event, run:

```bash
python manage.py deliver_demo_event EVENT_ID
```

Expected result: the command reports `suppressed`, the event becomes `suppressed`, and one attempt contains the rendered message and weather snapshot.

To smoke-test real normalized weather with an API key configured:

```bash
python manage.py check_weather 94107
```

## Known Gaps

- All execution exceptions currently become terminal `failed`; transient retry categories are not designed.
- Weather exceptions expose retryability, but delivery orchestration does not consume it yet.
- There is no recovery for a worker that dies after moving an event to `processing`.
- Row-lock semantics have not been exercised in a PostgreSQL concurrency test.
- Provider callback statuses and final delivery outcomes are not modeled.
- The fake weather observation time is current rather than fixed, though its content is deterministic.
- The user confirmed the real WeatherAPI.com smoke command succeeds with local credentials.
- Twilio Verify has mocked coverage but has not been live-smoke-tested with a service SID and test number.
- No authenticated verification endpoints or application-level verification throttles exist yet.
- `PhoneNumber.number` is globally unique as a current assumption.
- Direct model status assignment can bypass transition methods; application code must use services and transition methods.
- No seed command exists yet.

## Environment Note

The active local environment has been seen as `venv/`; the README recommends `.venv/`. Either works when activated. The dependency is `django-environ`, imported as `environ`. Do not install the unrelated `environ==1.0` package; it shadows `django-environ` and fails under Python 3.

## Handoff Update Checklist

At the end of the next slice:

- replace the “Next Recommended Slice” section
- update validation counts and results
- add newly discovered gaps
- move completed roadmap items to the completed section
- update the date
