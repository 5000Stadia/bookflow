"""Split sales equal a hand-entered invoice and its method-specific receipts."""
from tests.test_row3_host import hosted  # noqa: F401
from decimal import Decimal
import pytest
from tests.test_pos_sale import Company, _setup, _reads, _norm, _carts, twins  # noqa: F401


def split(co, key='split', methods=('Visa', 'Cash'), cart=None):
    cart = cart or {'lines': [{'item': 'Brass Shutoff Valve', 'quantity': '2'}, {'item': 'Mainline Clearing'}]}
    preview = co.run('pos sale preview', cart)
    amount = Decimal(preview['total']['amount'])
    share = min(Decimal('10.00'), (amount / len(methods)).quantize(Decimal('.01')))
    tenders = [{'payment_method': name, 'amount': str(share), 'reference': f'part-{i}'}
               for i, name in enumerate(methods[:-1])]
    tenders.append({'payment_method': methods[-1], 'amount': str(amount - share * (len(methods)-1) + 5)})
    body = dict(cart, ticket=key, expected_total=str(amount), tenders=tenders)
    return co.run('pos sale complete', body), body


def test_split_generated_carts_match_invoice_and_payments(twins):
    pos, typed = twins
    _setup(pos)
    reg = _setup(typed)
    for i, cart in enumerate(_carts(5114, 8)):
        body = {'lines': cart['lines'], **({'customer': cart['customer']} if cart['customer'] else {})}
        methods = ('Visa', 'Cash') if i % 2 else ('Visa', 'Check', 'Cash')
        sale, sent = split(pos, f'twin-split-{i}', methods, body)
        invoice = typed.run('invoice post', dict(body, customer=cart['customer'] or reg['walk_in_customer']['id'],
            site=reg['site']['id'], terms='Due on receipt', due_date=sale['date']))
        current = invoice
        for n, tender in enumerate(sent['tenders']):
            amount = Decimal(tender['amount']) - (5 if tender['payment_method'] == 'Cash' else 0)
            typed.run('payment receive', dict(customer=cart['customer'] or reg['walk_in_customer']['id'],
                date=sale['date'], amount=str(amount), deposit_to=reg['deposit_account']['id'],
                payment_method=tender['payment_method'], reference=tender.get('reference'),
                operation_key=f'twin-payment-{i}-{n}', applications={'mode': 'inline', 'items': [dict(
                    invoice=invoice['id'], expected_version=current['version'], amount=str(amount))]}))
            current = typed.run('invoice show', {'invoice': invoice['id']})
        mine = pos.run('invoice show', {'invoice': sale['sales_receipt']['id']})
        assert sale['document_type'] == 'invoice'
        assert sale['version'] == mine['version']
        assert sale['change']['amount'] == '5.00'
        assert _norm(mine) == _norm(current)
        receipt = pos.run('pos receipt show', {'sale': sale['id']})
        assert receipt['sale']['tenders'] == sale['tenders']
        retry = pos.run('pos sale complete', sent)
        assert retry['replayed'] and retry['sales_receipt'] == sale['sales_receipt']
    assert _reads(pos) == _reads(typed)


def test_split_validation_and_atomic_failure(root, monkeypatch):
    co = Company(root)
    _setup(co)
    cart = {'lines': [{'item': 'Mainline Clearing'}]}
    total = co.run('pos sale preview', cart)['total']['amount']
    def request(parts):
        return dict(cart, ticket='bad-split', expected_total=total, tenders=parts)
    for parts in [
        [{'payment_method': 'Cash', 'amount': '1'}, {'payment_method': 'Visa', 'amount': '1'}],
        [{'payment_method': 'Cash', 'amount': '10'}, {'payment_method': 'Visa', 'amount': total}],
        [{'payment_method': 'Visa', 'amount': '1000'}, {'payment_method': 'Check', 'amount': '1000'}],
        [{'payment_method': 'Cash', 'amount': '1000'}, {'payment_method': 'Cash', 'amount': '1000'}],
        [{'payment_method': 'Cash'}, {'payment_method': 'Visa', 'amount': '1'}],
    ]:
        assert co.fails('pos sale complete', request(parts)).code == 'E_VALIDATION'
    before = _reads(co)
    from bookflow.company import payments
    from bookflow.core.errors import BookflowError
    real = payments.apply
    count = 0
    def fail_second(*args, **kwargs):
        nonlocal count
        count += 1
        if count == 2:
            raise BookflowError('E_VALIDATION', message='simulated later payment failure')
        return real(*args, **kwargs)
    monkeypatch.setattr(payments, 'apply', fail_second)
    with pytest.raises(BookflowError):
        split(co, 'atomic-split')
    assert count == 2
    assert _reads(co) == before
    assert co.fails('pos receipt show', {'sale': 'atomic-split'}).code == 'E_RECORD_NOT_FOUND'


