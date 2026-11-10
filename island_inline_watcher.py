"""島語 inline 空位監看（Windows 版）。

Windows 一鍵啟動：雙擊 start.bat，由 uv 準備 Python 3.12 和 Playwright。
需要 Google Chrome。程式會開啟獨立的 Chrome 視窗讀取公開訂位頁。
Email 支援使用 SSL/TLS 或 STARTTLS 的 SMTP 服務。應用程式密碼只在這次
執行使用，也可以從 ISLAND_SMTP_PASSWORD 環境變數讀取。
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
from contextlib import contextmanager
from dataclasses import asdict, dataclass, replace
from datetime import date, datetime, timedelta, timezone
from email.errors import HeaderParseError
from email.headerregistry import Address
from email.message import EmailMessage
from pathlib import Path
from tkinter import messagebox, ttk
from urllib.parse import urlencode, urlparse

try:
    from playwright.sync_api import Error as BrowserError
    from playwright.sync_api import TimeoutError as BrowserTimeout
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None

TAIPEI = timezone(timedelta(hours=8))
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
MAX_DAYS = 7
CALENDAR_MONTHS = 12
CALENDAR = '#calendar-picker'
AVAILABLE_DAY = '[data-cy="bt-cal-day"][data-date]:not([disabled]):not([aria-disabled="true"])'
MAIL_FILE = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "IslandInlineWatcher" / "email.json"
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

# Read only the booking form. Inline renders full/waitlist slots as buttons too,
# with a separate data-cy prefix from slots that can be booked.
READ_FORM = r"""
({branch, month, day, adults, kids}) => {
    const text = node => (node?.textContent || '').trim();
    const visible = node => node && node.getClientRects().length > 0
        && getComputedStyle(node).visibility === 'visible';
    const form = document.querySelector('#book-now');
    if (!form && (document.querySelector('#px-captcha, iframe[src*="captcha"]')
        || /verify (?:you are|that you are) human|press & hold|just a moment/i.test(document.title))) {
        return {blocked: true};
    }

    const heading = [...document.querySelectorAll('h1')].find(node =>
        text(node).replace(/\s+/g, '') === ('島語' + branch).replace(/\s+/g, ''));
    const content = form?.querySelector('#book-now-content');
    if (!heading || !visible(content)
        || form.querySelector('#adult-picker')?.value !== String(adults)
        || form.querySelector('#kid-picker')?.value !== String(kids)
        || text(form.querySelector('#calendar-picker h4')).replace(/\s+/g, '') !== month
        || !text(form.querySelector('#date-picker')).startsWith(day)) {
        return false;
    }
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


@dataclass(frozen=True)
class MailSettings:
    host: str
    port: int
    security: str
    sender: str
    recipients: tuple[str, ...]

    @classmethod
    def from_fields(cls, host, port, security, sender, recipients):
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
        unique = {}
        for part in re.split(r"[,;，；\n]", recipients):
            if part.strip():
                recipient = address(part)
                unique.setdefault(recipient.casefold(), recipient)
        if not unique:
            raise ValueError("至少設定一個收件人。")
        return cls(host, int(port), security, sender, tuple(unique.values()))

    @classmethod
    def load(cls):
        values = json.loads(MAIL_FILE.read_text(encoding="utf-8"))
        return cls.from_fields(
            values["host"], str(values["port"]), values["security"],
            values["sender"], ",".join(values["recipients"]),
        )

    def save(self):
        MAIL_FILE.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=MAIL_FILE.parent, delete=False) as file:
            temporary = Path(file.name)
            try:
                json.dump(asdict(self), file, ensure_ascii=False, indent=2)
                file.close()
                temporary.replace(MAIL_FILE)
            finally:
                file.close()
                temporary.unlink(missing_ok=True)


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


def send_emails(settings, password, events, query=None, slots=(), delivered=None, cancel=None, booking_note=""):
    pending = {}
    for recipient in settings.recipients:
        if query is None:
            pending[recipient] = None
        else:
            new_slots = slots - delivered.get(recipient, set())
            if new_slots:
                pending[recipient] = new_slots
    if not pending or (cancel and cancel.is_set()):
        return

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
                try:
                    refused = client.send_message(mail_message(settings.sender, recipient, subject, body))
                except smtplib.SMTPRecipientsRefused:
                    continue
                if not refused:
                    if query is not None:
                        delivered.setdefault(recipient, set()).update(new_slots)
                    accepted += 1
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
    events.put(("mail_test" if query is None else "mail", detail))


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


