import json
from collections import Counter

def run(inputs):
    text = inputs.get('json_text', inputs.get('user_input', ''))
    duplicates = []
    def pairs(items):
        seen = set()
        for key, value in items:
            if key in seen: duplicates.append(key)
            seen.add(key)
        return dict(items)
    try:
        value = json.loads(text, object_pairs_hook=pairs)
    except (json.JSONDecodeError, TypeError) as exc:
        return {'valid': False, 'errors': [str(exc)], 'warnings': [], 'schema': None}
    warnings = [f'Duplicate key: {key}' for key in sorted(set(duplicates))]
    def schema(item):
        if isinstance(item, dict): return {'type': 'object', 'properties': {k: schema(v) for k, v in item.items()}, 'required': list(item)}
        if isinstance(item, list): return {'type': 'array', 'items': schema(item[0]) if item else {}}
        if isinstance(item, bool): return {'type': 'boolean'}
        if item is None: return {'type': 'null'}
        if isinstance(item, int): return {'type': 'integer'}
        if isinstance(item, float): return {'type': 'number'}
        return {'type': 'string'}
    return {'valid': True, 'errors': [], 'warnings': warnings, 'schema': schema(value) if inputs.get('generate_schema') else None}
