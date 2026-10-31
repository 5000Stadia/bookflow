"""Printed POS facts preserve the posted sale and grow with the complete cart."""
from io import BytesIO
import pytest
from pypdf import PdfReader
from tests.test_pos_tickets import co  # noqa: F401
from tests.test_row3_host import hosted  # noqa: F401
from bookflow.documents.pos_receipt import pdf, barcode_svg


def sale(co, ticket='receipt', count=1):
    cart = {'lines': [{'item': 'Mainline Clearing'} for _ in range(count)]}
    total = co.run('pos sale preview', cart)['total']['amount']
    return co.run('pos sale complete', dict(cart, ticket=ticket, expected_total=total, tender={'payment_method': 'Cash'}))


def test_receipt_original_totals_survive_reprint_and_void(co):
    sold = sale(co)
    first = co.run('pos receipt show', {'sale': sold['id']})
    copy = co.run('pos receipt show', {'sale': sold['sales_receipt']['name'], 'reprint': True})
    assert copy['reprint'] and not first['reprint']
    assert copy['lines'] == first['lines'] and copy['total'] == first['total'] == sold['total']['amount']
    assert first['customer'] is None and first['clerk']
    co.run('pos sale void', {'sale': sold['id'], 'expected_version': 1})
    void = co.run('pos receipt show', {'sale': sold['id'], 'reprint': True})
    assert void['status'] == 'voided' and void['total'] == first['total'] and void['lines'] == first['lines']
    text = PdfReader(BytesIO(pdf(void))).pages[0].extract_text()
    assert 'VOID' in text and 'Reprint' in text and first['total'] in text


def test_pdf_content_height_and_totals_follow_all_sixty_lines(co):
    one = co.run('pos receipt show', {'sale': sale(co, 'one')['id']})
    many = co.run('pos receipt show', {'sale': sale(co, 'many', 60)['id']})
    small, large = (PdfReader(BytesIO(pdf(r))) for r in (one, many))
    assert len(small.pages) == len(large.pages) == 1
    assert float(large.pages[0].mediabox.height) > float(small.pages[0].mediabox.height) * 3
    assert abs(float(large.pages[0].mediabox.width) - 80 * 72 / 25.4) < .01
    text = large.pages[0].extract_text()
    assert text.count('Mainline Clearing') == 60
    assert text.rfind('Mainline Clearing') < text.index('TOTAL')
    assert many['total'] in text
    assert '<svg' in barcode_svg(many['number'])


def test_58mm_long_names_and_amounts_survive(co):
    shown = co.run('pos receipt show', {'sale': sale(co)['id']})
    shown['width'] = 58
    shown['lines'][0]['name'] = 'Long industrial sealant name ' * 12
    doc = PdfReader(BytesIO(pdf(shown)))
    assert abs(float(doc.pages[0].mediabox.width) - 58 * 72 / 25.4) < .01
    text = doc.pages[0].extract_text()
    assert text.count('industrial') == 12
    assert shown['lines'][0]['amount'] in text


def test_receipt_uses_captured_names_prices_and_tax_facts(co):
    tax_code = next(c['id'] for c in co.run('sales-tax-code list')['items'] if c['code'] == 'Tax')
    customer = co.run('customer create', {'name': 'Receipt shopper', 'sales_tax_code_id': tax_code})
    cart = {'customer': customer['id'], 'lines': [{'item': 'Pipe Sealant', 'quantity': '2'}]}
    total = co.run('pos sale preview', cart)['total']['amount']
    sold = co.run('pos sale complete', dict(cart, ticket='captured', expected_total=total, tender={'payment_method': 'Cash'}))
    first = co.run('pos receipt show', {'sale': sold['id']})
    item = co.run('item show', {'item': 'Pipe Sealant'})
    co.run('item update', {'item': item['id'], 'expected_version': item['version'], 'name': 'Renamed sealant', 'price': '99.99'})
    second = co.run('pos receipt show', {'sale': sold['id'], 'reprint': True})
    assert first['customer'] and second['customer'] == first['customer']
    assert first['taxes'] and all(t['rate'] for t in first['taxes'])
    assert second['lines'] == first['lines'] and second['taxes'] == first['taxes']
    assert second['total'] == total


