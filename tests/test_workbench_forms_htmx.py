"""Every workbench form that posts goes through htmx.

A cookie-session write needs the X-Bookflow-Workbench header (the anti-CSRF rule in
adapters/http/app.py). The header comes from hx-headers on <body>, so htmx sends it only on
requests it issues itself: a native <form method="post"> fails with E_WORKBENCH_HEADER when a
person submits it. This reads every template and fails on a posting form with no hx-* verb.
"""
import re
from pathlib import Path

import bookflow.adapters.workbench as workbench

TEMPLATES = Path(workbench.__file__).parent / "templates"
# A quoted attribute may hold '>' (an arrow function in hx-on), so attributes are matched whole.
FORM = re.compile(r"""<form\b(?:[^>"']|"[^"]*"|'[^']*')*>""", re.I)
POSTS = re.compile(r"""\bmethod\s*=\s*["']?post\b""", re.I)
HTMX = re.compile(r"""\b(?:data-)?hx-(?:post|put|patch|delete)\s*=""", re.I)
BUTTON_POSTS = re.compile(r"""<(?:button|input)\b[^>]*\bformmethod\s*=\s*["']?post\b""", re.I)

# Forms that must submit natively (a file download, say), as "template.html: why". There are none.
EXEMPT: dict[str, str] = {}


def _offenders():
    found = []
    for path in sorted(TEMPLATES.rglob("*.html")):
        text = path.read_text(encoding="utf-8")
        name = path.relative_to(TEMPLATES).as_posix()
        if name in EXEMPT:
            continue
        for match in FORM.finditer(text):
            tag = match.group(0)
            line = text.count("\n", 0, match.start()) + 1
            if POSTS.search(tag) and not HTMX.search(tag):
                found.append(f"{name}:{line}: {tag[:160]}")
        for match in BUTTON_POSTS.finditer(text):
            line = text.count("\n", 0, match.start()) + 1
            found.append(f"{name}:{line}: formmethod=post bypasses htmx: {match.group(0)[:160]}")
    return found


def test_every_posting_form_submits_through_htmx():
    offenders = _offenders()
    assert not offenders, "posting forms without hx-post (they 403 in a real browser):\n" + "\n".join(offenders)


def test_the_guard_sees_forms():
    # The pattern must actually match the templates' forms, or the guard passes vacuously.
    tags = [m.group(0) for p in TEMPLATES.rglob("*.html") for m in FORM.finditer(p.read_text(encoding="utf-8"))]
    assert sum(1 for t in tags if POSTS.search(t)) >= 20, len(tags)
    assert not HTMX.search('<form method="post" action="/x">')
    assert FORM.search('<form method="post" hx-on::x="a => b > c" hx-post="/x">').group(0).endswith('hx-post="/x">')
    assert EXEMPT == {}, "an exemption needs a comment saying why the form must be native"
