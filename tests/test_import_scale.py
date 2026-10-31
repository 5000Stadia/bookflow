"""R107 increment 5: scale (design/bulk-import.md §5, §10 criterion 8, §11).

Done when criterion 8 holds with numbers recorded: a run's throughput is within 1.25 times a script looping the same
single command in-process; a process killed mid-run, at three points, resumes with no duplicates; peak memory at
10,000 rows is within 10 percent of that at 1,000; a file one row over the per-run cap is refused at preview with its
count and how to split it; and a preview over 100 records advises a backup first in `next`. The full-size numbers
come from `tools/measure_import.py` and are recorded in the design's As built; these tests run the same tool small,
except the memory bound, which needs both sizes.
"""
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from bookflow import BookflowError
from bookflow.company import tabular

from tests.test_imports import active_customers, books, company_db, rows_file, run_all  # noqa: F401 (fixture)

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / 'tools' / 'measure_import.py'


def measure(tmp_path, *args, timeout=600):
    """One run of the measuring tool in its own process: its JSON line."""
    done = subprocess.run([sys.executable, str(TOOL), *map(str, args[:2]), str(tmp_path), *map(str, args[2:])],
                          capture_output=True, text=True, timeout=timeout, env={**os.environ, 'PYTHONPATH': os.pathsep.join(
                              [str(ROOT / 'src'), str(ROOT), os.environ.get('PYTHONPATH', '')])})
    assert done.returncode == 0, done.stderr[-3000:]
    return json.loads(done.stdout.strip().splitlines()[-1])


@pytest.mark.timeout(600)
def test_a_run_is_within_a_quarter_of_looping_the_single_command(tmp_path):
    """Criterion 8's throughput, small: 300 customers and 30 invoices of 3 lines, each against a script looping the
    same command in-process over the same rows (`client.run`, as a script would). As built records 10,000 rows."""
    customers = measure(tmp_path, 'customer', 300, '--loop')
    assert customers['run_over_loop'] <= 1.25, customers
    invoices = measure(tmp_path, 'invoice', 30, '--loop')
    assert invoices['run_over_loop'] <= 1.25, invoices


KILL = textwrap.dedent('''
    import os, sys
    data_root, company, run_id, point = sys.argv[1:5]
    os.environ['BOOKFLOW_DATA_ROOT'] = data_root
    import bookflow
    from bookflow.company import batches, imports
    writes = {'n': 0}
    if point == 'inside_write':  # the seventh row's write, its key and outcome recorded, not yet committed
        real_recorder = imports._row_recorder
        def recorder(*args):
            record = real_recorder(*args)
            def dying(*inner):
                record(*inner)
                writes['n'] += 1
                if writes['n'] == 7:
                    os._exit(9)
            return dying
        imports._row_recorder = recorder
    elif point == 'after_write':  # the seventh write committed; gone before the next row
        real_nested = batches.run_nested
        def nested(s, ctx, name, payload, **kw):
            out = real_nested(s, ctx, name, payload, **kw)
            if name == 'customer create' and not kw.get('dry_run'):
                writes['n'] += 1
                if writes['n'] == 7:
                    os._exit(9)
            return out
        batches.run_nested = nested
    elif point == 'outcomes_unsaved':  # rows that wrote nothing, their outcomes being stored before the next write
        real_insert = imports._insert_rows
        def insert(s, values):
            if values:
                os._exit(9)
            real_insert(s, values)
        imports._insert_rows = insert
    client = bookflow.connect(data_root=data_root)
    client.run('import run', {'run': run_id}, company=company)
    sys.exit(3)  # never reached: the process is killed inside the run
''')


@pytest.mark.parametrize('point', ('inside_write', 'after_write', 'outcomes_unsaved'))
def test_a_process_killed_mid_run_resumes_with_no_duplicates(books, tmp_path, point):
    """Criterion 8: the process running a slice is killed outright (`os._exit`, no clean-up, a write transaction
    open at the first point) at three points; `import run` in a new process continues at the first row without an
    outcome and the books hold each customer once, each with one key and one batch effect."""
    if point == 'outcomes_unsaved':  # rows 11-15 already in from an earlier run of the source: no write, buffered
        run_all(books, books.run('import preview', {'kind': 'customer', 'content': rows_file(5, start=11)})['run']['id'])
    preview = books.run('import preview', {'kind': 'customer', 'content': rows_file(30)})
    script = tmp_path / 'kill.py'
    script.write_text(KILL)
    killed = subprocess.run([sys.executable, str(script), str(books.root), books.company, preview['run']['id'], point],
                            capture_output=True, text=True, timeout=300, cwd=str(ROOT),
                            env={**os.environ, 'PYTHONPATH': os.pathsep.join(
                                [str(ROOT / 'src'), str(ROOT), os.environ.get('PYTHONPATH', '')])})
    assert killed.returncode == 9, killed.stderr[-3000:]
    made = {'inside_write': 6, 'after_write': 7, 'outcomes_unsaved': 15}[point]
    assert len(active_customers(books)) == made
    shown = books.run('import show', {'run': preview['run']['id']})
    assert shown['run']['state'] == 'running'
    assert shown['counts'].get('created', 0) == (10 if point == 'outcomes_unsaved' else made)
    done = run_all(books, preview['run']['id'])
    want = {'created': 25, 'already_in': 5} if point == 'outcomes_unsaved' else {'created': 30}
    assert done['run']['state'] == 'done' and done['counts'] == {**want, 'not_run': 0}
    assert active_customers(books) == {f'Customer {i:03d}' for i in range(1, 31)}
    with company_db(books) as db:
        assert db.execute('SELECT count(*) FROM customers').fetchone()[0] == 30
        assert db.execute('SELECT count(*) FROM import_keys').fetchone()[0] == 30
        assert db.execute('SELECT count(*) FROM batch_effects').fetchone()[0] == 30
        assert db.execute("SELECT count(*) FROM import_rows WHERE phase = 'run'").fetchone()[0] == (
            35 if point == 'outcomes_unsaved' else 30)


