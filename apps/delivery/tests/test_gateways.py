import logging

from apps.delivery.gateways import DemoMessageSender


def test_demo_sender_logs_only_masked_metadata(caplog):
    phone_number = "+14155552671"
    message = "Wake up. The current weather is private content."

    with caplog.at_level(logging.INFO, logger="apps.delivery.gateways"):
        DemoMessageSender().send(channel="sms", to=phone_number, message=message)

    log_output = caplog.text
    assert phone_number not in log_output
    assert message not in log_output
    assert "*******2671" in log_output
    assert f"message_length={len(message)}" in log_output
