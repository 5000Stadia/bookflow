/* Independent record annotations. The command response is the only save authority. */
(() => {
  if (window.bookflowAnnotations) { window.bookflowAnnotations(); return; }
  const el = (tag, text, cls) => {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (cls) node.className = cls;
    return node;
  };
  const message = (node, text, failed = false) => {
    node.textContent = text;
    node.dataset.failed = String(failed);
  };
  function button(text, action) {
    const node = el('button', text); node.type = 'button';
    node.addEventListener('click', action); return node;
  }
  function initialize(panel) {
    if (panel.dataset.ready) return;
    panel.dataset.ready = 'true';
    const config = JSON.parse(panel.dataset.annotations);
    const base = '/companies/' + encodeURIComponent(config.company);
    const sections = {};
    function headers(name) {
      const result = {'X-Bookflow-Workbench': '1', 'X-Bookflow-Company': config.company,
        'X-Bookflow-Client-Name': 'bookflow-workbench'};
      const names = {'reason': 'X-Bookflow-Reason', 'source_ref': 'X-Bookflow-Source-Ref', 'directive': 'X-Bookflow-Directive'};
      const writes = config.writes.includes(name);
      for (const [field, header] of Object.entries(writes ? names : {})) {
        const value = document.querySelector(`[name="ctx:${field}"]`)?.value;
        if (value) result[header] = value;
      }
      for (const header of ['X-Bookflow-Reason', 'X-Bookflow-Source-Ref', 'X-Bookflow-Directive',
                            'Idempotency-Key', 'X-Bookflow-Client-Name', 'X-Bookflow-Client-Version']) {
        if (result[header] != null) result[header] = encodeURIComponent(result[header]);
      }
      result['X-Bookflow-Context-Encoding'] = 'percent-utf8';
      return result;
    }
    async function responseError(response) {
      let data;
      try { data = await response.json(); } catch (_) { throw Error(`Request failed (${response.status}). Your draft is unchanged.`); }
      throw Error([data.code, data.message, data.details ? JSON.stringify(data.details) : ''].filter(Boolean).join(' — '));
    }
    async function command(name, input, body) {
      const binary = body !== undefined;
      const requestHeaders = headers(name);
      requestHeaders['Content-Type'] = binary ? 'application/octet-stream' : 'application/json';
      if (binary) {
        const metadata = new TextEncoder().encode(JSON.stringify(input));
        if (metadata.length > 6144) throw Error('File metadata is too long (maximum 6144 bytes).');
        requestHeaders['X-Bookflow-Input'] = btoa(String.fromCharCode(...metadata)).replaceAll('+', '-').replaceAll('/', '_').replace(/=+$/, '');
      }
      const response = await fetch(base + (binary ? '/transfers/' : '/commands/') + name.replaceAll(' ', '.'), {
        method: 'POST', credentials: 'same-origin', headers: requestHeaders, body: binary ? body : JSON.stringify(input)
      });
      if (!response.ok) await responseError(response);
      if (response.status === 202) throw Error('The command has not completed. Refresh to check before retrying; your draft is unchanged.');
      if (name === 'attachment get') return response;
      const result = await response.json();
      if (result.error || result.code?.startsWith('E_')) throw Error(result.message || JSON.stringify(result));
      return result;
    }
    async function busy(container, status, pending, work) {
      if (container.getAttribute('aria-busy') === 'true') return;
      container.setAttribute('aria-busy', 'true');
      const controls = [...container.querySelectorAll('button,input,textarea')].filter(node => !node.disabled);
      controls.forEach(node => { node.disabled = true; });
      message(status, pending);
      try { await work(); } catch (error) { message(status, error.message || String(error), true); }
      finally { controls.forEach(node => { node.disabled = false; }); container.removeAttribute('aria-busy'); }
    }
    function attribution(container, actor, at) {
      const line = el('p', (actor || 'Unknown actor') + ' · ', 'muted');
      const time = el('time', at || ''); if (at) time.dateTime = at;
      line.append(time); container.append(line);
    }
    function noteEntry(note) {
      const entry = el('article', undefined, 'annotation-entry'); entry.dataset.noteId = note.id;
      const body = el('p', note.body, 'annotation-text'); entry.append(body);
      attribution(entry, note.author_name || note.author_id, note.at);
      if (note.edited_at) attribution(entry, note.updated_by_name || note.updated_by, note.edited_at);
      if (config.allowed['note edit'] && note.kind === 'comment') {
        const edit = button('Edit note', () => {
          edit.hidden = true;
          const form = el('form'); form.dataset.noteEdit = note.id;
          const label = el('label', 'Edit note text'); const input = el('textarea');
          input.name = 'body'; input.required = true; input.maxLength = 65536; input.rows = 4;
          input.value = note.body; label.append(input); form.append(label);
          const save = el('button', 'Save note'); save.type = 'submit';
          const status = el('p'); status.setAttribute('role', 'status');
          const cancel = button('Cancel edit', () => {
            if (input.value !== note.body && !confirm('Discard this note draft?')) return;
            form.remove(); edit.hidden = false; edit.focus();
          });
          form.append(save, cancel, status); entry.append(form); input.focus();
          form.addEventListener('submit', event => {
            event.preventDefault();
            busy(form, status, 'Saving note…', async () => {
              const result = await command('note edit', {note: note.id, expected_version: note.version, body: input.value});
              const replacement = noteEntry(result.note);
              entry.replaceWith(replacement); replacement.querySelector('button')?.focus();
              message(sections.notes.status, 'Note saved.'); refresh('activity');
            });
          });
        }); entry.append(edit);
      }
      return entry;
    }
    async function download(link, entry, status) {
      await busy(entry, status, 'Downloading and verifying file…', async () => {
        const response = await command('attachment get', {attachment: link.attachment_id}, new Uint8Array());
        const bytes = await response.arrayBuffer();
        const size = response.headers.get('Content-Length');
        const hash = response.headers.get('X-Bookflow-SHA256');
        if (size === null || !/^\d+$/.test(size) || Number(size) !== bytes.byteLength || bytes.byteLength !== link.attachment.size_bytes)
          throw Error('File size verification failed. No file was saved.');
        const actual = await sha256(bytes);
        if (!hash || actual !== hash.toLowerCase() || actual !== link.attachment.sha256.toLowerCase())
          throw Error('File checksum verification failed. No file was saved.');
        const blob = new Blob([bytes], {type: link.attachment.media_type});
        const url = URL.createObjectURL(blob);
        const a = el('a'); a.href = url; a.download = link.attachment.original_filename;
        document.body.append(a); a.click(); a.remove(); setTimeout(() => URL.revokeObjectURL(url), 30000);
        message(status, 'File verified. Download handed to your browser.');
      });
    }
    function fileEntry(link) {
      const entry = el('article', undefined, 'annotation-entry'); entry.dataset.linkId = link.id;
      entry.append(el('strong', link.attachment.original_filename), el('p', link.caption, 'annotation-text'),
        el('p', `${link.attachment.size_bytes} bytes · ${link.attachment.media_type}`, 'muted'));
      attribution(entry, link.linked_by_name || link.linked_by, link.linked_at);
      const status = el('p'); status.setAttribute('role', 'status');
      if (config.allowed['attachment get']) entry.append(button('Download file', () => download(link, entry, status)));
      if (config.allowed['attachment unlink']) entry.append(button('Unlink file', () => {
        if (!confirm(`Unlink “${link.attachment.original_filename}” from this record?`)) return;
        busy(entry, status, 'Unlinking file…', async () => {
          await command('attachment unlink', {link: link.id, expected_version: link.version});
          entry.remove(); message(sections.files.status, 'File unlinked.'); refresh('activity');
        });
      }));
      entry.append(status); return entry;
    }
    function activityEntry(item) {
      const entry = el('li', undefined, 'annotation-entry');
      attribution(entry, item.actor_name || item.actor_id, item.at);
      entry.append(el('p', item.summary || item.command));
      if (item.body != null) entry.append(el('p', item.body, 'annotation-text'));
      if (item.caption) entry.append(el('p', item.caption, 'annotation-text'));
      if (item.principal_id) entry.append(el('p', 'On behalf of ' + (item.principal_name || item.principal_id), 'muted'));
      const explanation = item.explanation;
      if (explanation) {
        if (explanation.reason) entry.append(el('p', explanation.reason, 'annotation-text'));
        if (explanation.directive_status === 'available' && explanation.directive) {
          const directive = explanation.directive;
          const line = el('p', undefined, 'annotation-text');
          const link = el('a', directive.code);
          link.href = '/c/' + encodeURIComponent(config.company) + '/directive/' + encodeURIComponent(directive.id);
          line.append(link);
          if (directive.text === '' && item.text_truncated) {
            line.append(document.createTextNode(' — Instruction omitted from this excerpt. '));
            const full = el('a', 'Read full instruction'); full.href = link.href;
            line.append(full);
          } else if (directive.text) {
            line.append(document.createTextNode(': ' + directive.text));
          }
          entry.append(line);
        } else if (explanation.directive_status === 'unavailable') {
          entry.append(el('p', 'Cited directive unavailable.', 'muted'));
        }
      }
      if (item.text_truncated) entry.append(el('p', 'Excerpt; full text is in audit event ' + item.event_id, 'muted'));
      return entry;
    }
    async function refresh(kind, more = false) {
      const section = sections[kind];
      if (!section || section.loading) return;
      if (!more && section.items.querySelector('[data-note-edit],[aria-busy="true"]')) {
        message(section.status, 'Finish the current action or cancel your note edit before refreshing. Your draft is unchanged.'); return;
      }
      section.loading = true;
      section.root.setAttribute('aria-busy', 'true');
      section.refresh.disabled = section.more.disabled = true;
      message(section.status, 'Loading…');
      try {
        const result = await command(section.command, {...config.target, limit: 20, ...(more ? {cursor: section.cursor} : {})});
        if (!more && section.items.querySelector('[data-note-edit],[aria-busy="true"]')) {
          message(section.status, 'New entries are available. Finish the current action or cancel your note edit, then refresh. Your draft is unchanged.');
          return;
        }
        if (!more) section.items.replaceChildren();
        result.items.forEach(item => section.items.append(section.draw(item)));
        section.cursor = result.next_cursor;
        section.more.hidden = !result.has_more;
        message(section.status, result.items.length ? (result.has_more ? 'More entries available.' : 'All entries loaded.') : 'No entries.');
      } catch (error) { message(section.status, `${error.message} Use Refresh to restart a stale list.`, true); }
      finally { section.loading = false; section.root.removeAttribute('aria-busy'); section.refresh.disabled = section.more.disabled = false; }
    }
    for (const [kind, name, draw] of [['notes', 'note list', noteEntry], ['files', 'attachment list', fileEntry], ['activity', 'activity', activityEntry]]) {
      const root = panel.querySelector(`[data-section="${kind}"]`);
      sections[kind] = {root, command: name, draw, items: root.querySelector('[data-items]'), status: root.querySelector('[data-list-status]'),
        refresh: root.querySelector('[data-refresh]'), more: root.querySelector('[data-more]')};
      const section = sections[kind];
      section.refresh.addEventListener('click', () => refresh(kind)); section.more.addEventListener('click', () => refresh(kind, true));
      if (config.allowed[name]) refresh(kind);
      else { section.refresh.hidden = true; message(section.status, 'You do not have access to this list.'); }
    }
    const noteForm = panel.querySelector('[data-note-add]');
    noteForm?.addEventListener('submit', event => {
      event.preventDefault();
      busy(noteForm, noteForm.querySelector('[data-status]'), 'Adding note…', async () => {
        await command('note add', {...config.target, body: noteForm.elements.body.value});
        noteForm.reset(); message(noteForm.querySelector('[data-status]'), 'Note added.');
        refresh('notes'); refresh('activity');
      });
    });
    const fileForm = panel.querySelector('[data-file-add]');
    fileForm?.addEventListener('submit', event => {
      event.preventDefault();
      const file = fileForm.elements.file.files[0]; if (!file) return;
      busy(fileForm, fileForm.querySelector('[data-status]'), 'Uploading file; waiting for save…', async () => {
        try {
          await command('attachment add', {...config.target, original_filename: file.name,
            media_type: file.type || 'application/octet-stream', caption: fileForm.elements.caption.value}, file);
          fileForm.reset(); message(fileForm.querySelector('[data-status]'), 'File attached.');
          refresh('files'); refresh('activity');
        } catch (error) {
          fileForm.elements.file.value = '';
          throw Error(error.message + ' Select the file again before retrying. Your caption is unchanged.');
        }
      });
    });
  }
  // SHA-256 fallback for a local HTTP workbench where SubtleCrypto is unavailable.
  async function sha256(buffer) {
    if (globalThis.crypto?.subtle) {
      const digest = await crypto.subtle.digest('SHA-256', buffer);
      return [...new Uint8Array(digest)].map(x => x.toString(16).padStart(2, '0')).join('');
    }
    const k = [0x428a2f98,0x71374491,0xb5c0fbcf,0xe9b5dba5,0x3956c25b,0x59f111f1,0x923f82a4,0xab1c5ed5,0xd807aa98,0x12835b01,0x243185be,0x550c7dc3,0x72be5d74,0x80deb1fe,0x9bdc06a7,0xc19bf174,0xe49b69c1,0xefbe4786,0x0fc19dc6,0x240ca1cc,0x2de92c6f,0x4a7484aa,0x5cb0a9dc,0x76f988da,0x983e5152,0xa831c66d,0xb00327c8,0xbf597fc7,0xc6e00bf3,0xd5a79147,0x06ca6351,0x14292967,0x27b70a85,0x2e1b2138,0x4d2c6dfc,0x53380d13,0x650a7354,0x766a0abb,0x81c2c92e,0x92722c85,0xa2bfe8a1,0xa81a664b,0xc24b8b70,0xc76c51a3,0xd192e819,0xd6990624,0xf40e3585,0x106aa070,0x19a4c116,0x1e376c08,0x2748774c,0x34b0bcb5,0x391c0cb3,0x4ed8aa4a,0x5b9cca4f,0x682e6ff3,0x748f82ee,0x78a5636f,0x84c87814,0x8cc70208,0x90befffa,0xa4506ceb,0xbef9a3f7,0xc67178f2];
    const state = [0x6a09e667,0xbb67ae85,0x3c6ef372,0xa54ff53a,0x510e527f,0x9b05688c,0x1f83d9ab,0x5be0cd19];
    const length = buffer.byteLength, padded = new Uint8Array(Math.ceil((length + 9) / 64) * 64);
    padded.set(new Uint8Array(buffer)); padded[length] = 128;
    const view = new DataView(padded.buffer); view.setUint32(padded.length - 8, Math.floor(length / 0x20000000)); view.setUint32(padded.length - 4, length * 8);
    const rotate = (x, n) => (x >>> n) | (x << (32 - n)); const words = new Int32Array(64);
    for (let offset = 0; offset < padded.length; offset += 64) {
      for (let i = 0; i < 16; i++) words[i] = view.getInt32(offset + i * 4);
      for (let i = 16; i < 64; i++) { const x = words[i - 15], y = words[i - 2]; words[i] = words[i - 16] + (rotate(x,7)^rotate(x,18)^(x>>>3)) + words[i - 7] + (rotate(y,17)^rotate(y,19)^(y>>>10)); }
      let [a,b,c,d,e,f,g,h] = state;
      for (let i = 0; i < 64; i++) { const t1 = (h + (rotate(e,6)^rotate(e,11)^rotate(e,25)) + ((e&f)^(~e&g)) + k[i] + words[i]) | 0; const t2 = ((rotate(a,2)^rotate(a,13)^rotate(a,22)) + ((a&b)^(a&c)^(b&c))) | 0; h=g;g=f;f=e;e=(d+t1)|0;d=c;c=b;b=a;a=(t1+t2)|0; }
      [a,b,c,d,e,f,g,h].forEach((x,i) => { state[i] = (state[i] + x) | 0; });
    }
    return state.map(x => (x >>> 0).toString(16).padStart(8,'0')).join('');
  }
  window.bookflowAnnotations = () => document.querySelectorAll('[data-annotations]').forEach(initialize);
  window.bookflowAnnotations();
  document.addEventListener('htmx:load', window.bookflowAnnotations);
})();
