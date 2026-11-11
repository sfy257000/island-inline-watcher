import threading
import time
import unittest
from email.message import EmailMessage
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

import booking_verification as verification
import island_inline_watcher as app


SENDER = 'test@gmail.com'
MESSAGE_ID = '<notification@example.com>'


def reply(code='0042', sender=SENDER, message_id=MESSAGE_ID):
    message = EmailMessage()
    message['From'] = sender
    message['To'] = SENDER
    message['In-Reply-To'] = message_id
    message.set_content(code + '\n\n> Original notification')
    return message.as_bytes()


class ReplyTests(unittest.TestCase):
    def test_search_filters_recent_sender_then_fetches_only_matching_reply_body(self):
        client = Mock()
        matching = reply()
        other = reply(message_id='<different@example.com>')
        def uid(command, *args):
            if command == 'SEARCH':
                self.assertIn('SINCE', args)
                self.assertIn('FROM', args)
                self.assertNotIn('HEADER', args)
                return 'OK', [b'1 2']
            message_id, section = args
            if 'HEADER.FIELDS' in section:
                return 'OK', [(b'header', other if message_id == b'2' else matching)]
            self.assertEqual(message_id, b'1')
            self.assertEqual(section, '(BODY.PEEK[])')
            return 'OK', [(b'body', matching)]
        client.uid.side_effect = uid
        seen = set()
        self.assertEqual(verification.find_reply(client, SENDER, MESSAGE_ID, seen), '0042')
        self.assertEqual(seen, {b'1', b'2'})

    def test_wrong_sender_or_other_notification_is_rejected(self):
        for raw in (reply(sender='other@gmail.com'), reply(message_id='<different@example.com>'), reply(code='12345')):
            self.assertIsNone(verification.parse_reply(raw, SENDER, MESSAGE_ID))

    def test_matching_reply_preserves_leading_zeroes(self):
        self.assertEqual(verification.parse_reply(reply(), SENDER, MESSAGE_ID), '0042')

    def test_expired_reply_wait_never_submits_or_connects(self):
        challenge = Mock()
        with patch.object(verification, 'gmail_mailbox') as mailbox:
            result = verification.wait_for_reply(Mock(), 'test-password', MESSAGE_ID,
                time.monotonic()-1, threading.Event(), challenge)
        self.assertFalse(result)
        mailbox.assert_not_called()
        challenge.submit.assert_not_called()

    def test_cancelled_retry_does_not_reconnect(self):
        cancel = threading.Event()
        challenge = Mock()
        challenge.pending.return_value = True
        challenge.page.wait_for_timeout.side_effect = lambda _: cancel.set()
        with (patch.object(verification, 'gmail_mailbox') as mailbox,
              patch.object(verification, 'find_reply', side_effect=TimeoutError('temporary'))):
            result = verification.wait_for_reply(Mock(sender=SENDER), 'test-password', MESSAGE_ID,
                time.monotonic()+30, cancel, challenge)
        self.assertFalse(result)
        self.assertEqual(mailbox.call_count, 1)
        challenge.submit.assert_not_called()


class BookingProfileTests(unittest.TestCase):
    def test_old_profile_without_salutation_loads_with_other(self):
        with TemporaryDirectory() as folder, patch.object(app, 'SETTINGS_ENV', Path(folder)/'.env'):
            app.write_saved_settings(app.BOOKING_KEY, {
                'family_name':'測試', 'given_name':'測試', 'phone':'0912345678',
                'email':'test@example.com', 'table':app.ANY_TABLE,
            })
            self.assertEqual(app.BookingProfile.load().salutation, '其他')

    def test_salutation_choices_survive_save_and_reload(self):
        with TemporaryDirectory() as folder, patch.object(app, 'SETTINGS_ENV', Path(folder)/'.env'):
            for salutation in app.SALUTATIONS:
                profile = app.BookingProfile.from_fields('測試', '測試', '0912345678',
                    'test@example.com', app.ANY_TABLE, salutation)
                profile.save()
                self.assertEqual(app.BookingProfile.load(), profile)

    def test_invalid_salutation_is_rejected(self):
        with self.assertRaises(ValueError):
            app.BookingProfile.from_fields('測試', '測試', '0912345678', '', app.ANY_TABLE, 'invalid')


if __name__ == '__main__':
    unittest.main()
