from collections import OrderedDict
from pathlib import Path
import re
from .config import LANG_CODE, MOD_ID
from .editor_store import TranslationEditorStore
from .translation import composed_label
from .portal import portal_aliases
from .suggestions import SuggestionRequest
from . import exchange
from datetime import datetime

OPTION = 'sanzuriverr_translation_options'
MODAL_STATE = 'sanzuriverr_translation_editor'


class TranslationEditor:
    def __init__(self, main, catalog, path=None):
        self.main = main
        self.suggestion_factory = SuggestionRequest
        self.pg = main.pygame
        self.store = TranslationEditorStore(path or Path('mod_data') / MOD_ID / 'translation_editor.json', catalog)
        self.catalog = catalog


        aliases = portal_aliases(catalog)
        self.portal_aliases = aliases
        previous_missing = dict(self.store.missing)
        self.store.missing = {
            key: sample for key, sample in self.store.missing.items()
            if self.store.detection_mode == 'off' or
            (self.candidate(key) and not aliases.get(key)
             and composed_label(key, catalog) is None)
        }
        self.pending = OrderedDict((key, {'key': key, 'sample': sample})
                                   for key, sample in self.store.missing.items())
        self.missing_dirty = previous_missing != self.store.missing
        self.collection_error = ''
        self.next_save_retry = 0
        self.recent = OrderedDict()
        self.deferred = set()
        self.depth = 0
        self.suspended = 0
        self.modal = None
        self.next_popup = 0
        self.resume_pending = False

    def _sync_text_cache(self):
        revision = getattr(self.store, 'revision', 0)
        if getattr(self, '_text_cache_revision', None) != revision:
            self._text_cache_revision = revision
            self._candidate_cache = {}
            self._display_cache = {}
            self._translated_values = set(self.catalog.values())
            self.portal_aliases = portal_aliases(self.catalog)

    @staticmethod
    def _cache_text(cache, key, value):
        # Keep transient counters and unusually long descriptions bounded.
        if len(key) <= 4096:
            if len(cache) >= 1024:
                del cache[next(iter(cache))]
            cache[key] = value
        return value

    def candidate(self, key):
        if not isinstance(key, str):
            return False
        self._sync_text_cache()
        if key in self._candidate_cache:
            return self._candidate_cache[key]
        return self._cache_text(self._candidate_cache, key, self._candidate(key))

    def _candidate(self, key):
        if not isinstance(key, str) or not 1 < len(key) <= 12000 or key in self.store.ignored:
            return False
        catalog = getattr(self, 'catalog', self.store.catalog if hasattr(self.store, 'catalog') else {})
        if catalog.get(key) or key.strip() in ('a', 'an'):
            return False
        if getattr(self, 'portal_aliases', {}).get(key):
            return False
        visible = self.translate_display(key)
        visible = re.sub(r'\{[^{}]*\}', '', visible)
        visible = re.sub(r'\[([^\]:]*):[^\]]*\]', r'\1', visible)
        # Strip only complete key combinations in explicit Korean key hints.
        # English prose elsewhere remains eligible for missing collection.
        def key_hint(match):
            names = [part.strip() for part in match.group(1).split('+')]
            if all(catalog.get(name) == name or re.fullmatch(r'[A-Z]|F(?:[1-9]|1[0-2])', name) for name in names):
                return ''
            return match.group(0)
        visible = re.sub(r'(?:단축키|다음 카테고리):[ \t]*([^\n]+)', key_hint, visible)
        visible = re.sub(r'\([A-Z]\)(?=\s*또는 클릭하여 입력)', '', visible)
        visible = re.sub(r'\((?:Enter 또는 좌클릭|Esc 또는 우클릭)\)', '', visible)
        # Known abbreviations in a Korean sentence are not untranslated prose.
        visible = re.sub(r'(?<![A-Za-z])(?:HP|SP|FPS|UI|SDL|ESC|CTRL|ALT|SHIFT)(?![A-Za-z])', '', visible)
        if re.fullmatch(r'(?:[A-Z]|F(?:[1-9]|1[0-2]))', visible.strip()):
            return False
        return bool(re.search('[A-Za-z]', visible))

    def translate_display(self, value):
        self._sync_text_cache()
        if value in self._display_cache:
            return self._display_cache[value]
        return self._cache_text(self._display_cache, value, self._translate_display(value))

    def _translate_display(self, value):
        """Translate complete lines of assembled tooltips; exact overrides win."""
        catalog = getattr(self, 'catalog', self.store.catalog if hasattr(self.store, 'catalog') else {})
        if value in catalog:
            return catalog[value]
        composed = composed_label(value, catalog)
        if composed is not None:
            return composed
        return '\n'.join(catalog.get(line) or composed_label(line, catalog) or line for line in value.split('\n'))

    def observe(self, key, result, values, missing):
        if self.store.detection_mode == 'off': return
        if self.suspended or self.modal or not missing or not self.candidate(key):
            return
        record = {'key': key, 'sample': result, 'values': dict(values)}
        self.recent[result] = record
        self.recent.move_to_end(result)
        while len(self.recent) > 2048:
            self.recent.popitem(last=False)
        if self.depth:
            self.enqueue(record)

    def enqueue(self, record):
        key = record['key']
        if key not in self.pending and not self.catalog.get(key) and key not in self.store.ignored:
            self.pending[key] = record
            self.missing_dirty = True

    def flush_missing(self):
        if not self.missing_dirty or self.pg.time.get_ticks() < self.next_save_retry:
            return
        try:
            self.store.save_missing({key: row['sample'] for key, row in self.pending.items()}, force=True)
        except (OSError, ValueError) as exc:
            self.collection_error = '누락 목록 저장 실패: ' + str(exc)
            self.next_save_retry = self.pg.time.get_ticks() + 5000
            print('[%s translation editor] %s' % (MOD_ID, self.collection_error))
        else:
            self.missing_dirty = False
            self.collection_error = ''
            self.next_save_retry = 0

    def observe_draw(self, value):
        if self.store.detection_mode == 'off': return
        # A pre-resolved string can reach the drawing API without calling T().
        self._sync_text_cache()
        if type(value) is str and value in self.recent:
            self.enqueue(self.recent[value])
        elif type(value) is str and (value in self.pending or value in self._translated_values):
            return
        elif type(value) is str and self.candidate(value):
            self.enqueue({'key': value, 'sample': value})

    def open(self, view, mode='settings', key=None):
        pg = self.pg
        if self.modal and self.modal.get('suggestion'):
            self.modal['suggestion'].cancel()
        if self.modal is None:
            self.saved_state = view.state
            self.saved_target = view.examine_target
            self.background = view.screen.copy()
            self.restore_text = any(getattr(view, name, False) for name in
                                    ('search_focused', 'mutator_param_search_focused', 'combat_log_query_focused'))
        view.state = MODAL_STATE
        view.repeat_keys.clear()
        self.modal = {'mode': mode, 'key': key, 'text': self.catalog.get(key, key or ''),
                      'cursor': 0, 'preedit': '', 'selection': False, 'error': '',
                      'page': 0, 'scroll': 0, 'source_scroll': 0, 'button_index': 0,
                      'suggestion_scroll': 0, 'suggestion': None}
        self.modal['cursor'] = len(self.modal['text'])
        if mode == 'edit':
            self.modal['suggestion'] = self.suggestion_factory(key, dict(self.catalog))
            pg.key.start_text_input()
        else:
            pg.key.stop_text_input()

    def close(self, view):
        if self.modal and self.modal.get('suggestion'):
            self.modal['suggestion'].cancel()
        self.modal = None
        # Keep this entire frame in the paused state. Resume on the next frame
        # after the input that closed the popup has been consumed.
        self.resume_pending = True
        view.repeat_keys.clear()
        view.events = []
        if self.restore_text:
            self.pg.key.start_text_input()
        else:
            self.pg.key.stop_text_input()
        self.next_popup = self.pg.time.get_ticks() + 1500

    def ready(self, view):
        if not self.store.enabled or self.modal or self.pg.time.get_ticks() < self.next_popup:
            return None
        import Localisation as loc
        if loc.get_locale() != LANG_CODE or view.state in (self.main.STATE_OPTIONS, MODAL_STATE):
            return None
        if any(getattr(view, name, False) for name in
               ('search_focused', 'mutator_param_search_focused', 'combat_log_query_focused')):
            return None
        game = getattr(view, 'game', None)
        if game and (not game.is_awaiting_input() or game.gameover or game.victory):
            return None
        return next((key for key in self.pending if key not in self.deferred
                     and key not in self.store.ignored and not self.catalog.get(key)), None)

    def action(self, view, action):
        m = self.modal
        try:
            if action == 'close':
                self.close(view)
            elif action == 'toggle':
                modes = ('off', 'instant', 'collect')
                self.action(view, ('mode', modes[(modes.index(self.store.detection_mode) + 1) % len(modes)]))
            elif isinstance(action, tuple) and action[0] == 'mode':
                self.flush_missing()
                if self.missing_dirty:
                    raise ValueError(self.collection_error or '누락 목록을 저장한 후 모드를 변경할 수 있습니다.')
                self.store.set_mode(action[1])
                self.recent.clear()
                adapter = getattr(self, 'language_adapter', None)
                if adapter: adapter.observer = None if action[1] == 'off' else self.observe
            elif action == 'youtube':
                import os
                os.startfile('https://youtube.com/@sanzuriverr?si=LhGn21Y6S3r9OeYt')
            elif action in ('pending', 'saved', 'settings'):
                self.open(view, action)
            elif action == 'export':
                folder = self.store.path.parent / 'exports'
                folder.mkdir(parents=True, exist_ok=True)
                path = folder / ('translations-' + datetime.now().strftime('%Y%m%d-%H%M%S-%f') + '.txt')
                exchange.export_text(path, self.store.overrides)
                self.open(view, 'share_result')
                self.modal['message'] = '내보내기 완료: %d개\n\n' % len(self.store.overrides) + str(path.resolve())
                try:
                    exchange.open_folder(folder)
                except OSError as exc:
                    self.modal['error'] = '파일 저장은 완료했지만 폴더 열기 실패: ' + str(exc)
            elif action == 'import':
                path = exchange.choose_file(self.store.path.parent.resolve(), self.pg.display.get_wm_info().get('window', 0))
                view.events = []
                self.pg.event.clear([self.pg.KEYDOWN, self.pg.KEYUP, self.pg.MOUSEBUTTONDOWN, self.pg.MOUSEBUTTONUP])
                if path is not None:
                    imported = exchange.read_text(path)
                    self.open(view, 'share_review')
                    rows = []
                    for key, value in imported['entries'].items():
                        old = self.catalog.get(key)
                        rows.append({'key': key, 'value': value, 'old': old,
                                     'kind': '새 항목' if old is None else '중복' if old == value else '충돌',
                                     'use': old is None})
                    self.modal.update(rows=rows, import_errors=imported['errors'], review_scroll=0)
            elif action == 'import_toggle':
                row = m['rows'][m['page']]
                if row['kind'] != '중복': row['use'] = not row['use']
            elif action in ('review_prev', 'review_next'):
                m['page'] = max(0, min(len(m['rows']) - 1, m['page'] + (-1 if action == 'review_prev' else 1)))
                m['review_scroll'] = 0
            elif action == 'import_apply':
                if m['import_errors']: raise ValueError('파일 오류를 수정한 후 다시 가져오세요.')
                entries = {r['key']: r['value'] for r in m['rows'] if r['use']}
                if not entries: raise ValueError('적용할 항목이 없습니다.')
                self.store.merge_translations(entries)
                for cache_name in ('search_cache', 'bestiary_search_cache'):
                    cache = getattr(view, cache_name, None)
                    if cache is not None: cache.clear()
                for key in entries:
                    self.pending.pop(key, None)
                    self.deferred.discard(key)
                self.open(view, 'share_result')
                self.modal['message'] = '%d개 번역을 적용했습니다.\n가져온 번역은 다음 내보내기에 함께 포함됩니다.\n적용 전 저장 파일은 자동 백업했습니다.' % len(entries)
            elif isinstance(action, tuple) and action[0] == 'edit':
                self.open(view, 'edit', action[1])
            elif action == 'save':
                if m['preedit']:
                    m['error'] = '한글 조합을 먼저 확정한 뒤 저장하세요.'
                    return
                self.store.save_translation(m['key'], m['text'])
                self.pending.pop(m['key'], None)
                self.deferred.discard(m['key'])
                self.close(view)
            elif action == 'apply_suggestion':
                request = m.get('suggestion')
                if request and request.status == 'ready':
                    if m['preedit']:
                        m['error'] = '한글 조합을 먼저 확정한 뒤 추천을 적용하세요.'
                        return
                    m['text'] = request.text
                    m['cursor'] = len(m['text'])
                    m['selection'], m['scroll'], m['error'] = False, 0, ''
            elif action == 'retry_suggestion':
                request = m.get('suggestion')
                if request and request.status == 'error':
                    request.cancel()
                    m['suggestion'] = self.suggestion_factory(m['key'], dict(self.catalog))
                    m['suggestion_scroll'] = 0
            elif action == 'skip':
                self.deferred.add(m['key'])
                self.close(view)
            elif action == 'ignore':
                self.store.ignore(m['key'])
                self.pending.pop(m['key'], None)
                self.close(view)
            elif action == 'remove':
                self.store.remove_translation(m['key'])
                self.deferred.add(m['key'])
                self.close(view)
            elif action in ('prev', 'next'):
                m['page'] = max(0, m['page'] + (-1 if action == 'prev' else 1))
        except (OSError, ValueError, ImportError, AttributeError, self.pg.error) as exc:
            (self.modal or m)['error'] = str(exc)
            print('[%s translation editor] %s' % (MOD_ID, exc))

    def insert(self, text):
        m = self.modal
        if m['selection']:
            m['text'], m['cursor'], m['selection'] = '', 0, False
        pos = m['cursor']
        m['text'] = m['text'][:pos] + text.replace('\r\n', '\n').replace('\r', '\n') + m['text'][pos:]
        m['cursor'] = pos + len(text.replace('\r\n', '\n').replace('\r', '\n'))
        m['preedit'] = ''

    def handle(self, view):
        pg, m = self.pg, self.modal
        events = list(view.events)
        ime_frame = bool(m['preedit']) or any(e.type in (pg.TEXTEDITING, pg.TEXTINPUT) for e in events)
        for event in events:
            if self.modal is not m:
                break
            if event.type == pg.WINDOWFOCUSLOST:
                m['preedit'] = ''
                pg.key.stop_text_input()
            elif event.type == pg.WINDOWFOCUSGAINED and m['mode'] == 'edit':
                pg.key.start_text_input()
            elif event.type == pg.MOUSEBUTTONDOWN and event.button == 1:
                point = view.get_mouse_pos()
                hit = next((action for rect, action in self.buttons if rect.collidepoint(point)), None)
                if hit is not None:
                    self.action(view, hit)
                elif m['mode'] == 'edit' and self.input_rect.collidepoint(point):
                    line_no = min(len(self.input_lines) - 1, max(0, (point[1] - self.input_rect.y - 8) // self.line_height + m['scroll']))
                    line, start = self.input_lines[line_no]
                    offset = max(0, point[0] - self.input_rect.x - 10)
                    col = min(range(len(line) + 1), key=lambda n: abs(self.font.size(line[:n])[0] - offset))
                    m['cursor'], m['selection'] = start + col, False
            elif event.type == pg.MOUSEWHEEL and m['mode'] == 'edit':
                if self.source_rect.collidepoint(view.get_mouse_pos()):
                    m['source_scroll'] = max(0, min(self.source_max, m['source_scroll'] - event.y * 2))
                elif self.suggestion_rect.collidepoint(view.get_mouse_pos()):
                    m['suggestion_scroll'] = max(0, min(self.suggestion_max, m['suggestion_scroll'] - event.y * 2))
            elif event.type == pg.MOUSEWHEEL and m['mode'] in ('share_review', 'share_result'):
                m['review_scroll'] = max(0, min(m.get('review_max', 0), m.get('review_scroll', 0) - event.y * 3))
            elif m['mode'] == 'edit' and event.type == pg.TEXTEDITING:
                m['preedit'] = event.text
            elif m['mode'] == 'edit' and event.type == pg.TEXTINPUT:
                self.insert(event.text)
            elif event.type == pg.KEYDOWN:
                mod = getattr(event, 'mod', 0)
                ctrl = bool(mod & pg.KMOD_CTRL)
                if event.key == pg.K_ESCAPE:
                    if not ime_frame:
                        self.action(view, 'skip' if m['mode'] == 'edit' else 'close')
                elif m['mode'] in ('share_review', 'share_result') and event.key in (pg.K_PAGEUP, pg.K_PAGEDOWN):
                    m['review_scroll'] = max(0, min(m.get('review_max',0),m.get('review_scroll',0) + (-5 if event.key == pg.K_PAGEUP else 5)))
                elif m['mode'] != 'edit' and self.buttons:
                    if event.key in (pg.K_TAB, pg.K_DOWN, pg.K_UP):
                        delta = -1 if event.key == pg.K_UP else 1
                        m['button_index'] = (m['button_index'] + delta) % len(self.buttons)
                    elif event.key in (pg.K_RETURN, pg.K_SPACE):
                        self.action(view, self.buttons[m['button_index'] % len(self.buttons)][1])
                elif m['mode'] == 'edit' and not ime_frame:
                    if ctrl and event.key == pg.K_RETURN:
                        self.action(view, 'save')
                    elif ctrl and event.key == pg.K_a:
                        m['selection'] = True
                    elif ctrl and event.key in (pg.K_v, pg.K_c):
                        try:
                            if not pg.scrap.get_init(): pg.scrap.init()
                            if event.key == pg.K_c:
                                pg.scrap.put(pg.SCRAP_TEXT, m['text'].encode('utf-8') + b'\0')
                            else:
                                raw = pg.scrap.get(pg.SCRAP_TEXT)
                                if raw: self.insert(raw.rstrip(b'\0').decode('utf-8'))
                        except (pg.error, UnicodeError) as exc:
                            m['error'] = '클립보드 읽기/쓰기 실패: ' + str(exc)
                    elif event.key == pg.K_RETURN:
                        self.insert('\n')
                    elif event.key in (pg.K_BACKSPACE, pg.K_DELETE):
                        if m['selection']:
                            m['text'], m['cursor'], m['selection'] = '', 0, False
                        else:
                            p = m['cursor']
                            if event.key == pg.K_BACKSPACE and p:
                                m['text'] = m['text'][:p - 1] + m['text'][p:]; m['cursor'] -= 1
                            elif event.key == pg.K_DELETE:
                                m['text'] = m['text'][:p] + m['text'][p + 1:]
                    elif event.key in (pg.K_LEFT, pg.K_RIGHT, pg.K_HOME, pg.K_END):
                        if event.key == pg.K_HOME: m['cursor'] = 0
                        elif event.key == pg.K_END: m['cursor'] = len(m['text'])
                        else: m['cursor'] = max(0, min(len(m['text']), m['cursor'] + (-1 if event.key == pg.K_LEFT else 1)))
                        m['selection'] = False
                    elif event.key in (pg.K_UP, pg.K_DOWN):
                        row = max(i for i, (_, start) in enumerate(self.input_lines) if start <= m['cursor'])
                        column = m['cursor'] - self.input_lines[row][1]
                        row = max(0, min(len(self.input_lines) - 1, row + (-1 if event.key == pg.K_UP else 1)))
                        line, start = self.input_lines[row]
                        m['cursor'], m['selection'] = start + min(column, len(line)), False
        view.repeat_keys.clear()
        view.events = []  # Never deliver editing keys/clicks to the game below.

    def wrap(self, text, width):
        lines, buf, start = [], '', 0
        for index, char in enumerate(text):
            if char == '\n':
                lines.append((buf, start)); buf, start = '', index + 1
            elif buf and self.font.size(buf + char)[0] > width:
                lines.append((buf, start)); buf, start = char, index
            else:
                buf += char
        lines.append((buf, start))
        return lines

    def label(self, surface, text, pos, color=(224, 229, 237)):
        surface.blit(self.font.render(text, True, color), pos)

    def button(self, view, text, rect, action):
        pg = self.pg
        selected = self.modal['mode'] != 'edit' and len(self.buttons) == self.modal['button_index']
        pg.draw.rect(view.screen, (57, 94, 100) if selected else (43, 66, 79), rect, border_radius=5)
        pg.draw.rect(view.screen, (101, 154, 162), rect, 1, border_radius=5)
        self.label(view.screen, text, (rect.x + 12, rect.y + (rect.height - self.font.get_height()) // 2))
        self.buttons.append((rect, action))

    def draw(self, view):
        pg, m = self.pg, self.modal
        self.font = view.font
        self.line_height = max(28, self.font.get_linesize() + 3)
        screen = view.screen
        if self.background.get_size() != screen.get_size():
            self.background = pg.transform.scale(self.background, screen.get_size())
        screen.blit(self.background, (0, 0))
        shade = pg.Surface(screen.get_size(), pg.SRCALPHA); shade.fill((0, 0, 0, 170)); screen.blit(shade, (0, 0))
        width, height = min(1120, screen.get_width() - 48), min(820, screen.get_height() - 48)
        box = pg.Rect((screen.get_width() - width) // 2, (screen.get_height() - height) // 2, width, height)
        pg.draw.rect(screen, (19, 28, 38), box, border_radius=8)
        pg.draw.rect(screen, (111, 181, 174), box, 2, border_radius=8)
        x, y, w = box.x + 24, box.y + 22, box.width - 48
        self.buttons = []
        self.label(screen, '삼도리버 · 번역 도우미', (x, y), (133, 216, 204)); y += 48
        footer = box.bottom - 72
        def button(text, yy, action, xx=x, ww=None):
            self.button(view, text, pg.Rect(xx, yy, ww or w, 40), action)
        if m['mode'] == 'settings':
            self.label(screen, '업데이트에 의한 번역 누락을 직접 수정 가능합니다.', (x, y)); y += 40
            mode_labels = {'off': '번역 누락 감지 안 함', 'instant': '감지된 번역 누락 즉시 수정하기', 'collect': '감지된 번역 누락 저장해두기'}
            button(mode_labels[self.store.detection_mode], y, 'toggle'); y += 48
            button('저장된 번역 누락 수정하기 (%d개)' % len(self.pending), y, 'pending'); y += 48
            button('내가 저장한 번역 (%d개)' % len(self.store.overrides), y, 'saved'); y += 56
            self.label(screen, '커스텀 번역은 txt 형식으로 공유 가능합니다. 커뮤니티를 위해 공유해주세요!', (x, y)); y += 38
            button('개인 번역 내보내기 (.txt)', y, 'export'); y += 56
            button('번역 파일 가져오기 (.txt)', y, 'import'); y += 56
            banner_height = min(153, max(40, footer - y - 18))
            rect = pg.Rect(x, y, w, banner_height)
            self.button(view, '', rect, 'youtube')
            if not hasattr(self, '_youtube_banner'):
                self._youtube_banner = pg.image.load(str(Path(__file__).resolve().parent.parent / 'youtube_banner.png')).convert_alpha()
                self._youtube_banner_scaled = {}
            image_height = banner_height - 12
            if image_height not in self._youtube_banner_scaled:
                source = self._youtube_banner
                size = (round(source.get_width() * image_height / source.get_height()), image_height)
                self._youtube_banner_scaled[image_height] = pg.transform.smoothscale(source, size)
            banner = self._youtube_banner_scaled[image_height]
            screen.blit(banner, (rect.x + 12, rect.y + 6))
            self.label(screen, '모드 제작자 유튜브로 가기',
                       (rect.x + 12 + banner.get_width() + 24,
                        rect.centery - self.font.get_height() // 2))
            button('닫기  [Esc]', footer, 'close')
        elif m['mode'] in ('share_review', 'share_result'):
            if m['mode'] == 'share_result':
                body = m['message']
                button('설정으로', footer, 'settings')
                bottom = footer - 68
            else:
                rows, errors = m['rows'], m['import_errors']
                counts = {kind: sum(r['kind'] == kind for r in rows) for kind in ('새 항목', '중복', '충돌')}
                self.label(screen, '새 항목 %d · 중복 %d · 충돌 %d · 오류 %d' % (*counts.values(), len(errors)), (x, y)); y += 34
                self.label(screen, '충돌은 기본적으로 기존 번역 유지 · 내용을 마우스 휠로 스크롤', (x, y)); y += 36
                m['page'] = max(0, min(m['page'], len(rows) - 1))
                if rows:
                    row = rows[m['page']]
                    body = '%d / %d · %s\n\n원문:\n%s\n\n기존 번역:\n%s\n\n가져온 번역:\n%s' % (m['page']+1,len(rows),row['kind'],row['key'],row['old'] or '(없음)',row['value'])
                else: body = '번역 항목이 없습니다.'
                if errors: body = '파일 오류: 적용할 수 없습니다.\n' + '\n'.join(errors) + '\n\n' + body
                bottom = footer - 120
                controls = [('이전 항목', 'review_prev'), ('다음 항목', 'review_next')]
                if rows and row['kind'] != '중복':
                    controls.append(('가져온 번역 사용' if row['use'] else '기존 유지 / 추가 안 함', 'import_toggle'))
                for i,(label,action) in enumerate(controls):
                    button(label, footer-50, action, x+i*w//3, w//3-10)
                button('취소 / 설정으로', footer, 'settings', x, w//2-10)
                if not errors:
                    button('선택한 %d개 적용' % sum(r['use'] for r in rows), footer, 'import_apply', x+w//2, w//2-10)
            area = pg.Rect(x, y, w, max(28, bottom-y))
            lines = self.wrap(body, w-12)
            count = max(1, area.height//self.line_height)
            m['review_max'] = max(0,len(lines)-count)
            m['review_scroll'] = min(m.get('review_scroll',0),m['review_max'])
            clip=screen.get_clip(); screen.set_clip(clip.clip(area))
            for n,(line,_) in enumerate(lines[m['review_scroll']:m['review_scroll']+count]):
                self.label(screen,line,(x,y+n*self.line_height))
            screen.set_clip(clip)
        elif m['mode'] in ('pending', 'saved'):
            keys = list(self.store.overrides if m['mode'] == 'saved' else self.pending)
            per_page = max(1, (footer - y - 56) // 49)
            m['page'] = min(m['page'], max(0, (len(keys) - 1) // per_page))
            self.label(screen, '문구를 선택하면 수정할 수 있습니다.  %d / %d쪽' % (m['page'] + 1, max(1, (len(keys) + per_page - 1) // per_page)), (x, y)); y += 38
            for key in keys[m['page'] * per_page:(m['page'] + 1) * per_page]:
                label = key.replace('\n', ' / ')
                while self.font.size(label)[0] > w - 35: label = label[:-2]
                button(label, y, ('edit', key)); y += 49
            for index, (label, action) in enumerate([('이전', 'prev'), ('다음', 'next'), ('설정으로', 'settings')]):
                button(label, footer, action, x + index * (w // 3), w // 3 - 10)
        else:
            self.label(screen, '원문 · 마우스 휠로 스크롤', (x, y)); y += 32
            self.source_rect = pg.Rect(x, y, w, 76)
            source = self.wrap(m['key'], w - 20)
            count = max(1, (self.source_rect.height - 16) // self.line_height)
            self.source_max = max(0, len(source) - count)
            m['source_scroll'] = min(m['source_scroll'], self.source_max)
            pg.draw.rect(screen, (12, 18, 25), self.source_rect)
            for n, (line, _) in enumerate(source[m['source_scroll']:m['source_scroll'] + count]):
                self.label(screen, line, (x + 10, y + 8 + n * self.line_height), (183, 193, 210))
            y += 88
            request = m.get('suggestion')
            self.label(screen, '추천 번역 · MyMemory 자동 요청 · 휠로 스크롤', (x, y))
            if request and request.status == 'ready':
                button('추천 적용', y - 4, 'apply_suggestion', x + w - 170, 170)
            elif request and request.status == 'error':
                button('다시 요청', y - 4, 'retry_suggestion', x + w - 170, 170)
            y += 40
            self.suggestion_rect = pg.Rect(x, y, w, 80)
            pg.draw.rect(screen, (22, 38, 44), self.suggestion_rect)
            status = request.status if request else 'loading'
            suggested = (request.text if status == 'ready' else
                         '추천 요청 실패: ' + request.error if status == 'error' else
                         '추천 번역을 가져오는 중입니다. 직접 입력해도 됩니다.')
            lines = self.wrap(suggested, w - 20)
            count = max(1, (self.suggestion_rect.height - 16) // self.line_height)
            self.suggestion_max = max(0, len(lines) - count)
            m['suggestion_scroll'] = min(m['suggestion_scroll'], self.suggestion_max)
            clip = screen.get_clip(); screen.set_clip(clip.clip(self.suggestion_rect))
            for n, (line, _) in enumerate(lines[m['suggestion_scroll']:m['suggestion_scroll'] + count]):
                self.label(screen, line, (x + 10, y + 8 + n * self.line_height), (183, 214, 204))
            screen.set_clip(clip)
            y += 92
            self.label(screen, '한국어 번역 · {변수}와 [표시어:식별자]는 유지하세요.', (x, y)); y += 32
            self.input_rect = pg.Rect(x, y, w, max(60, footer - y - 94))
            pg.draw.rect(screen, (30, 43, 55) if not m['selection'] else (43, 67, 89), self.input_rect)
            pg.draw.rect(screen, (133, 216, 204), self.input_rect, 1)
            display = m['text'][:m['cursor']] + m['preedit'] + m['text'][m['cursor']:]
            self.input_lines = self.wrap(display, w - 24)
            caret = m['cursor'] + len(m['preedit'])
            caret_row = max(i for i, (_, start) in enumerate(self.input_lines) if start <= caret)
            visible_count = max(1, (self.input_rect.height - 16) // self.line_height)
            m['scroll'] = min(m['scroll'], caret_row)
            m['scroll'] = max(m['scroll'], caret_row - visible_count + 1)
            clip = screen.get_clip(); screen.set_clip(clip.clip(self.input_rect))
            for n, (line, start) in enumerate(self.input_lines[m['scroll']:m['scroll'] + visible_count]):
                self.label(screen, line, (x + 10, y + 8 + n * self.line_height))
            line, start = self.input_lines[caret_row]
            cx = x + 10 + self.font.size(line[:caret - start])[0]
            cy = y + 8 + (caret_row - m['scroll']) * self.line_height
            if m['preedit'] or (pg.time.get_ticks() // 500) % 2 == 0:
                pg.draw.line(screen, (255, 223, 138), (cx, cy), (cx, cy + self.font.get_height()), 2)
            if m['preedit']:
                pg.draw.line(screen, (255, 223, 138), (max(x + 10, cx - self.font.size(m['preedit'])[0]), cy + self.font.get_height()), (cx, cy + self.font.get_height()), 1)
            screen.set_clip(clip)
            pg.key.set_text_input_rect(pg.Rect(cx, cy, 2, self.line_height))
            self.label(screen, 'Enter: 줄바꿈 · Ctrl+Enter: 저장 · Ctrl+A: 전체 선택 · Ctrl+V: 붙여넣기', (x, self.input_rect.bottom + 10))
            actions = [('저장하고 적용', 'save'), ('나중에  [Esc]', 'skip'), ('이 문구 제외', 'ignore')]
            if m['key'] in self.store.overrides: actions[-1] = ('내 번역 삭제', 'remove')
            for index, (label, action) in enumerate(actions):
                button(label, footer, action, x + index * (w // 3), w // 3 - 10)
        error = m['error'] or self.collection_error
        if error:
            error_lines = self.wrap(error, w)
            for index, (line, _) in enumerate(error_lines[:2]):
                self.label(screen, line, (x, footer - 60 + index * 27), (255, 166, 140))


def install_editor(main, editor):
    import Localisation as loc
    cls, pg = main.PyGameView, main.pygame
    required = ('get_options_entries', 'draw_options_menu', 'process_options_input', 'draw_screen', 'draw_string', 'draw_wrapped_string')
    for name in required:
        if not hasattr(cls, name): raise RuntimeError('Translation editor: missing PyGameView.' + name)
    original_entries, original_options = cls.get_options_entries, cls.draw_options_menu
    original_input, original_screen = cls.process_options_input, cls.draw_screen

    def entries(view):
        return list(original_entries(view)) + [OPTION]

    def options(view):
        original_options(view)
        rects = [rect for rect, _ in view.ui_rects]
        x = view.options_menu_x or view.screen.get_width() // 3
        y = max((r.bottom for r in rects), default=view.screen.get_height() // 2) + 12
        view.draw_string('삼도리버 번역 도우미', view.screen, x, y, mouse_content=OPTION, pre_resolved=True)

    def options_input(view):
        events = list(view.events)
        for event in events:
            target = view.examine_target
            confirm = event.type == pg.KEYDOWN and event.key in view.key_binds[main.KEY_BIND_CONFIRM]
            if event.type == pg.MOUSEBUTTONDOWN and event.button == 1:
                target = next((value for rect, value in view.ui_rects if rect.collidepoint(view.get_mouse_pos())), None)
                confirm = True
            if confirm and target == OPTION:
                editor.open(view)
                view.events = []
                return
            view.events = [event]
            original_input(view)
        view.events = events

    def draw_screen(view):
        if editor.store.detection_mode != 'off': editor.flush_missing()
        if editor.resume_pending:
            editor.resume_pending = False
            view.state = editor.saved_state
            view.examine_target = editor.saved_target
            view.repeat_keys.clear()
            view.events = []
            view.screen.blit(editor.background, (0, 0))
            return original_screen(view)
        just_opened = False
        if not editor.modal:
            key = editor.ready(view)
            if key:
                editor.open(view, 'edit', key)
                just_opened = True
        if editor.modal:
            editor.suspended += 1
            try:
                editor.draw(view)
                if not just_opened:
                    editor.handle(view)
                    if editor.modal: editor.draw(view)
                view.repeat_keys.clear()
                view.events = []
            finally:
                editor.suspended -= 1
        if editor.collection_error and not editor.modal:
            view.draw_string('번역 도우미: 누락 목록을 저장하지 못했습니다. 설정에서 확인하세요.',
                             view.screen, 24, view.screen.get_height() - 40,
                             color=(255, 166, 140), pre_resolved=True)
        return original_screen(view)

    def drawing(original):
        def draw(view, value, *args, **kwargs):
            if getattr(view, '_sanzuriverr_native_language_row', False):
                return original(view, value, *args, **kwargs)
            record = editor.recent.get(value) if editor.store.detection_mode != 'off' and type(value) is str else None
            if loc.get_locale() == LANG_CODE and kwargs.get('pre_resolved') and record and record['key'] in editor.store.overrides:
                value = editor.store.overrides[record['key']].format(**record['values'])
            elif (kwargs.get('pre_resolved') and type(value) is str
                  and loc.get_locale() == LANG_CODE and editor.catalog.get(value)):
                value = editor.catalog[value]
            elif type(value) is str and loc.get_locale() == LANG_CODE:
                value = editor.translate_display(value)
            if editor.store.detection_mode == 'off' or editor.suspended or editor.modal:
                return original(view, value, *args, **kwargs)
            editor.depth += 1
            try:
                if loc.get_locale() == LANG_CODE:
                    editor.observe_draw(value)
                return original(view, value, *args, **kwargs)
            finally:
                editor.depth -= 1
        return draw

    cls.get_options_entries, cls.draw_options_menu = entries, options
    cls.process_options_input, cls.draw_screen = options_input, draw_screen
    cls.draw_string = drawing(cls.draw_string)
    cls.draw_wrapped_string = drawing(cls.draw_wrapped_string)
    main._sanzuriverr_translation_editor = editor
    return True
