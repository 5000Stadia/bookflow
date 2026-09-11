"""One home for how a noun becomes a URL segment, and how a segment becomes that noun again.

A command noun is written the way a bookkeeper says it -- ``bill payment``, ``sales-tax
payment``, ``hub audit`` -- and a space is not a legal character in an ``href``. A browser
papers over a raw space by encoding it on navigation, so the page still opens; a strict
client refuses the link outright and nothing that is not a browser can follow it.

So every noun-bearing link is built through :func:`segment`, which spells a multi-word noun
the way this codebase already spells a multi-word document kind -- with a hyphen -- and every
noun-bearing route resolves its path through :func:`noun`, which accepts both that spelling
and the literal noun, so a link saved before the spelling existed still opens the same page.

``SEGMENTS`` is derived from ``registry.NOUN_MODULES``: the index every command module is
loaded by, which ``tests/test_registry.py`` holds to what the modules actually register. A
noun declared tomorrow is spelled and routed the day it is declared, and no list of nouns is
written out a second time here to go stale. The generated ``<noun> query`` nouns are absent
from that index and want no entry: no page addresses one as a segment, because a list's query
is reached as its own noun's ``query`` verb.
"""

from bookflow.core import registry


def segment(noun: str) -> str:
    """The path segment a noun is written as inside a URL."""
    return noun.replace(" ", "-")


# Only the multi-word nouns need an entry: every other noun is already its own segment, and a
# segment with no entry is handed back untouched so an unknown one still reaches the route's
# own error rather than being rewritten into a different noun.
SEGMENTS: dict[str, str] = {
    segment(declared): declared
    for nouns in registry.NOUN_MODULES.values()
    for declared in nouns
    if " " in declared
}


def noun(path_segment: str) -> str:
    """The noun a path segment names. Both the hyphenated spelling and the literal noun resolve."""
    return SEGMENTS.get(path_segment, path_segment)


def base(company_id: str | None, page_noun: str) -> str:
    """Where a noun's own page lives, under its company or under the hub."""
    return f"/c/{company_id}/{segment(page_noun)}" if company_id else f"/hub/{segment(page_noun)}"
