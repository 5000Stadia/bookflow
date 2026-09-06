import pytest

from bookflow.adapters.mcp.limits import json_seconds
from bookflow.core.errors import BookflowError


@pytest.mark.parametrize('raw', ['0', '29', '86401', '1.5', 'NaN', '-1', 'secret-value', '٣٠٠'])
def test_operator_json_deadline_is_finite_and_never_echoes_configuration(raw, monkeypatch):
    monkeypatch.setenv('BOOKFLOW_MCP_JSON_SECONDS', raw)
    with pytest.raises(BookflowError) as caught:
        json_seconds()
    assert caught.value.code == 'E_CONFIG_INVALID'
    assert caught.value.details == {'key': 'BOOKFLOW_MCP_JSON_SECONDS'}
    if raw == 'secret-value':
        assert raw not in caught.value.message


def test_default_and_raised_deadline(monkeypatch):
    monkeypatch.delenv('BOOKFLOW_MCP_JSON_SECONDS', raising=False)
    assert json_seconds() == 300
    monkeypatch.setenv('BOOKFLOW_MCP_JSON_SECONDS', '3600')
    assert json_seconds() == 3600
