# Current Handoff

Last updated: 2026-07-15

## Current State

The project has a working Django foundation, domain models, a synchronous demo-delivery vertical slice, a real WeatherAPI.com adapter, a Twilio Verify boundary, and Twilio SMS and Voice senders behind `MessageSender`. Non-demo submissions map validated Twilio Message or Call SIDs into the existing `DeliveryResult` and delivery attempt. Both adapters use bounded HTTP timeouts and safe project-owned errors without leaking Twilio objects or raw responses.

Separate opt-in staging commands are the only current executable real-provider paths. They are disabled by default, require command-line confirmation and explicitly authorized destinations, reject demo and wrong-channel events before adapter construction, and use fake weather to isolate provider submission.

`POST /twilio/voice/status/` validates Twilio signatures against the configured canonical HTTPS callback URL. It maps signed Call SID, Call Status, and Sequence Number fields into normalized attempt-level provider status. Sequence numbers make duplicates and out-of-order callbacks no-ops, terminal provider outcomes cannot regress, and the local event remains `submitted`.

The most recent validation result is:

- `python manage.py check`: passed
- `python manage.py makemigrations --check`: passed
- `python manage.py migrate`: passed on a fresh temporary SQLite database
- `pytest`: 128 passed (temporary SQLite override; local PostgreSQL role was unavailable)
- `ruff check .`: passed
- `docker compose config`: passed
- `docker compose build`: passed

No user-facing verification endpoint, SMS status callback, queue, periodic dispatcher, or AWS resource is implemented. The user confirmed the credentialed weather smoke command succeeds. A live Twilio SMS smoke reached the Messages API, returned a Message SID, and produced local `submitted` state. Twilio later reported `undelivered` with error `30034` because the new US 10DLC sender is not attached to an approved A2P campaign. Twilio Verify, Voice, and Voice callbacks have mocked coverage but have not been live-smoke-tested from this repository session.

## Next Recommended Slice

Implement Phase 7: a local due-event dispatcher.

Stop after:

- management command to find due events in bounded batches
- PostgreSQL-safe row claiming and cancellation-race behavior
- missed-event grace-window policy
- stale-processing recovery design
- a thirty-event seed command covering channels, times, statuses, and demo behavior
- PostgreSQL concurrency coverage where row-lock semantics matter
- focused tests and documentation updates

Do not add SQS, EventBridge, AWS resources, broad user-facing APIs, or deployment infrastructure in the same slice. Preserve the existing provider and demo boundaries.

## Start Here

Read:

1. `apps/delivery/services.py`
2. `apps/delivery/tests/test_services.py`
3. `apps/scheduling/models.py`
4. Phase 7 in `docs/roadmap.md`
5. Django and PostgreSQL row-lock documentation

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

To intentionally submit one due non-demo SMS event to the explicitly authorized staging number, configure `TWILIO_SMS_FROM_NUMBER`, `TWILIO_SMS_SMOKE_TO_NUMBER`, and `TWILIO_SMS_SMOKE_ENABLED=true`, then run:

```bash
python manage.py send_staging_sms_event EVENT_ID --confirm-send
```

This makes a real Twilio request. It has not been run in this repository session.

To intentionally place one due non-demo Voice event to its explicitly authorized staging number, configure `TWILIO_VOICE_FROM_NUMBER`, the public `TWILIO_VOICE_STATUS_CALLBACK_URL`, `TWILIO_VOICE_SMOKE_TO_NUMBER`, and `TWILIO_VOICE_SMOKE_ENABLED=true`, then run:

```bash
python manage.py send_staging_voice_event EVENT_ID --confirm-call
```

This places a real call and has not been run in this repository session. The callback URL must exactly match the public HTTPS URL Twilio signs.

## Known Gaps

- All execution exceptions currently become terminal `failed`; transient retry categories are not designed.
- Weather exceptions expose retryability, but delivery orchestration does not consume it yet.
- There is no recovery for a worker that dies after moving an event to `processing`.
- Row-lock semantics have not been exercised in a PostgreSQL concurrency test.
- SMS provider callbacks and final SMS delivery outcomes are not modeled.
- The fake weather observation time is current rather than fixed, though its content is deterministic.
- The user confirmed the real WeatherAPI.com smoke command succeeds with local credentials.
- Twilio Verify has mocked coverage but has not been live-smoke-tested with a service SID and test number.
- Twilio SMS API submission is live-smoke-tested, but successful carrier delivery remains pending Sole Proprietor A2P 10DLC registration and campaign association for the purchased sender.
- Twilio Voice submission and signed callbacks have mocked coverage but have not been live-smoke-tested with a public HTTPS callback URL and authorized staging number.
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
