# Reviewer Demo

## Purpose and safety boundary

This walkthrough demonstrates ownership, scheduling, audited weather-aware delivery, SQS processing, callback controls, and operational evidence without enabling automatic real-provider delivery. The live staging application is `https://wakeupcall.afam.app`; public registration is intentionally absent, so use an existing ordinary user and a separate staff user.

Keep `EnableRealWorkerDelivery=false`. API- and browser-created events are demos and must finish as `suppressed`, not reach Twilio. Treat `submitted` as provider acceptance only, never final handset delivery.

## 1. Establish the live deployment

Open `https://wakeupcall.afam.app/health/`.

Expected: HTTP 200 with `{"status": "ok"}`. In AWS, web and worker each show one running task, the Scheduler is enabled at one-minute cadence, the delivery queue and DLQ are empty after processing, and all five alarms are `OK`.

## 2. Show ordinary-user ownership

1. Sign in as an ordinary user at `/login/`.
2. Open `/phones/` and `/events/`.
3. Confirm phone numbers are masked and only that user's events appear.
4. Attempt to open another user's known event ID only if a prepared test fixture exists.

Expected: ordinary navigation has no Admin link; cross-owner API or browser lookups return `404`; rendered messages, weather payloads, attempts, provider SIDs, and full phone numbers are absent from the user-facing pages.

## 3. Show phone enrollment and verification

Use an already verified record for a deterministic review. If a real Verify smoke is explicitly authorized, enroll the authorized E.164 number, start verification, and enter the received code.

Expected: enrollment returns masked metadata, the code is never echoed or stored, and the record becomes verified only after Twilio reports approval. Start/check throttles are three and ten attempts per hour per user. Skip the real Verify request when cost or destination authorization is unclear.

## 4. Show scheduling mutations

1. Create a future Voice demo event using a five-digit US ZIP and an ISO 8601 time with an explicit offset.
2. Reschedule it to another future explicit-offset time.
3. Switch it to SMS, then back to Voice.
4. Create a second event and cancel it.

Expected: stored/displayed time normalizes to UTC; only the requested time or channel changes; cancellation moves only a still-`scheduled` event to `cancelled`; no mutation creates an attempt or calls a provider.

## 5. Show demo delivery and weather audit

Create a demo event due within the 15-minute grace window and allow the minute Scheduler to process it. Then sign in as staff and inspect the event and its delivery attempt in Django Admin.

Expected: the event becomes `suppressed`; one attempt contains the rendered announcement and normalized weather snapshot; its provider SID is empty. A demo log uses a masked destination and message length, never the message body or full number.

For deterministic local presentation, the equivalent commands are:

```bash
python manage.py seed_scheduling_scenarios
python manage.py dispatch_due_events
```

Expected seed result after processing: nine demo `suppressed`, seven demo `failed`, four demo `scheduled`, two demo `cancelled`, one quarantined demo `processing`, four real `scheduled`, and three pre-seeded real `submitted`. No demo attempt has a provider SID.

## 6. Show the SQS boundary

In CloudWatch Logs, show one worker tick expanding into identifier-only delivery messages, followed by acknowledged demo deliveries. In SQS, show the main queue and DLQ returning to zero.

Expected: a tick can publish duplicate identifiers, but row-locking and terminal-state checks prevent a second provider execution. Retryable pre-sender failures use bounded visibility delay; sender-boundary failures are not automatically replayed because provider acceptance may be ambiguous.

## 7. Show Admin behavior

Sign in as staff and open Django Admin.

Expected: lifecycle/audit fields are visible but protected from arbitrary editing. Select a mixture of scheduled and terminal events and use the controlled bulk-cancel action only on prepared demo records; scheduled records cancel and terminal records remain unchanged.

## 8. Show inbound SMS controls

The Twilio SMS-capable number is configured to `POST` to `https://wakeupcall.afam.app/twilio/sms/inbound/`. From the verified owner's authorized handset, send one of the bounded commands only after creating a future pending event:

- `SMS` switches the earliest pending event to SMS.
- `TIME 2030-07-16T18:30:00-07:00` reschedules it using an explicit offset.
- `STOP` cancels it; Twilio Advanced Opt-Out supplies the compliance response while the application returns empty TwiML.

Expected: the signed webhook derives ownership from the verified sender, targets the earliest pending event, stores no message body or sender number, and makes duplicate Message SIDs idempotent. This is a real provider smoke and should be presented as optional evidence, not a deterministic test prerequisite.

## 9. Explain Voice behavior without forcing a live call

The worker embeds `https://wakeupcall.afam.app/twilio/voice/action/` in one-digit `<Gather>` TwiML and supplies `https://wakeupcall.afam.app/twilio/voice/status/` when creating the call. Digit `1` cancels the owner's earliest pending event; digit `2` switches it to SMS. Signed callbacks are idempotent and never trust request-supplied ownership.

The Voice-capable Twilio sender is configured and loaded by the worker, but a live Voice smoke remains an explicit, authorized manual action rather than a deterministic test prerequisite. Deterministic tests cover Voice submission, status ordering, duplicate DTMF actions, invalid signatures, and PostgreSQL concurrency.

## 10. Close with operational evidence and limitations

Show:

- immutable image tag and matching task image digest
- successful one-shot migration exit code
- stable ECS services and healthy ALB target
- Scheduler enabled, queues empty, alarms `OK`
- real worker delivery set to `false`
- Secrets Manager references rather than plaintext credentials

State the important limitations plainly: successful Twilio API submission is not final handset delivery; physical SMS delivery is pending A2P campaign approval; live inbound SMS, Voice, and callback smokes are optional/manual; stale `processing` events are quarantined; and a crash after provider acceptance but before SID persistence has an unavoidable ambiguous outcome.
