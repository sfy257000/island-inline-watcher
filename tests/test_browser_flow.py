"""Browser regressions on an isolated inline-shaped page; no real bookings or email."""
import json
import queue
import threading
import time
import unittest
from dataclasses import replace
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import island_inline_watcher as app


DAY = date(2026, 11, 19)
QUERY = app.Query("桃園台茂店", DAY, DAY, 2, 0, 120)
PROFILE = app.BookingProfile("測試", "測試", "0912345678", "test@example.com", app.ANY_TABLE)
FIXTURE = r"""
<meta charset="utf-8">
<h1>島語桃園台茂店</h1>
<div id="book-now">
  <select id="adult-picker"><option>2</option><option>3</option></select>
  <select id="kid-picker"><option>0</option><option>1</option></select>
  <button id="date-picker" aria-expanded="false">11月19日週四</button>
  <div id="calendar-picker" style="display:none">
    <h4>2026年11月</h4><button class="nextMonth">+</button><button class="prevMonth">-</button>
    <button data-cy="bt-cal-day" data-date="2026-11-19">19</button>
    <button data-cy="bt-cal-day" data-date="2026-11-20">20</button>
  </div>
  <div id="table-tag-selector">
    <button aria-pressed="false">一般</button><button aria-pressed="false">板前吧台</button>
  </div>
  <div id="book-now-content"></div>
</div>
<section id="summary" style="display:none"><div>
  <p id="summary-text"></p>
  <button data-cy="book-now-action-button">完成預訂</button>
</div></section>
<form id="contact-form" style="display:none">
  <label><input type="radio" name="salutation" value="1" checked>小姐</label>
  <label><input type="radio" name="salutation" value="0">先生</label>
  <label><input type="radio" name="salutation" value="2">其他</label>
  <select><option>Taiwan +886</option></select>
  <input data-cy="familyName"><input data-cy="givenName"><input data-cy="phone"><input data-cy="email">
  <button data-cy="submit" type="button">確認訂位</button>
</form>
<div id="px-captcha" style="display:none;width:300px;height:200px">按壓不放以確認您是人類</div>
<script>
const $ = s => document.querySelector(s);
window.actions = []; window.selectedTable = ''; window.blockAt = '';
window.opened = 0; window.submitted = 0; window.delayed = false;
function block() { $('#px-captcha').style.display = 'block'; }
function slots(full=false) {
  $('#book-now-content').innerHTML = full ? '<p data-cy="booking-full-text">已額滿</p>' :
    '<button data-cy="book-now-time-slot-box-11-30">11:30</button>';
}
slots();
document.addEventListener('click', e => {
  const b = e.target.closest('button'); if (!b) return;
  window.actions.push({text:b.innerText, at:performance.now()});
  if (b.id === 'date-picker') {
    const open = b.getAttribute('aria-expanded') !== 'true';
    b.setAttribute('aria-expanded', String(open));
    $('#calendar-picker').style.display = open ? 'block' : 'none';
  }
  if (b.matches('[data-date]')) {
    const [, month, day] = b.dataset.date.split('-').map(Number);
    $('#date-picker').innerText = `${month}月${day}日`;
    $('#date-picker').setAttribute('aria-expanded', 'false');
    $('#calendar-picker').style.display = 'none'; slots();
  }
  if (b.className === 'nextMonth' || b.className === 'prevMonth') {
    const next = b.className === 'nextMonth';
    $('#calendar-picker h4').innerText = next ? '2026年12月' : '2026年11月';
    document.querySelectorAll('[data-date]').forEach(n => {
      n.dataset.date = next ? n.dataset.date.replace('-11-', '-12-') : n.dataset.date.replace('-12-', '-11-');
    });
  }
  if (b.closest('#table-tag-selector')) {
    document.querySelectorAll('#table-tag-selector button').forEach(n => n.setAttribute('aria-pressed', String(n === b)));
    window.selectedTable = b.innerText;
    if (window.blockAt === 'table') { block(); return; }
    if (window.delayed) {
      fetch('/availability').then(r => r.json()).then(() => slots(b.innerText === '一般'));
    } else slots(b.innerText === '一般');
  }
  if (b.matches('[data-cy="book-now-time-slot-box-11-30"]')) {
    if (window.blockAt === 'slot') { block(); return; }
    setTimeout(() => {
      $('#summary-text').innerText = '您已選擇預訂島語桃園台茂店：\n2026年11月19日\n11:30\n2大\n' + window.selectedTable;
      $('#summary').style.display = 'block';
    }, 150);
  }
  if (b.matches('[data-cy="book-now-action-button"]')) {
    window.opened++;
    if (window.blockAt === 'form') { block(); return; }
    $('#contact-form').style.display = 'block';
  }
  if (b.matches('[data-cy="submit"]')) {
    window.submitted++;
    if (window.blockAt === 'submit') block();
  }
});
</script>
"""


