"""Optional, review-only MyMemory suggestions. No UI or persistence in workers."""
import html
import json
import re
import threading
import urllib.parse
import urllib.request

from .editor_store import validate_translation
from .proper_names import load_proper_names
from .config import VERSION

ENDPOINT = 'https://api.mymemory.translated.net/get'
TIMEOUT = 12
MAX_RESPONSE = 262144
MAX_CHUNKS = 24
_TOKEN = re.compile(r'ZXQ\d{5}QXZ')
_STRUCTURE = re.compile(r'\{[^{}]*\}|\[[^\[\]]*\]')


class SuggestionError(ValueError):
    pass


def _protect(source, catalog, proper_names=None):
    proper_names = load_proper_names() if proper_names is None else proper_names
    terms = dict(catalog)
    terms.update({name: row['ko'] for name, row in proper_names.items()})
    replacements = {}
    if 'ZXQ' in source:
        raise SuggestionError('원문에 번역 보호 토큰과 충돌하는 문자가 있습니다.')

    def token(value):
        key = 'ZXQ%05dQXZ' % len(replacements)
        replacements[key] = value
        return key

    def structure(match):
        value = match[0]
        if value.startswith('['):
            body = value[1:-1]
            if ':' in body:
                label, ident = body.rsplit(':', 1)
                # IDs are never translated. Visible labels still reach the service.
                if terms.get(label):
                    return token('[' + terms[label] + ':' + ident + ']')
                label = re.sub(r'\{[^{}]*\}', lambda m: token(m[0]), label)
                return token('[') + label + token(':' + ident + ']')
            return token('[' + terms.get(body, body) + ':' + body.lower() + ']')
        return token(value)

    protected = _STRUCTURE.sub(structure, source)
    # Only compact catalog names, never full description sentences, are glossary
    # candidates. Tokenization prevents later matches from changing replacements.
    names = [name for name, value in terms.items()
             if isinstance(name, str) and isinstance(value, str) and value
             and (name in proper_names or name != value) and ((name in proper_names) or (2 <= len(name) <= 70
             and len(name.split()) <= 8 and re.fullmatch(r"[A-Za-z][A-Za-z '\-]*", name)))
             and not any(c in value for c in '{}[]') and name in source]


    for group in ([name for name in names if name in proper_names],
                  [name for name in names if name not in proper_names]):
        if not group:
            continue
        pattern = re.compile(r'(?<!\w)(?:' + '|'.join(re.escape(n) for n in sorted(group, key=len, reverse=True)) + r')(?!\w)')
        def replace_name(match):
            name = match[0]
            ko = terms[name]
            row = proper_names.get(name)
            line_start = protected.rfind('\n', 0, match.start()) + 1
            line_end = protected.find('\n', match.end())
            line = protected[line_start:line_end if line_end >= 0 else len(protected)]
            quoted = match.start() > 0 and protected[match.start()-1] in '\"“'
            # A standalone title has no quotes. A unit sharing a spell's name
            # is only a spell reference when the sentence explicitly says so.
            spell_context = re.search(r'\b(?:cast|recast|channel|learn)\s+(?:your\s+)?$', protected[:match.start()], re.I) or re.match(r'\s+spell\b', protected[match.end():], re.I)
            if row and 'spell' in row['kinds'] and (row['kinds'] == ['spell'] or spell_context) and line.strip() != name and not quoted:
                ko = '"' + ko + '"'
            return token(ko)
        protected = pattern.sub(replace_name, protected)
    return protected, replacements


def _chunks(text):
    """Preserve separators and UTF-8 boundaries; never cut protection tokens."""
    parts = re.findall(r'ZXQ\d{5}QXZ|\s+|(?:(?!ZXQ\d{5}QXZ)\S)+', text)
    chunk = ''
    for part in parts:
        if len((chunk + part).encode('utf-8')) > 500:
            if chunk:
                yield chunk
                chunk = ''
            # Large whitespace blocks remain local and need no service request.
            if part.isspace():
                yield part
                continue
            if len(part.encode('utf-8')) > 500:
                for char in part:
                    if len((chunk + char).encode('utf-8')) > 500:
                        yield chunk
                        chunk = ''
                    chunk += char
                continue
        chunk += part
    if chunk:
        yield chunk


def _fetch(text, opener):
    query = urllib.parse.urlencode({'q': text, 'langpair': 'en|ko'})
    request = urllib.request.Request(ENDPOINT + '?' + query,
                                     headers={'User-Agent': 'RW3KoreanSanzuRiverr/' + VERSION})
    with opener(request, timeout=TIMEOUT) as response:
        raw = response.read(MAX_RESPONSE + 1)
    if len(raw) > MAX_RESPONSE:
        raise SuggestionError('번역 서비스 응답이 허용 크기를 초과했습니다.')
    data = json.loads(raw.decode('utf-8'))
    if str(data.get('responseStatus')) != '200' or data.get('quotaFinished'):
        raise SuggestionError('번역 서비스 오류: ' + str(data.get('responseDetails') or data.get('responseStatus'))[:180])
    value = data.get('responseData', {}).get('translatedText')
    if not isinstance(value, str) or not value.strip():
        raise SuggestionError('번역 서비스가 빈 제안을 반환했습니다.')
    return html.unescape(value).strip()


def suggest_translation(source, catalog, cancel_event=None, opener=None):
    """Return a validated draft; raises on transport, quota or token corruption."""
    validate_translation(source, source)
    event = cancel_event or threading.Event()
    protected, replacements = _protect(source, catalog)
    # Newlines remain local; translators may otherwise collapse paragraphs.
    chunks = [piece for line in re.split(r'(\r\n|\r|\n)', protected)
              for piece in _chunks(line)]
    if sum(bool(c.strip()) for c in chunks) > MAX_CHUNKS:
        raise SuggestionError('원문이 자동 번역 요청 한도를 초과합니다 (최대 24개 조각).')
    output = []
    for chunk in chunks:
        if event.is_set():
            raise SuggestionError('번역 요청이 취소되었습니다.')
        if not chunk.strip():
            output.append(chunk)
            continue
        leading = chunk[:len(chunk) - len(chunk.lstrip())]
        trailing = chunk[len(chunk.rstrip()):]
        output.append(leading + _fetch(chunk.strip(), opener or urllib.request.urlopen) + trailing)
    result = ''.join(output)
    if event.is_set():
        raise SuggestionError('번역 요청이 취소되었습니다.')
    if sorted(_TOKEN.findall(result)) != sorted(replacements):
        raise SuggestionError('번역 서비스가 보호 토큰을 변경했습니다. 직접 편집해 주세요.')
    result = _TOKEN.sub(lambda m: replacements[m[0]], result)
    validate_translation(source, result)
    return result


class SuggestionRequest:
    """Poll status: loading/ready/error/cancelled; read text only after ready."""
    def __init__(self, source, catalog):
        self.status = 'loading'
        self.text = ''
        self.error = ''
        self._cancel = threading.Event()
        self._lock = threading.Lock()
        snapshot = dict(catalog)
        self._thread = threading.Thread(target=self._run, args=(source, snapshot), daemon=True)
        self._thread.start()

    def cancel(self):
        with self._lock:
            self._cancel.set()
            self.status = 'cancelled'

    def _run(self, source, catalog):
        try:
            result = suggest_translation(source, catalog, self._cancel)
            with self._lock:
                if not self._cancel.is_set():
                    self.text = result
                    self.status = 'ready'
        except Exception as error:
            with self._lock:
                if not self._cancel.is_set():
                    self.error = '%s: %s' % (type(error).__name__, str(error)[:240])
                    self.status = 'error'
