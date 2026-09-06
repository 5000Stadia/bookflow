#!/usr/bin/env python3
"""Private Bookflow development presentation over AgentBridge's plan and notes."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import html
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from http.cookies import SimpleCookie, CookieError
import importlib.util
import json
import os
from pathlib import Path
import re
import secrets
import threading
import time
from urllib.parse import parse_qs, urlsplit

spec = importlib.util.spec_from_file_location('agentbridge_page', Path(__file__).resolve().parents[1] / 'design/bridge.py')
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)
esc = html.escape


class State:
    def __init__(self, root, key_file, origin):
        self.root, self.key_file, self.origin = Path(root), Path(key_file), origin
        self.lock = threading.RLock()
        self.key = None
        self.sessions = {}
        self.comments = self.root / 'notes/comments.jsonl'
        self.comments.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.comments, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        os.close(fd)
        self.comments.chmod(0o600)

    def refresh_key(self):
        try:
            value = self.key_file.read_text().strip()
            if not re.fullmatch(r'[0-9a-f]{64}', value):
                raise ValueError('Invalid key')
        except (OSError, ValueError):
            self.sessions.clear()
            self.key = None
            return False
        if value != self.key:
            self.sessions.clear()
            self.key = value
        return True

    def issue(self, key):
        with self.lock:
            if not re.fullmatch(r'[0-9a-f]{64}', key) or not self.refresh_key() or not secrets.compare_digest(key, self.key):
                return None
            self.sessions = {k: v for k, v in self.sessions.items() if v['expires'] > time.time()}
            if len(self.sessions) >= 64:
                self.sessions.pop(min(self.sessions, key=lambda k: self.sessions[k]['expires']))
            token = secrets.token_hex(32)
            self.sessions[token] = {'csrf': secrets.token_hex(32), 'expires': time.time() + 86400, 'targets': set()}
            return token

    def session(self, cookie):
        with self.lock:
            if not self.refresh_key():
                return None
            try:
                cookies = SimpleCookie(cookie)
                token = cookies['bookflow_roadmap'].value
            except (KeyError, ValueError, CookieError):
                return None
            session = self.sessions.get(token)
            if session and session['expires'] > time.time():
                return session
            self.sessions.pop(token, None)
            return None

    def data(self):
        project = bridge.Project(self.root, self.comments)
        status = json.loads((self.root / 'notes/roadmap-status.json').read_text())
        return project, status

    def targets(self, project, status):
        return {'project'} | {row.number for row in project.rows} | set(status.get('rows', {})) | {
            str(row['row']) for group in ('next', 'later', 'completed') for row in status.get(group, []) if 'row' in row
        } | {str(note['row']) for note in project.comments()}

    def append(self, session, fields):
        with self.lock:
            if not self.refresh_key() or not any(value is session and value['expires'] > time.time() for value in self.sessions.values()):
                raise PermissionError('Session expired')
            project, status = self.data()
            target = fields.get('row', 'project')
            if target not in self.targets(project, status) | session['targets']:
                return False
            project.add_comment(target, fields.get('author', '').strip()[:120] or 'K', fields['text'].strip(), 'human')
            return True


def form(target, csrf):
    return bridge._form_html(target, 'K').replace('<textarea', '<input type="hidden" name="csrf" value="' + esc(csrf) + '"><textarea').replace(
        'A detail for whoever builds this. It reaches them before they start.', 'Leave a detail or suggestion here…')


def draft_page(message, fields=None):
    fields = fields or {}
    draft = '\n'.join([fields.get('author', ''), fields.get('row', ''), fields.get('text', '')]).strip()
    return ('<!doctype html><meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>Bookflow roadmap</title><style>' + bridge.CSS + '</style><main><h1>Bookflow roadmap</h1><p>'
            + esc(message) + '</p>' + ('<p>Your unsaved note is preserved below. Copy it before reopening your bookmark.</p>'
            '<textarea aria-label="Unsaved note" style="width:100%;min-height:220px">' + esc(draft) + '</textarea>' if draft else '') + '</main>')



def correction_page(state, session, fields):
    project, status = state.data()
    targets = state.targets(project, status) | session['targets']
    labels = {row.number: status.get('rows', {}).get(row.number, {}).get('title', 'Module ' + row.number) for row in project.rows}
    labels['project'] = 'General project notes'
    options = '<option value="" disabled selected>Choose a module</option>' + ''.join(
        '<option value="' + esc(target) + '">' + esc(labels.get(target, 'Module ' + target)) + '</option>' for target in sorted(targets))
    return ('<!doctype html><meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>Keep your roadmap note</title><style>' + bridge.CSS + '</style><main><h1>Choose a module for your note</h1>'
            '<p>The original target <code>' + esc(fields.get('row', '')) + '</code> is not recognized. Your note has not been saved yet.</p>'
            '<form class="add" method="post" action="/note">'
            '<input type="hidden" name="csrf" value="' + esc(session['csrf']) + '">'
            '<label>Module <select name="row" required>' + options + '</select></label>'
            '<textarea name="text" required>' + esc(fields.get('text', '')) + '</textarea>'
            '<input name="author" aria-label="Your name" value="' + esc(fields.get('author', '')) + '">'
            '<button>Save note</button></form></main>')


def render(state, session):
    project, status = state.data()
    with state.lock:
        session['targets'].update(state.targets(project, status))
    notes = project.comments()
    grouped = {}
    for note in notes:
        grouped.setdefault(str(note['row']), []).append(note)
    def discussion(target):
        return ('<details class="discussion"><summary>Notes (' + str(len(grouped.get(target, []))) + ')</summary>'
                + '<div class="notes">' + ''.join(bridge._note_html(n) for n in grouped.get(target, []))
                + '</div>' + form(target, session['csrf']) + '</details>')
    def card(title, summary, badge='', target=None, details=''):
        return ('<article class="card"><div class="head"><div class="body"><h3>' + esc(title) + '</h3>'
                + ('<span class="badge flight">' + esc(badge) + '</span>' if badge else '')
                + '<p>' + esc(summary) + '</p>' + details + '</div></div>'
                + (discussion(target) if target else '') + '</article>')
    out = ['<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
           '<title>Bookflow roadmap</title><style>', bridge.CSS,
           'h2{font-size:21px;margin:32px 0 12px}h3{font-size:18px;margin:0 0 8px}p{overflow-wrap:anywhere}'
           '.discussion>summary,details>summary{padding:12px 18px;cursor:pointer;color:var(--accent)}'
           'nav{display:flex;flex-wrap:wrap;gap:10px;margin:18px 0 0}nav a{color:var(--accent);padding:7px 12px;'
           'text-decoration:none;border:1px solid var(--line);border-radius:30px}section{scroll-margin-top:16px}'
           '.scope{font-size:14px}.updated{margin-top:12px;color:var(--muted);font-size:13px}'
           '</style></head><body><header><div class="wrap"><h1>Bookflow · Roadmap</h1><p>', esc(status['summary']),
           '</p><div class="updated">Progress updated ', bridge._when(status['updated_at']),
           ' · <a href="/">Refresh</a></div><nav>',
           ''.join('<a href="#' + key + '">' + title + '</a>' for key, title in [('now','Now'),('next','Next'),('later','Later'),('done','Reviewed'),('notes','Your notes')]),
           '</nav></div></header><main><section id="now"><h2>In progress</h2>']
    rows = {row.number: row for row in project.rows}
    for key in ['22', '9', '24'] + [key for key in rows if key not in {'22','9','24'}]:
        if key not in rows:
            continue
        row = rows[key]
        info = status.get('rows', {}).get(key, {})
        detail = '<details class="scope"><summary>Full scope and completion criteria</summary><p>' + bridge._inline(row.target) + '</p><p>' + bridge._inline(row.done) + '</p></details>'
        out.append(card(info.get('title', 'Module ' + key), info.get('summary', 'Progress update pending.'), info.get('status', 'Open'), key, detail))
    out.append('</section>')
    for key, title in [('next','Next on the roadmap'),('later','Later · planned, not being built'),('completed','Reviewed increments')]:
        out.append('<section id="' + ('done' if key == 'completed' else key) + '"><h2>' + title + '</h2>')
        for info in status.get(key, []):
            out.append(card(info['title'], info['summary'], target=str(info['row']) if 'row' in info else None))
        out.append('</section>')
    displayed = set(rows) | {str(info['row']) for info in status.get('completed', []) if 'row' in info}
    archived = set(grouped) - displayed - {'project'}
    if archived:
        out.append('<section><h2>Archived module notes</h2>')
        for key in sorted(archived):
            out.append(card(status.get('rows', {}).get(key, {}).get('title', 'Module ' + key), 'Your notes remain available after the module leaves the active list.', target=key))
        out.append('</section>')
    out.append('<section id="notes"><h2>Your notes and future ideas</h2><p>Leave ideas here at any time. I’ll read them at normal work boundaries and fold them into the relevant plan. Nothing is automatically executed.</p>'
               + discussion('project') + '</section></main><footer>Progress summaries are dated checkpoints, not a live worker monitor. Opening or refreshing this page does not interrupt development.</footer></body></html>')
    return ''.join(out)


def handler(state):
    class Handler(BaseHTTPRequestHandler):
        server_version = 'Bookflow-roadmap'
        def log_message(self, *args):
            pass
        def send_page(self, status, content, extra=None):
            body = content.encode()
            self.send_response(status)
            for k,v in {'Content-Type':'text/html; charset=utf-8','Content-Length':str(len(body)),
                        'Cache-Control':'no-store','Referrer-Policy':'same-origin','X-Content-Type-Options':'nosniff',
                        'Content-Security-Policy':"default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'", **(extra or {})}.items():
                self.send_header(k,v)
            self.end_headers()
            self.wfile.write(body)
        def host_ok(self):
            return self.headers.get('Host') == urlsplit(state.origin).netloc
        def do_GET(self):
            if not self.host_ok():
                return self.send_page(403, draft_page('Use your roadmap bookmark.'))
            path = urlsplit(self.path).path
            if path.startswith('/access/'):
                token = state.issue(path.removeprefix('/access/'))
                if not token:
                    return self.send_page(403, draft_page('This access link is not valid.'))
                return self.send_page(303, '', {'Location':'/', 'Referrer-Policy':'no-referrer', 'Set-Cookie':'bookflow_roadmap=' + token + '; HttpOnly; SameSite=Strict; Path=/; Max-Age=86400'})
            if path != '/':
                return self.send_page(404, draft_page('Page not found.'))
            session = state.session(self.headers.get('Cookie',''))
            if not session:
                return self.send_page(401, draft_page('Open your private roadmap bookmark to sign in.'))
            self.send_page(200, render(state, session))
        def do_POST(self):
            if not self.host_ok() or urlsplit(self.path).path != '/note':
                return self.send_page(404, draft_page('Page not found.'))
            try:
                length = int(self.headers.get('Content-Length','-1'))
            except ValueError:
                length = -1
            if not 0 <= length <= 65536:
                return self.send_page(413, draft_page('Note size is invalid. Use Back to keep your draft.'))
            if self.headers.get_content_type() != 'application/x-www-form-urlencoded':
                return self.send_page(415, draft_page('Use the note form on the roadmap.'))
            raw_body = self.rfile.read(length).decode('utf-8', errors='replace')
            try:
                raw = parse_qs(raw_body, max_num_fields=10)
            except ValueError:
                return self.send_page(400, draft_page('This form has too many fields.', {'text': raw_body}))
            fields = {key: value[0] for key,value in raw.items()}
            session = state.session(self.headers.get('Cookie',''))
            if not session:
                return self.send_page(401, draft_page('Your login expired. Reopen your bookmark after keeping this draft.', fields))
            if self.headers.get('Origin') != state.origin or not re.fullmatch(r'[0-9a-f]{64}', fields.get('csrf','')) or not secrets.compare_digest(fields.get('csrf',''), session['csrf']):
                return self.send_page(403, draft_page('This form could not be verified. Keep the draft and reopen the page.', fields))
            if not fields.get('text','').strip():
                return self.send_page(400, draft_page('Write a note before saving.', fields))
            try:
                accepted = state.append(session, fields)
            except PermissionError:
                return self.send_page(401, draft_page('Your login expired. Keep your draft before reopening the bookmark.', fields))
            if not accepted:
                return self.send_page(409, correction_page(state, session, fields))
            self.send_page(303, '', {'Location':'/#notes'})
    return Handler


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root', type=Path, required=True)
    p.add_argument('--key-file', type=Path, required=True)
    p.add_argument('--host', default='127.0.0.1')
    p.add_argument('--port', type=int, default=8787)
    args = p.parse_args()
    origin = f'http://{args.host}:{args.port}'
    state = State(args.root, args.key_file, origin)
    if not state.refresh_key():
        p.error('A private 64-hex-character key file is required.')
    server = ThreadingHTTPServer((args.host,args.port), handler(state))
    try:
        server.serve_forever()
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
