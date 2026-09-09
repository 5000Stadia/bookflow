"""Help describes schema requirements without treating alternatives as cumulative."""
from typing import Annotated, Literal

import pytest
from pydantic import BaseModel, Field, ValidationError
from typer.testing import CliRunner

from bookflow.adapters.cli.app import _flatten, build_app
from bookflow.company.deposit_models import PostInput


def leaves(model):
    return {leaf[0]: leaf for leaf in _flatten(model)}


def test_deposit_inline_draft_and_optional_cash_back_requirements():
    fields = leaves(PostInput)
    assert fields['operation_key'][4]
    assert fields['document.mode'][4]
    for path, mode in [('document.deposit_to', 'inline'), ('document.date', 'inline'),
                       ('document.draft', 'draft'), ('document.expected_version', 'draft')]:
        assert not fields[path][4]
        assert f'Required when document.mode is {mode}.' in fields[path][3]
    for path in ('document.cash_back.account', 'document.cash_back.amount'):
        assert not fields[path][4]
        assert 'Required when document.cash_back is supplied.' in fields[path][3]
    PostInput.model_validate({'operation_key': 'probe', 'document': {'mode': 'inline', 'deposit_to': 'Bank', 'date': '2026-06-01'}})
    PostInput.model_validate({'operation_key': 'probe', 'document': {'mode': 'draft', 'draft': '01M00000000000000000000000', 'expected_version': 1}})
    with pytest.raises(ValidationError) as missing_tag:
        PostInput.model_validate({'operation_key': 'probe', 'document': {'deposit_to': 'Bank', 'date': '2026-06-01'}})
    assert missing_tag.value.errors()[0]['type'] == 'union_tag_not_found'
    with pytest.raises(ValidationError):
        PostInput.model_validate({'operation_key': 'probe', 'document': {'mode': 'inline', 'deposit_to': 'Bank', 'date': '2026-06-01', 'cash_back': {}}})


def test_shared_required_leaf_and_optional_parent_are_distinguished():
    class A(BaseModel):
        mode: Literal['a'] = 'a'
        shared: str
        only_a: str
        mixed: str
    class B(BaseModel):
        mode: Literal['b']
        shared: str
        mixed: str = 'branch-b default'
    class Required(BaseModel):
        choice: Annotated[A | B, Field(discriminator='mode')]
    class Optional(BaseModel):
        nested: A | None = None
        choice: Annotated[A | B | None, Field(discriminator='mode')] = None
    required = leaves(Required)
    assert required['choice.shared'][4] and required['choice.mode'][4]
    assert not required['choice.only_a'][4]
    assert required['choice.mixed'][5] is None
    assert 'Required when choice.mode is a.' in required['choice.mixed'][3]
    optional = leaves(Optional)
    assert not optional['nested.shared'][4]
    assert 'Required when nested is supplied.' in optional['nested.shared'][3]
    # The schema accepts omission of the parent; help must not demand its children.
    Optional.model_validate({})
    assert all(not row[4] for row in optional.values())
    assert 'Required when choice is supplied.' in optional['choice.mode'][3]


def test_real_deposit_cli_help_exposes_conditional_guidance():
    result = CliRunner().invoke(build_app('deposit post'), ['deposit', 'post', '--help'], terminal_width=240, color=False)
    assert result.exit_code == 0, result.output
    text = ' '.join(result.output.split())
    assert 'Required when document.mode is draft.' in text
    assert 'Required when document.cash_back is supplied.' in text
    assert '--document-mode' in text and '--document-draft' in text
