"""SanzuRiverr catalog reader and adapter for the game's Localisation API.
"""
import csv
import re
from pathlib import Path
from .config import LANG_CODE, LANG_NAME, TSV_PATH
from .composition import composed_description

_ESCAPES = {'n': '\n', 'r': '\r', 't': '\t', '\\': '\\'}
_ESCAPED_CHARACTER = re.compile(r'\\(.)', re.DOTALL)


def decode_field(value):
    return _ESCAPED_CHARACTER.sub(lambda match: _ESCAPES.get(match[1], match[1]), value)


def read_catalog(path=TSV_PATH):
    """Read escaped TSV without interpreting quotation marks in sentences.

    Four-column files carry two metadata columns; historical two/three-column
    rows are accepted. Invalid rows and duplicates identify their source line.
    """
    entries = {}
    first_lines = {}
    with Path(path).open('r', encoding='utf-8', newline='') as stream:
        rows = csv.reader(stream, delimiter='\t', quoting=csv.QUOTE_NONE)
        for row in rows:
            if not row or row == ['source_file', 'group', 'en', 'ko']:
                continue
            if len(row) not in (2, 3, 4):
                raise ValueError(f'{path}:{rows.line_num}: expected 2–4 TSV columns, got {len(row)}')
            key, value = map(decode_field, row[-2:])
            if key in entries:
                raise ValueError(f'{path}:{rows.line_num}: duplicate key {key!r}; first at line {first_lines[key]}')
            entries[key] = value
            first_lines[key] = rows.line_num
    if not entries:
        raise ValueError(f'{path}: translation catalog is empty')
    return entries


class CatalogProvider:
    """Serve Korean in memory and delegate other languages and cache clears."""
    def __init__(self, catalog, upstream):
        self.catalog = catalog
        self.upstream = upstream

    def __call__(self, language):
        return self.catalog if language == LANG_CODE else self.upstream(language)

    def cache_clear(self):
        clear = getattr(self.upstream, 'cache_clear', None)
        if clear is not None:
            clear()


def composed_label(message, catalog):
    """Resolve names assembled by CommonContent and the custom-run menu."""
    if '\n' in message:
        return composed_description(message, catalog)
    touched = re.fullmatch(r'Touched by (.+)', message)
    if touched and touched[1] in ('Physical', 'Fire', 'Ice', 'Lightning', 'Dark', 'Holy', 'Arcane', 'Poison') and catalog.get(touched[1]):
        return catalog[touched[1]] + '의 가호'
    bonus = re.fullmatch(r'([A-Za-z]+) (.+) Bonus', message)
    if bonus and catalog.get(bonus[1]) and catalog.get(bonus[2]):
        return catalog[bonus[1]] + ' ' + catalog[bonus[2]] + ' 증가'
    effect = re.fullmatch(r'(.+) (Weakness|Frenzy)', message)
    if effect and catalog.get(effect[1]):
        return catalog[effect[1]] + (' 취약' if effect[2] == 'Weakness' else ' 광란')
    skeletal = re.fullmatch(r'Skeletal (.+)', message)
    if skeletal and catalog.get(skeletal[1]):
        return '해골 ' + catalog[skeletal[1]]
    # Spells.Slimy prepends this modifier, including to propagated summons.
    slimy = re.fullmatch(r'Slimy (.+)', message)
    if slimy:
        base = catalog.get(slimy[1]) or composed_label(slimy[1], catalog)
        if base:
            return '끈적이는 ' + base
    repeat = re.fullmatch(r'(.+) Repeater', message)
    if repeat and catalog.get(repeat[1]):
        return catalog[repeat[1]] + ' 반복 시전'
    # Summary statistics and numbered names retain their numeric text exactly.
    # Only translate a remainder that is already known; never guess new names.
    for pattern, leading in ((r'(\s*\d[\d,]*\s+)(.+)', True),
                             (r'(.+?)(\s+\d[\d,]*\s*)', False)):
        numbered = re.fullmatch(pattern, message)
        if numbered:
            name = numbered[2] if leading else numbered[1]
            translated = catalog.get(name) or composed_label(name, catalog)
            if translated:
                return numbered[1] + translated if leading else translated + numbered[2]
    duration = re.fullmatch(r'(.+) \((\d+)\)', message)
    if duration:
        base = catalog.get(duration[1]) or composed_label(duration[1], catalog)
        if base:
            return base + ' (' + duration[2] + ')'
    if message.startswith('Ruinous ') and catalog.get(message[8:]):
        return '파멸의 ' + catalog[message[8:]]
    turns = re.fullmatch(r'(\d+) \(([LG])\)', message)
    if turns:
        template = catalog.get('{n} (' + turns[2] + ')')
        if template:
            return template.format(n=turns[1])
    stacks = re.fullmatch(r'(.+) x(\d+)( \(\d+\))?', message)
    if stacks:
        base = catalog.get(stacks[1]) or composed_label(stacks[1], catalog)
        if base is None and re.fullmatch(r'[가-힣0-9 ·]+', stacks[1]):
            base = stacks[1]
        if base:
            return base + ' ×' + stacks[2] + (stacks[3] or '')
    if ' or ' in message:
        alternatives = message.split(' or ')
        if all(catalog.get(part) for part in alternatives):
            return ' 또는 '.join(catalog[part] for part in alternatives)
    for pattern, suffix in ((r'(.+) Spawner', ' 생성기'), (r'Summon (.+)', ' 소환')):
        unit = re.fullmatch(pattern, message)
        if unit:
            name = unit[1]
            base = catalog.get(name) or composed_label(name, catalog)
            # CommonContent.SimpleSummon.calc_text appends a literal 's',
            # even for names such as Orc Wolf. Do not guess unknown units.
            if base is None and pattern == r'Summon (.+)' and name.endswith('s'):
                base = catalog.get(name[:-1]) or composed_label(name[:-1], catalog)
            if base:
                return base + suffix
    match = re.fullmatch(r'([A-Za-z]\w*)\s+((?:\([^()]*\)\s*)*)', message)
    if match and catalog.get(match[1]):
        suffix = re.sub(r'\(([^()]*)\)',
                        lambda m: '(' + catalog.get(m[1], m[1]) + ')', match[2])
        return (catalog[match[1]] + ' ' + suffix).rstrip()
    return None