def read_slot_state(page, query, day, timeout=20000):
    return page.wait_for_function(
        READ_FORM,
        arg={
            "branch": query.branch,
            "month": f"{day.year}年{day.month}月",
            "day": f"{day.month}月{day.day}日",
            "adults": query.adults,
            "kids": query.kids,
        },
        polling=400,
        timeout=timeout,
    ).json_value()


def check_day(page, query, day):
    try:
        url = query.url(day)
        if page.url == url:
            response = page.reload(wait_until="domcontentloaded", timeout=25000)
        else:
            response = page.goto(url, wait_until="domcontentloaded", timeout=25000)
        if response and response.status in (401, 403, 429):
            return Result(day, detail=f"inline 拒絕或限制存取（HTTP {response.status}）。", failed=True, stop=True)
        if response and response.status >= 400:
            return Result(day, detail=f"inline 回傳 HTTP {response.status}。", failed=True)

        state = read_slot_state(page, query, day)
        if state.get("blocked"):
            return Result(day, detail="inline 要求驗證瀏覽器。", failed=True, stop=True)
        return Result(day, tuple(state["slots"]), state.get("detail", ""))
    except BrowserTimeout:
        return Result(day, detail="等待 inline 回應逾時，或 inline 未套用指定日期、人數。", failed=True)
    except BrowserError:
        if page.is_closed():
            return Result(day, detail="查詢用的 Chrome 視窗已關閉。", failed=True, stop=True)
        return Result(day, detail="讀取 inline 訂位頁失敗。", failed=True)


def calendar_dates(page, query, today, cancel):
    """Use the calendar's selectable days as candidates, then verify their slots."""
    page.wait_for_function(
        r"""({branch, adults, kids}) =>
            [...document.querySelectorAll('h1')].some(n => n.textContent.replace(/\s+/g, '') === '島語' + branch)
            && document.querySelector('#adult-picker')?.value === String(adults)
            && document.querySelector('#kid-picker')?.value === String(kids)""",
        arg={"branch": query.branch, "adults": query.adults, "kids": query.kids},
        timeout=3000,
    )
    other_dates = page.locator('#book-now-content [data-cy="see-other-dates-or-pax"]:visible')
    remaining = None
    if other_dates.count():
        match = re.search(r"還有\s*(\d+)\s*個日期可選", other_dates.inner_text(timeout=2000))
        if match:
            remaining = int(match[1])

    page.locator('#date-picker[aria-expanded="false"]').click(timeout=5000)
    calendar = page.locator(CALENDAR)
    calendar.wait_for(state="visible", timeout=5000)
    header = calendar.locator("h4").first
    prev_button = calendar.locator('.prevMonth:visible:not([disabled]):not([aria-disabled="true"])')
    next_button = calendar.locator('.nextMonth:visible:not([disabled]):not([aria-disabled="true"])')

    def move(button):
        old_month = header.inner_text(timeout=3000)
        button.click(timeout=5000)
        page.wait_for_function(
            "(old) => { const h = document.querySelector('#calendar-picker h4'); return h && h.innerText !== old; }",
            arg=old_month,
            timeout=5000,
        )

    def month_key(label):
        match = re.fullmatch(r"\s*(\d{4})年\s*(\d{1,2})月\s*", label)
        if not match:
            raise ValueError("無法辨識 inline 的月份")
        return int(match[1]), int(match[2])

    first_month = month_key(header.inner_text(timeout=3000))
    picked = page.locator("#date-picker").inner_text(timeout=3000)
    if not picked.startswith(f"{today.month}月{today.day}日"):
        remaining = None
    limit = date(today.year + 1, today.month, 1)
    days = set()
    match = re.match(r"(\d{1,2})月(\d{1,2})日", picked)
    if match and int(match[1]) == first_month[1]:
        selected_day = date(first_month[0], int(match[1]), int(match[2]))
        if today < selected_day < limit:
            days.add(selected_day)

    for _ in range(CALENDAR_MONTHS):
        if cancel.is_set():
            return [], ""
        if month_key(header.inner_text(timeout=3000)) <= (today.year, today.month):
            break
        if not prev_button.count():
            return sorted(days), "無法回到本月，部分日期可能未查到。"
        move(prev_button)
    else:
        return sorted(days), "無法回到本月，部分日期可能未查到。"

    for index in range(CALENDAR_MONTHS):
        if cancel.is_set():
            break
        visible_dates = calendar.locator(AVAILABLE_DAY).evaluate_all("nodes => nodes.map(n => n.dataset.date)")
        days.update(day for value in visible_dates if today < (day := date.fromisoformat(value)) < limit)
        if remaining is not None and len(days) >= remaining:
            break
        if index == CALENDAR_MONTHS - 1 or not next_button.count():
            break
        move(next_button)
    if remaining is not None and len(days) < remaining and not cancel.is_set():
        return sorted(days), "inline 公布的可訂日期數與日曆不一致，部分日期可能未查到。"
    return sorted(days), ""


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


