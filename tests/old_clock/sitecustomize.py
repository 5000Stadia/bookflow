"""Start a historical release's child process inside the world its seeds were written for.

`tests.provenance.child_env` puts this directory on the import path of a child that runs an
older copy of Bookflow (a `git archive` of a past commit), never of a child running the product
under test. Python imports ``sitecustomize`` at startup, so this runs before the child's own code.

A past release's demo seed carries absolute dates (estimate REF-WORK-EST-1A expires 2026-10-09),
and its code judges "expired" against ``bookflow.core.clock.now`` -- the wall clock. Today's code
judges a seed as of the day it writes it (``clock.as_of_day``), but frozen code cannot change, so
on a later real day its own `demo reset` refuses its own seed. Here its clock reads the real
time shifted back by one fixed offset: the parent test session's start
(``BOOKFLOW_TEST_OLD_CLOCK_ANCHOR``, set by ``child_env``) maps onto ``PINNED_START``. So the same
commit builds the same books on any real day, and time still runs forward in order across every
past-release child of the session (a heartbeat written by a later child is never earlier). Every
pinned commit in tests/ (2026-09-05 .. 2026-09-15) has ``clock.now``.
"""

from datetime import datetime, timezone

#: After every pinned commit's date and before the earliest seed expiry it must still accept.
PINNED_START = datetime(2026, 9, 16, 12, 0, tzinfo=timezone.utc)


def _pin() -> None:
    import os
    anchor = os.environ.get("BOOKFLOW_TEST_OLD_CLOCK_ANCHOR")
    if anchor is None:
        return
    try:
        from bookflow.core import clock
    except ImportError:  # a child that is not Bookflow at all
        return
    offset = datetime.fromtimestamp(float(anchor), timezone.utc) - PINNED_START
    real_now = clock.now
    clock.now = lambda: real_now() - offset


_pin()
