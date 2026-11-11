"""島語 inline 空位監看（Windows 版）。

Windows 一鍵啟動：雙擊 start.bat，由 uv 準備 Python 3.12 和 Playwright。
需要 Google Chrome。程式會開啟獨立的 Chrome 視窗讀取公開訂位頁。
訂位與 Email 設定可儲存到專案的 .env。SMTP 密碼可選擇一併儲存。
也可以從 ISLAND_SMTP_PASSWORD 環境變數讀取 SMTP 密碼。
"""
import base64
import json
import os
import queue
import re
import smtplib
import ssl
import tempfile
import threading
import tkinter as tk
import webbrowser
from contextlib import closing, contextmanager, suppress
from dataclasses import asdict, dataclass, replace
from datetime import date, datetime, timedelta, timezone
from email.errors import HeaderParseError
from email.headerregistry import Address
from email.message import EmailMessage
from pathlib import Path
from time import monotonic
from tkinter import messagebox, ttk
from urllib.parse import unquote, urlencode, urlparse
from uuid import uuid4

from dotenv import dotenv_values, set_key

from booking_verification import (
    CODE_LENGTH, GMAIL_SMTP, IMAP_ERRORS, REPLY_WAIT_SECONDS, find_challenge, gmail_mailbox, wait_for_reply,
)

try:
    from playwright.sync_api import Error as BrowserError
    from playwright.sync_api import TimeoutError as BrowserTimeout
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None

TAIPEI = timezone(timedelta(hours=8))
APP_VERSION = "v1.00"
BOOKING_ROOT = "https://inline.app/booking/-NeqTSgDQOAYi30lg4a7:inline-live-3"
BRANCHES = {
    "台北漢來店": "-NeqTStJZDIBQHEMSDI8",
    "桃園台茂店": "-OfXjSw3386qiY1yTpOZ",
    "台中漢神洲際店": "-OuaB8PT0YWMRAavQhfS",
    "高雄漢神店": "-OUYVD5L8af9l-fOxBi5",
}
MIN_INTERVAL = 4
DEFAULT_INTERVAL = 120
MAX_INTERVAL = 86400
ACTION_INTERVAL = 2.0
STATE_SETTLE_SECONDS = 0.8
MAX_DAYS = 7
CALENDAR_MONTHS = 12
CALENDAR = '#calendar-picker'
AVAILABLE_DAY = '[data-cy="bt-cal-day"][data-date]:not([disabled]):not([aria-disabled="true"])'
SETTINGS_ENV = Path(__file__).resolve().with_name(".env")
BROWSER_PROFILE = Path(__file__).resolve().parent / ".browser-native-profile"
BOOKING_KEY = "ISLAND_BOOKING_PROFILE"
MAIL_KEY = "ISLAND_MAIL_SETTINGS"
LEGACY_MAIL_FILE = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "IslandInlineWatcher" / "email.json"
DAY_OPTIONS = ("不限", "只限週六、週日", "台灣放假日（2026–2027）", "台灣上班日（2026–2027）")
ANY_TABLE = "不限桌型"
TABLE_TYPES = ("一般", "板前吧台")
TABLE_OPTIONS = (ANY_TABLE, *TABLE_TYPES)
INLINE_TERMS = "https://inline.app/tos"
INLINE_PRIVACY = "https://inline.app/privacy"
# Weekday exceptions from the DGPA 2026 and 2027 government office calendar CSVs.
# Source: https://data.gov.tw/en/datasets/14718 (Open Government Data License 1.0).
HOLIDAY_YEARS = (2026, 2027)
WEEKDAY_HOLIDAYS = frozenset("""
2026-01-01 2026-02-16 2026-02-17 2026-02-18 2026-02-19 2026-02-20 2026-02-27
2026-04-03 2026-04-06 2026-05-01 2026-06-19 2026-09-25 2026-09-28 2026-10-09
2026-10-26 2026-12-25 2027-01-01 2027-02-04 2027-02-05 2027-02-08 2027-02-09
2027-02-10 2027-03-01 2027-04-05 2027-04-06 2027-04-30 2027-06-09 2027-09-15
2027-09-28 2027-10-11 2027-10-25 2027-12-24 2027-12-31
""".split())

BROWSER_VERIFICATION = r"""
() => {
    const visible = node => {
        const {width, height} = node.getBoundingClientRect();
        if (!width || !height) return false;
        for (let parent = node; parent; parent = parent.parentElement) {
            const style = getComputedStyle(parent);
            if (style.display === 'none' || style.visibility !== 'visible' || style.opacity === '0') return false;
        }
        return true;
    };
    const challenge = document.querySelectorAll(
        '[id^="px-captcha"], iframe[title*="human verification" i], '
        + 'iframe[src*="captcha.px" i], iframe[src*="captcha.perimeterx" i]',
    );
    const message = /按[壓住]不放以確認您是人類|press(?:&|and)holdtoconfirm/i;
    const textNodes = [...document.querySelectorAll('p, span, div, h1, h2')];
    return [...challenge].some(visible) || textNodes.some(node =>
        message.test(node.innerText?.replace(/\s+/g, '') || '') && visible(node)
        && ![...node.children].some(child => message.test(child.innerText?.replace(/\s+/g, '') || ''))
    ) || /verify (?:you are|that you are) human|human verification|press (?:&|and) hold|人機驗證/.test(
        document.title.toLowerCase(),
    );
}
"""
VERIFICATION_DETAIL = "inline 要求瀏覽器人機驗證。"
BOOKING_FORM_READY = r"""
branch => {
    const detectBrowserVerification = """ + BROWSER_VERIFICATION + r""";
    if (detectBrowserVerification()) return 'blocked';
    const heading = [...document.querySelectorAll('h1')].some(node =>
        node.textContent.replace(/\s+/g, '') === ('島語' + branch).replace(/\s+/g, ''));
    return heading && !!document.querySelector('#book-now #adult-picker')
        && !!document.querySelector('#book-now #kid-picker')
        && !!document.querySelector('#book-now #date-picker');
}
"""
QUERY_PAGE_STATE = r"""
() => {
    const form = document.querySelector('#book-now');
    const content = form?.querySelector('#book-now-content');
    return {
        ready: document.readyState,
        formPresent: !!form,
        adults: form?.querySelector('#adult-picker')?.value,
        kids: form?.querySelector('#kid-picker')?.value,
        selectedDate: form?.querySelector('#date-picker')?.textContent.trim(),
        contentVisible: !!content?.getClientRects().length,
        loading: /正在查詢|載入中|請稍候/.test(content?.innerText || ''),
        slotButtons: content?.querySelectorAll('button[data-cy^="book-now-time-slot-box-"]').length || 0,
    };
}
"""

# Read only the booking form. Inline renders full/waitlist slots as buttons too,
# with a separate data-cy prefix from slots that can be booked.
READ_FORM = r"""
({branch, month, day, adults, kids, selectionOnly = false}) => {
    const text = node => (node?.textContent || '').trim();
    const visible = node => node && node.getClientRects().length > 0
        && getComputedStyle(node).visibility === 'visible';
    const detectBrowserVerification = """ + BROWSER_VERIFICATION + r""";
    if (detectBrowserVerification()) return {blocked: true};

    const form = document.querySelector('#book-now');

    const heading = [...document.querySelectorAll('h1')].find(node =>
        text(node).replace(/\s+/g, '') === ('島語' + branch).replace(/\s+/g, ''));
    const content = form?.querySelector('#book-now-content');
    if (!heading || !visible(content)
        || form.querySelector('#adult-picker')?.value !== String(adults)
        || form.querySelector('#kid-picker')?.value !== String(kids)
        || ![...form.querySelectorAll('#calendar-picker h4')].some(node => text(node).replace(/\s+/g, '') === month)
        || !text(form.querySelector('#date-picker')).startsWith(day)) {
        return false;
    }
    if (selectionOnly) return true;
    if (/正在查詢|載入中|請稍候/.test(content.innerText)) return false;

    const slots = [...content.querySelectorAll('button[data-cy^="book-now-time-slot-box-"]')]
        .filter(node => visible(node) && !node.disabled && node.getAttribute('aria-disabled') !== 'true')
        .map(node => {
            const match = node.getAttribute('data-cy').match(/-(\d{1,2})-(\d{2})$/);
            return match ? match[1].padStart(2, '0') + ':' + match[2] : null;
        })
        .filter(Boolean);
    if (slots.length) return {slots: [...new Set(slots)].sort()};

    const full = content.querySelector('[data-cy="booking-full-text"]');
    const fullSlots = [...content.querySelectorAll('button[data-cy^="book-now-full-time-slot-box-"]')];
    if (visible(full)) return {slots: [], detail: text(full)};
    if (fullSlots.some(visible)) return {slots: [], detail: '顯示的時段皆已額滿'};
    return false;
}
"""


