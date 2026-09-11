"""A noun is spelled one way inside a URL, and both spellings still open the page.

``bill payment``, ``sales-tax payment`` and ``hub audit`` are nouns with a space in them, and
a space is not a legal character in an ``href``. These hold the one place that spells a noun
for a URL to the whole declared noun set, so a noun added later cannot quietly reintroduce a
link a strict client refuses to follow.
"""

from __future__ import annotations

import re

from bookflow.adapters.workbench import routing as Routing
from bookflow.core import registry

# What one path segment may carry without being percent-encoded: RFC 3986's unreserved and
# sub-delim characters, plus `:` and `@`. A `/` is absent because it would end the segment.
UNENCODED = re.compile(r"\A[A-Za-z0-9\-._~!$&'()*+,;=:@]*\Z")


def declared_nouns() -> set[str]:
    """Every noun any command module registers, from the index the modules are loaded by."""
    return {noun for nouns in registry.NOUN_MODULES.values() for noun in nouns}


def test_every_declared_noun_spells_a_segment_no_client_has_to_encode():
    for noun in declared_nouns():
        assert UNENCODED.match(Routing.segment(noun)), (noun, Routing.segment(noun))


def test_a_multi_word_noun_resolves_from_its_segment_and_from_its_literal_name():
    multi_word = {noun for noun in declared_nouns() if " " in noun}
    assert multi_word, "the fixture below is only meaningful while multi-word nouns exist"
    assert "bill payment" in multi_word and "hub audit" in multi_word
    for noun in multi_word:
        assert Routing.segment(noun) != noun
        # A saved link carrying the literal noun keeps working alongside the new spelling.
        assert Routing.noun(Routing.segment(noun)) == noun
        assert Routing.noun(noun) == noun


def test_a_single_word_noun_is_its_own_segment_and_survives_the_round_trip():
    for noun in declared_nouns():
        if " " in noun:
            continue
        assert Routing.segment(noun) == noun
        assert Routing.noun(noun) == noun


def test_no_two_nouns_claim_the_same_segment():
    nouns = sorted(declared_nouns())
    segments = [Routing.segment(noun) for noun in nouns]
    assert len(set(segments)) == len(set(nouns)), sorted(
        segment for segment in segments if segments.count(segment) > 1)
    # A hyphenated noun must never be shadowed by the segment of a different noun.
    assert not {segment for segment in Routing.SEGMENTS if segment in declared_nouns()}


def test_a_segment_no_noun_declares_is_handed_back_untouched():
    """An unknown segment reaches the route's own error instead of being rewritten."""
    assert Routing.noun("not-a-noun") == "not-a-noun"
    assert Routing.noun("sales-receipt") == "sales-receipt"


def test_a_noun_page_lives_under_its_company_or_under_the_hub():
    assert Routing.base("C1", "bill payment") == "/c/C1/bill-payment"
    assert Routing.base(None, "hub audit") == "/hub/hub-audit"
    assert Routing.base("C1", "invoice") == "/c/C1/invoice"


def test_the_segment_map_is_derived_from_the_declared_nouns_rather_than_retyped():
    assert Routing.SEGMENTS == {
        Routing.segment(noun): noun for noun in declared_nouns() if " " in noun}
