"""Getting back to the documents you already wrote, from the document you have open.

Navigation and presentation only. Nothing here writes, invents a command or computes an
amount: every figure shown is copied from the summary its own query command returned.

**The order the arrows walk** is the order the list page shows, because it is the only
order these query commands offer: accounting date, then the document's own stable id,
which for documents dated the same day is the order they were entered. ``ORDER`` says
exactly that on the page, so a user can predict where an arrow lands before pressing it.

**Voided and closed documents stay in the sequence.** A voided invoice is a document you
still need to reach — often the one you are looking for. The sales list shows voided
documents by default, so arrows that skipped them would step over rows the user can see;
and a document left out of its own sequence has no arrows at all while you are standing
on it. Estimates are queried with ``active=None`` for the same reason: their query
command otherwise defaults to active-only and a closed estimate would be outside its own
sequence.

**What this costs.** ``form_bar`` opens no read at all — it is links. ``strip`` costs one
bounded query, and a second only for a company holding more than ``PAGE`` documents of
that type. ``recent`` is fetched by the browser after the form has rendered, so no form
render waits on it.

**The limit, stated rather than hidden.** ``<noun> query`` pages oldest-first from an
offset and offers no descending order and no "the document before this one" anchor, so
neither the document before a given one nor the newest few can be found in bounded work
once a company holds more than one page of them. Past that size the affected control is
disabled and says so instead of guessing. One command change closes all of it: a
descending direction on the sales and work query inputs.
"""
from urllib.parse import quote

from bookflow.core.errors import BookflowError

NOUNS = ('invoice', 'sales-receipt', 'estimate')

# The largest page every one of these query commands accepts.
PAGE = 200
RECENT = 5

LABELS = {'invoice': ('invoice', 'Invoices'),
          'sales-receipt': ('sales receipt', 'Sales receipts'),
          'estimate': ('estimate', 'Estimates')}

ORDER = ('Ordered by document date, then by the order they were entered — '
         'the same order as the list.')

UNPLACEABLE = 'This one could not be placed in the list; open the list to find it.'
TOO_MANY = ('There are more {plural} than one page holds, so the {singular} before this '
            'one cannot be found from here. Use the list and its filters.')


def _article(word):
    return ('an ' if word[0] in 'aeiou' else 'a ') + word


def _base(company_id, noun):
    return '/c/' + quote(str(company_id), safe='') + '/' + noun


def _shell(company_id, noun):
    singular, plural = LABELS[noun]
    base = _base(company_id, noun)
    return {'noun': noun, 'singular': singular, 'plural': plural, 'base': base,
            'list_url': base, 'find_label': 'Find ' + _article(singular), 'order': ORDER,
            'recent_url': None, 'record_url': None, 'previous': None, 'next': None,
            'at_start': False, 'at_end': False, 'position': None, 'total': None,
            'unavailable': None, 'steps': False}


def _raw(noun, raw):
    """Every document of the type, voided and closed ones included."""
    return dict(raw, active=None) if noun == 'estimate' else dict(raw)


def _step(base, row):
    """One arrow's destination, labelled with what the user will recognise it by."""
    total = row.get('total') or {}
    status = row.get('status')
    parts = [part for part in (row.get('number'), row.get('date'), row.get('customer_name'),
                               total.get('amount')) if part]
    if status in ('voided', 'cancelled'):
        parts.append(status)
    elif row.get('active') is False:
        parts.append('closed')
    return {'url': base + '/' + quote(str(row['id']), safe=''),
            'number': row.get('number'), 'label': ' · '.join(str(part) for part in parts)}


def _sequence(read, company_id, noun, record):
    """The stretch of the sequence this document sits in, and whether its ends are real.

    One query answers everything for a company whose documents of this type fit in a
    page. Past that, the query is anchored on the document's own date, which reaches
    everything after it but nothing before the day it falls on.
    """
    whole = read(noun + ' query', _raw(noun, {'limit': PAGE}), company_id)
    if not whole.get('has_more'):
        return whole['items'], True, True
    date = record.get('date') or (record.get('revision') or {}).get('date')
    if not date:
        return [], False, False
    forward = read(noun + ' query', _raw(noun, {'limit': PAGE, 'date_from': date}), company_id)
    return forward['items'], False, not forward.get('has_more')


def form_bar(company_id, noun, verb, record_id):
    """Toolbar for a document form. Pure: it opens no read.

    A form carries no arrows on purpose. Stepping off a half-entered correction would
    throw away what was typed, and a document being created is not in the sequence yet.
    """
    if not company_id or noun not in NOUNS:
        return None
    view = _shell(company_id, noun)
    if verb in ('post', 'create'):
        view['recent_url'] = '/c/' + quote(str(company_id), safe='') + '/_recent/' + noun
    elif record_id:
        view['record_url'] = view['base'] + '/' + quote(str(record_id), safe='')
    return view


def strip(read, company_id, noun, record):
    """Toolbar for a saved document, with the documents either side of it.

    A read this cannot perform costs the arrows, never the page: a member who may not
    read the list still gets the document and its link to one.
    """
    if not company_id or noun not in NOUNS or not isinstance(record, dict):
        return None
    view = _shell(company_id, noun)
    document_id = record.get('id')
    if not document_id:
        return view
    view['steps'] = True
    try:
        items, from_start, to_end = _sequence(read, company_id, noun, record)
    except BookflowError:
        view['steps'] = False
        return view
    index = next((position for position, row in enumerate(items)
                  if row.get('id') == document_id), None)
    if index is None:
        view['unavailable'] = UNPLACEABLE
        return view
    if index > 0:
        view['previous'] = _step(view['base'], items[index - 1])
    else:
        view['at_start'] = from_start
    if index + 1 < len(items):
        view['next'] = _step(view['base'], items[index + 1])
    else:
        view['at_end'] = to_end
    if from_start:
        view['position'], view['total'] = index + 1, len(items)
    elif view['previous'] is None:
        # Only the step that genuinely cannot be answered says so.
        view['unavailable'] = TOO_MANY.format(plural=view['plural'].lower(),
                                              singular=view['singular'])
    return view


def recent(read, company_id, noun, limit=RECENT):
    """The last few documents of this type, for the page where a new one is written."""
    if not company_id or noun not in NOUNS:
        return None
    view = _shell(company_id, noun)
    try:
        out = read(noun + ' query', _raw(noun, {'limit': PAGE}), company_id)
    except BookflowError:
        view['documents'] = []
        view['unavailable'] = 'The list of ' + view['plural'].lower() + ' is not available here.'
        return view
    if out.get('has_more'):
        # Oldest-first paging cannot reach the newest without walking every page.
        view['documents'] = []
        view['unavailable'] = ('There are more ' + view['plural'].lower()
                               + ' than one page holds; open the list to search them.')
        return view
    view['documents'] = [_step(view['base'], row) for row in reversed(out['items'][-limit:])]
    return view
