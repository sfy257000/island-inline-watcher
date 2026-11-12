"""Small synchronous Chrome client using documented page and DOM operations.

Chrome keeps its native display, language, and profile settings. The client does
not install initialization scripts, patch browser properties, or subscribe to
the JavaScript debugger/console. Human verification always remains manual.
"""
import base64
import fnmatch
import json
import os
from pathlib import Path
import re
import socket
import subprocess
from time import monotonic, sleep
from types import SimpleNamespace
from urllib.request import ProxyHandler, build_opener

from browser_errors import BrowserError, BrowserTimeout
from websocket import create_connection, WebSocketException, WebSocketTimeoutException


VISIBLE = "n => !!n && n.getClientRects().length > 0 && getComputedStyle(n).visibility === 'visible'"


def chrome_executable():
    candidates = [Path(os.environ.get(name, '')) / 'Google/Chrome/Application/chrome.exe'
                  for name in ('PROGRAMFILES', 'PROGRAMFILES(X86)', 'LOCALAPPDATA')]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise BrowserError('Google Chrome is not installed')


class NativeBrowser:
    def __init__(self, profile_dir, *, headless=False, verification_js='() => false'):
        self.process = self.socket = None
        self.sequence = 0
        self.responses = {}
        self.commands = set()
        self.handlers = {}
        self.closed = False
        self.verification_js = verification_js
        profile = Path(profile_dir).resolve()
        profile.mkdir(parents=True, exist_ok=True)
        with socket.socket() as reserved:
            reserved.bind(('127.0.0.1', 0))
            port = reserved.getsockname()[1]
        command = [str(chrome_executable()), f'--user-data-dir={profile}',
                   '--remote-debugging-address=127.0.0.1', f'--remote-debugging-port={port}',
                   '--no-first-run', '--no-default-browser-check']
        if headless:
            command.append('--headless=new')
        command.append('about:blank')
        try:
            self.process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            opener = build_opener(ProxyHandler({}))
            deadline = monotonic() + 12
            while monotonic() < deadline:
                try:
                    with opener.open(f'http://127.0.0.1:{port}/json/list', timeout=0.3) as response:
                        targets = json.load(response)
                    target = next((t for t in targets if t.get('type') == 'page'), None)
                    if target:
                        break
                except OSError:
                    pass
                if self.process.poll() is not None:
                    raise BrowserError('The dedicated Chrome profile is already open or Chrome could not start')
                sleep(0.1)
            else:
                raise BrowserTimeout('Chrome did not start in time')
            self.socket = create_connection(target['webSocketDebuggerUrl'], timeout=5,
                                             suppress_origin=True, http_no_proxy=['127.0.0.1', 'localhost'])
            self.pages = [NativePage(self)]
        except (OSError, WebSocketException) as exc:
            self.close()
            raise BrowserError('Could not connect to the dedicated Chrome') from exc
        except Exception:
            self.close()
            raise

    def on(self, event, handler):
        self.handlers.setdefault(event, []).append(handler)

    def _receive(self, timeout):
        if self.closed or not self.socket:
            raise BrowserError('Chrome is closed')
        self.socket.settimeout(max(0.001, timeout))
        try:
            raw = self.socket.recv()
        except WebSocketTimeoutException:
            return
        except (WebSocketException, OSError) as exc:
            self.closed = True
            raise BrowserError('Chrome connection closed') from exc
        if not raw:
            self.closed = True
            raise BrowserError('Chrome connection closed')
        message = json.loads(raw)
        if 'id' in message:
            self.responses[message['id']] = message
        else:
            for handler in self.handlers.get(message.get('method'), ()):
                handler(message.get('params', {}))

    def send(self, method, params=None, timeout=10):
        if self.closed or not self.socket:
            raise BrowserError('Chrome is closed')
        self.sequence += 1
        self.commands.add(method)
        message_id = self.sequence
        try:
            self.socket.send(json.dumps({'id': message_id, 'method': method, 'params': params or {}}))
        except (WebSocketException, OSError) as exc:
            self.closed = True
            raise BrowserError('Chrome connection closed') from exc
        deadline = monotonic() + timeout
        while message_id not in self.responses:
            if monotonic() >= deadline:
                raise BrowserTimeout(f'{method} timed out')
            self._receive(deadline - monotonic())
        response = self.responses.pop(message_id)
        if 'error' in response:
            raise BrowserError(f'Chrome command failed: {method}')
        return response.get('result', {})

    def close(self):
        if self.socket:
            try:
                if not self.closed:
                    self.send('Browser.close', timeout=2)
            except BrowserError:
                pass
            self.socket.close()
            self.socket = None
        self.closed = True
        if self.process:
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                # Only the Chrome process owned by this instance is stopped.
                self.process.terminate()
                self.process.wait(timeout=5)

    def new_page(self):
        return self.pages[0]

    def add_cookies(self, cookies):
        self.send('Network.setCookies', {'cookies': cookies})

    def cookies(self, url):
        return self.send('Network.getCookies', {'urls': [url]})['cookies']