@pytest.mark.timeout(1500)
def test_memory_at_ten_thousand_rows_is_within_ten_percent_of_a_thousand(tmp_path):
    """Criterion 8's memory bound: the peak resident memory of the preview and of the run of 10,000 customers is
    within 10 percent of 1,000's, each size measured alone in its own process (about 6 minutes). The peaks are the
    import's own: the tool resets the high-water mark after the companies are made, whose setup peaks higher."""
    small = measure(tmp_path, 'customer', 1000)
    large = measure(tmp_path, 'customer', 10_000, timeout=1400)
    assert large['hwm_preview_mb'] <= 1.10 * small['hwm_preview_mb'], (small, large)
    assert large['hwm_run_mb'] <= 1.10 * small['hwm_run_mb'], (small, large)
    # and the run holds nothing across slices: the most it holds after any slice, too (a reference cycle once kept
    # each slice's books, every customer, until a full collection: +14 percent at 10,000).
    assert large['rss_slices_mb']['max'] <= 1.10 * small['rss_slices_mb']['max'], (small, large)


def test_a_file_one_row_over_the_cap_is_refused_at_preview_with_its_count_and_how_to_split_it(books):
    rows = tabular.MAX_ROWS + 1
    text = 'Customer,City\n' + ''.join(f'Customer {i:06d},Springfield\n' for i in range(rows))
    with pytest.raises(BookflowError) as caught:
        books.run('import preview', {'kind': 'customer', 'content': text})
    details, message = caught.value.details, caught.value.message
    assert details['problem'] == 'too_many_rows' and details['rows'] == rows and details['files'] == 2
    assert details['max_rows'] == tabular.MAX_ROWS
    assert message == (f'The file has {rows:,} rows; one import reads at most {tabular.MAX_ROWS:,}. Split it between '
                       f'rows into 2 files of at most {tabular.MAX_ROWS:,} rows each, each starting with the header '
                       'row, and import them one after another as the same source: the rows already in are never made '
                       'twice.')
    with company_db(books) as db:
        assert db.execute('SELECT count(*) FROM import_runs').fetchone()[0] == 0
    assert len(tabular.read(text.rsplit('Customer', 1)[0]).rows) == tabular.MAX_ROWS  # at the cap: read


def test_a_document_file_over_the_cap_is_split_between_documents_never_inside_one(books, monkeypatch):
    """A cut through an invoice would import it short: the later file's rows hash to the invoice's key, already in,
    with no error. The refusal of a document kind says to keep each document's rows in one file."""
    monkeypatch.setattr(tabular, 'MAX_ROWS', 4)
    monkeypatch.setattr(tabular, 'COUNTED_ROWS', 8)
    text = 'Invoice No,Customer,Date,Item,Qty,Rate\n' + ''.join(
        f'INV-{n},Acme,2026-02-02,Labor,1,10\n' for n in (1, 1, 1, 2, 2, 2))
    with pytest.raises(BookflowError) as caught:
        books.run('import preview', {'kind': 'invoice', 'content': text})
    assert caught.value.details == {'field': 'content', 'problem': 'too_many_rows', 'rows': 6, 'more_than': False,
                                    'max_rows': 4, 'files': 2}
    assert caught.value.message == (
        'The file has 6 rows; one import reads at most 4. Split it between documents into at least 2 files of at most '
        "4 rows, keeping every document's rows together in one file (a document cut across two files would import "
        'short: its rows in the later file count as already in), each file starting with the header row, and import '
        'them one after another as the same source: the documents already in are never made twice.')
    with pytest.raises(BookflowError) as caught:
        books.run('import preview', {'kind': 'customer', 'content': 'Customer\n' + 'A\n' * 5})
    assert caught.value.message.startswith('The file has 5 rows; one import reads at most 4. Split it between rows '
                                           'into 2 files of at most 4 rows each')


