"""Cost deferrals preserve posted dimensions across grouped inventory recosts."""
from tests.test_bill_item_lines import books, _inventory_part


def test_recost_job_class_and_period_views_remain_consistent(books):
    run = books['run']
    run('company update', {'use_classes': True}, reason='Track class reporting')
    item = _inventory_part(books)
    classes = [run('class create', {'name': name})['id'] for name in ('East', 'West')]
    run('bill post', dict(vendor=books['vendor'], date='2017-01-01',
        items=[dict(item=item, quantity='2', unit_cost='0.03')]), reason='Stock')
    sales = [run('invoice post', dict(customer=books['customer'], date='2017-01-02',
        lines=[dict(item=item, quantity='1', unit_price='2', class_id=class_id)]), reason='Sale')
        for class_id in classes]
    run('bill post', dict(vendor=books['vendor'], date='2016-12-31',
        items=[dict(item=item, quantity='2', unit_cost='0.05')]), reason='Revalue stock')

    def statement(verb, basis='cash', first='2017-01-01', last='2017-02-28'):
        return run('report '+verb, dict(date_from=first, date_to=last, basis=basis, limit=200))

    for verb in ('profit-and-loss-by-job', 'profit-and-loss-by-class'):
        result = statement(verb)
        assert result['totals']['cost_of_goods_sold']['minor_units'] == 0
        assert all(column['totals']['cost_of_goods_sold']['minor_units'] == 0 for column in result['columns'])
    for index, sale in enumerate(sales):
        run('payment receive', dict(customer=books['customer'], date='2017-02-01',
            amount='1', operation_key='half-'+str(index), deposit_to=books['bank'],
            payment_method=books['methods']['Cash'], applications={'mode':'inline', 'items':[
                dict(invoice=sale['id'], amount='1', expected_version=1)]}), reason='Half paid')
    for verb in ('profit-and-loss', 'profit-and-loss-by-job', 'profit-and-loss-by-class'):
        assert statement(verb)['totals']['cost_of_goods_sold']['minor_units'] == 4
        assert statement(verb, first='2017-01-01', last='2017-01-31')['totals']['cost_of_goods_sold']['minor_units'] == 0
        assert statement(verb, first='2017-02-01')['totals']['cost_of_goods_sold']['minor_units'] == 4
    for index, sale in enumerate(sales):
        run('payment receive', dict(customer=books['customer'], date='2017-02-02',
            amount='1', operation_key='rest-'+str(index), deposit_to=books['bank'],
            payment_method=books['methods']['Cash'], applications={'mode':'inline', 'items':[
                dict(invoice=sale['id'], amount='1', expected_version=2)]}), reason='Finish payment')
    for verb in ('profit-and-loss-by-job', 'profit-and-loss-by-class'):
        cash, accrual = statement(verb), statement(verb, 'accrual')
        assert cash['totals'] == accrual['totals']
        assert cash['columns'] == accrual['columns']