def test_plain_clerk_can_split_but_not_read_invoice(hosted):
    from fastapi.testclient import TestClient
    from bookflow.hub import pos_presets
    from tests.test_item_pictures_review import _membership_version
    from tests.test_pos_layer import H, REASON as HEADERS, _password, _pos
    cid = hosted.company_id
    _pos(hosted, True)
    preset = pos_presets.preset('pos-clerk')
    user = hosted.ok('user.add', {'username': 'split-clerk', 'password': _password('split-clerk'),
        'company': cid, 'role': 'readonly'}, headers=HEADERS)['user_id']
    hosted.ok('membership.grant', {'user': user, 'company': cid, 'role': 'readonly',
        'grants': preset['grants'], 'denies': preset['denies'],
        'expected_version': _membership_version(hosted, user)}, headers=HEADERS)
    with TestClient(hosted.handle.app) as till:
        assert till.post('/login', json={'username': 'split-clerk', 'password': _password('split-clerk')}).status_code == 200
        cart = {'lines': [{'item': 'Mainline Clearing'}]}
        def call(name, body):
            return till.post(f'/companies/{cid}/commands/{name}', headers=H, json=body)
        shown = call('pos.sale.preview', cart).json()
        sale = call('pos.sale.complete', dict(cart, ticket='clerk-split', expected_total=shown['total']['amount'],
            tenders=[{'payment_method': 'Visa', 'amount': '10'}, {'payment_method': 'Cash',
                'amount': str(Decimal(shown['total']['amount'])-5)}]))
        assert sale.status_code == 200, sale.text
        assert sale.json()['change']['amount'] == '5.00'
        assert call('invoice.show', {'invoice': sale.json()['sales_receipt']['id']}).status_code == 403
        assert call('pos.receipt.show', {'sale': sale.json()['id']}).status_code == 200


def test_split_printed_number_disambiguates_receipt_sequence(root):
    from io import BytesIO
    from pypdf import PdfReader
    from bookflow.documents.pos_receipt import pdf
    co = Company(root)
    _setup(co)
    sold, _ = split(co, 'numbered-split')
    cart = {'lines': [{'item': 'Mainline Clearing'}]}
    total = co.run('pos sale preview', cart)['total']['amount']
    single = co.run('pos sale complete', dict(cart, ticket='numbered-single', expected_total=total,
        tender={'payment_method': 'Cash'}))
    co.run('sales-receipt update', {'sales_receipt': single['sales_receipt']['id'], 'expected_version': 1,
        'number': sold['sales_receipt']['name']})
    printed = co.run('pos receipt show', {'sale': sold['id']})
    assert printed['number'] == 'INV:' + sold['sales_receipt']['name']
    assert co.run('pos receipt show', {'sale': printed['number']})['sale']['id'] == sold['id']
    assert co.run('pos receipt show', {'sale': sold['sales_receipt']['name']})['sale']['id'] == single['id']
    assert co.run('pos refund lines', {'sale': printed['number']})['sale_id'] == sold['id']
    text = PdfReader(BytesIO(pdf(printed))).pages[0].extract_text()
    assert printed['number'] in text and 'Visa' in text and 'Cash' in text and 'Cash change 5.00' in text


def test_split_through_python_cli_http_mcp(root, tmp_path):
    import anyio
    from tests.mcp_matrix_support import Matrix
    async def witness():
        matrix = Matrix()
        try:
            await matrix.open(root, tmp_path)
            results = {}
            for surface in matrix.documents:
                async def call(name, body):
                    return await matrix.call(surface, name, body)
                info = await call('company show', {})
                await call('company update', {'pos_enabled': True, 'expected_version': info['info_version']})
                cart = {'lines': [{'item': 'Mainline Clearing'}]}
                total = (await call('pos sale preview', cart))['total']['amount']
                body = dict(cart, ticket='surface-split', expected_total=total, tenders=[
                    {'payment_method': 'Visa', 'amount': '10', 'reference': '4242'},
                    {'payment_method': 'Cash', 'amount': str(Decimal(total)-5)}])
                sale = await call('pos sale complete', body)
                replay = await call('pos sale complete', body)
                receipt = await call('pos receipt show', {'sale': sale['id']})
                results[surface] = (sale['document_type'], sale['total'], sale['change'],
                    [(t['payment_method']['name'], t['amount'], t['applied'], t['change']) for t in sale['tenders']],
                    replay['id'] == sale['id'] and replay['replayed'], receipt['number'])
            assert all(value == results['python'] for value in results.values())
            assert results['python'][0] == 'invoice' and results['python'][4]
        finally:
            await matrix.close()
    anyio.run(witness)