def test_counting_past_the_cap_stops_and_a_csv_or_workbook_attachment_refuses_on_one_field(books, monkeypatch):
    from tests.test_import_rules import _workbook
    import io
    monkeypatch.setattr(tabular, 'MAX_ROWS', 3)
    monkeypatch.setattr(tabular, 'COUNTED_ROWS', 6)
    with pytest.raises(BookflowError) as caught:
        books.run('import preview', {'kind': 'customer', 'content': 'Customer\n' + 'A\n' * 50})
    assert caught.value.details['rows'] == 6 and caught.value.details['more_than'] is True
    assert caught.value.details['files'] == 3
    assert caught.value.message.startswith('The file has more than 6 rows; one import reads at most 3. Split it '
                                           'between rows into at least 3 files')
    company = books.run('company show')
    fields = []
    for name, media, raw in (('c.csv', 'text/csv', ('Customer\n' + 'A\n' * 5).encode()),
                             ('c.xlsx', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                              _workbook([['Customer']] + [[f'C{i}'] for i in range(5)]))):
        added = books.run('attachment add', dict(record_type='company_info', record_id=company['company_id'],
                                                 original_filename=name, media_type=media),
                          input_stream=io.BytesIO(raw))
        with pytest.raises(BookflowError) as caught:
            books.run('import preview', {'kind': 'customer', 'attachment': added['attachment']['id']})
        assert caught.value.details['problem'] == 'too_many_rows' and caught.value.details['rows'] == 5
        fields.append(caught.value.details['field'])
    assert fields == ['attachment', 'attachment']
    monkeypatch.setattr(tabular, 'MAX_TEXT_BYTES', 100)
    with pytest.raises(BookflowError) as caught:
        books.run('import preview', {'kind': 'customer', 'content': 'Customer\n' + 'Acme Plumbing\n' * 10})
    assert caught.value.details['problem'] == 'too_large' and caught.value.details['field'] == 'content'


def test_a_sub_customer_before_its_parent_across_tiny_slices_and_a_kill_between_them(books, tmp_path):
    """`_WaitingOn` across slices: a job listed before its customer runs the customer first, in the same slice; a
    process killed after the customer's write committed, before the job's, resumes with each made once and the job
    under that customer."""
    text = ('Customer,Parent,City\nJob A,Acme,Springfield\nAcme,,Springfield\nJob B,Bolt,Springfield\n'
            'Solo,,Springfield\nBolt,,Springfield\n')
    preview = books.run('import preview', {'kind': 'customer', 'content': text})
    assert preview['counts'] == {'create': 5}, preview['counts']
    script = tmp_path / 'kill.py'
    script.write_text(KILL.replace("client.run('import run', {'run': run_id}", "client.run('import run', "
                                   "{'run': run_id, 'limit': 2}").replace('writes[\'n\'] == 7', 'writes[\'n\'] == 1'))
    killed = subprocess.run([sys.executable, str(script), str(books.root), books.company, preview['run']['id'],
                             'after_write'], capture_output=True, text=True, timeout=300, cwd=str(ROOT),
                            env={**os.environ, 'PYTHONPATH': os.pathsep.join(
                                [str(ROOT / 'src'), str(ROOT), os.environ.get('PYTHONPATH', '')])})
    assert killed.returncode == 9, killed.stderr[-3000:]
    assert active_customers(books) == {'Acme'}  # the parent made first, for the job; killed before the job
    while True:
        out = books.run('import run', {'run': preview['run']['id'], 'limit': 1})
        if out['run']['state'] != 'running':
            break
    assert out['run']['state'] == 'done' and out['counts'] == {'created': 5, 'not_run': 0}, out['counts']
    with company_db(books) as db:
        made = dict(db.execute('SELECT name, count(*) FROM customers GROUP BY name').fetchall())
        parents = dict(db.execute('SELECT c.name, p.name FROM customers c LEFT JOIN customers p '
                                  'ON p.id = c.parent_id').fetchall())
        assert db.execute('SELECT count(*) FROM import_keys').fetchone()[0] == 5
    assert made == {'Acme': 1, 'Bolt': 1, 'Job A': 1, 'Job B': 1, 'Solo': 1}
    assert parents == {'Acme': None, 'Bolt': None, 'Solo': None, 'Job A': 'Acme', 'Job B': 'Bolt'}


def test_a_preview_over_a_hundred_rows_advises_a_backup_first_in_next(books):
    hundred = books.run('import preview', {'kind': 'customer', 'content': rows_file(100)})
    assert hundred['next'] == f"import run {hundred['run']['id']}"
    more = books.run('import preview', {'kind': 'customer', 'content': rows_file(101, start=101)})
    assert more['next'] == f"company backup first (101 rows; the import takes none), then import run {more['run']['id']}"
    assert books.run('import show', {'run': more['run']['id']})['next'] == more['next']
    first = books.run('import run', {'run': more['run']['id'], 'limit': 10})
    assert first['next'] == f"import run {more['run']['id']}"  # once running, the advice has been given