@dataclass(frozen=True)
class Query:
    branch: str
    start: date | None
    end: date | None
    adults: int
    kids: int
    interval: int
    day_kind: str = DAY_OPTIONS[0]
    earliest: str = "00:00"
    latest: str = "23:59"

    def matches_date(self, day):
        if self.day_kind == DAY_OPTIONS[0]:
            return True
        weekend = day.weekday() >= 5
        if self.day_kind == DAY_OPTIONS[1]:
            return weekend
        if day.year not in HOLIDAY_YEARS:
            return False
        holiday = weekend or day.isoformat() in WEEKDAY_HOLIDAYS
        return holiday == (self.day_kind == DAY_OPTIONS[2])

    def booking_url(self):
        return f"{BOOKING_ROOT}/{BRANCHES[self.branch]}?language=zh-tw"

    def url(self, day):
        payload = {
            "language": "zh-tw",
            "date": day.isoformat(),
            "adults": self.adults,
            "kids": self.kids,
        }
        encoded = base64.b64encode(json.dumps(payload).encode("utf-8")).decode("ascii")
        params = urlencode({"language": "zh-tw", "pre_filled_form": encoded})
        return f"{BOOKING_ROOT}/{BRANCHES[self.branch]}?{params}"


@dataclass(frozen=True)
class Result:
    day: date
    slots: tuple[str, ...] = ()
    detail: str = ""
    failed: bool = False
    stop: bool = False
    needs_verification: bool = False
    discovery_only: bool = False
    retryable: bool = False


def read_saved_settings(key):
    if not SETTINGS_ENV.is_file():
        return None
    value = dotenv_values(SETTINGS_ENV, encoding="utf-8-sig", interpolate=False).get(key)
    if value is None:
        return None
    settings = json.loads(value)
    if not isinstance(settings, dict):
        raise ValueError("設定格式錯誤。")
    return settings


def write_saved_settings(key, settings):
    # JSON quoting produces one dotenv value even when the settings contain
    # quotes, backslashes, or dollar signs.
    value = json.dumps(json.dumps(settings, ensure_ascii=False), ensure_ascii=False)
    with tempfile.NamedTemporaryFile(dir=SETTINGS_ENV.parent, prefix=".env.", delete=False) as file:
        temporary = Path(file.name)
    try:
        if SETTINGS_ENV.exists():
            temporary.write_bytes(SETTINGS_ENV.read_bytes().removeprefix(b"\xef\xbb\xbf"))
        set_key(temporary, key, value, quote_mode="never", encoding="utf-8")
        temporary.replace(SETTINGS_ENV)
    finally:
        temporary.unlink(missing_ok=True)


@dataclass(frozen=True)
class BookingProfile:
    family_name: str
    given_name: str
    phone: str
    email: str
    table: str

    @classmethod
    def from_fields(cls, family_name, given_name, phone, email, table):
        family_name, given_name = family_name.strip(), given_name.strip()
        if not family_name or not given_name:
            raise ValueError("請填寫訂位人的姓和名。")
        phone = re.sub(r"[\s-]", "", phone)
        if not re.fullmatch(r"09[0-9]{8}", phone):
            raise ValueError("請輸入可收簡訊的台灣手機門號（09 開頭，10 碼）。")
        email = email.strip()
        if email:
            try:
                parsed = Address(addr_spec=email)
                if not parsed.username or not parsed.domain:
                    raise ValueError()
                email = parsed.addr_spec
            except (HeaderParseError, ValueError):
                raise ValueError("請輸入完整的聯絡 Email。") from None
        if table not in TABLE_OPTIONS:
            raise ValueError("請選擇用餐桌型。")
        return cls(family_name, given_name, phone, email, table)

    @classmethod
    def load(cls):
        values = read_saved_settings(BOOKING_KEY)
        return cls.from_fields(**values) if values is not None else None

    def save(self):
        write_saved_settings(BOOKING_KEY, asdict(self))


@dataclass(frozen=True)
class MailSettings:
    host: str
    port: int
    security: str
    sender: str
    recipients: tuple[str, ...]
    reply_verification: bool = False

    @classmethod
    def from_fields(cls, host, port, security, sender, recipients, reply_verification=False):
        host = host.strip()
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]*", host):
            raise ValueError("請輸入 SMTP 主機名稱，例如 smtp.gmail.com。")
        if not re.fullmatch(r"[0-9]{1,5}", port) or not 1 <= int(port) <= 65535:
            raise ValueError("SMTP 連接埠必須介於 1 和 65535。")
        if security not in ("SSL/TLS", "STARTTLS"):
            raise ValueError("請選擇 SSL/TLS 或 STARTTLS。")

        def address(value):
            try:
                parsed = Address(addr_spec=value.strip())
            except (HeaderParseError, TypeError, ValueError):
                raise ValueError("請使用完整的 Email 地址。") from None
            if not parsed.username or not parsed.domain:
                raise ValueError("請使用完整的 Email 地址。")
            return parsed.addr_spec

        sender = address(sender)
        if not isinstance(reply_verification, bool):
            raise ValueError("回信驗證設定格式錯誤。")
        if reply_verification and (
            host.casefold() != GMAIL_SMTP or sender.rpartition("@")[2].casefold() != "gmail.com"
        ):
            raise ValueError("回信驗證請使用 Gmail 寄件帳號和 smtp.gmail.com。")
        unique = {}
        for part in re.split(r"[,;，；\n]", recipients):
            if part.strip():
                recipient = address(part)
                unique.setdefault(recipient.casefold(), recipient)
        if not unique:
            raise ValueError("至少設定一個收件人。")
        return cls(host, int(port), security, sender, tuple(unique.values()), reply_verification)

    @classmethod
    def load(cls):
        values = read_saved_settings(MAIL_KEY)
        password = ""
        if values is None:
            values = json.loads(LEGACY_MAIL_FILE.read_text(encoding="utf-8"))
        else:
            password = values.get("password", "")
            if not isinstance(password, str):
                raise ValueError("SMTP 密碼格式錯誤。")
        settings = cls.from_fields(
            values["host"], str(values["port"]), values["security"],
            values["sender"], ",".join(values["recipients"]), values.get("reply_verification", False),
        )
        return settings, password

    def save(self, password=""):
        values = asdict(self)
        if password:
            values["password"] = password
        write_saved_settings(MAIL_KEY, values)


@contextmanager
def mail_connection(settings, password):
    tls = ssl.create_default_context()
    if settings.security == "SSL/TLS":
        client = smtplib.SMTP_SSL(settings.host, settings.port, timeout=12, context=tls)
    else:
        client = smtplib.SMTP(settings.host, settings.port, timeout=12)
    try:
        if settings.security == "STARTTLS":
            client.starttls(context=tls)
        client.login(settings.sender, password)
        yield client
    finally:
        client.close()


def mail_message(sender, recipient, subject, body):
    message = EmailMessage()
    message["From"] = sender
    message["To"] = recipient
    message["Subject"] = subject
    message.set_content(body)
    return message


def notification_body(query, slots, booking_note=""):
    lines = [
        f"島語 {query.branch} 有可訂時段",
        f"人數：{query.adults} 位大人、{query.kids} 位小孩",
        f"發現時間：{datetime.now(TAIPEI):%Y-%m-%d %H:%M:%S}（台灣時間）",
        "",
    ]
    for day in sorted({day for day, _ in slots}):
        times = "、".join(sorted(time for d, time in slots if d == day))
        lines.extend([f"{day.isoformat()}　{times}", query.url(day), ""])
    if booking_note:
        lines.extend([booking_note, "請使用執行監看的電腦上已開啟的 Chrome 視窗。", ""])
    return "\n".join(lines + ["請以開啟 inline 時顯示的最新空位為準。"])


