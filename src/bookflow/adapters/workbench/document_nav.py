"""Getting back to the documents you already wrote, from the document you have open.

Navigation and presentation only. Nothing here writes, invents a command or computes an
amount: every figure shown is copied from the summary its own query command returned.

**The order the arrows walk** is the order the list page shows: accounting date, then
the document's own stable id, which for documents dated the same day is the order they
were entered. ``ORDER`` says exactly that on the page, so a user can predict where an
arrow lands before pressing it. Which end of that order the list opens on is the list's
own choice and does not change what "previous" and "next" mean here.

**Voided and closed documents stay in the sequence.** A voided invoice is a document you
still need to reach — often the one you are looking for. The sales list shows voided
documents by default, so arrows that skipped them would step over rows the user can see;
and a document left out of its own sequence has no arrows at all while you are standing
on it. Estimates are queried with ``active=None`` for the same reason: their query
command otherwise defaults to active-only and a closed estimate would be outside its own
sequence.

**What this costs.** ``form_bar`` opens no read at all — it is links. ``strip`` costs one
bounded query for a company whose documents of this type fit in a page, and three for one
past that size: the page-sized probe that establishes it, then one window either side.
``recent`` costs one query of ``RECENT`` rows and is fetched by the browser after the
form has rendered, so no form render waits on it.

**How both ends are reached at any size.** ``<noun> query`` takes a ``direction``, and
descending is the exact reverse of the order above — the document before a given one is
the document after it in the descending page. So past one page ``strip`` anchors two
bounded windows on the document's own date: descending from it for the document before,
ascending from it for the document after. ``recent`` asks for the newest ``RECENT``
directly. Neither control is disabled by company size any more. What is still not
answered past one page is the document's position in the whole list, which would cost an
unbounded count, so no position is shown rather than a wrong one.
"""
from urllib.parse import quote

from bookflow.core.errors import BookflowError

NOUNS = ('invoice', 'sales-receipt', 'estimate', 'bill')

# The largest page every one of these query commands accepts.
PAGE = 200
RECENT = 5

LABELS = {'invoice': ('invoice', 'Invoices'),
          'sales-receipt': ('sales receipt', 'Sales receipts'),
          'estimate': ('estimate', 'Estimates'),
          'bill': ('bill', 'Bills')}

ORDER = ('Ordered by document date, then by the order they were entered — '
         'the same order as the list.')

UNPLACEABLE = 'This one could not be placed in the list; open the list to find it.'


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
    # Whose document it is, named for whichever side of the books it sits on: a sale carries a
    # customer and a bill carries a vendor, and one arrow label reads both.
    parts = [part for part in (row.get('number'), row.get('date'),
                               row.get('customer_name') or row.get('vendor_name'),
                               total.get('amount')) if part]
    if status in ('voided', 'cancelled'):
        parts.append(status)
    elif row.get('active') is False:
        parts.append('closed')
    return {'url': base + '/' + quote(str(row['id']), safe=''),
            'number': row.get('number'), 'label': ' · '.join(str(part) for part in parts)}


def _window(read, company_id, noun, raw):
    out = read(noun + ' query', _raw(noun, raw), company_id)
    return out['items'], bool(out.get('has_more'))


def _place(items, document_id):
    return next((position for position, row in enumerate(items)
                 if row.get('id') == document_id), None)


def _neighbours(read, company_id, noun, record):
    """The documents either side of this one, and whether its ends are the list's ends.

    One query answers everything for a company whose documents of this type fit in a
    page, and it can also say where in the whole list this document sits. Past that,
    two windows anchored on the document's own date answer both sides in bounded work:
    descending walks back from it, ascending walks forward, and each is the exact
    reverse of the other. ``None`` means this document could not be placed at all.
    """
    document_id = record['id']
    items, more = _window(read, company_id, noun, {'limit': PAGE})
    if not more:
        here = _place(items, document_id)
        if here is None:
            return None
        return {'previous': items[here - 1] if here else None,
                'next': items[here + 1] if here + 1 < len(items) else None,
                'at_start': here == 0, 'at_end': here + 1 == len(items),
                'position': here + 1, 'total': len(items)}
    date = record.get('date') or (record.get('revision') or {}).get('date')
    if not date:
        return None
    back, back_more = _window(read, company_id, noun,
                              {'limit': PAGE, 'date_to': date, 'direction': 'desc'})
    ahead, ahead_more = _window(read, company_id, noun, {'limit': PAGE, 'date_from': date})
    here, there = _place(back, document_id), _place(ahead, document_id)
    if here is None or there is None:
        return None
    return {'previous': back[here + 1] if here + 1 < len(back) else None,
            'next': ahead[there + 1] if there + 1 < len(ahead) else None,
            'at_start': here + 1 == len(back) and not back_more,
            'at_end': there + 1 == len(ahead) and not ahead_more,
            'position': None, 'total': None}


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
        found = _neighbours(read, company_id, noun, record)
    except BookflowError:
        view['steps'] = False
        return view
    if found is None:
        view['unavailable'] = UNPLACEABLE
        return view
    view.update({key: found[key] for key in ('at_start', 'at_end', 'position', 'total')})
    for step in ('previous', 'next'):
        if found[step] is not None:
            view[step] = _step(view['base'], found[step])
    return view


def recent(read, company_id, noun, limit=RECENT):
    """The last few documents of this type, for the page where a new one is written."""
    if not company_id or noun not in NOUNS:
        return None
    view = _shell(company_id, noun)
    try:
        # The newest few, asked for directly: one page of `limit` at any company size.
        items, _ = _window(read, company_id, noun, {'limit': limit, 'direction': 'desc'})
    except BookflowError:
        view['documents'] = []
        view['unavailable'] = 'The list of ' + view['plural'].lower() + ' is not available here.'
        return view
    view['documents'] = [_step(view['base'], row) for row in items]
    return view