class NativePage:
    def __init__(self, browser):
        self.browser = browser
        self.handlers = {}
        self.requests = {}
        self.routes = []
        browser.on('Network.requestWillBeSent', self._request_started)
        browser.on('Network.loadingFinished', lambda p: self._request_finished(p, 'requestfinished'))
        browser.on('Network.loadingFailed', lambda p: self._request_finished(p, 'requestfailed'))

    def on(self, event, handler):
        if not self.handlers:
            self.browser.send('Network.enable')
        self.handlers.setdefault(event, []).append(handler)

    def _request_started(self, params):
        # BrowserFlow tracks request objects by identity.
        request = NativeRequest(params['request']['url'], params.get('type', '').lower())
        self.requests[params['requestId']] = request
        for handler in self.handlers.get('request', ()):
            handler(request)

    def _request_finished(self, params, event):
        request = self.requests.pop(params['requestId'], None)
        if request:
            for handler in self.handlers.get(event, ()):
                handler(request)

    def evaluate(self, source, arg=None):
        function = re.match(r'^\s*(?:async\s+)?(?:\([^()]*\)|[\w$]+)\s*=>', source)
        expression = f'({source})({self._argument(arg)})' if function else source
        result = self.browser.send('Runtime.evaluate', {'expression': expression, 'returnByValue': True,
                                                        'awaitPromise': True}, timeout=10)
        if result.get('exceptionDetails'):
            raise BrowserError('Page evaluation failed')
        return result.get('result', {}).get('value')

    def _argument(self, value):
        if isinstance(value, NativeLocator):
            return f'({value._nodes()})[0]'
        if isinstance(value, dict):
            return '{' + ','.join(json.dumps(k) + ':' + self._argument(v) for k, v in value.items()) + '}'
        if isinstance(value, (list, tuple)):
            return '[' + ','.join(self._argument(v) for v in value) + ']'
        return json.dumps(value, ensure_ascii=True)

    def wait_for_function(self, source, *, arg=None, polling=100, timeout=20000):
        deadline = monotonic() + timeout / 1000
        while monotonic() < deadline:
            try:
                value = self.evaluate(source, arg)
                if value:
                    return SimpleNamespace(json_value=lambda: value)
            except BrowserError:
                if self.is_closed():
                    raise
            self.wait_for_timeout(polling if isinstance(polling, int) else 100)
        raise BrowserTimeout('Waiting for page state timed out')

    def wait_for_timeout(self, milliseconds):
        deadline = monotonic() + milliseconds / 1000
        while monotonic() < deadline:
            self.browser._receive(min(0.1, deadline - monotonic()))

    @property
    def url(self):
        return self.evaluate('location.href')

    def goto(self, url, *, wait_until='domcontentloaded', timeout=25000):
        previous_loader = self.browser.send('Page.getFrameTree')['frameTree']['frame'].get('loaderId')
        self.browser.send('Page.navigate', {'url': url})
        return self._navigation_ready(url, timeout, previous_loader)

    def reload(self, *, wait_until='domcontentloaded', timeout=25000):
        url = self.url
        previous_loader = self.browser.send('Page.getFrameTree')['frameTree']['frame'].get('loaderId')
        self.browser.send('Page.reload')
        return self._navigation_ready(url, timeout, previous_loader)

    def _navigation_ready(self, url, timeout, previous_loader):
        deadline = monotonic() + timeout / 1000
        while monotonic() < deadline:
            try:
                loader = self.browser.send('Page.getFrameTree')['frameTree']['frame'].get('loaderId')
                if loader == previous_loader:
                    self.wait_for_timeout(100)
                    continue
                state = self.evaluate("() => ({url:location.href, ready:document.readyState, status:performance.getEntriesByType('navigation')[0]?.responseStatus || 200})")
                if state and state['url'] == url and state['ready'] in ('interactive', 'complete'):
                    return SimpleNamespace(status=state['status'])
            except BrowserError:
                if self.is_closed():
                    raise
            self.wait_for_timeout(100)
        raise BrowserTimeout('Navigation timed out')

    def is_closed(self):
        return self.browser.closed or self.browser.process.poll() is not None

    def bring_to_front(self):
        self.browser.send('Page.bringToFront')

    def screenshot(self, *, path):
        result = self.browser.send('Page.captureScreenshot', {'format': 'png'}, timeout=5)
        Path(path).write_bytes(base64.b64decode(result['data']))

    def locator(self, selector):
        return NativeLocator(self, [('css', selector)])

    def query_selector(self, selector):
        handles = self.locator(selector).first.element_handles()
        return handles[0] if handles else None

    def _check_verification(self):
        if self.evaluate(self.browser.verification_js):
            raise BrowserError('Human verification requires manual interaction')

    def route(self, pattern, handler):
        """Fixture interception, also useful for local regression tests."""
        self.routes.append((pattern, handler))
        if len(self.routes) == 1:
            def paused(params):
                for pattern, handler in reversed(self.routes):
                    if fnmatch.fnmatch(params['request']['url'], pattern):
                        handler(NativeRoute(self.browser, params['requestId']))
                        return
                self.browser.send('Fetch.continueRequest', {'requestId': params['requestId']})
            self.browser.on('Fetch.requestPaused', paused)
            self.browser.send('Fetch.enable', {'patterns': [{'urlPattern': '*'}]})