def send_emails(
    settings, password, events, query=None, slots=(), delivered=None, cancel=None,
    booking_note="", reply_message_id=None,
):
    if delivered is None:
        delivered = {}
    pending = {settings.sender: slots} if reply_message_id else {}
    recipients = settings.recipients
    if query is None and settings.reply_verification and all(
        recipient.casefold() != settings.sender.casefold() for recipient in recipients
    ):
        recipients = (*recipients, settings.sender)
    for recipient in recipients:
        if reply_message_id and recipient.casefold() == settings.sender.casefold():
            continue
        if query is None:
            pending[recipient] = None
        else:
            new_slots = slots - delivered.get(recipient, set())
            if new_slots:
                pending[recipient] = new_slots
    if not pending or (cancel and cancel.is_set()):
        return False

    reply_sent = False
    accepted = 0
    error = ""
    try:
        with mail_connection(settings, password) as client:
            for recipient, new_slots in pending.items():
                if cancel and cancel.is_set():
                    break
                if query is None:
                    subject = "島語 inline 空位監看｜測試信"
                    body = "這封信由島語 inline 空位監看程式寄出，用來確認 Email 設定。"
                else:
                    subject = f"島語 {query.branch}｜發現可訂時段"
                    body = notification_body(query, new_slots, booking_note)
                can_reply = bool(reply_message_id and recipient == settings.sender)
                if can_reply:
                    subject = f"島語 {query.branch}｜請回覆簡訊驗證碼｜{reply_message_id[1:9]}"
                    body = (
                        f"請用收到這封信的 Gmail 回覆原信，第一行只填 inline 簡訊上的 {CODE_LENGTH} 位數字。"
                        "請在頁面顯示的期限內回覆，並保持監看程式和原本的 Chrome 頁面開啟。\n"
                        "驗證碼會保留在 Gmail 回信中。\n\n" + body
                    )
                message = mail_message(settings.sender, recipient, subject, body)
                if can_reply:
                    message["Message-ID"] = reply_message_id
                    message["Reply-To"] = settings.sender
                try:
                    refused = client.send_message(message)
                except smtplib.SMTPRecipientsRefused:
                    continue
                if not refused:
                    if query is not None:
                        delivered.setdefault(recipient, set()).update(new_slots)
                    accepted += 1
                    reply_sent |= can_reply
    except smtplib.SMTPAuthenticationError:
        error = "驗證失敗，請確認寄件帳號與應用程式密碼。"
    except (OSError, UnicodeError, smtplib.SMTPException):
        error = "無法完成寄送，請確認 SMTP 主機、加密方式與網路。"
    title = "測試信" if query is None else "空位通知"
    detail = f"{title}：SMTP 已接受 {accepted}/{len(pending)} 位收件人。"
    if error:
        detail += error
    elif accepted < len(pending):
        detail += "其餘收件人尚未送出。"
    if query is None and settings.reply_verification and accepted:
        try:
            with gmail_mailbox(settings.sender, password):
                detail += " Gmail IMAP 連線成功。"
        except IMAP_ERRORS:
            detail += " Gmail IMAP 連線失敗，請確認 Gmail 帳號與應用程式密碼。"
    events.put(("mail_test" if query is None else "mail", detail))
    return reply_sent


def taipei_today():
    return datetime.now(TAIPEI).date()


def parse_date(value):
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value.strip()):
        raise ValueError("請使用 YYYY-MM-DD 格式輸入日期。")
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        raise ValueError("請輸入有效的日期。") from None


def parse_time(value, default):
    value = value.strip()
    if not value:
        return default
    if not re.fullmatch(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]", value):
        raise ValueError("時間請用 24 小時制 HH:MM，例如 17:30。")
    return value


class BrowserVerificationRequired(Exception):
    pass


class BrowserOperationCancelled(Exception):
    pass


class QueryTrace:
    """Record timings and fixed step names, never page content or form values."""

    def __init__(self):
        self.started = monotonic()
        self.file = None
        self.path = Path(__file__).resolve().parent / "logs" / (
            datetime.now(TAIPEI).strftime("query-%Y%m%d-%H%M%S-") + uuid4().hex[:8] + ".jsonl"
        )
        try:
            self.path.parent.mkdir(exist_ok=True)
            self.file = self.path.open("w", encoding="utf-8")
        except OSError:
            pass

    def record(self, event, stage, **timings):
        if self.file:
            try:
                self.file.write(json.dumps({
                    "elapsed": round(monotonic() - self.started, 3),
                    "event": event, "stage": stage, **timings,
                }, ensure_ascii=False) + "\n")
                self.file.flush()
            except OSError:
                self.close()

    def close(self):
        if self.file:
            with suppress(OSError):
                self.file.close()
            self.file = None


class BrowserFlow:
    """Pace page mutations and wait for inline's availability requests to finish."""

    def __init__(self, page, cancel, trace=None):
        self.page, self.cancel, self.trace = page, cancel, trace
        self.last_action = None
        self.pending = set()
        page.on("request", self.request_started)
        page.on("requestfinished", lambda request: self.pending.discard(request))
        page.on("requestfailed", lambda request: self.pending.discard(request))

    def request_started(self, request):
        host = urlparse(request.url).hostname or ""
        if request.resource_type in ("fetch", "xhr") and (host == "inline.app" or host.endswith(".inline.app")):
            self.pending.add(request)

    def check(self):
        if self.cancel.is_set():
            raise BrowserOperationCancelled
        if self.page.evaluate(BROWSER_VERIFICATION):
            raise BrowserVerificationRequired

    def perform(self, stage, operation):
        while self.last_action is not None and monotonic() - self.last_action < ACTION_INTERVAL:
            self.check()
            self.page.wait_for_timeout(100)
        self.check()
        started = monotonic()
        if self.trace:
            self.trace.record("action", stage, gap=None if self.last_action is None else round(started - self.last_action, 3))
        self.last_action = started
        try:
            result = operation()
            self.check()
        except (BrowserError, BrowserVerificationRequired, BrowserOperationCancelled) as exc:
            if self.trace:
                self.trace.record("interrupted", stage, reason=type(exc).__name__, duration=round(monotonic() - started, 3))
            raise
        if self.trace:
            self.trace.record("complete", stage, duration=round(monotonic() - started, 3))
        return result


def slot_form_args(query, day):
    return {
        "branch": query.branch,
        "month": f"{day.year}年{day.month}月",
        "day": f"{day.month}月{day.day}日",
        "adults": query.adults,
        "kids": query.kids,
    }


def read_slot_state(page, query, day, timeout=20000, *, flow=None):
    deadline = monotonic() + timeout / 1000
    previous, stable_since = None, monotonic()
    while monotonic() < deadline:
        if flow and flow.cancel.is_set():
            raise BrowserOperationCancelled
        state = page.evaluate(READ_FORM, slot_form_args(query, day))
        if state and state.get("blocked"):
            return state
        now = monotonic()
        if not state or state != previous or (flow and flow.pending):
            stable_since = now
        elif now - stable_since >= STATE_SETTLE_SECONDS:
            if flow and flow.trace:
                flow.trace.record("state_ready", "讀取時段", slot_count=len(state["slots"]))
            return state
        previous = state
        page.wait_for_timeout(100)
    raise BrowserTimeout("等待 inline 時段更新逾時")


def verification_message(stage):
    return f"{VERIFICATION_DETAIL} 偵測時正在「{stage}」。"


def verification_result(day, stage):
    return Result(day, detail=verification_message(stage), failed=True, stop=True, needs_verification=True)


def active_browser_stop(page, day, stage):
    if page.is_closed():
        return Result(day, detail="查詢用的 Chrome 視窗已關閉。", failed=True, stop=True)
    with suppress(BrowserError):
        if page.evaluate(BROWSER_VERIFICATION):
            return verification_result(day, stage)
    return None


def open_calendar(page, flow):
    calendar = page.locator(CALENDAR)
    picker = page.locator("#book-now #date-picker")
    if picker.get_attribute("aria-expanded", timeout=4000) == "false":
        flow.perform("展開日曆", lambda: picker.click(timeout=5000))
    calendar.wait_for(state="visible", timeout=5000)
    return calendar


def calendar_month(calendar):
    label = calendar.locator("h4").first.inner_text(timeout=3000)
    match = re.fullmatch(r"\s*(\d{4})年\s*(\d{1,2})月\s*", label)
    if not match:
        raise ValueError("無法辨識 inline 的月份")
    return int(match[1]), int(match[2])


def move_calendar(page, calendar, direction, flow):
    button = calendar.locator(f'.{direction}Month:visible:not([disabled]):not([aria-disabled="true"])')
    if not button.count():
        return False
    previous = calendar.locator("h4").first.inner_text(timeout=3000)
    flow.perform("切換日曆月份", lambda: button.click(timeout=5000))
    page.wait_for_function(
        "(previous) => { const h = document.querySelector('#calendar-picker h4'); return h && h.innerText !== previous; }",
        arg=previous,
        timeout=5000,
    )
    return True


def select_booking_date(page, day, cancel, *, flow=None):
    flow = flow or BrowserFlow(page, cancel)
    calendar = open_calendar(page, flow)
    target_month = (day.year, day.month)
    for _ in range(CALENDAR_MONTHS):
        if cancel.is_set():
            return False
        flow.check()
        # Desktop inline renders two months. A date in the second panel is
        # already clickable; moving the month would be redundant.
        target = calendar.locator(f'{AVAILABLE_DAY}[data-date="{day.isoformat()}"]:visible')
        if target.count():
            flow.perform("選擇查詢日期", lambda: target.click(timeout=5000))
            return True
        month = calendar_month(calendar)
        if month == target_month:
            break
        direction = "next" if month < target_month else "prev"
        if not move_calendar(page, calendar, direction, flow):
            break
    flow.perform("收起日曆", lambda: page.locator('#date-picker[aria-expanded="true"]').click(timeout=5000))
    return False


def selected_booking_date(page) -> date | None:
    picked = page.locator('#book-now #date-picker').inner_text(timeout=3000)
    match = re.match(r"(\d{1,2})月(\d{1,2})日", picked)
    if not match:
        return None
    month, day = map(int, match.groups())
    labels = page.locator(f'{CALENDAR} h4').all_text_contents()
    for label in labels:
        heading = re.fullmatch(r"\s*(\d{4})年\s*(\d{1,2})月\s*", label)
        if heading and int(heading[2]) == month:
            return date(int(heading[1]), month, day)
    return None


