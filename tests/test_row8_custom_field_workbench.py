"""Journal custom-field form witnesses. Run only after the parent releases HOLD."""
import copy
from types import SimpleNamespace

import pytest

from bookflow.adapters.workbench import forms as F
from bookflow.adapters.workbench.register import edit_projection
from bookflow.company.journal_models import JournalPostInput
from tests.test_row3_host import hosted  # noqa: F401
from tests.test_row5_workbench_forms import _browser, WB
from tests.test_row8_register_workbench import _config, register_records  # noqa: F401


def definition(identifier, kind="text", **extra):
    return dict(id=identifier, kind=kind, name=f"Field {identifier}", choices=[], **extra)


@pytest.mark.parametrize("kind,value,expected", [
    ("text", "", ""), ("text", "unset", "unset"), ("number", "0", "0"),
    ("bool", "false", False), ("bool", "true", True), ("bool", "invalid", "invalid"),
    ("date", "2026-02-30", "2026-02-30"),
])
def test_explicit_patch_types_survive_repeated_render(kind, value, expected):
    cmd = SimpleNamespace(input_model=JournalPostInput)
    attempt = {"cf:field": value, "cf-kind:field": kind, "cf-state:field": "set"}
    for definitions in ([definition("field", kind)], []):
        fields = F.custom_field_descriptors(definitions, {}, attempt, update=True)
        assert len(fields) == 1
        assert fields[0]["value"] == value
        assert fields[0]["wire_kind"] == kind
        assert fields[0]["state"] == "set"
        raw, _, _ = F.translate(cmd, attempt, {})
        assert raw["custom_fields"]["field"] == expected
        assert type(raw["custom_fields"]["field"]) is type(expected)
    attempt["cf-state:field"] = "clear"
    assert F.translate(cmd, attempt, {})[0]["custom_fields"] == {"field": None}
    attempt["cf-state:field"] = "keep"
    assert "custom_fields" not in F.translate(cmd, attempt, {})[0]


def test_inventory_defaults_and_normalized_choice_originals():
    definitions = [definition(str(i), default="new") for i in range(48)]
    fields = F.custom_field_descriptors(definitions, {}, {}, update=True)
    assert len(fields) == 48
    assert all(f["value"] is None and f["default"] is None for f in fields)
    choice = definition("choice", "choice")
    choice["choices"] = [{"value": "STRASSE", "active": True}]
    field = F.custom_field_descriptors([choice], {"choice": "Straße"}, {}, update=True)[0]
    assert field["value"] == "Straße" and field["selected"] == "STRASSE"
    assert field["state"] == "keep"
    assert not any(f["path"].startswith("custom_fields.root") for f in F.leaves(JournalPostInput))


def _create(hosted, kind="text", **extra):
    return hosted.ok("custom-field.create", {"name": f"UI {kind}", "kind": kind,
                     "scopes": ["journal_entry"], **extra}, company=hosted.company_id)


def test_generated_scope_defaults_and_disappearing_attempt(hosted):
    field = _create(hosted, default="new default")
    browser = _browser(hosted)
    for noun in ("journal", "register"):
        route = f"/c/{hosted.company_id}/{noun}/post"
        page = browser.get(route)
        assert page.status_code == 200, page.text
        assert f'name="cf:{field["id"]}"' in page.text
        assert 'name="f:custom_fields.root"' not in page.text
    hosted.ok("custom-field.deactivate", {"custom_field": field["id"], "expected_version": field["version"]}, company=hosted.company_id)
    for noun in ("journal", "register"):
        response = browser.post(f"/c/{hosted.company_id}/{noun}/post", headers=WB, data={
            "originals": "{}", f"cf:{field['id']}": "<attempt & exact>",
            f"cf-kind:{field['id']}": "text", f"cf-state:{field['id']}": "set",
            f"cf-label:{field['id']}": "<old label>", "action": "preview",
        })
        assert response.status_code == 200
        assert "Unavailable field attempt" in response.text
        assert 'value="&lt;attempt &amp; exact&gt;"' in response.text
        assert 'value="set" selected' in response.text
        assert '<old label>' not in response.text


