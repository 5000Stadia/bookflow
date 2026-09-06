"""Text-control conversion for declared custom-field definition values.

The core still validates the selected kind, choices, defaults and every change.
"""


def decode_definition_default(command, raw, originals=None):
    if getattr(command, 'name', None) not in {'custom-field create', 'custom-field update'}:
        return
    kind = raw.get('kind', (originals or {}).get('kind'))
    value = raw.get('default')
    if kind == 'bool' and isinstance(value, str) and value in {'true', 'false'}:
        raw['default'] = value == 'true'