def start_booking(page, query, result, profile, cancel):
    """Open one contact form and submit it once, then leave verification to the user."""
    entering_form = False
    submitting = False
    try:
        if cancel.is_set() or urlparse(page.url).hostname != "inline.app":
            return ""
        table = page.locator("#table-tag-selector:visible")
        if table.count():
            candidates = TABLE_TYPES if profile.table == ANY_TABLE else (profile.table,)
        elif profile.table in (ANY_TABLE, TABLE_TYPES[0]):
            candidates = ("",)
        else:
            return ""

        action = page.locator('button[data-cy="book-now-action-button"]:visible')
        selected_time = ""
        for table_name in candidates:
            if cancel.is_set():
                return ""
            times = result.slots
            try:
                if table_name:
                    option = table.get_by_role("button", name=table_name, exact=True)
                    if not option.count() or not option.is_enabled(timeout=3000):
                        continue
                    if option.get_attribute("aria-pressed", timeout=3000) != "true":
                        option.click(timeout=4000)
                        state = read_slot_state(page, query, result.day, timeout=6000)
                        if state.get("blocked"):
                            return "inline 要求瀏覽器驗證。請到 Chrome 查看。"
                        times = tuple(time for time in times if time in state["slots"])
                    if option.get_attribute("aria-pressed", timeout=3000) != "true":
                        continue
                for time in sorted(times):
                    if cancel.is_set():
                        return ""
                    hour, minute = time.split(":")
                    alternatives = (f"{hour}-{minute}", f"{int(hour)}-{minute}")
                    attributes = ", ".join(f'[data-cy="book-now-time-slot-box-{key}"]' for key in alternatives)
                    slot = page.locator(f'#book-now-content button:is({attributes}):visible')
                    if not slot.count() or not slot.is_enabled(timeout=3000):
                        continue
                    slot.click(timeout=4000)
                    action.wait_for(state="visible", timeout=5000)
                    summary = action.evaluate("node => node.parentElement.parentElement.innerText", timeout=3000)
                    if booking_summary_matches(summary, query, result.day, time, table_name):
                        selected_time = time
                        break
            except BrowserTimeout:
                continue
            if selected_time:
                break
        if not selected_time or cancel.is_set():
            return ""

        entering_form = True
        action.click(timeout=6000)
        form = page.locator("form#contact-form:visible")
        form.wait_for(state="visible", timeout=10000)
        if cancel.is_set():
            return ""
        if urlparse(page.url).hostname != "inline.app":
            return "已停止自動操作，請檢查 Chrome 的訂位頁面。"
        country = form.evaluate(
            """form => [...form.querySelectorAll('select')].find(select =>
                [...select.options].some(option => option.textContent.trim() === 'Taiwan +886')
            )?.selectedOptions[0]?.textContent.trim()""",
            timeout=3000,
        )
        if country != "Taiwan +886":
            return "已開啟訂位表單，請到 Chrome 確認手機國碼。"

        for field, value in (
            ("familyName", profile.family_name), ("givenName", profile.given_name),
            ("phone", profile.phone), ("email", profile.email),
        ):
            if cancel.is_set():
                return ""
            if value:
                form.locator(f'input[data-cy="{field}"]').fill(value, timeout=5000)
        deposit_policy = form.locator('#deposit-policy:visible')
        if deposit_policy.count():
            return "聯絡資料已填好。請到 Chrome 閱讀訂金／取消條款並繼續訂位。"
        if (form.locator('input[aria-invalid="true"]:visible').count()
                or not form.evaluate("form => form.checkValidity()", timeout=3000)):
            return "請到 Chrome 檢查已填的聯絡資料，再確認訂位。"
        if cancel.is_set():
            return ""
        submit = form.locator('button[data-cy="submit"]:visible').filter(
            has_text=re.compile(r"^\s*確認訂位\s*$"),
        )
        submitting = True
        submit.click(timeout=6000)
        selected_table = f"（{table_name}）" if table_name else ""
        return (
            f"{result.day.isoformat()} {selected_time}{selected_table} 已點選確認訂位。"
            "請到 Chrome 查看並完成簡訊驗證。"
        )
    except BrowserError:
        if submitting:
            return "無法確認表單是否已送出，請到 Chrome 檢查目前狀態。"
        if entering_form:
            return "已暫停自動訂位，請到 Chrome 檢查表單。"
        return ""