def test_retired_choice_remains_selected_and_exact(hosted):
    field = _create(hosted, "choice", choices=[{"value": "Old"}, {"value": "New"}])
    hosted.ok("custom-field.update", {"custom_field": field["id"], "expected_version": field["version"],
        "choices": [{"id": c["id"], "value": c["value"], "active": c["value"] == "New"} for c in field["choices"]]}, company=hosted.company_id)
    response = _browser(hosted).post(f"/c/{hosted.company_id}/journal/post", headers=WB, data={
        "originals": "{}", f"cf:{field['id']}": "Old", f"cf-kind:{field['id']}": "choice",
        f"cf-state:{field['id']}": "set", "action": "preview",
    })
    assert '<option value="Old" selected>Old — unavailable choice</option>' in response.text


def test_register_snapshot_preservation_and_historical_labels(hosted, register_records):
    bank, expense, first = register_records
    field = _create(hosted, "bool")
    projection = edit_projection(first, bank, lambda row: row["name"])
    corrected = hosted.ok("register.update", {**projection["payload"], "custom_fields": {field["id"]: False}}, company=hosted.company_id)
    snapshot = copy.deepcopy(corrected["revision"]["custom_fields_snapshot"])
    hosted.ok("custom-field.update", {"custom_field": field["id"], "expected_version": field["version"], "name": "Today's label"}, company=hosted.company_id)
    projection = edit_projection(corrected, bank, lambda row: row["name"])
    assert projection is not None and "custom_fields" not in projection["payload"]
    assert projection["custom_fields"][0]["value"] is False
    saved = hosted.ok("register.update", projection["payload"], company=hosted.company_id)
    assert saved["changed"] is False
    assert saved["revision"]["custom_fields_snapshot"] == snapshot
    browser = _browser(hosted)
    detail = browser.get(f"/c/{hosted.company_id}/journal/{saved['id']}")
    assert '<dt>UI bool · bool</dt><dd>false</dd>' in detail.text
    config = _config(browser.get(f"/c/{hosted.company_id}/account/{bank['id']}/register?edit={saved['id']}"))
    assert config["edit"]["custom_fields"][0]["value"] is False
    updated_form = browser.get(f"/c/{hosted.company_id}/register/{saved['id']}/update")
    assert updated_form.status_code == 200
    assert f'name="cf:{field["id"]}"' in updated_form.text


def test_generated_register_empty_optional_allocations_stay_absent():
    from bookflow.company.register_models import RegisterPostInput, RegisterUpdateInput
    for model in (RegisterPostInput, RegisterUpdateInput):
        raw, _, _ = F.translate(SimpleNamespace(input_model=model), {
            'f:category': 'category-id', 'collection:allocations': '1',
            'cf:text': '', 'cf-kind:text': 'text', 'cf-state:text': 'set',
        }, {})
        assert raw['category'] == 'category-id'
        assert 'allocations' not in raw
        assert raw['custom_fields'] == {'text': ''}


def test_generated_register_update_retains_complete_entry_but_patches_custom_fields():
    from bookflow.company.register_models import RegisterUpdateInput
    original = {'category': 'category-id', 'category_line_id': 'line-id', 'memo': '', 'custom_fields': {'field': False}}
    raw, _, _ = F.translate(SimpleNamespace(name='register update', input_model=RegisterUpdateInput), {
        'f:category': 'category-id', 'f:category_line_id': 'line-id', 'f:memo': '',
        'collection:allocations': '1', 'cf:field': 'false', 'cf-kind:field': 'bool', 'cf-state:field': 'keep',
    }, original)
    assert raw == {'category': 'category-id', 'category_line_id': 'line-id', 'memo': ''}
