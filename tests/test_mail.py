import queue
import smtplib
import unittest
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import island_inline_watcher as app


class MailTests(unittest.TestCase):
    def settings(self, recipients='first@example.com\nsecond@example.com\nthird@example.com'):
        return app.MailSettings.from_fields('smtp.example.com', '465', 'SSL/TLS',
                                            'sender@example.com', recipients)

    def test_multiple_recipient_separators_and_duplicates(self):
        settings = self.settings('first@example.com，second@example.com;\n'
                                 'third@example.com；FIRST@example.com, second@example.com\r\n')
        self.assertEqual(settings.recipients,
                         ('first@example.com', 'second@example.com', 'third@example.com'))

    def test_invalid_or_empty_recipient_list_is_rejected(self):
        for value in ('  ; ,\n', 'first@example.com\ninvalid-address'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.settings(value)

    def test_multiple_recipients_survive_save_and_reload(self):
        settings = self.settings()
        with TemporaryDirectory() as directory, patch.object(app, 'SETTINGS_ENV', Path(directory) / '.env'):
            settings.save('test-password')
            loaded, password = app.MailSettings.load()
        self.assertEqual(loaded, settings)
        self.assertEqual(password, 'test-password')

    def test_each_recipient_gets_all_matching_dates_without_other_addresses(self):
        settings = self.settings()
        query = app.Query('桃園台茂店', None, None, 3, 0, 120)
        slots = {(date(2026, 11, 20), '11:30'), (date(2026, 11, 20), '14:30'),
                 (date(2026, 12, 18), '11:30')}
        with patch.object(app, 'mail_connection') as connection:
            client = connection.return_value.__enter__.return_value
            client.send_message.return_value = {}
            app.send_emails(settings, 'test-password', queue.Queue(), query, slots)
        messages = [call.args[0] for call in client.send_message.call_args_list]
        self.assertEqual([message['To'] for message in messages], list(settings.recipients))
        for message in messages:
            self.assertIn('2026-11-20　11:30、14:30', message.get_content())
            self.assertIn('2026-12-18　11:30', message.get_content())
            self.assertIsNone(message['Cc'])
            self.assertIsNone(message['Bcc'])

    def test_retry_only_failed_recipient_and_notify_new_slots_to_everyone(self):
        settings = self.settings()
        query = app.Query('桃園台茂店', None, None, 3, 0, 120)
        slots = {(date(2026, 11, 20), '11:30')}
        delivered = {}
        with patch.object(app, 'mail_connection') as connection:
            client = connection.return_value.__enter__.return_value
            refused = smtplib.SMTPRecipientsRefused({'second@example.com': (550, b'refused')})
            client.send_message.side_effect = [{}, refused, {}, {}, {}, {}, {}]
            app.send_emails(settings, 'test-password', queue.Queue(), query, slots, delivered)
            self.assertEqual(set(delivered), {'first@example.com', 'third@example.com'})
            app.send_emails(settings, 'test-password', queue.Queue(), query, slots, delivered)
            self.assertEqual(client.send_message.call_count, 4)
            self.assertEqual(client.send_message.call_args.args[0]['To'], 'second@example.com')
            app.send_emails(settings, 'test-password', queue.Queue(), query, slots, delivered)
            self.assertEqual(client.send_message.call_count, 4)
            app.send_emails(settings, 'test-password', queue.Queue(), query,
                            slots | {(date(2026, 11, 20), '17:30')}, delivered)
            new_messages = [call.args[0] for call in client.send_message.call_args_list[-3:]]
        self.assertEqual([message['To'] for message in new_messages], list(settings.recipients))
        self.assertTrue(all('2026-11-20　17:30' in message.get_content() for message in new_messages))
        self.assertTrue(all('2026-11-20　11:30' not in message.get_content() for message in new_messages))

    def test_existing_single_recipient_settings_remain_valid(self):
        settings = self.settings('first@example.com')
        with TemporaryDirectory() as directory, patch.object(app, 'SETTINGS_ENV', Path(directory) / '.env'):
            app.write_saved_settings(app.MAIL_KEY, {
                'host': settings.host, 'port': settings.port, 'security': settings.security,
                'sender': settings.sender, 'recipients': list(settings.recipients),
            })
            loaded, password = app.MailSettings.load()
        self.assertEqual(loaded, settings)
        self.assertEqual(password, '')


if __name__ == '__main__':
    unittest.main()