class BrowserInteractions:
    """Interaction regressions shared with the production native backend."""
    def booking(self, **kwargs):
        return app.start_booking(self.page, QUERY, app.Result(DAY, ("11:30",)), PROFILE,
                                 self.cancel, flow=self.flow, **kwargs)

    def test_reused_matching_page_requires_no_calendar_clicks(self):
        for _ in range(2):
            result = app.check_day(self.page, QUERY, DAY, self.cancel, refresh=False, flow=self.flow)
            self.assertEqual(result.slots, ("11:30",))
        self.assertEqual(self.page.evaluate("actions"), [])

    def test_delayed_table_response_does_not_reuse_stale_slots(self):
        def delayed(route):
            time.sleep(1.1)
            route.fulfill(content_type="application/json", body="{}")
        self.page.route("**/availability", delayed)
        self.page.evaluate("window.delayed = true")
        handoff = self.booking(preview=True)
        self.assertIsNotNone(handoff)
        self.assertEqual(self.page.evaluate("selectedTable"), "板前吧台")
        self.assertEqual(self.page.evaluate("opened"), 0)
        self.assertEqual(self.page.evaluate("submitted"), 0)
        actions = self.page.evaluate("actions.map(a => a.text)")
        self.assertEqual(actions, ["一般", "板前吧台", "11:30"])

    def test_captcha_stops_at_each_booking_stage(self):
        for stage in ("table", "slot", "form", "submit"):
            with self.subTest(stage=stage):
                self.page.goto(QUERY.booking_url())
                self.page.evaluate("s => window.blockAt = s", stage)
                handoff = self.booking()
                self.assertTrue(handoff.needs_verification)
                self.assertEqual(self.page.evaluate("submitted"), int(stage == "submit"))
                actions = self.page.evaluate("actions.map(a => a.text)")
                self.assertEqual(actions[-1], {"table": "一般", "slot": "11:30",
                                              "form": "完成預訂", "submit": "確認訂位"}[stage])

    def test_action_spacing_and_cancellation(self):
        with patch.object(app, "ACTION_INTERVAL", 0.25):
            self.flow.perform("測試動作一", lambda: self.page.locator("#date-picker").click())
            self.flow.perform("測試動作二", lambda: self.page.locator("#date-picker").click())
        actions = self.page.evaluate("actions")
        self.assertGreaterEqual(actions[1]["at"] - actions[0]["at"], 200)
        self.cancel.set()
        with self.assertRaises(app.BrowserOperationCancelled):
            self.flow.perform("取消後的動作", lambda: self.page.locator("#date-picker").click())
        self.assertEqual(len(self.page.evaluate("actions")), 2)

    def test_captcha_during_action_gap_prevents_next_click(self):
        self.flow.perform("第一個動作", lambda: None)
        self.page.evaluate("setTimeout(block, 150)")
        with patch.object(app, "ACTION_INTERVAL", 0.5):
            with self.assertRaises(app.BrowserVerificationRequired):
                self.flow.perform("禁止的動作", lambda: self.page.locator("#date-picker").click())
        self.assertEqual(self.page.evaluate("actions"), [])

    def test_date_changes_in_same_page_including_next_month(self):
        self.assertTrue(app.select_booking_date(self.page, date(2026, 12, 20), self.cancel, flow=self.flow))
        self.assertEqual(self.page.locator("#calendar-picker h4").inner_text(), "2026年12月")
        self.assertEqual(self.page.locator("#date-picker").inner_text(), "12月20日")
        self.assertIn("+", self.page.evaluate("actions.map(a => a.text)"))

    def test_unrestricted_count_outside_content_avoids_month_sweep(self):
        self.page.evaluate("""() => {
            const summary = document.createElement('p');
            summary.dataset.cy = 'see-other-dates-or-pax';
            summary.innerText = '預定其他日期\\n還有 1 個日期可選';
            document.querySelector('#book-now').append(summary);
        }""")
        days, note = app.calendar_dates(self.page, replace(QUERY, start=None, end=None),
                                         date(2026, 11, 10), self.cancel, flow=self.flow)
        self.assertEqual(days, [DAY, date(2026, 11, 20)])
        self.assertEqual(note, "")
        self.assertEqual(self.page.evaluate("actions.map(a => a.text)"), ["11月19日週四"])

    def test_missing_total_never_blindly_scans_twelve_months(self):
        days, note = app.calendar_dates(self.page, QUERY, date(2026, 11, 10), self.cancel, flow=self.flow)
        self.assertEqual(days, [DAY, date(2026, 11, 20)])
        self.assertIn("掃描可能不完整", note)
        self.assertNotIn("+", self.page.evaluate("actions.map(a => a.text)"))
        self.assertNotIn("-", self.page.evaluate("actions.map(a => a.text)"))

    def test_calendar_reaches_later_availability_across_empty_months(self):
        self.page.evaluate('''() => {
            const summary=document.createElement('p');
            summary.dataset.cy='see-other-dates-or-pax';
            summary.innerText='預定其他日期\\n還有 4 個日期可選';
            document.querySelector('#book-now').append(summary);
            const calendar=document.querySelector('#calendar-picker');
            calendar.querySelectorAll('[data-date]').forEach(n=>n.remove());
            window.monthMoves=0;
            document.addEventListener('click',event=>{
                if (!event.target.matches('.nextMonth')) return;
                event.stopImmediatePropagation();
                window.monthMoves++;
                const month=11+window.monthMoves;
                calendar.querySelector('h4').innerText=month===12?'2026年12月':'2027年1月';
                calendar.querySelectorAll('[data-date]').forEach(n=>n.remove());
                if(window.monthMoves===2) {
                    for(const day of ['06','08','09','10']) {
                        const button=document.createElement('button');
                        button.dataset.cy='bt-cal-day';
                        button.dataset.date='2027-02-'+day;
                        button.innerText=day;
                        calendar.append(button);
                    }
                }
            },true);
        }''')
        days,note=app.calendar_dates(self.page,replace(QUERY,start=None,end=None),
                                     date(2026,11,10),self.cancel,flow=self.flow)
        self.assertEqual(days,[DAY,date(2027,2,6),date(2027,2,8),date(2027,2,9),date(2027,2,10)])
        self.assertEqual(note,'')
        self.assertEqual(self.page.evaluate('monthMoves'),2)

    def test_calendar_retries_opening_click_that_did_not_expand(self):
        self.page.evaluate('''() => {
            window.calendarOpenAttempts=0;
            document.addEventListener('click',event=>{
                if(event.target.id!=='date-picker') return;
                window.calendarOpenAttempts++;
                if(window.calendarOpenAttempts===1) event.stopImmediatePropagation();
            },true);
        }''')
        calendar=app.open_calendar(self.page,self.flow)
        self.assertTrue(calendar.is_visible())
        self.assertEqual(self.page.locator('#date-picker').get_attribute('aria-expanded'),'true')
        self.assertEqual(self.page.evaluate('calendarOpenAttempts'),2)

    def test_visible_second_month_needs_no_month_navigation(self):
        self.page.evaluate("""() => {
            document.querySelector('#calendar-picker').insertAdjacentHTML('beforeend',
                '<h4>2026年12月</h4><button data-cy="bt-cal-day" data-date="2026-12-20">20</button>');
        }""")
        day = date(2026, 12, 20)
        self.assertTrue(app.select_booking_date(self.page, day, self.cancel, flow=self.flow))
        state = app.read_slot_state(self.page, QUERY, day, flow=self.flow)
        self.assertEqual(state['slots'], ['11:30'])
        self.assertNotIn("+", self.page.evaluate("actions.map(a => a.text)"))

    def test_unrestricted_bootstrap_reads_site_selection_without_selecting_today(self):
        query = replace(QUERY, start=None, end=None)
        result = app.check_day(self.page, query, date(2026, 11, 10), self.cancel,
                               refresh=False, flow=self.flow, select_date=False)
        self.assertEqual(result.day, DAY)
        self.assertEqual(result.slots, ('11:30',))
        self.assertEqual(self.page.evaluate('actions'), [])

    def test_unrestricted_calendar_discovers_today_when_selected_month_is_missing(self):
        today = date(2026, 12, 1)
        query = replace(QUERY, start=None, end=None)
        self.page.evaluate("""() => {
            const calendar = document.querySelector('#calendar-picker');
            calendar.querySelector('h4').textContent = '2026年12月';
            calendar.querySelectorAll('[data-date]').forEach(button => button.remove());
            calendar.insertAdjacentHTML('beforeend', '<button data-cy="bt-cal-day" data-date="2026-12-01">1</button>');
        }""")
        result = app.check_day(self.page, query, today, self.cancel,
                               refresh=False, flow=self.flow, select_date=False)
        self.assertTrue(result.discovery_only)
        self.assertFalse(result.stop)
        days, _ = app.calendar_dates(self.page, query, today, self.cancel, flow=self.flow)
        self.assertEqual(days, [today])

    def test_inline_modal_without_px_id_is_detected(self):
        self.page.evaluate("""() => {
            const dialog = document.createElement('div');
            dialog.innerHTML = '<h2>在我們繼續之前...</h2><p>按壓不放以確認您是人類<br>（而不是機器人）。</p>';
            document.body.append(dialog);
        }""")
        self.assertTrue(self.page.evaluate(app.BROWSER_VERIFICATION))
        with self.assertRaises(app.BrowserVerificationRequired):
            self.flow.perform("不應操作", lambda: self.page.locator('#date-picker').click())
        self.assertEqual(self.page.evaluate('actions'), [])

    def test_hidden_challenge_and_normal_recaptcha_are_not_blocking(self):
        self.page.evaluate("""() => {
            const frame = document.createElement('iframe'); frame.title = 'reCAPTCHA'; document.body.append(frame);
            const wrapper = document.createElement('div'); wrapper.style.opacity = '0';
            wrapper.innerHTML = '<div id="px-captcha-modal" style="width:300px;height:200px">按壓不放以確認您是人類</div>';
            document.body.append(wrapper);
        }""")
        self.assertFalse(self.page.evaluate(app.BROWSER_VERIFICATION))

    def test_query_timeout_ends_current_round_and_can_retry(self):
        with patch.object(app, 'select_booking_date', side_effect=app.BrowserTimeout('blocked')):
            result = app.check_day(self.page, QUERY, date(2026, 11, 20), self.cancel,
                                   refresh=False, flow=self.flow)
        self.assertTrue(result.failed)
        self.assertTrue(result.stop)
        self.assertTrue(result.retryable)

    def test_table_timeout_stops_instead_of_trying_other_tables(self):
        with patch.object(app, 'read_slot_state', side_effect=app.BrowserTimeout('unknown state')):
            handoff = self.booking()
        self.assertIsNotNone(handoff)
        self.assertIn('已停止自動訂位', handoff.note)
        self.assertEqual(self.page.evaluate('actions.map(a => a.text)'), ['一般'])

    def test_booking_waits_for_delayed_country_and_fills_contact_fields(self):
        self.page.evaluate("""() => {
            const form = document.querySelector('#contact-form');
            const country = form.querySelector('select');
            country.name = 'country-code';
            country.replaceChildren();
            const unrelated = document.createElement('select');
            unrelated.innerHTML = '<option>Other setting</option>';
            form.prepend(unrelated);
            window.contactState = {};
            form.querySelectorAll('input[data-cy]').forEach(input => {
                input.disabled = true;
                input.addEventListener('input', () => window.contactState[input.dataset.cy] = input.value);
            });
            const phone = form.querySelector('[data-cy="phone"]');
            phone.type = 'tel';
            phone.addEventListener('input', () => {
                phone.value = phone.value.replace(/^0/, '');
                window.contactState.phone = phone.value;
            });
            document.addEventListener('click', event => {
                if (event.target.matches('[data-cy="book-now-action-button"]')) {
                    setTimeout(() => {
                        country.innerHTML = '<option value="US">United States +1</option><option value="TW">Taiwan +886</option>';
                        form.querySelectorAll('input[data-cy]').forEach(input => input.disabled = false);
                    }, 400);
                }
            });
        }""")
        handoff = self.booking()
        self.assertTrue(handoff.submitted, handoff)
        self.assertEqual(self.page.evaluate('contactState'), {
            'familyName': PROFILE.family_name, 'givenName': PROFILE.given_name,
            'phone': PROFILE.phone[1:], 'email': PROFILE.email,
        })
        self.assertEqual(self.page.locator('select[name="country-code"]').input_value(), 'TW')
        self.assertEqual(self.page.evaluate('submitted'), 1)

    def test_selected_salutation_is_applied_to_contact_form(self):
        for salutation in app.SALUTATIONS:
            with self.subTest(salutation=salutation):
                self.page.evaluate("document.querySelector('#contact-form').style.display='block'")
                app.fill_booking_contact(self.page, replace(PROFILE, salutation=salutation),
                                         self.cancel, self.flow)
                label = self.page.evaluate("() => document.querySelector('#contact-form input[type=radio]:checked').closest('label').innerText.trim()")
                self.assertEqual(label, salutation)

    def test_reply_reconnects_then_pastes_code_into_original_sms_dialog_once(self):
        import booking_verification as verification
        from unittest.mock import Mock
        self.page.evaluate('''() => {
            const dialog=document.createElement('div');
            dialog.dataset.cy='phone-verification-container';
            for(let i=0;i<4;i++) {const input=document.createElement('input'); input.dataset.cy='phone-code-'+i; dialog.append(input);}
            window.otpSubmissions=0;
            dialog.addEventListener('paste', event => {
                event.preventDefault();
                const code=event.clipboardData.getData('text/plain');
                dialog.querySelectorAll('input').forEach((input,index) => input.value=code[index]);
                window.otpSubmissions++;
            });
            document.body.append(dialog);
        }''')
        checkout = self.page.query_selector('form#contact-form')
        challenge = verification.find_challenge(self.page, checkout, self.page.url, self.cancel, timeout=1)
        self.assertIsNotNone(challenge)
        settings = app.MailSettings('smtp.gmail.com', 465, 'SSL/TLS', 'test@gmail.com', ('test@gmail.com',), True)
        retried = Mock()
        with (patch.object(verification, 'gmail_mailbox') as mailbox,
              patch.object(verification, 'find_reply', side_effect=[TimeoutError('temporary'), '0042'])):
            submitted = verification.wait_for_reply(settings, 'test-password', '<test@gmail.com>',
                time.monotonic()+15, self.cancel, challenge, on_retry=retried)
        self.assertTrue(submitted)
        retried.assert_called_once()
        self.assertEqual(mailbox.call_count, 2)
        self.assertEqual(self.page.locator(verification.OTP_INPUTS).evaluate_all('nodes => nodes.map(n => n.value)'),
                         ['0', '0', '4', '2'])
        self.assertEqual(self.page.evaluate('otpSubmissions'), 1)


