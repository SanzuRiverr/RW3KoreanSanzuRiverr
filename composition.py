"""Source-scoped translation of descriptions assembled from optional clauses."""
import json
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=1)
def composition_data():
    path = Path(__file__).resolve().parent.parent / 'composition_rules.json'
    with path.open(encoding='utf-8') as stream:
        data = json.load(stream)
    if data.get('schema') != 1:
        raise ValueError('Unsupported composition rules schema')
    return data


def description_rules():
    return composition_data()['families']


def composed_description(message, catalog):
    if '\n' not in message:
        return None
    lines = message.split('\n')
    for family in description_rules():
        if lines[0] not in family['starts']:
            continue
        clauses = family['clauses']
        if all(line in clauses for line in lines):
            return '\n'.join(catalog.get(line) or clauses[line] for line in lines)
    # Unknown clauses must remain visible to the missing-string collector.
    return None
