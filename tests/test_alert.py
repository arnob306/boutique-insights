import pytest

from src.alert import (
    ALERT_VAR,
    REASONS,
    SUBJECT,
    alert_message,
    alert_settings,
    send_alert,
)
from src.reports.delivery import DeliveryError

ENV = {
    'SMTP_HOST': 'smtp.example.com', 'SMTP_PORT': '587',
    'SMTP_USER': 'sender@example.com', 'SMTP_PASSWORD': 'app-password-123',
    'REPORT_TO': 'mum@example.com', ALERT_VAR: 'me@example.com',
}


class Recorder:
    def __init__(self, error=None):
        self.sent = []
        self.error = error

    def __call__(self, settings, message):
        if self.error:
            raise self.error
        self.sent.append(message)


# --- who it goes to ---------------------------------------------------------------

def test_there_is_no_alert_without_an_alert_address():
    assert alert_settings({k: v for k, v in ENV.items() if k != ALERT_VAR}) is None


def test_a_blank_alert_address_means_no_alert():
    assert alert_settings({**ENV, ALERT_VAR: ''}) is None


def test_the_alert_goes_to_the_alert_address_not_to_her():
    settings = alert_settings(ENV)
    assert settings.recipient == 'me@example.com'
    assert settings.user == 'sender@example.com'


def test_an_alert_address_that_is_not_an_email_is_explained():
    with pytest.raises(DeliveryError, match='ALERT_TO'):
        alert_settings({**ENV, ALERT_VAR: 'not-an-address'})


def test_missing_mail_settings_are_named():
    env = {k: v for k, v in ENV.items() if k != 'SMTP_HOST'}
    with pytest.raises(DeliveryError, match='SMTP_HOST'):
        alert_settings(env)


# --- what it says -----------------------------------------------------------------

@pytest.mark.parametrize('reason,phrase', [
    ('input', 'inbox'),
    ('untrusted', 'problems'),
    ('delivery', 'SMTP'),
    ('crash', 'unexpected'),
])
def test_each_reason_says_what_to_do(reason, phrase):
    message = alert_message(reason, alert_settings(ENV))
    assert message['Subject'] == SUBJECT
    assert message['To'] == 'me@example.com'
    body = message.get_body(('plain',)).get_content()
    assert phrase in body
    assert REASONS[reason] in body
    assert message.get_body(('html',)) is not None


def test_the_alert_never_carries_the_password():
    for reason in REASONS:
        assert 'app-password-123' not in alert_message(
            reason, alert_settings(ENV)).as_string()


def test_an_unknown_reason_is_a_programming_error():
    with pytest.raises(ValueError, match='weird'):
        alert_message('weird', alert_settings(ENV))


# --- sending it -------------------------------------------------------------------

def test_a_sent_alert_is_reported_and_delivered():
    sender = Recorder()
    note = send_alert('input', sender, ENV)
    assert 'Alert emailed' in note
    assert len(sender.sent) == 1


def test_without_an_alert_address_it_says_how_to_get_alerts_and_sends_nothing():
    sender = Recorder()
    env = {k: v for k, v in ENV.items() if k != ALERT_VAR}
    note = send_alert('input', sender, env)
    assert ALERT_VAR in note
    assert sender.sent == []


def test_a_bad_alert_address_is_reported_never_raised():
    note = send_alert('input', Recorder(), {**ENV, ALERT_VAR: 'nope'})
    assert note.startswith('Could not send the alert')
    assert 'ALERT_TO' in note


def test_a_failing_mail_server_is_reported_never_raised():
    note = send_alert('delivery', Recorder(error=DeliveryError('Could not send.')), ENV)
    assert note.startswith('Could not send the alert')
