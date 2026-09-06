"""Operator transport timing; never a business collection or authorization limit."""

import os

from bookflow.core.errors import BookflowError


def json_seconds():
    raw = os.environ.get('BOOKFLOW_MCP_JSON_SECONDS', '300')
    try:
        if not raw.isascii() or not raw.isdecimal():
            raise ValueError
        value = int(raw)
        if not 30 <= value <= 86400:
            raise ValueError
        return value
    except (ValueError, TypeError):
        raise BookflowError('E_CONFIG_INVALID', message='BOOKFLOW_MCP_JSON_SECONDS must be an integer from30 through86400 seconds.',
                            details={'key': 'BOOKFLOW_MCP_JSON_SECONDS'}) from None