class NativeRequest:
    def __init__(self, url, resource_type):
        self.url, self.resource_type = url, resource_type


class NativeRoute:
    def __init__(self, browser, request_id):
        self.browser, self.request_id = browser, request_id

    def fulfill(self, *, content_type, body, status=200):
        self.browser.send('Fetch.fulfillRequest', {'requestId': self.request_id, 'responseCode': status,
            'responseHeaders': [{'name': 'Content-Type', 'value': content_type + '; charset=utf-8'}],
            'body': base64.b64encode(body.encode('utf-8')).decode('ascii')})


class NativeLocator:
    def __init__(self, page, steps):
        self.page, self.steps = page, steps

    def locator(self, selector):
        return NativeLocator(self.page, self.steps + [('css', selector)])

    @property
    def first(self):
        return NativeLocator(self.page, self.steps + [('index', 0)])

    def nth(self, index):
        return NativeLocator(self.page, self.steps + [('index', index)])

    def get_by_role(self, role, *, name, exact=False):
        return NativeLocator(self.page, self.steps + [('role', {'role': role, 'name': name, 'exact': exact})])

    def filter(self, *, has_text):
        pattern = has_text.pattern if isinstance(has_text, re.Pattern) else re.escape(has_text)
        return NativeLocator(self.page, self.steps + [('text', pattern)])

    def _nodes(self):
        return r'''(() => {
            const visible = ''' + VISIBLE + r''';
            let nodes = [document];
            for (const [type, value] of ''' + json.dumps(self.steps) + r''') {
                if (type === 'css') {
                    const checkVisible = value.includes(':visible');
                    const selector = value.replaceAll(':visible', '');
                    nodes = [...new Set(nodes.flatMap(n => [...n.querySelectorAll(selector)]))];
                    if (checkVisible) nodes = nodes.filter(visible);
                } else if (type === 'index') nodes = nodes.slice(value, value + 1);
                else if (type === 'role') {
                    nodes = nodes.flatMap(n => [...n.querySelectorAll(value.role === 'button' ? 'button,[role="button"]' : '[role="'+value.role+'"]')]);
                    nodes = nodes.filter(n => value.exact ? n.innerText.trim() === value.name : n.innerText.includes(value.name));
                } else if (type === 'text') nodes = nodes.filter(n => new RegExp(value).test(n.innerText));
            }
            return nodes;
        })()'''

    def count(self):
        return self.page.evaluate(f'({self._nodes()}).length')

    def evaluate_all(self, source):
        return self.page.evaluate(f'({source})({self._nodes()})')

    def _ready(self, timeout=5000):
        deadline = monotonic() + timeout / 1000
        while monotonic() < deadline:
            count = self.count()
            if count == 1:
                return
            if count > 1:
                raise BrowserError('Locator matched more than one element')
            self.page.wait_for_timeout(100)
        raise BrowserTimeout('Locator did not match an element')

    def evaluate(self, source, arg=None, *, timeout=5000):
        self._ready(timeout)
        return self.page.evaluate(f'({source})(({self._nodes()})[0], {self.page._argument(arg)})')

    def inner_text(self, *, timeout=5000):
        return self.evaluate('node => node.innerText', timeout=timeout)

    def all_text_contents(self):
        return self.evaluate_all('nodes => nodes.map(n => n.textContent)')

    def get_attribute(self, name, *, timeout=5000):
        return self.evaluate('(node, name) => node.getAttribute(name)', name, timeout=timeout)

    def input_value(self, *, timeout=5000):
        return self.evaluate('node => node.value', timeout=timeout)

    def is_visible(self):
        return bool(self.page.evaluate(f'({VISIBLE})(({self._nodes()})[0])'))

    def is_enabled(self, *, timeout=5000):
        return self.evaluate("n => !n.disabled && n.getAttribute('aria-disabled') !== 'true'", timeout=timeout)

    def wait_for(self, *, state='visible', timeout=5000):
        deadline = monotonic() + timeout / 1000
        while monotonic() < deadline:
            ready = self.is_visible() if state in ('visible', 'hidden') else bool(self.count())
            if ready == (state in ('visible', 'attached')):
                return
            self.page._check_verification()
            self.page.wait_for_timeout(100)
        raise BrowserTimeout('Locator state did not become ready')

    def click(self, *, timeout=5000):
        self.wait_for(timeout=timeout)
        self.page._check_verification()
        point = self.evaluate('''n => {
            if (n.disabled || n.getAttribute('aria-disabled') === 'true') throw new Error('Disabled');
            n.scrollIntoView({behavior:'instant', block:'center', inline:'center'});
            const r=n.getBoundingClientRect(); const x=r.x+r.width/2, y=r.y+r.height/2;
            const hit=document.elementFromPoint(x,y);
            if (!hit || !(n === hit || n.contains(hit))) throw new Error('Covered');
            return {x,y};
        }''')
        self.page._check_verification()
        for kind in ('mousePressed', 'mouseReleased'):
            self.page.browser.send('Input.dispatchMouseEvent', {'type': kind, **point, 'button': 'left', 'clickCount': 1})

    def select_option(self, *, value, timeout=5000):
        self.page._check_verification()
        return self.evaluate('''(node, value) => {
            if (![...node.options].some(o => o.value === value && !o.disabled)) throw new Error('Invalid option');
            Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype,'value').set.call(node,value);
            node.dispatchEvent(new Event('input',{bubbles:true}));
            node.dispatchEvent(new Event('change',{bubbles:true}));
        }''', value, timeout=timeout)

    def fill(self, value, *, timeout=5000):
        self.page._check_verification()
        self.evaluate('''(node,value) => {
            if(node.disabled || node.readOnly) throw new Error('Read-only');
            node.focus();
            Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(node,value);
            node.dispatchEvent(new Event('input',{bubbles:true}));
            node.dispatchEvent(new Event('change',{bubbles:true}));
        }''', value, timeout=timeout)

    def element_handles(self):
        handles = []
        for i in range(self.count()):
            result = self.page.browser.send('Runtime.evaluate', {
                'expression': f'({self._nodes()})[{i}]', 'returnByValue': False, 'objectGroup': 'elements',
            })
            if result.get('result', {}).get('objectId'):
                handles.append(NativeElement(self.page, result['result']['objectId']))
        return handles


