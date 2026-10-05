"""Packaged, source-audited spell/equipment/unit glossary for suggestions."""
import json
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=1)
def load_proper_names():
    path = Path(__file__).resolve().parent.parent / 'proper_names.json'
    with path.open(encoding='utf-8') as stream:
        data = json.load(stream)
    if data.get('schema') != 1 or not isinstance(data.get('entries'), list):
        raise ValueError('고유명사 용어표 형식 오류: ' + str(path))
    result = {}
    for row in data['entries']:
        en, ko, kinds = row.get('en'), row.get('ko'), row.get('kinds')
        if (not isinstance(en, str) or not en or not isinstance(ko, str) or not ko
                or not isinstance(kinds, list) or not kinds
                or not set(kinds) <= {'spell', 'equipment', 'unit', 'status', 'attribute', 'effect'} or en in result):
            raise ValueError('고유명사 용어표 항목 오류: ' + repr(en))
        result[en] = row
    return result
