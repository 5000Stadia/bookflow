"""The login form reports a failure only when the host refused the sign-in."""
import json

from tests.test_row5_browser_acceptance import PASSWORD, _Cdp, browser_site  # noqa: F401

# Record every text the error line ever shows, across the navigation a good login causes.
WATCH = """(() => {
  sessionStorage.setItem('login-errors', '[]');
  const err = document.getElementById('err');
  new MutationObserver(() => {
    const seen = JSON.parse(sessionStorage.getItem('login-errors'));
    if (err.textContent) seen.push(err.textContent);
    sessionStorage.setItem('login-errors', JSON.stringify(seen));
  }).observe(err, {childList: true, characterData: true, subtree: true});
})()"""


def _submit(browser, site, password):
    browser.navigate(site.base_url + "/login")
    browser.evaluate(WATCH)
    browser.evaluate(f"""(() => {{
      document.querySelector('[name="username"]').value = {json.dumps(site.login)};
      document.querySelector('[name="password"]').value = {json.dumps(password)};
      document.querySelector('form[hx-post="/login"]').requestSubmit();
    }})()""")


def test_a_good_login_never_flashes_a_failure(browser_site, tmp_path):
    browser = _Cdp(tmp_path / "login-good")
    try:
        _submit(browser, browser_site, PASSWORD)
        browser.wait_for("!!document.querySelector('.nav-group')")
        assert json.loads(browser.evaluate("sessionStorage.getItem('login-errors')")) == []
    finally:
        browser.close()


def test_a_wrong_password_says_so_and_stays_on_the_form(browser_site, tmp_path):
    browser = _Cdp(tmp_path / "login-bad")
    try:
        _submit(browser, browser_site, PASSWORD + " wrong")
        browser.wait_for("!!document.getElementById('err').textContent")
        assert browser.evaluate("document.getElementById('err').textContent") == \
            "That username and password did not match. Try again."
        assert browser.evaluate("location.pathname") == "/login"
    finally:
        browser.close()
