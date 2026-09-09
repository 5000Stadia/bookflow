"""Paging a list forwards and back, out of the page's own address.

Presentation only: this mints no cursor, decodes none, and knows nothing of what one
holds. It arranges the ones the query command already handed out.

**Why the page has to remember.** A query command answers "the page after this one" and
nothing else — its continuation is forward-only, and there is no cursor for the page
before this one. So the page carries where it has been: the cursor that opened each page
it passed through, in ``trail``, and its own place in that walk, in ``page``. Stepping
back re-opens the last cursor the trail kept; stepping back from page two opens the list
with no cursor, which is page one.

**The trail is bounded, because a URL is.** Past ``DEPTH`` entries the oldest is dropped
rather than growing an address until the server refuses to accept it. From there the
walk back is honest about what it lost: ``previous`` stops being offered once the trail
has run out before page two, and ``first`` — which needs nothing but the filters — is
offered on every page after the first, so a bookkeeper is never stranded deep in a list.

**Filters end the walk on purpose.** ``first``, ``previous`` and ``next`` carry every
other control on the page unchanged, so paging never silently drops a filter; changing a
filter submits the list's own form, which carries no trail, so a new set of results
starts at its own first page rather than at somebody else's offset.
"""
from urllib.parse import urlencode

from bookflow.adapters.workbench.document_form import NOUNS as _SALES_NOUNS
from bookflow.adapters.workbench.work import NOUNS as _WORK_NOUNS

# The lists of documents a bookkeeper writes. These open on the most recently written
# one, because "show me the one I just wrote" is the question a document list is opened
# with; every other list keeps the order its own records already carry.
NEWEST_FIRST = tuple(dict.fromkeys(_SALES_NOUNS + _WORK_NOUNS))

# The address is the page's whole memory, so these two names are reserved on a list URL.
TRAIL = 'trail'
PAGE = 'page'
CARRIED = ('cursor', TRAIL, PAGE)

# Enough steps back to walk out of anywhere a person paged into by hand, and few enough
# that the address stays well inside what a server will accept.
DEPTH = 12


def _number(params):
    value = params.get(PAGE, '1')
    return int(value) if value.isdigit() and len(value) <= 6 and int(value) > 0 else 1


def controls(path, params, next_cursor):
    """The paging links for one list page, and which page of the walk it is.

    ``params`` is the request's own query parameters; every one of them that is not
    part of this walk travels unchanged into each link.
    """
    kept = [(key, value) for key, value in params.multi_items() if key not in CARRIED]
    cursor = params.get('cursor')
    trail = [value for value in params.getlist(TRAIL) if value]
    number = _number(params) if cursor else 1

    def url(cursor, trail, number):
        pairs = list(kept) + [(TRAIL, value) for value in trail]
        if cursor:
            pairs += [('cursor', cursor), (PAGE, str(number))]
        return path + ('?' + urlencode(pairs) if pairs else '')

    view = {'page': number, 'first_url': None, 'previous_url': None, 'next_url': None}
    if next_cursor:
        walked = (trail + [cursor]) if cursor else trail
        view['next_url'] = url(next_cursor, walked[-DEPTH:], number + 1)
    if cursor:
        view['first_url'] = url(None, [], 1)
        if trail:
            view['previous_url'] = url(trail[-1], trail[:-1], number - 1)
        elif number <= 2:
            # Page two came from the unpaged list, which needs no cursor at all.
            view['previous_url'] = view['first_url']
    return view