def run_query(query, repeat, cancel, events, settings=None, password="", booking=None):
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel="chrome", headless=False)
            try:
                page = browser.new_page(locale="zh-TW", timezone_id="Asia/Taipei")
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
                    halted = False
                    booking_note = ""
                    index = 0
                    while index < len(days):
                        if cancel.is_set():
                            return
                        day = days[index]
                        events.put(("status", f"正在查詢 {query.branch} {day.isoformat()}…"))
                        result = matching_times(query, check_day(page, query, day))
                        if cancel.is_set():
                            return
                        matches = query.matches_date(day)
                        if matches or result.stop:
                            new_slots = set(result.slots) - previous.get(day, set())
                            if not result.failed:
                                previous[day] = set(result.slots)
                                round_slots.update((day, slot) for slot in result.slots)
                            events.put(("result", (result, bool(new_slots))))
                        if result.stop:
                            halted = True
                            break
                        if booking and matches and result.slots:
                            booking_note = start_booking(page, query, result, booking, cancel)
                            if booking_note:
                                events.put(("booking", booking_note))
                                try:
                                    page.bring_to_front()
                                except BrowserError:
                                    pass
                                break

                        if query.start is None and index == 0:
                            events.put(("status", f"正在掃描 {query.branch} 的 inline 可訂日期…"))
                            try:
                                candidates, note = calendar_dates(page, query, today, cancel)
                                days.extend(day for day in candidates if query.matches_date(day))
                                if not note:
                                    events.put(("prune", {day for day in days if query.matches_date(day)}))
                                    if len(days) == 1 and (not matches or not result.slots):
                                        note = (
                                            "日曆無其他可選日期，今日結果未確認。"
                                            if result.failed and matches else "目前沒有顯示符合條件的可訂日期或時段。"
                                        )
                            except (BrowserError, ValueError):
                                note = "本輪未能完整讀取 inline 日曆。"
                        index += 1
                        if index < len(days) and cancel.wait(4):
                            return
                    if settings and round_slots:
                        mail_history = {} if booking_note else delivered
                        send_emails(settings, password, events, query, round_slots, mail_history, cancel, booking_note)
                    if booking_note:
                        try:
                            while not cancel.is_set() and not page.is_closed():
                                page.wait_for_timeout(300)
                        except BrowserError:
                            pass
                        events.put(("status", "訂位視窗已結束，請以 inline 畫面及簡訊通知確認訂位結果。"))
                        return
                    if halted:
                        events.put(("status", "查詢已停止。可用下方按鈕直接開啟 inline。"))
                        return
                    if not repeat:
                        events.put(("status", note or "本次查詢完成。"))
                        return
                    status = (note or "本輪完成。") + f"{query.interval} 秒後再次查詢。"
                    events.put(("status", status))
                    cancel.wait(query.interval)
            finally:
                browser.close()
    except BrowserError:
        events.put(("status", "無法開啟或連線至查詢用 Chrome，請確認已安裝 Google Chrome。"))
    finally:
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
        self.mail_test_button = None
        self.mail_testing = False
        self.booking_profile = None
        self.booking_in_progress = False
        mail_hint = "可在 Email 設定中指定寄件帳號及收件人。"
        try:
            self.mail_settings = MailSettings.load()
        except FileNotFoundError:
            pass
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            mail_hint = "讀取 Email 設定失敗，請重新設定。"

        root.title("島語｜inline 空位監看")
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

        self.status = tk.StringVar(value="日期格式 YYYY-MM-DD。結束日期留空時只查一天，時間留空時不限時段。")
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
            text="資料只保留在這次執行。程式最多送出一筆，簡訊驗證及訂金由你完成。",
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
                self.booking_profile = BookingProfile.from_fields(*(variable.get() for variable in variables))
            except ValueError as exc:
                messagebox.showerror("請確認訂位資料", str(exc), parent=dialog)
                return
            self.status.set("已設定訂位資料。勾選「自動訂位」後可開始監看。")
            dialog.destroy()

        ttk.Button(buttons, text="完成", command=save).pack(side="left")
        dialog.grab_set()

    def open_mail_settings(self):
        dialog = tk.Toplevel(self.root)
        dialog.title("Email 通知設定")
        dialog.transient(self.root)
        dialog.resizable(False, False)
        frame = ttk.Frame(dialog, padding=16)
        frame.pack(fill="both", expand=True)
        config = self.mail_settings or MailSettings("smtp.gmail.com", 465, "SSL/TLS", "", ())
        variables = [
            tk.StringVar(value=value) for value in (
                config.host, str(config.port), config.security, config.sender,
                ", ".join(config.recipients), self.mail_password,
            )
        ]
        labels = ("SMTP 主機", "連接埠", "加密", "寄件 Email（登入帳號）", "收件 Email", "應用程式密碼")
        for i, (label, variable) in enumerate(zip(labels, variables)):
            ttk.Label(frame, text=label).grid(row=i, column=0, sticky="w", padx=(0, 10), pady=4)
            if i == 2:
                field = ttk.Combobox(frame, textvariable=variable, values=("SSL/TLS", "STARTTLS"), state="readonly")
            else:
                field = ttk.Entry(frame, textvariable=variable, width=47, show="*" if i == 5 else "")
            field.grid(row=i, column=1, sticky="ew", pady=4)

        ttk.Label(frame, text="收件人可填多個地址，用逗號分隔。密碼只在本次執行使用。").grid(
            row=6, columnspan=2, sticky="w", pady=(4, 6),
        )
        ttk.Label(frame, textvariable=self.mail_status, wraplength=490).grid(row=7, columnspan=2, sticky="w")

        def read_fields():
            settings = MailSettings.from_fields(*(variable.get() for variable in variables[:5]))
            password = variables[5].get() or os.environ.get("ISLAND_SMTP_PASSWORD", "")
            if settings.host.casefold() == "smtp.gmail.com":
                password = password.replace(" ", "")
            return settings, password

        def save():
            try:
                settings, _ = read_fields()
                settings.save()
            except (OSError, ValueError) as exc:
                messagebox.showerror("無法儲存 Email 設定", str(exc), parent=dialog)
                return
            self.mail_settings = settings
            self.mail_password = variables[5].get()
            self.mail_enabled.set(True)
            self.mail_status.set(f"已設定 {len(settings.recipients)} 位收件人。")
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
        buttons.grid(row=8, columnspan=2, sticky="e", pady=(9, 0))
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
                if settings.host.casefold() == "smtp.gmail.com":
                    password = password.replace(" ", "")
                if not password:
                    raise ValueError("請在 Email 設定輸入本次執行要用的應用程式密碼。")
        except ValueError as exc:
            messagebox.showerror("請確認查詢條件", str(exc))
            return
        if booking:
            dates = f"{query.start} 至 {query.end}" if query.start else "不限日期"
            approved = messagebox.askyesno(
                "啟動自動訂位",
                f"島語 {query.branch}｜{query.adults} 大 {query.kids} 小｜{booking.table}\n"
                f"{dates}｜{query.day_kind}｜{query.earliest} 至 {query.latest}\n\n"
                "找到符合條件的第一個場次後，程式會將你的聯絡資料送到 inline，最多送出一筆。"
                "你仍需在 Chrome 完成簡訊驗證及依店家規則支付訂金。\n\n"
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
        if not self.last_query or self.booking_in_progress:
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
                self.booking_in_progress = True
                self.status.set(payload)
                self.auto_book.set(False)
                self.open.configure(state="disabled")
                self.stop.configure(text="關閉訂位視窗")
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
                self.booking_in_progress = False
                if self.cancel.is_set():
                    self.status.set("監看已停止。")
                self.worker = None
        self.root.after(120, self.handle_events)

    def display_result(self, result, new_slots):
        day = result.day.isoformat()
        if result.failed:
            text, tag = f"查詢未確認｜{result.detail}", "failed"
        elif result.slots:
            text, tag = f"可訂｜{'、'.join(result.slots)}", "available"
        else:
            text, tag = f"無可訂時段｜{result.detail}", ""
        values = (day, text, datetime.now(TAIPEI).strftime("%H:%M:%S"))
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