def check_day(page, query, day, cancel, *, refresh, flow=None, select_date=True):
    flow = flow or BrowserFlow(page, cancel)
    stage = "開啟或切換查詢日期"
    try:
        stopped = active_browser_stop(page, day, stage)
        if stopped or cancel.is_set():
            return stopped or Result(day, failed=True)
        url = query.booking_url()
        booking = urlparse(url)
        current = urlparse(page.url)
        same_branch = (
            current.scheme == booking.scheme and current.netloc == booking.netloc
            and unquote(current.path).rstrip("/") == booking.path
        )
        response = None
        if not same_branch:
            flow.pending.clear()
            response = flow.perform("載入分店頁面", lambda: page.goto(url, wait_until="domcontentloaded", timeout=25000))
        elif refresh:
            flow.pending.clear()
            response = flow.perform("重新整理分店頁面", lambda: page.reload(wait_until="domcontentloaded", timeout=25000))
        if response and response.status in (401, 403, 429):
            try:
                page.wait_for_function(BROWSER_VERIFICATION, polling=250, timeout=2000)
            except BrowserTimeout:
                return Result(day, detail=f"inline 拒絕或限制存取（HTTP {response.status}）。", failed=True, stop=True)
            return verification_result(day, stage)
        if response and response.status >= 400:
            return Result(day, detail=f"inline 回傳 HTTP {response.status}。", failed=True,
                          stop=True, retryable=response.status >= 500)

        ready = page.wait_for_function(BOOKING_FORM_READY, arg=query.branch, polling=250, timeout=20000).json_value()
        if ready == "blocked":
            return verification_result(day, stage)
        # Reused pages can also already show the requested date and party size.
        state = page.evaluate(READ_FORM, slot_form_args(query, day))
        if state:
            stage = "讀取時段"
            state = read_slot_state(page, query, day, flow=flow)
            if state.get("blocked"):
                return verification_result(day, stage)
            return Result(day, tuple(state["slots"]), state.get("detail", ""))

        stage = "設定查詢人數"
        for field, count in (("adult-picker", query.adults), ("kid-picker", query.kids)):
            if cancel.is_set():
                return Result(day, failed=True)
            picker = page.locator(f"#book-now #{field}")
            if picker.input_value(timeout=4000) != str(count):
                flow.perform(stage, lambda: picker.select_option(value=str(count), timeout=5000))
        if cancel.is_set():
            return Result(day, failed=True)
        stage = "選擇查詢日期"
        if not select_date:
            # After midnight the site's initial selection can still be yesterday.
            selected_day = selected_booking_date(page)
            if selected_day is None:
                return Result(day, detail="inline 預設日期尚未確認", discovery_only=True)
            day = selected_day
            if day < taipei_today():
                return Result(day, detail="略過 inline 已過的預設日期", discovery_only=True)
        # Changing party size may already leave the correct date selected.
        selected = page.evaluate(READ_FORM, {**slot_form_args(query, day), "selectionOnly": True})
        if select_date and not selected and not select_booking_date(page, day, cancel, flow=flow):
            return active_browser_stop(page, day, stage) or Result(day, detail="inline 日曆未開放這一天。")
        stage = "讀取時段"
        state = read_slot_state(page, query, day, flow=flow)
        if state.get("blocked"):
            return verification_result(day, stage)
        return Result(day, tuple(state["slots"]), state.get("detail", ""))
    except BrowserOperationCancelled:
        return Result(day, failed=True)
    except BrowserVerificationRequired:
        return verification_result(day, stage)
    except BrowserTimeout:
        stopped = active_browser_stop(page, day, stage)
        if stopped:
            return stopped
        if query.start is None and stage == "讀取時段" and day < taipei_today():
            return Result(day, detail="讀取時段時已跨日，略過已過的日期", discovery_only=True)
        if flow.trace:
            with suppress(BrowserError):
                flow.trace.record("query_timeout", stage, page_state=page.evaluate(QUERY_PAGE_STATE),
                                  pending_requests=len(flow.pending))
        return Result(
            day, detail=f"{stage}逾時，本輪查詢未完成。", failed=True, stop=True, retryable=True,
        )
    except (BrowserError, ValueError):
        return active_browser_stop(page, day, stage) or Result(
            day, detail=f"{stage}失敗，本輪查詢未完成。", failed=True, stop=True, retryable=True,
        )


def calendar_dates(page, query, today, cancel, *, flow=None):
    """Use the calendar's selectable days as candidates, then verify their slots."""
    flow = flow or BrowserFlow(page, cancel)
    flow.check()
    page.wait_for_function(
        r"""({branch, adults, kids}) =>
            [...document.querySelectorAll('h1')].some(n => n.textContent.replace(/\s+/g, '') === '島語' + branch)
            && document.querySelector('#adult-picker')?.value === String(adults)
            && document.querySelector('#kid-picker')?.value === String(kids)""",
        arg={"branch": query.branch, "adults": query.adults, "kids": query.kids},
        timeout=3000,
    )
    # This summary is a sibling of #book-now-content on the real page.
    other_dates = page.locator('#book-now [data-cy="see-other-dates-or-pax"]:visible')
    remaining = None
    if other_dates.count():
        match = re.search(r"還有\s*(\d+)\s*個日期可選", other_dates.inner_text(timeout=2000))
        if match:
            remaining = int(match[1])

    selected_day = selected_booking_date(page)
    calendar = open_calendar(page, flow)
    limit = date(today.year + 1, today.month, 1)
    days = set()
    if selected_day is not None and today <= selected_day < limit:
        days.add(selected_day)

    for index in range(CALENDAR_MONTHS):
        if cancel.is_set():
            break
        flow.check()
        previous_count = len(days)
        visible_dates = calendar.locator(AVAILABLE_DAY).evaluate_all("nodes => nodes.map(n => n.dataset.date)")
        days.update(day for value in visible_dates if today <= (day := date.fromisoformat(value)) < limit)
        other_days = days - {selected_day}
        if remaining is not None and len(other_days) >= remaining:
            return sorted(days), ""
        if remaining is None:
            return sorted(days), "已讀取目前日曆的可訂日期；網站未提供日期總數，未自動翻月，掃描可能不完整。"
        if index > 0 and len(days) == previous_count:
            break
        if index == CALENDAR_MONTHS - 1 or not move_calendar(page, calendar, "next", flow):
            break
    return sorted(days), "日曆與公布的可訂日期數不一致，已停止翻月，掃描可能不完整。"


def matching_times(query, result):
    if not result.slots:
        return result
    slots = tuple(slot for slot in result.slots if query.earliest <= slot <= query.latest)
    if not slots:
        return replace(result, slots=(), detail="inline 有空位，但皆不在設定的時段內")
    return replace(result, slots=slots)


def booking_summary_matches(summary, query, day, time, table):
    lines = {re.sub(r"\s+", "", line) for line in summary.splitlines()}
    party = f"{query.adults}大"
    if query.kids:
        party += f",{query.kids}小"
    normalized = {line.replace("，", ",") for line in lines}
    return (
        f"您已選擇預訂島語{query.branch}：" in lines
        and f"{day.year}年{day.month}月{day.day}日" in lines
        and time in lines
        and party in normalized
        and (not table or table in lines)
    )


@dataclass(frozen=True)
class BookingHandoff:
    note: str
    verification_url: str = ""
    submitted: bool = False
    needs_verification: bool = False


def check_booking_verification(page):
    if page.evaluate(BROWSER_VERIFICATION):
        raise BrowserVerificationRequired


def wait_booking_summary(page, action, query, day, time, table, flow):
    deadline = monotonic() + 5
    while monotonic() < deadline:
        flow.check()
        if action.is_visible():
            summary = action.evaluate("node => node.parentElement.parentElement.innerText", timeout=3000)
            if booking_summary_matches(summary, query, day, time, table):
                return True
        page.wait_for_timeout(100)
    return False


