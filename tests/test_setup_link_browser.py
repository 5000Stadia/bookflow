"""R182: the owner opens the one-time setup link in real headless Chrome, sets a password and lands in the books.

The form submits through htmx, as every workbench form does; the link is used up afterwards; at a phone's
width nothing scrolls sideways. Set BOOKFLOW_SHOTS_DIR to keep a screenshot of the page at each width.
"""
import base64
import os
from pathlib import Path
import sqlite3

import pytest

from bookflow.hub import setup_links
from tests.test_row5_browser_acceptance import CHROME, _Cdp, browser_site  # noqa: F401

pytestmark = pytest.mark.skipif(not CHROME.exists(), reason='Chrome unavailable')


def _fresh_owner(root: Path, login: str) -> str:
    """The installation's owner as `bookflow setup` leaves them: no password yet."""
    from bookflow.core.config import Config
    user_id = Config.load(root / "config.toml").user_table(login)["user_id"]
    with sqlite3.connect(root / "hub.db") as db:
        db.execute("UPDATE users SET password_hash = NULL WHERE id = ?", (user_id,))
    return user_id


@pytest.mark.parametrize('width', (1440, 390))
def test_the_owner_sets_a_password_from_the_link_and_lands_in_the_books(browser_site, tmp_path, width):
    root = Path(os.environ["BOOKFLOW_DATA_ROOT"])
    user_id = _fresh_owner(root, browser_site.login)
    secret, _ = setup_links.create(root, user_id)
    link = f"{browser_site.base_url}/setup/{secret}"
    b = _Cdp(tmp_path / "setup-chrome")
    try:
        b.viewport(width, 900)
        b.navigate(link)
        b.wait_for('!!document.querySelector(\'form[hx-post^="/setup/"]\') && !!window.htmx')
        assert browser_site.login in b.evaluate('document.body.innerText')
        assert b.evaluate('document.documentElement.scrollWidth <= window.innerWidth + 1')
        shots = os.environ.get('BOOKFLOW_SHOTS_DIR')
        if shots:
            Path(shots).mkdir(parents=True, exist_ok=True)
            data = b.call('Page.captureScreenshot', {'format': 'png'}, timeout=30)['data']
            (Path(shots) / f'setup-link-{width}.png').write_bytes(base64.b64decode(data))
        # Two different entries are refused on the page, and the link stays usable.
        b.evaluate("""(() => { document.querySelector('#password').value = 'garden-path-2026';
            document.querySelector('#again').value = 'garden-path-2027';
            document.querySelector('form[hx-post^="/setup/"]').requestSubmit(); })()""")
        b.wait_for("document.querySelector('#err').textContent.includes('do not match')")
        assert setup_links.peek(root, secret) is not None
        b.evaluate("""(() => { document.querySelector('#again').value = 'garden-path-2026';
            document.querySelector('form[hx-post^="/setup/"]').requestSubmit(); })()""")
        b.wait_for("!location.pathname.startsWith('/setup/')", timeout=30)
        assert setup_links.peek(root, secret) is None
        assert b.evaluate(f"fetch({link!r}).then(r => r.status)", await_promise=True) == 410
        # Signed in: the session the link opened reads the hub as Dana.
        assert b.evaluate("fetch('/commands/user.list', {method: 'POST', headers: {'Content-Type': 'application/json',"
                          " 'X-Bookflow-Workbench': '1'}, body: '{}'}).then(r => r.status)", await_promise=True) != 401
    finally:
        b.close()