class BrowserProfileTests(unittest.TestCase):
    def test_dedicated_profile_preserves_cookie_and_storage_after_restart(self):
        with TemporaryDirectory() as folder:
            profile = Path(folder) / 'dedicated-profile'
            for attempt in range(2):
                context = app.launch_query_browser(profile_dir=profile, headless=True)
                try:
                    page = context.pages[0] if context.pages else context.new_page()
                    page.route('https://profile-test.invalid/**', lambda r: r.fulfill(
                        content_type='text/html', body='<meta charset="utf-8"><title>Local profile test</title>'))
                    page.goto('https://profile-test.invalid/')
                    if attempt == 0:
                        context.add_cookies([{'name': 'profile-test', 'value': 'retained',
                                             'url': 'https://profile-test.invalid', 'expires': time.time() + 3600}])
                        page.evaluate("localStorage.setItem('profile-test', 'retained')")
                    else:
                        self.assertEqual(page.evaluate("localStorage.getItem('profile-test')"), 'retained')
                        self.assertEqual(context.cookies('https://profile-test.invalid')[0]['value'], 'retained')
                finally:
                    context.close()


class NativeBrowserTests(BrowserInteractions, unittest.TestCase):
    """Run the same interaction regressions through the production CDP client."""
    @classmethod
    def setUpClass(cls):
        pass

    @classmethod
    def tearDownClass(cls):
        pass

    def setUp(self):
        self.profile = TemporaryDirectory()
        self.context = app.launch_query_browser(profile_dir=self.profile.name, headless=True)
        self.page = self.context.pages[0]
        self.page.route("https://inline.app/**", lambda r: r.fulfill(content_type="text/html", body=FIXTURE))
        self.page.goto(QUERY.booking_url())
        self.cancel = threading.Event()
        self.flow = app.BrowserFlow(self.page, self.cancel)
        self.pace = patch.object(app, "ACTION_INTERVAL", 0.1)
        self.pace.start()

    def tearDown(self):
        self.pace.stop()
        self.context.close()
        self.profile.cleanup()

    def test_sms_nodes_keep_identity_when_form_is_replaced(self):
        from booking_verification import InlineChallenge, OTP_INPUTS
        self.page.evaluate('''() => {
            const dialog=document.createElement('div');
            dialog.dataset.cy='phone-verification-container';
            for(let i=0;i<4;i++) {const input=document.createElement('input'); input.dataset.cy='phone-code-'+i; dialog.append(input);}
            document.body.append(dialog);
        }''')
        challenge = InlineChallenge(self.page, self.page.url, self.page.locator(OTP_INPUTS).element_handles())
        self.assertTrue(challenge.pending())
        self.page.evaluate('''() => {
            const dialog=document.querySelector('[data-cy="phone-verification-container"]');
            dialog.replaceWith(dialog.cloneNode(true));
        }''')
        self.assertFalse(challenge.pending())

    def test_reload_waits_for_new_document(self):
        self.page.evaluate("document.body.dataset.oldDocument='yes'")
        response = self.page.reload()
        self.assertEqual(response.status, 200)
        self.assertIsNone(self.page.evaluate('document.body.dataset.oldDocument'))

    def test_page_operations_do_not_enable_javascript_debug_events(self):
        self.page.evaluate('() => document.title')
        self.page.locator('#date-picker').click()
        self.assertNotIn('Runtime.enable', self.context.commands)
        self.assertNotIn('Inspector.enable', self.context.commands)

    def test_unrestricted_monitor_keeps_running_across_midnight(self):
        query = replace(QUERY, start=None, end=None)
        today, tomorrow = DAY, date(2026, 11, 20)
        stale_page = FIXTURE + """<script>
            document.querySelector('#book-now-content').textContent = '載入中';
            document.querySelector('#calendar-picker').insertAdjacentHTML('beforeend',
                '<button data-cy="bt-cal-day" data-date="2026-11-21">21</button>');
        </script>"""
        self.page.route('https://inline.app/**', lambda route: route.fulfill(
            content_type='text/html', body=FIXTURE if today == DAY else stale_page))
        cancel = unittest.mock.Mock()
        cancel.is_set.return_value = False
        def wait(seconds):
            nonlocal today
            if seconds == query.interval:
                if today == DAY:
                    today = tomorrow
                else:
                    cancel.is_set.return_value = True
            return cancel.is_set()
        cancel.wait.side_effect = wait
        events = queue.Queue()
        with (TemporaryDirectory() as folder, patch.object(app, 'launch_query_browser') as launch,
              patch.object(app, '__file__', str(Path(folder) / 'watcher.py')),
              patch.object(app, 'taipei_today', side_effect=lambda: today)):
            launch.return_value.pages = [self.page]
            app.run_query(query, True, cancel, events)
            log = next((Path(folder) / 'logs').glob('*.jsonl'))
            entries = [json.loads(line) for line in log.read_text(encoding='utf-8').splitlines()]
        skip = next(i for i, entry in enumerate(entries) if entry['event'] == 'discover_dates')
        self.assertEqual(entries[skip]['date'], DAY.isoformat())
        self.assertEqual([entry['date'] for entry in entries[skip:] if entry['event'] == 'result'],
                         ['2026-11-20', '2026-11-21'])
        results = [payload[0] for kind, payload in events.queue if kind == 'result']
        self.assertTrue(all(result.slots == ('11:30',) and not result.stop for result in results))
        self.assertEqual(cancel.wait.call_args_list.count(unittest.mock.call(query.interval)), 2)

    def test_monitor_reloads_after_page_ready_timeout(self):
        loads = 0
        def respond(route):
            nonlocal loads
            loads += 1
            body = '<meta charset="utf-8"><p>載入中</p>' if loads == 1 else FIXTURE
            route.fulfill(content_type='text/html', body=body)
        self.page.route(QUERY.booking_url(), respond)
        original_wait = self.page.wait_for_function
        def wait_for_page(source, **kwargs):
            if source == app.BOOKING_FORM_READY:
                kwargs['timeout'] = 300
            return original_wait(source, **kwargs)
        cancel = unittest.mock.Mock()
        cancel.is_set.return_value = False
        intervals = 0
        def wait(seconds):
            nonlocal intervals
            self.assertEqual(seconds, QUERY.interval)
            intervals += 1
            cancel.is_set.return_value = intervals == 2
            return cancel.is_set()
        cancel.wait.side_effect = wait
        events = queue.Queue()
        with (TemporaryDirectory() as folder, patch.object(app, 'launch_query_browser') as launch,
              patch.object(app, '__file__', str(Path(folder) / 'watcher.py')),
              patch.object(self.page, 'wait_for_function', side_effect=wait_for_page)):
            launch.return_value.pages = [self.page]
            app.run_query(QUERY, True, cancel, events)
            log = next((Path(folder) / 'logs').glob('*.jsonl'))
            entries = [json.loads(line) for line in log.read_text(encoding='utf-8').splitlines()]
        self.assertEqual(loads, 2)
        results = [payload[0] for kind, payload in events.queue if kind == 'result']
        self.assertTrue(results[0].retryable)
        self.assertEqual(results[1].slots, ('11:30',))
        snapshot = next(entry for entry in entries if entry['event'] == 'query_timeout')
        self.assertFalse(snapshot['page_state']['formPresent'])