def start_booking(page, query, result, profile, cancel, *, flow=None, preview=False):
    """Open one contact form and submit it once, then describe the handoff."""
    flow = flow or BrowserFlow(page, cancel)
    entering_form = False
    submitting = False
    verification_url = ""
    stage = "選擇桌型"
    try:
        if cancel.is_set() or urlparse(page.url).hostname != "inline.app":
            return None
        check_booking_verification(page)
        table = page.locator("#table-tag-selector:visible")
        if table.count():
            candidates = TABLE_TYPES if profile.table == ANY_TABLE else (profile.table,)
        elif profile.table in (ANY_TABLE, TABLE_TYPES[0]):
            candidates = ("",)
        else:
            return None

        action = page.locator('button[data-cy="book-now-action-button"]:visible')
        selected_time = ""
        for table_name in candidates:
            if cancel.is_set():
                return None
            times = result.slots
            stage = "選擇桌型"
            try:
                check_booking_verification(page)
                if table_name:
                    option = table.get_by_role("button", name=table_name, exact=True)
                    if not option.count() or not option.is_enabled(timeout=3000):
                        continue
                    if option.get_attribute("aria-pressed", timeout=3000) != "true":
                        flow.perform(stage, lambda: option.click(timeout=4000))
                        state = read_slot_state(page, query, result.day, timeout=10000, flow=flow)
                        if state.get("blocked"):
                            raise BrowserVerificationRequired
                        times = tuple(time for time in times if time in state["slots"])
                    if option.get_attribute("aria-pressed", timeout=3000) != "true":
                        continue
                stage = "選擇時段"
                for time in sorted(times):
                    if cancel.is_set():
                        return None
                    check_booking_verification(page)
                    hour, minute = time.split(":")
                    alternatives = (f"{hour}-{minute}", f"{int(hour)}-{minute}")
                    attributes = ", ".join(f'[data-cy="book-now-time-slot-box-{key}"]' for key in alternatives)
                    slot = page.locator(f'#book-now-content button:is({attributes}):visible')
                    if not slot.count() or not slot.is_enabled(timeout=3000):
                        continue
                    flow.perform(stage, lambda: slot.click(timeout=4000))
                    if wait_booking_summary(page, action, query, result.day, time, table_name, flow):
                        selected_time = time
                        break
            except BrowserTimeout:
                check_booking_verification(page)
                raise
            if selected_time:
                break
        if not selected_time or cancel.is_set():
            return None

        check_booking_verification(page)
        if preview:
            return BookingHandoff("訂位摘要已核對，測試停在開啟聯絡表單前。")
        stage = "開啟訂位表單"
        entering_form = True
        flow.perform(stage, lambda: action.click(timeout=6000))
        form = page.locator("form#contact-form:visible")
        form.wait_for(state="visible", timeout=10000)
        if cancel.is_set():
            return None
        if urlparse(page.url).hostname != "inline.app":
            return BookingHandoff("已停止自動操作，請檢查 Chrome 的訂位頁面。")
        check_booking_verification(page)
        stage = "填寫聯絡資料"
        verification_url = page.url
        country = form.evaluate(
            """form => [...form.querySelectorAll('select')].find(select =>
                [...select.options].some(option => option.textContent.trim() === 'Taiwan +886')
            )?.selectedOptions[0]?.textContent.trim()""",
            timeout=3000,
        )
        if country != "Taiwan +886":
            return BookingHandoff("已開啟訂位表單，請到 Chrome 確認手機國碼。", verification_url)

        for field, value in (
            ("familyName", profile.family_name), ("givenName", profile.given_name),
            ("phone", profile.phone), ("email", profile.email),
        ):
            if cancel.is_set():
                return None
            if value:
                flow.perform(stage, lambda: form.locator(f'input[data-cy="{field}"]').fill(value, timeout=5000))
        check_booking_verification(page)
        deposit_policy = form.locator('#deposit-policy:visible')
        if deposit_policy.count():
            return BookingHandoff("聯絡資料已填好。請到 Chrome 閱讀訂金／取消條款並繼續訂位。", verification_url)
        if (form.locator('input[aria-invalid="true"]:visible').count()
                or not form.evaluate("form => form.checkValidity()", timeout=3000)):
            return BookingHandoff("請到 Chrome 檢查已填的聯絡資料，再確認訂位。", verification_url)
        if cancel.is_set():
            return None
        submit = form.locator('button[data-cy="submit"]:visible').filter(
            has_text=re.compile(r"^\s*確認訂位\s*$"),
        )
        check_booking_verification(page)
        stage = "送出確認訂位"
        submitting = True
        flow.perform(stage, lambda: submit.click(timeout=6000))
        check_booking_verification(page)
        selected_table = f"（{table_name}）" if table_name else ""
        return BookingHandoff(
            f"{result.day.isoformat()} {selected_time}{selected_table} 已點選確認訂位。"
            "請到 Chrome 查看訂位狀態。",
            verification_url, submitted=True,
        )
    except BrowserOperationCancelled:
        return BookingHandoff("已停止自動訂位，請到 Chrome 檢查目前狀態。") if entering_form else None
    except (BrowserVerificationRequired, BrowserError) as exc:
        stopped = active_browser_stop(page, result.day, stage)
        if isinstance(exc, BrowserVerificationRequired) or (stopped and stopped.needs_verification):
            note = verification_message(stage) + " 自動訂位已暫停，請到 Chrome 查看。"
            if submitting:
                note += " 請先確認訂位是否已送出。"
            return BookingHandoff(note, needs_verification=True)
        if submitting:
            return BookingHandoff("無法確認表單是否已送出，請到 Chrome 檢查目前狀態。", verification_url)
        if entering_form:
            return BookingHandoff("已暫停自動訂位，請到 Chrome 檢查表單。", verification_url)
        return BookingHandoff(f"{stage}失敗，已停止自動訂位，請到 Chrome 檢查目前狀態。")


def notify_and_verify_booking(page, settings, password, query, slots, handoff, events, cancel):
    checkout = None
    challenge = None
    if settings.reply_verification and handoff.verification_url and not cancel.is_set():
        try:
            if not page.is_closed() and page.url == handoff.verification_url:
                checkout = page.query_selector("form#contact-form")
                if checkout and handoff.submitted:
                    challenge = find_challenge(page, checkout, handoff.verification_url, cancel, timeout=8)
        except BrowserError:
            checkout = None

    if challenge is None:
        send_emails(settings, password, events, query, slots, {}, cancel, handoff.note)
        if checkout is None:
            return
        try:
            challenge = find_challenge(page, checkout, handoff.verification_url, cancel)
        except BrowserError:
            return
        if challenge is None:
            return
        # The other recipients already got the regular notification.
        settings = replace(settings, recipients=(settings.sender,))

    deadline = monotonic() + REPLY_WAIT_SECONDS
    message_id = f"<{uuid4().hex}@gmail.com>"
    reply_sent = send_emails(settings, password, events, query, slots, {}, cancel, handoff.note, message_id)
    if cancel.is_set():
        return
    if not reply_sent:
        events.put(("status", "驗證通知寄送失敗。請到原本的 Chrome 視窗手動驗證。"))
        return

    events.put(("status", f"正在等 Gmail 回信。請回覆原信，第一行只填簡訊上的 {CODE_LENGTH} 位數字。"))
    try:
        submitted = wait_for_reply(settings, password, message_id, deadline, cancel, challenge)
        detail = (
            "已送出回信中的驗證碼。請查看 Chrome 或 inline 通知確認訂位結果。"
            if submitted else "已結束等候回信。請到原本的 Chrome 視窗查看或手動驗證。"
        )
    except IMAP_ERRORS:
        detail = "Gmail 收信失敗。請到原本的 Chrome 視窗手動驗證。"
    except BrowserError:
        detail = "無法確認驗證碼是否已送出。請到原本的 Chrome 視窗查看。"
    events.put(("status", detail))


def wait_for_chrome(page, cancel):
    try:
        while not cancel.is_set() and not page.is_closed():
            page.wait_for_timeout(300)
    except BrowserError:
        pass


def launch_query_browser(*, profile_dir=None, headless=False):
    """Reuse a dedicated profile without touching the user's everyday Chrome."""
    from native_browser import NativeBrowser
    return NativeBrowser(BROWSER_PROFILE if profile_dir is None else profile_dir,
                         headless=headless, verification_js=BROWSER_VERIFICATION)