def test_pdf_unicode_names_are_preserved(co):
    shown = co.run('pos receipt show', {'sale': sale(co)['id']})
    shown['lines'][0]['name'] = 'Café Ωμέγα Журнал 客户'
    text = PdfReader(BytesIO(pdf(shown))).pages[0].extract_text()
    assert 'Café Ωμέγα Журнал 客户' in text


def test_discount_and_fine_price_are_the_posted_facts(co):
    item = co.run('item show', {'item': 'Mainline Clearing'})
    co.run('item update', {'item': item['id'], 'expected_version': item['version'], 'price': '0.0125'})
    cart = {'lines': [{'item': item['id'], 'quantity': '1000'}, {'item': 'Loyalty Discount'}]}
    total = co.run('pos sale preview', cart)['total']['amount']
    sold = co.run('pos sale complete', dict(cart, ticket='discount-fine', expected_total=total, tender={'payment_method':'Cash'}))
    shown = co.run('pos receipt show', {'sale': sold['id']})
    assert shown['lines'][0]['price'] == '0.0125'
    assert shown['lines'][0]['amount'] == '12.50'
    assert shown['discounts'].startswith('-') and shown['subtotal'] == '12.50'
    assert shown['total'] == total


def test_original_receipt_number_survives_renumber_and_selects_void(co):
    sold = sale(co)
    original = sold['sales_receipt']['name']
    changed = co.run('sales-receipt update', {'sales_receipt': sold['sales_receipt']['id'],
                    'expected_version': 1, 'number': 'Changed-receipt-客户'})
    shown = co.run('pos receipt show', {'sale': original, 'reprint': True})
    assert shown['number'] == original
    assert shown['total'] == sold['total']['amount']
    void = co.run('pos sale void', {'sale': original, 'expected_version': changed['version']})
    assert void['status'] == 'voided'


def test_unicode_receipt_number_is_not_silently_encoded_as_different_number(co):
    shown = co.run('pos receipt show', {'sale': sale(co)['id']})
    shown['number'] = '客户-1'
    assert barcode_svg(shown['number']) == ''
    assert shown['number'] in PdfReader(BytesIO(pdf(shown))).pages[0].extract_text()


def test_plain_clerk_can_read_print_and_reprint_but_not_general_receipts(hosted):
    from fastapi.testclient import TestClient
    from bookflow.hub import pos_presets
    from tests.test_item_pictures_review import _membership_version
    from tests.test_pos_layer import H, REASON, _password, _pos
    cid = hosted.company_id
    _pos(hosted, True)
    cart = {'lines':[{'item':'Pipe Sealant'}]}
    total = hosted.ok('pos.sale.preview', cart, company=cid)['total']['amount']
    sold = hosted.ok('pos.sale.complete', dict(cart, ticket='clerk-receipt', expected_total=total,
                     tender={'payment_method':'Cash'}), company=cid, headers=REASON)
    settings = pos_presets.preset('pos-clerk')
    user = hosted.ok('user.add', {'username':'receipt-clerk', 'password':_password('receipt-clerk'),
                                'company':cid, 'role':'readonly'}, headers=REASON)['user_id']
    hosted.ok('membership.grant', {'user':user, 'company':cid, 'role':'readonly', 'grants':settings['grants'],
                  'denies':settings['denies'], 'expected_version':_membership_version(hosted,user)}, headers=REASON)
    with TestClient(hosted.handle.app) as client:
        assert client.post('/login', json={'username':'receipt-clerk', 'password':_password('receipt-clerk')}).status_code == 200
        command = client.post(f'/companies/{cid}/commands/pos.receipt.show', headers=H, json={'sale':sold['id'],'reprint':True})
        assert command.status_code == 200 and command.json()['total'] == total
        assert 'account_id' not in command.text and 'cost' not in command.text
        page = client.get(f'/c/{cid}/pos/receipt/{sold["id"]}?reprint=true')
        assert page.status_code == 200 and 'Reprint' in page.text
        printed = client.get(f'/c/{cid}/pos/receipt/{sold["id"]}.pdf?reprint=true')
        assert printed.status_code == 200 and printed.content.startswith(b'%PDF')
        refused = client.post(f'/companies/{cid}/commands/sales-receipt.show', headers=H,
                             json={'sales_receipt':sold['sales_receipt']['id']})
        assert refused.status_code == 403
