"""Calendar affordances from declared input types and existing date validators."""

from typing import get_args

from pydantic import TypeAdapter


def is_date(annotation, *, field=None, model=None, name=None):
    from bookflow.company.ledger_reports import iso_date
    from bookflow.company.journal_models import _iso_date as journal_date
    from bookflow.company.inventory_models import _iso_date as inventory_date
    from bookflow.company.rate_models import iso_date as rate_date
    validators = (iso_date, journal_date, inventory_date, rate_date)

    def validated(value):
        return (getattr(value, 'func', None) in validators
                or any(validated(child) for child in get_args(value)))

    typed = field.rebuild_annotation() if field is not None else annotation
    if validated(typed):
        return True
    if field is not None and isinstance(field.json_schema_extra, dict) and field.json_schema_extra.get('format') == 'date':
        return True
    if model is not None:
        for validator in model.__pydantic_decorators__.field_validators.values():
            if name in validator.info.fields and validator.func in validators:
                return True
    schema = TypeAdapter(typed).json_schema()

    def declared(node):
        return (node.get('format') == 'date'
                or node.get('pattern') == r'^[0-9]{4}-[0-9]{2}-[0-9]{2}$'
                or any(declared(child) for child in node.get('anyOf', [])))

    return declared(schema)