def run_query(query, repeat, cancel, events, settings=None, password="", booking=None):
    trace = QueryTrace()
    trace.record("started", "持續監看" if repeat else "查詢一次", auto_booking=bool(repeat and booking),
                 branch=query.branch, adults=query.adults, kids=query.kids,
                 unrestricted=query.start is None, interval=query.interval, persistent_profile=True,
                 browser_backend="native_chrome")
    try:
        with closing(launch_query_browser()) as browser:
            page = browser.pages[0] if browser.pages else browser.new_page()
            flow = BrowserFlow(page, cancel, trace)
            previous = {}
            delivered = {}
            while not cancel.is_set():
                today = taipei_today()
                if query.end is not None and today > query.end:
                    events.put(("status", "監看的日期已過。"))
                    return
                first_day = max(query.start, today) if query.start is not None else today
                end_day = query.end or today
                days = [first_day + timedelta(days=i) for i in range((end_day - first_day).days + 1)]
                if query.start is not None:
                    days = [day for day in days if query.matches_date(day)]
                round_slots = set()
                note = "" if days else "剩餘日期不符合目前設定的日期條件。"
                halted = None
                handoff = None
                index = 0
                while index < len(days):
                    if cancel.is_set():
                        return
                    day = days[index]
                    events.put(("status", f"正在查詢 {query.branch} {day.isoformat()}…"))
                    result = matching_times(query, check_day(
                        page, query, day, cancel, refresh=index == 0, flow=flow,
                        select_date=query.start is not None or index != 0,
                    ))
                    day = result.day
                    days[index] = day
                    if result.discovery_only:
                        trace.record("discover_dates", result.detail, date=day.isoformat())
                    else:
                        trace.record("result", result.detail if result.failed else "讀取時段",
                                     date=day.isoformat(), slot_count=len(result.slots), failed=result.failed,
                                     needs_verification=result.needs_verification)
                    if cancel.is_set():
                        return
                    today = taipei_today()
                    matches = not result.discovery_only and day >= today and query.matches_date(day)
                    if matches or result.stop:
                        new_slots = set(result.slots) - previous.get(day, set())
                        if not result.failed:
                            previous[day] = set(result.slots)
                            round_slots.update((day, slot) for slot in result.slots)
                        last_slots = tuple(sorted(previous.get(day, ()))) if result.failed else ()
                        events.put(("result", (result, bool(new_slots), last_slots)))
                    if result.stop:
                        halted = result
                        break
                    if repeat and booking and matches and result.slots:
                        handoff = start_booking(page, query, result, booking, cancel, flow=flow)
                        if handoff:
                            kind = "booking_verification" if handoff.needs_verification else "booking"
                            events.put((kind, handoff.note))
                            with suppress(BrowserError):
                                page.bring_to_front()
                            break

                    if query.start is None and index == 0 and not result.failed:
                        events.put(("status", f"正在掃描 {query.branch} 的 inline 可訂日期…"))
                        try:
                            candidates, note = calendar_dates(page, query, today, cancel, flow=flow)
                            days.extend(candidate for candidate in candidates
                                        if (result.discovery_only or candidate != day) and query.matches_date(candidate))
                            if not note:
                                events.put(("prune", set(days[1:]) | ({day} if matches else set())))
                                if len(days) == 1 and (not matches or not result.slots):
                                    note = (
                                        "日曆無其他可選日期，今日結果未確認。"
                                        if result.failed and matches else "目前沒有顯示符合條件的可訂日期或時段。"
                                    )
                        except BrowserOperationCancelled:
                            return
                        except (BrowserError, ValueError, BrowserVerificationRequired):
                            halted = active_browser_stop(page, day, "掃描其他可訂日期")
                            halted = halted or Result(day, detail="掃描日曆失敗，本輪查詢未完成。",
                                                      failed=True, stop=True, retryable=True)
                            events.put(("result", (halted, False, tuple(sorted(previous.get(day, ()))))))
                            break
                    index += 1
                    if index < len(days) and cancel.wait(4):
                        return
                if settings and round_slots:
                    if handoff:
                        notify_and_verify_booking(page, settings, password, query, round_slots, handoff, events, cancel)
                    else:
                        send_emails(settings, password, events, query, round_slots, delivered, cancel)
                if handoff:
                    trace.record("handoff", "訂位操作停止", submitted=handoff.submitted,
                                 needs_verification=handoff.needs_verification)
                    wait_for_chrome(page, cancel)
                    events.put(("status", "訂位視窗已結束，請以 inline 畫面及簡訊通知確認訂位結果。"))
                    return
                if halted:
                    if halted.retryable and repeat:
                        trace.record("retry_scheduled", halted.detail, delay=query.interval)
                        events.put(("status", f"{halted.detail}{query.interval} 秒後自動重新查詢。"))
                        if cancel.wait(query.interval):
                            return
                        continue
                    if halted.needs_verification:
                        trace.record("verification", "查詢操作停止")
                        log_note = f" 步驟紀錄：logs/{trace.path.name}" if trace.file else ""
                        events.put(("verification", halted.detail + " 這次查詢已停止，請在原本的 Chrome 視窗查看。" + log_note))
                        with suppress(BrowserError):
                            page.bring_to_front()
                        wait_for_chrome(page, cancel)
                        events.put(("status", "驗證視窗已關閉，專用瀏覽器資料已保留。可再次開始查詢。"))
                    else:
                        events.put(("status", "查詢已停止。可用下方按鈕直接開啟 inline。"))
                    return
                if not repeat:
                    events.put(("status", note or "本次查詢完成。"))
                    return
                status = (note or "本輪完成。") + f"{query.interval} 秒後再次查詢。"
                events.put(("status", status))
                cancel.wait(query.interval)
    except BrowserError:
        events.put(("status", "無法開啟或連線至查詢用 Chrome。請確認已安裝 Chrome，且其他查詢或診斷未占用專用瀏覽器。"))
    finally:
        trace.record("finished", "查詢結束")
        trace.close()
        events.put(("finished", None))