class NativeElement:
    """Retain node identity, especially while waiting for one SMS challenge."""
    def __init__(self, page, object_id):
        self.page, self.object_id = page, object_id

    def evaluate(self, source, arg=None, **kwargs):
        references = []
        def encode(value):
            if isinstance(value, NativeElement):
                references.append({'objectId': value.object_id})
                return {'__nativeElement__': len(references) - 1}
            if isinstance(value, dict):
                return {key: encode(item) for key, item in value.items()}
            if isinstance(value, (list, tuple)):
                return [encode(item) for item in value]
            return value
        encoded = encode(arg)
        declaration = '''function(argument, ...nodes) {
            const decode = value => {
                if (Array.isArray(value)) return value.map(decode);
                if (value && typeof value === 'object') {
                    if ('__nativeElement__' in value) return nodes[value.__nativeElement__];
                    return Object.fromEntries(Object.entries(value).map(([k,v]) => [k,decode(v)]));
                }
                return value;
            };
            return (''' + source + ''')(this, decode(argument));
        }'''
        result = self.page.browser.send('Runtime.callFunctionOn', {
            'objectId': self.object_id, 'functionDeclaration': declaration,
            'arguments': [{'value': encoded}, *references], 'returnByValue': True,
        })
        if result.get('exceptionDetails'):
            raise BrowserError('Element evaluation failed')
        return result.get('result', {}).get('value')
