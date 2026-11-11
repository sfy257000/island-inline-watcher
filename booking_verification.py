"""Read one Gmail reply and submit it to the matching inline SMS prompt."""

import imaplib
import re
import ssl
from contextlib import contextmanager
from dataclasses import dataclass
from email import policy
from email.parser import BytesParser
from datetime import datetime, timedelta, timezone
from math import inf
from time import monotonic
from urllib.parse import urlsplit

GMAIL_SMTP = "smtp.gmail.com"
CODE_LENGTH = 4
REPLY_WAIT_SECONDS = 180
IMAP_ERRORS = (OSError, imaplib.IMAP4.error)


@contextmanager
def gmail_mailbox(sender, password):
    with imaplib.IMAP4_SSL(
        "imap.gmail.com", 993, ssl_context=ssl.create_default_context(), timeout=12,
    ) as client:
        client.login(sender, password)
        # Gmail can file a reply sent to yourself under Sent or All Mail.
        status, folders = client.list()
        mailbox = b"INBOX"
        if status == "OK":
            for line in folders or []:
                if isinstance(line, bytes):
                    match = re.fullmatch(rb'\(([^)]*)\) (?:"[^"]*"|NIL) (.+)', line)
                    if match and rb"\all" in match[1].lower().split():
                        mailbox = match[2]
                        break
        status, _ = client.select(mailbox, readonly=True)
        if status != "OK":
            raise imaplib.IMAP4.error("Could not open mailbox")
        yield client


def matches_reply(message, sender, message_id):
    try:
        addresses = message.get_all("From", ())
        if len(addresses) != 1 or addresses[0].defects or len(addresses[0].addresses) != 1:
            return False
        if addresses[0].addresses[0].addr_spec.casefold() != sender.casefold():
            return False
        replies = message.get_all("In-Reply-To", ())
        return len(replies) == 1 and str(replies[0]).strip() == message_id
    except (LookupError, TypeError, ValueError):
        return False


def parse_reply(raw_message, sender, message_id):
    """Accept only a direct reply with a four-digit first line."""
    try:
        message = BytesParser(policy=policy.default).parsebytes(raw_message)
        if not matches_reply(message, sender, message_id):
            return None
        part = message.get_body(preferencelist=("plain",))
        if part is not None:
            first_line = next((line.strip() for line in part.get_content().splitlines() if line.strip()), "")
            if re.fullmatch(rf"[0-9]{{{CODE_LENGTH}}}", first_line):
                return first_line
    except (LookupError, TypeError, ValueError):
        pass
    return None


def find_reply(client, sender, message_id, seen):
    # Gmail can time out searching an arbitrary header across All Mail. Search
    # its indexed date/sender fields, then inspect reply headers locally.
    since = (datetime.now(timezone.utc) - timedelta(days=1)).strftime('%d-%b-%Y')
    quoted_sender = '"' + sender.replace('\\', '\\\\').replace('"', '\\"') + '"'
    status, matches = client.uid("SEARCH", None, "SINCE", since, "FROM", quoted_sender)
    if status != "OK":
        raise imaplib.IMAP4.error("Could not search for reply")
    for uid in reversed(((matches or [b""])[0] or b"").split()):
        if uid in seen:
            continue
        status, headers = client.uid("FETCH", uid, "(BODY.PEEK[HEADER.FIELDS (FROM IN-REPLY-TO)])")
        if status != "OK":
            raise imaplib.IMAP4.error("Could not fetch reply headers")
        relevant = any(isinstance(part, tuple) and matches_reply(
            BytesParser(policy=policy.default).parsebytes(part[1]), sender, message_id,
        ) for part in headers or ())
        if not relevant:
            seen.add(uid)
            continue
        status, parts = client.uid("FETCH", uid, "(BODY.PEEK[])")
        if status != "OK":
            raise imaplib.IMAP4.error("Could not fetch reply")
        seen.add(uid)
        for part in parts or ():
            if isinstance(part, tuple):
                code = parse_reply(part[1], sender, message_id)
                if code:
                    return code
    return None


def wait_for_reply(settings, password, message_id, deadline, cancel, challenge, on_retry=None):
    """Keep the mailbox open only while the original OTP form is pending."""
    if cancel.is_set() or monotonic() >= deadline or not challenge.pending():
        return False
    seen = set()
    while not cancel.is_set() and monotonic() < deadline and challenge.pending():
        try:
            with gmail_mailbox(settings.sender, password) as client:
                while not cancel.is_set() and monotonic() < deadline and challenge.pending():
                    code = find_reply(client, settings.sender, message_id, seen)
                    if code:
                        if not cancel.is_set() and monotonic() < deadline:
                            return challenge.submit(code)
                        return False
                    challenge.page.wait_for_timeout(min(3000, max(0, (deadline - monotonic()) * 1000)))
        except (OSError, imaplib.IMAP4.abort):
            if cancel.is_set() or monotonic() >= deadline or not challenge.pending():
                break
            if on_retry:
                on_retry()
            challenge.page.wait_for_timeout(min(3000, max(0, (deadline - monotonic()) * 1000)))
    return False


# Inline's booking form uses four boxes and submits automatically on full-code paste.
OTP_INPUTS = '[data-cy="phone-verification-container"] input[data-cy^="phone-code-"]'
CHECK_OR_PASTE = """
(first, {inputs, url, code}) => {
    if (location.href !== url) return false;
    const dialog = first.closest('[data-cy="phone-verification-container"]');
    if (!dialog || dialog.querySelector('[data-shake]')) return false;
    const current = [...dialog.querySelectorAll('input[data-cy^="phone-code-"]')];
    if (current.length !== inputs.length || !inputs.every((input, index) =>
        current[index] === input && input.isConnected && !input.disabled && !input.readOnly
        && input.value === '' && input.getClientRects().length
        && getComputedStyle(input).visibility === 'visible'
        && input.getAttribute('data-cy') === 'phone-code-' + index)) return false;

    if (code === null) return true;
    const clipboardData = new DataTransfer();
    clipboardData.setData('text/plain', code);
    const paste = new ClipboardEvent('paste', {clipboardData, bubbles: true, cancelable: true});
    return !first.dispatchEvent(paste);
}
"""


@dataclass
class InlineChallenge:
    page: object
    url: str
    inputs: list

    def pending(self):
        return not self.page.is_closed() and self.page.url == self.url and self._check(None)

    def submit(self, code):
        return self._check(code)

    def _check(self, code):
        return self.inputs[0].evaluate(CHECK_OR_PASTE, {
            "inputs": self.inputs, "url": self.url, "code": code,
        })


def find_challenge(page, checkout, url, cancel, timeout=inf):
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.netloc != "inline.app" or not parsed.path.startswith("/booking/"):
        return None
    deadline = monotonic() + timeout
    while not cancel.is_set() and not page.is_closed() and page.url == url and monotonic() < deadline:
        if not checkout.evaluate("form => form.isConnected"):
            return None
        inputs = page.locator(OTP_INPUTS).element_handles()
        if len(inputs) == CODE_LENGTH:
            challenge = InlineChallenge(page, url, inputs)
            if challenge.pending():
                return challenge
        page.wait_for_timeout(250)
    return None