class WatcherWindow:
    def __init__(self, root):
        self.root = root
        self.events = queue.Queue()
        self.cancel = threading.Event()
        self.worker = None
        self.last_query = None
        self.closing = False
        self.mail_settings = None
        self.mail_password = ""
        self.remember_mail_password = False
        self.mail_test_button = None
        self.mail_testing = False
        self.booking_profile = None
        mail_hint = "可在 Email 設定中指定寄件帳號及收件人。"
        booking_hint = ""
        try:
            self.mail_settings, self.mail_password = MailSettings.load()
            self.remember_mail_password = bool(self.mail_password)
        except FileNotFoundError:
            pass
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            mail_hint = "讀取 Email 設定失敗，請重新設定。"
        try:
            self.booking_profile = BookingProfile.load()
        except (OSError, ValueError, TypeError, AttributeError):
            booking_hint = "讀取訂位資料失敗，請從「訂位資料」重新設定。"

        root.title(f"島語｜inline 空位監看 {APP_VERSION}")
        root.geometry("820x645")
        root.minsize(680, 520)
        root.protocol("WM_DELETE_WINDOW", self.close)
        frame = ttk.Frame(root, padding=20)
        frame.pack(fill="both", expand=True)

        ttk.Label(frame, text="島語 inline 空位監看", font=("Microsoft JhengHei", 17, "bold")).pack(anchor="w")
        ttk.Label(frame, text="選分店、日期與人數，查詢或持續監看可訂時段。").pack(anchor="w", pady=(3, 14))

        fields = ttk.Frame(frame)
        fields.pack(fill="x")
        today = taipei_today().isoformat()
        self.branch = tk.StringVar(value="桃園台茂店")
        self.start = tk.StringVar(value=today)
        self.end = tk.StringVar()
        self.adults = tk.StringVar(value="2")
        self.kids = tk.StringVar(value="0")
        self.interval = tk.StringVar(value=str(DEFAULT_INTERVAL))
        self.any_date = tk.BooleanVar()
        self.mail_enabled = tk.BooleanVar(value=self.mail_settings is not None)
        self.mail_status = tk.StringVar(value=mail_hint)
        self.day_kind = tk.StringVar(value=DAY_OPTIONS[0])
        self.earliest = tk.StringVar()
        self.latest = tk.StringVar()
        self.auto_book = tk.BooleanVar(value=False)

        for col in (1, 3, 5):
            fields.columnconfigure(col, weight=1)
        labels = (
            "分店", "開始日期", "結束日期（選填）",
            "大人", "小孩", "輪間隔（秒）",
            "日期類型", "最早（HH:MM）", "最晚（HH:MM）",
        )
        widgets = (
            ttk.Combobox(fields, textvariable=self.branch, values=list(BRANCHES), state="readonly", width=15),
            ttk.Entry(fields, textvariable=self.start, width=12),
            ttk.Entry(fields, textvariable=self.end, width=12),
            ttk.Combobox(fields, textvariable=self.adults, values=tuple(range(1, 8)), state="readonly", width=5),
            ttk.Combobox(fields, textvariable=self.kids, values=tuple(range(6)), state="readonly", width=5),
            ttk.Entry(fields, textvariable=self.interval, width=9),
            ttk.Combobox(fields, textvariable=self.day_kind, values=DAY_OPTIONS, state="readonly", width=22),
            ttk.Entry(fields, textvariable=self.earliest, width=8),
            ttk.Entry(fields, textvariable=self.latest, width=8),
        )
        for i, (label, widget) in enumerate(zip(labels, widgets)):
            row, col = divmod(i, 3)
            ttk.Label(fields, text=label).grid(row=row, column=col * 2, sticky="w", padx=(0, 6), pady=4)
            widget.grid(row=row, column=col * 2 + 1, sticky="ew", padx=(0, 10), pady=4)
        self.date_entries = widgets[1:3]
        options = ttk.Frame(frame)
        options.pack(fill="x", pady=(7, 0))
        ttk.Checkbutton(options, text="不限日期", variable=self.any_date, command=self.toggle_dates).pack(side="left")
        ttk.Checkbutton(options, text="Email 通知", variable=self.mail_enabled).pack(side="left", padx=(14, 7))
        self.mail_setup = ttk.Button(options, text="Email 設定…", command=self.open_mail_settings)
        self.mail_setup.pack(side="left")
        self.booking_toggle = ttk.Checkbutton(options, text="自動訂位", variable=self.auto_book)
        self.booking_toggle.pack(side="left", padx=(14, 7))
        self.booking_setup = ttk.Button(options, text="訂位資料…", command=self.open_booking_profile)
        self.booking_setup.pack(side="left")

        actions = ttk.Frame(frame)
        actions.pack(fill="x", pady=12)
        self.once = ttk.Button(actions, text="查詢一次（不送出訂位）", command=lambda: self.start_query(False))
        self.watch = ttk.Button(actions, text="開始監看", command=lambda: self.start_query(True))
        self.stop = ttk.Button(actions, text="停止", command=self.stop_query, state="disabled")
        self.open = ttk.Button(actions, text="開啟 inline", command=self.open_booking, state="disabled")
        for button in (self.once, self.watch, self.stop, self.open):
            button.pack(side="left", padx=(0, 7))

        self.status = tk.StringVar(
            value=booking_hint or "日期格式 YYYY-MM-DD。結束日期留空時只查一天，時間留空時不限時段。",
        )
        ttk.Label(frame, textvariable=self.status, wraplength=635).pack(anchor="w")
        ttk.Label(frame, textvariable=self.mail_status, wraplength=635).pack(anchor="w", pady=(1, 10))

        results_frame = ttk.Frame(frame)
        results_frame.pack(fill="both", expand=True)
        self.rows = ttk.Treeview(results_frame, columns=("day", "times", "updated"), show="headings", selectmode="browse")
        self.rows.heading("day", text="日期")
        self.rows.heading("times", text="inline 查詢結果")
        self.rows.heading("updated", text="更新時間（台灣）")
        self.rows.column("day", width=105, stretch=False)
        self.rows.column("times", width=370)
        self.rows.column("updated", width=125, stretch=False)
        self.rows.tag_configure("available", foreground="#146339")
        self.rows.tag_configure("failed", foreground="#9b3624")
        scroll = ttk.Scrollbar(results_frame, orient="vertical", command=self.rows.yview)
        self.rows.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.rows.pack(side="left", fill="both", expand=True)
        self.rows.bind("<Double-1>", lambda event: self.open_booking() if self.rows.identify_row(event.y) else None)
        ttk.Label(frame, text="發現新時段會提示音。選取結果後可開啟已預填日期與人數的 inline 頁面。").pack(anchor="w", pady=(9, 0))
        root.after(120, self.handle_events)

    def toggle_dates(self):
        for entry in self.date_entries:
            entry.configure(state="disabled" if self.any_date.get() else "normal")
        if self.worker is None:
            self.status.set(
                "不限日期會掃描 inline 日曆中目前可選的日期，範圍為本月起 12 個月。"
                if self.any_date.get()
                else "日期格式 YYYY-MM-DD。結束日期留空時只查一天，時間留空時不限時段。"
            )

    def open_booking_profile(self):
        dialog = tk.Toplevel(self.root)
        dialog.title("自動訂位資料")
        dialog.transient(self.root)
        dialog.resizable(False, False)
        frame = ttk.Frame(dialog, padding=16)
        frame.pack(fill="both", expand=True)
        profile = self.booking_profile or BookingProfile("", "", "", "", ANY_TABLE)
        variables = [tk.StringVar(value=value) for value in (
            profile.family_name, profile.given_name, profile.phone, profile.email, profile.table,
        )]
        for row, (label, variable) in enumerate(zip(
            ("姓", "名", "台灣手機", "聯絡 Email（選填）", "桌型"), variables,
        )):
            ttk.Label(frame, text=label).grid(row=row, column=0, sticky="w", padx=(0, 10), pady=4)
            if row == 4:
                widget = ttk.Combobox(frame, textvariable=variable, values=TABLE_OPTIONS, state="readonly")
            else:
                widget = ttk.Entry(frame, textvariable=variable, width=40)
            widget.grid(row=row, column=1, sticky="ew", pady=4)
        ttk.Label(
            frame,
            text="儲存後下次會自動帶入。可在 Email 設定開啟簡訊回信驗證。",
            wraplength=395,
        ).grid(row=5, columnspan=2, sticky="w", pady=(6, 5))

        buttons = ttk.Frame(frame)
        buttons.grid(row=6, columnspan=2, sticky="e", pady=(6, 0))
        ttk.Button(buttons, text="inline 服務條款", command=lambda: webbrowser.open(INLINE_TERMS)).pack(
            side="left", padx=(0, 6),
        )
        ttk.Button(buttons, text="inline 隱私權條款", command=lambda: webbrowser.open(INLINE_PRIVACY)).pack(
            side="left", padx=(0, 6),
        )

        def save():
            try:
                profile = BookingProfile.from_fields(*(variable.get() for variable in variables))
                profile.save()
            except ValueError as exc:
                messagebox.showerror("請確認訂位資料", str(exc), parent=dialog)
                return
            except OSError as exc:
                messagebox.showerror("無法儲存訂位資料", str(exc), parent=dialog)
                return
            self.booking_profile = profile
            self.status.set("已儲存訂位資料，下次開啟會自動帶入。")
            dialog.destroy()

        ttk.Button(buttons, text="儲存", command=save).pack(side="left")
        dialog.grab_set()

    def open_mail_settings(self):
        dialog = tk.Toplevel(self.root)
        dialog.title("Email 通知設定")
        dialog.transient(self.root)
        dialog.resizable(False, False)
        frame = ttk.Frame(dialog, padding=16)
        frame.pack(fill="both", expand=True)
        config = self.mail_settings or MailSettings(GMAIL_SMTP, 465, "SSL/TLS", "", ())
        variables = [
            tk.StringVar(value=value) for value in (
                config.host, str(config.port), config.security, config.sender,
                self.mail_password,
            )
        ]
        labels = ("SMTP 主機", "連接埠", "加密", "寄件 Email（登入帳號）", "收件 Email（可多人）", "應用程式密碼")
        for i, label in enumerate(labels):
            ttk.Label(frame, text=label).grid(row=i, column=0, sticky="w", padx=(0, 10), pady=4)
            if i == 4:
                field = ttk.Frame(frame)
                recipient_field = tk.Text(field, width=47, height=4, wrap="none")
                recipient_field.insert("1.0", "\n".join(config.recipients))
                recipient_field.pack(side="left", fill="both", expand=True)
                scrollbar = ttk.Scrollbar(field, orient="vertical", command=recipient_field.yview)
                scrollbar.pack(side="right", fill="y")
                recipient_field.configure(yscrollcommand=scrollbar.set)
            else:
                variable = variables[i if i < 4 else 4]
                if i == 2:
                    field = ttk.Combobox(frame, textvariable=variable, values=("SSL/TLS", "STARTTLS"), state="readonly")
                else:
                    field = ttk.Entry(frame, textvariable=variable, width=47, show="*" if i == 5 else "")
            field.grid(row=i, column=1, sticky="ew", pady=4)

        remember_password = tk.BooleanVar(value=self.remember_mail_password)
        reply_verification = tk.BooleanVar(value=config.reply_verification)
        ttk.Label(frame, text="每行一個收件地址，也可用逗號或分號分隔。重複地址會自動合併。").grid(
            row=6, columnspan=2, sticky="w", pady=(4, 6),
        )
        ttk.Checkbutton(frame, text="記住應用程式密碼（明文儲存在本機 .env）", variable=remember_password).grid(
            row=7, columnspan=2, sticky="w",
        )
        ttk.Checkbutton(frame, text="允許用 Gmail 回信完成簡訊驗證", variable=reply_verification).grid(
            row=8, columnspan=2, sticky="w",
        )
        ttk.Label(frame, text="驗證信會寄到上方的寄件 Gmail，請用同一帳號回覆。").grid(
            row=9, columnspan=2, sticky="w", pady=(0, 4),
        )
        ttk.Label(frame, textvariable=self.mail_status, wraplength=490).grid(row=10, columnspan=2, sticky="w")

        def read_fields():
            settings = MailSettings.from_fields(
                *(variable.get() for variable in variables[:4]), recipient_field.get("1.0", "end-1c"),
                reply_verification=reply_verification.get(),
            )
            password = variables[4].get() or os.environ.get("ISLAND_SMTP_PASSWORD", "")
            if settings.host.casefold() == GMAIL_SMTP:
                password = password.replace(" ", "")
            return settings, password

        def save():
            try:
                settings, _ = read_fields()
                entered_password = variables[4].get()
                saved_password = entered_password if remember_password.get() else ""
                if remember_password.get() and not saved_password.strip():
                    raise ValueError("請輸入要記住的 SMTP 應用程式密碼。")
                settings.save(saved_password)
            except (OSError, ValueError) as exc:
                messagebox.showerror("無法儲存 Email 設定", str(exc), parent=dialog)
                return
            self.mail_settings = settings
            self.mail_password = entered_password
            self.remember_mail_password = bool(saved_password)
            self.mail_enabled.set(True)
            self.mail_status.set(f"已儲存 {len(settings.recipients)} 位收件人。")
            dialog.destroy()

        def test():
            try:
                settings, password = read_fields()
                if not password:
                    raise ValueError("請輸入 SMTP 應用程式密碼。")
            except ValueError as exc:
                messagebox.showerror("請確認 Email 設定", str(exc), parent=dialog)
                return
            self.mail_testing = True
            self.mail_test_button.configure(state="disabled")
            self.mail_status.set("正在寄送測試信…")
            threading.Thread(target=send_emails, args=(settings, password, self.events), daemon=True).start()

        buttons = ttk.Frame(frame)
        buttons.grid(row=11, columnspan=2, sticky="e", pady=(9, 0))
        self.mail_test_button = ttk.Button(buttons, text="寄送測試信", command=test)
        if self.mail_testing:
            self.mail_test_button.configure(state="disabled")
        self.mail_test_button.pack(side="left", padx=(0, 7))
        ttk.Button(buttons, text="儲存", command=save).pack(side="left")
        dialog.grab_set()

    def read_query(self, repeat):
        start = end = None
        if not self.any_date.get():
            start = parse_date(self.start.get())
            end = parse_date(self.end.get()) if self.end.get().strip() else start
            if start < taipei_today():
                raise ValueError("開始日期須為今天或之後。")
            if not 0 <= (end - start).days < MAX_DAYS:
                raise ValueError("日期範圍須為 1 到 7 天，且結束日期不得早於開始日期。")
        adults, kids = int(self.adults.get()), int(self.kids.get())
        if adults + kids > 7:
            raise ValueError("大人與小孩合計最多 7 位。")
        earliest = parse_time(self.earliest.get(), "00:00")
        latest = parse_time(self.latest.get(), "23:59")
        if earliest > latest:
            raise ValueError("最晚時間須等於或晚於最早時間。")
        day_kind = self.day_kind.get()
        if day_kind in DAY_OPTIONS[2:]:
            if start is not None and (start.year not in HOLIDAY_YEARS or end.year not in HOLIDAY_YEARS):
                raise ValueError("台灣辦公日曆目前內建 2026、2027 年。其他年份請改選不限或週末。")
            if start is None and taipei_today().year not in HOLIDAY_YEARS:
                raise ValueError("台灣辦公日曆目前內建 2026、2027 年。請改選不限或週末。")
        interval = DEFAULT_INTERVAL
        if repeat:
            value = self.interval.get().strip()
            if not value.isascii() or not value.isdecimal() or len(value) > 5 or not MIN_INTERVAL <= int(value) <= MAX_INTERVAL:
                raise ValueError(f"輪間隔請輸入 {MIN_INTERVAL} 至 {MAX_INTERVAL} 秒的整數。")
            interval = int(value)
        query = Query(self.branch.get(), start, end, adults, kids, interval, day_kind, earliest, latest)
        if start is not None and not any(
            query.matches_date(start + timedelta(days=offset)) for offset in range((end - start).days + 1)
        ):
            raise ValueError("指定日期範圍沒有符合所選日期類型的日期。")
        return query

    def start_query(self, repeat):
        settings = None
        password = ""
        booking = None
        try:
            query = self.read_query(repeat)
            if repeat and self.auto_book.get():
                booking = self.booking_profile
                if not booking:
                    raise ValueError("請先在「訂位資料」填寫姓名和台灣手機門號。")
            if self.mail_enabled.get():
                settings = self.mail_settings
                if not settings:
                    raise ValueError("請先開啟 Email 設定，設定寄件帳號和收件人。")
                password = self.mail_password or os.environ.get("ISLAND_SMTP_PASSWORD", "")
                if settings.host.casefold() == GMAIL_SMTP:
                    password = password.replace(" ", "")
                if not password:
                    raise ValueError("請在 Email 設定輸入本次執行要用的應用程式密碼。")
        except ValueError as exc:
            messagebox.showerror("請確認查詢條件", str(exc))
            return
        if booking:
            dates = f"{query.start} 至 {query.end}" if query.start else "不限日期"
            verification = (
                "若 inline 要求簡訊驗證，程式會寄信到你的 Gmail，讀取你對該信的回覆並送出驗證碼。"
                if settings and settings.reply_verification
                else "若 inline 要求簡訊驗證，請到原本的 Chrome 視窗完成。"
            )
            approved = messagebox.askyesno(
                "啟動自動訂位",
                f"島語 {query.branch}｜{query.adults} 大 {query.kids} 小｜{booking.table}\n"
                f"{dates}｜{query.day_kind}｜{query.earliest} 至 {query.latest}\n\n"
                "找到符合條件的第一個場次後，程式會將你的聯絡資料送到 inline，最多送出一筆。"
                f"{verification} 訂金與相關條款請在 Chrome 處理。\n\n"
                "你已閱讀並同意 inline 服務條款與隱私權條款，並同意啟動這次自動訂位嗎？",
                parent=self.root,
                default=messagebox.NO,
            )
            if not approved:
                return
        self.last_query = query
        self.rows.delete(*self.rows.get_children())
        self.cancel.clear()
        self.once.configure(state="disabled")
        self.watch.configure(state="disabled")
        self.stop.configure(state="normal")
        self.open.configure(state="normal")
        self.mail_setup.configure(state="disabled")
        self.booking_setup.configure(state="disabled")
        self.booking_toggle.configure(state="disabled")
        self.status.set("正在開啟查詢用 Chrome…")
        self.worker = threading.Thread(
            target=run_query, args=(query, repeat, self.cancel, self.events, settings, password, booking), daemon=True,
        )
        self.worker.start()

    def stop_query(self):
        self.cancel.set()
        self.stop.configure(state="disabled")
        self.status.set("正在停止。若有查詢進行中，會等這次回應或逾時。")

    def open_booking(self):
        if not self.last_query or self.open.instate(("disabled",)):
            return
        selected = self.rows.selection()
        day = date.fromisoformat(selected[0]) if selected else self.last_query.start or taipei_today()
        webbrowser.open(self.last_query.url(day))

    def handle_events(self):
        while True:
            try:
                kind, payload = self.events.get_nowait()
            except queue.Empty:
                break
            if kind == "status":
                if not self.cancel.is_set():
                    self.status.set(payload)
            elif kind == "result":
                self.display_result(*payload)
            elif kind == "booking":
                self.status.set(payload)
                self.auto_book.set(False)
                self.open.configure(state="disabled")
                self.stop.configure(text="關閉訂位視窗")
                self.root.bell()
            elif kind in ("verification", "booking_verification") and not self.cancel.is_set():
                self.status.set(payload)
                if kind == "booking_verification":
                    self.auto_book.set(False)
                self.stop.configure(text="關閉驗證視窗")
                self.open.configure(state="disabled")
                self.root.bell()
            elif kind == "prune":
                for row in self.rows.get_children():
                    if date.fromisoformat(row) not in payload:
                        self.rows.delete(row)
            elif kind in ("mail", "mail_test"):
                self.mail_status.set(payload)
                if kind == "mail_test":
                    self.mail_testing = False
                    if self.mail_test_button and self.mail_test_button.winfo_exists():
                        self.mail_test_button.configure(state="normal")
            elif kind == "finished":
                if self.closing:
                    self.root.destroy()
                    return
                self.once.configure(state="normal")
                self.watch.configure(state="normal")
                self.stop.configure(state="disabled", text="停止")
                self.open.configure(state="normal")
                self.mail_setup.configure(state="normal")
                self.booking_setup.configure(state="normal")
                self.booking_toggle.configure(state="normal")
                if self.cancel.is_set():
                    self.status.set("監看已停止。")
                self.worker = None
        self.root.after(120, self.handle_events)

    def display_result(self, result, new_slots, last_slots=()):
        day = result.day.isoformat()
        updated = datetime.now(TAIPEI).strftime("%H:%M:%S")
        if result.failed and last_slots and self.rows.exists(day):
            text, tag = f"先前可訂｜{'、'.join(last_slots)}（本次未確認）", "failed"
            updated = self.rows.item(day, "values")[2]
        elif result.failed:
            text, tag = f"查詢未確認｜{result.detail}", "failed"
        elif result.slots:
            text, tag = f"可訂｜{'、'.join(result.slots)}", "available"
        else:
            text, tag = f"無可訂時段｜{result.detail}", ""
        values = (day, text, updated)
        if self.rows.exists(day):
            self.rows.item(day, values=values, tags=(tag,))
        else:
            self.rows.insert("", "end", iid=day, values=values, tags=(tag,))
        if new_slots:
            self.root.bell()

    def close(self):
        if self.worker:
            self.closing = True
            self.stop_query()
        else:
            self.root.destroy()


def main():
    root = tk.Tk()
    if sync_playwright is None:
        root.withdraw()
        messagebox.showerror("缺少 Playwright", "請雙擊專案資料夾的 start.bat，啟動時會自動安裝 Playwright。")
        root.destroy()
        return
    WatcherWindow(root)
    root.mainloop()


if __name__ == "__main__":
    main()
