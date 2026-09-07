"""Complete comparison first; authenticated logical-key projection second."""
import hashlib
from bookflow.company import deposit_dependency_history as history
from bookflow.company.deposit_dependency_models import PageInput, ChangePage
from bookflow.core.errors import BookflowError

DOMAIN = b'deposit-dependency-page-v1\0'


def _key(item):
    return [item.event_id, item.kind, item.record_id, list(item.fields)]


def changes_page(s, guard, original_request, page, binding):
    page = PageInput.model_validate_json(page.model_dump_json(), strict=True)
    comparison = history.compare(s, guard, original_request, binding)
    recipe = {'v': 1, 'guard': hashlib.sha256(guard.encode()).hexdigest(),
              'current': comparison.current.digest, 'endpoint': comparison.current.endpoint,
              'limit': page.limit}
    start = 0
    if page.cursor is not None:
        prior = history._decode(s, page.cursor, binding, domain=DOMAIN)
        if not isinstance(prior, dict) or set(prior) != set(recipe) | {'last'}:
            raise history.invalid_guard()
        if {key: prior[key] for key in recipe} != recipe:
            raise BookflowError('E_PREVIEW_STALE', details={'reason': 'deposit_dependency_page'})
        matches = [index for index, item in enumerate(comparison.changes) if _key(item) == prior['last']]
        if len(matches) != 1:
            raise history.invalid_guard()
        start = matches[0] + 1
    selected = comparison.changes[start:start + page.limit]
    cursor = history._encode(s, {**recipe, 'last': _key(selected[-1])}, binding, domain=DOMAIN) if selected and start + len(selected) < len(comparison.changes) else None
    return ChangePage(items=selected, total_count=len(comparison.changes), next_cursor=cursor,
        unknown_history=comparison.unknown_history, unknown_records=comparison.unknown_records)