_OBJECT_PARTICLE = re.compile(
    r'(\[[^\]\n]+\]|\{[^{}\n]+\})(["”’\']?)(을\(를\)|를\(을\)|을|를)(?=$|[\s,.;!?…])'
)


def format_korean(message, values):
    """Choose object particles at explicit field/markup boundaries only."""
    def particle(match):
        token, quote, original = match.groups()
        rendered = token.format(**values) if values else token
        visible = re.sub(r'\[([^\]:]+):[^\]]+\]', r'\1', rendered)
        visible = visible.strip('[] \t\"\'“”‘’')
        if visible and '\uac00' <= visible[-1] <= '\ud7a3':
            original = '을' if (ord(visible[-1]) - 0xac00) % 28 else '를'
        else:
            number = re.search(r'(?<![\w.])(-?\d[\d,]*(?:\.\d+)?)$', visible)
            if number:
                value = number[1].replace(',', '')
                # Ordinary game quantities use Sino-Korean readings. Keep
                # huge integers unchanged rather than guessing their final unit.
                if '.' in value or abs(int(value)) < 10**12:
                    original = '을' if value[-1] in '013678' else '를'
            elif re.search(r'(?:^|\s)(?:HP|SP)$', visible):
                original = '를'
        return token + quote + original
    message = _OBJECT_PARTICLE.sub(particle, message)
    return message.format(**values) if values else message


class LanguageAdapter:
    def __init__(self, localisation, catalog, observer=None):
        self.api = localisation
        self.provider = CatalogProvider(catalog, localisation.load)
        self.original_translate = localisation.T
        self.original_locales = localisation.available_locales
        self.observer = observer

    def composed_label(self, message):
        return composed_label(message, self.provider.catalog)

    def locales(self):
        return sorted({'en', LANG_CODE, *(self.original_locales() or ())})

    def translate(self, message, **values):
        if self.api.get_locale() == LANG_CODE:
            corrections = {
                'Pull the target {tiles} tile{s} toward the caster': ('s', ''),
                'Reincarnates when killed ({lives} time{suffix})': ('suffix', ''),
                'Inflict {buff} for {duration} turn{s}': ('s', ''),
            }
            correction = corrections.get(message)
            if correction:
                values[correction[0]] = correction[1]
            spawn_messages = (
                'Has a {chance}% chance each turn to spawn {num} {spawn}',
                'Each turn spawns {num} {spawn}',
            )
            if message in spawn_messages and values.get('num') == 'a':
                values['num'] = 1
            translated = self.provider.catalog.get(message)
            if translated is None:
                translated = self.composed_label(message)
            missing = translated is None or translated == ''
            result = message if missing else translated
            result = (result.format(**values) if values else result) if missing else format_korean(result, values)
            if self.observer:
                self.observer(message, result, values, missing)


            return result
        return self.original_translate(message, **values)

    def attach(self):
        self.api.LANG_DISPLAY_NAME[LANG_CODE] = LANG_NAME
        self.api.load = self.provider
        self.api.available_locales = self.locales
        self.api.T = self.translate


def register_language(catalog, observer=None):
    import Localisation
    adapter = LanguageAdapter(Localisation, catalog, observer)
    adapter.attach()
    return adapter