class WorkerTests(unittest.TestCase):
    def test_retries_preserve_previous_slots_and_email_deduplication(self):
        events = queue.Queue()
        settings = app.MailSettings('smtp.example.com', 465, 'SSL/TLS',
                                    'sender@example.com', ('recipient@example.com',))
        timeout = app.Result(DAY, detail='頁面載入逾時', failed=True, stop=True, retryable=True)
        results = [app.Result(DAY, ('11:30',)), timeout, timeout,
                   app.Result(DAY, ('11:30',)), app.Result(DAY, ('11:30', '17:30'))]
        cancel = unittest.mock.Mock()
        cancel.is_set.return_value = False
        intervals = 0
        def wait(seconds):
            nonlocal intervals
            self.assertEqual(seconds, QUERY.interval)
            intervals += 1
            cancel.is_set.return_value = intervals == len(results)
            return cancel.is_set()
        cancel.wait.side_effect = wait
        with (TemporaryDirectory() as folder, patch.object(app, 'launch_query_browser'),
              patch.object(app, '__file__', str(Path(folder) / 'watcher.py')),
              patch.object(app, 'check_day', side_effect=results) as check,
              patch.object(app, 'mail_connection') as mail):
            client = mail.return_value.__enter__.return_value
            client.send_message.return_value = {}
            app.run_query(QUERY, True, cancel, events, settings=settings, password='test')
        self.assertEqual(check.call_count, 5)
        self.assertEqual(client.send_message.call_count, 2)
        messages = [call.args[0].get_content() for call in client.send_message.call_args_list]
        self.assertIn(f'{DAY.isoformat()}　11:30', messages[0])
        self.assertIn(f'{DAY.isoformat()}　17:30', messages[1])
        self.assertNotIn(f'{DAY.isoformat()}　11:30', messages[1])
        failed_results = [payload for kind, payload in events.queue if kind == 'result' and payload[0].failed]
        self.assertTrue(all(payload[2] == ('11:30',) for payload in failed_results))

    def test_stop_during_retry_wait_prevents_another_query(self):
        cancel = unittest.mock.Mock()
        cancel.is_set.return_value = False
        cancel.wait.return_value = True
        with (TemporaryDirectory() as folder, patch.object(app, 'launch_query_browser'),
              patch.object(app, '__file__', str(Path(folder) / 'watcher.py')),
              patch.object(app, 'check_day', return_value=app.Result(
                  DAY, failed=True, stop=True, retryable=True)) as check):
            app.run_query(QUERY, True, cancel, queue.Queue())
        check.assert_called_once()

    def test_query_once_does_not_automatically_retry(self):
        cancel = threading.Event()
        with (TemporaryDirectory() as folder, patch.object(app, 'launch_query_browser'),
              patch.object(app, '__file__', str(Path(folder) / 'watcher.py')),
              patch.object(app, 'check_day', return_value=app.Result(
                  DAY, failed=True, stop=True, retryable=True)) as check):
            app.run_query(QUERY, False, cancel, queue.Queue())
        check.assert_called_once()

    def test_unrestricted_worker_keeps_real_selected_day_and_deduplicates(self):
        events = queue.Queue()
        with (TemporaryDirectory() as folder, patch.object(app, "launch_query_browser"),
              patch.object(app, "__file__", str(Path(folder) / "watcher.py")),
              patch.object(app, "check_day", side_effect=[app.Result(DAY), app.Result(date(2026, 11, 20))]) as check,
              patch.object(app, "calendar_dates", return_value=([DAY, date(2026, 11, 20)], ""))):
            app.run_query(replace(QUERY, start=None, end=None), False, threading.Event(), events)
        self.assertEqual(check.call_count, 2)
        self.assertFalse(check.call_args_list[0].kwargs['select_date'])
        self.assertEqual([payload[0].day for kind, payload in events.queue if kind == 'result'],
                         [DAY, date(2026, 11, 20)])

    def test_verification_keeps_previously_read_slots(self):
        events = queue.Queue()
        with (TemporaryDirectory() as folder, patch.object(app, "launch_query_browser"),
              patch.object(app, "__file__", str(Path(folder) / "watcher.py")),
              patch.object(app, "check_day", side_effect=[
                  app.Result(DAY, ("11:30",)), app.verification_result(DAY, "讀取時段"),
              ]), patch.object(app, "wait_for_chrome")):
            cancel = unittest.mock.Mock()
            cancel.is_set.return_value = False
            cancel.wait.return_value = False
            app.run_query(QUERY, True, cancel, events)
        results = [payload for kind, payload in events.queue if kind == "result"]
        self.assertEqual(len(results), 2)
        self.assertTrue(results[1][0].needs_verification)
        self.assertEqual(results[1][2], ("11:30",))

    def test_query_once_never_enters_booking_even_with_profile(self):
        events = queue.Queue()
        with TemporaryDirectory() as folder:
            fake_file = Path(folder) / "watcher.py"
            with (patch.object(app, "__file__", str(fake_file)), patch.object(app, "launch_query_browser") as pw,
                  patch.object(app, "check_day", return_value=app.Result(DAY, ("11:30",))),
                  patch.object(app, "start_booking") as booking):
                app.run_query(QUERY, False, threading.Event(), events, booking=PROFILE)
                booking.assert_not_called()
            logs = list((Path(folder) / "logs").glob("*.jsonl"))
            self.assertEqual(len(logs), 1)
            entries = [json.loads(line) for line in logs[0].read_text(encoding="utf-8").splitlines()]
            self.assertFalse(entries[0]["auto_booking"])
            self.assertEqual(entries[-1]["event"], "finished")
            self.assertNotIn(PROFILE.phone, logs[0].read_text(encoding="utf-8"))
        self.assertIn("result", [kind for kind, _ in events.queue])

    def test_failed_unrestricted_query_does_not_scan_calendar(self):
        with (TemporaryDirectory() as folder, patch.object(app, "launch_query_browser"),
              patch.object(app, "__file__", str(Path(folder) / "watcher.py")),
              patch.object(app, "check_day", return_value=app.Result(DAY, failed=True)),
              patch.object(app, "calendar_dates") as calendar):
            app.run_query(replace(QUERY, start=None, end=None), False, threading.Event(), queue.Queue())
            calendar.assert_not_called()


if __name__ == "__main__":
    unittest.main()
