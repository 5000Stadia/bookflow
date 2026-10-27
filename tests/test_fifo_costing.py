"""FIFO costing by replay (R104 increment 7, design/advanced-inventory.md §3): the walk itself.

Every figure below is worked by hand in the comments, in cents, with each issue taking whole layers
oldest first and round-half-even of W*u/U from the layer it splits, and every spread by cumulative
endpoint shares (floor of the running total).
"""
import random

import pytest

from bookflow.company.inventory_costing import StockRefusal, replay

MICRO = 10 ** 6
FIFO = (('2027-01-01', 'fifo'),)


def row(identity, kind, units=0, value=0, date='2027-01-02', sequence=1, *, returns=None, landed_on=None,
        corrects=None, reverses=None, filled_by=None, position=None):
    return dict(id=identity, kind=kind, quantity_microunits=units * MICRO, value_minor_units=value,
                effective_date=date, sequence=sequence, returns_movement_id=returns, landed_on_movement_id=landed_on,
                corrects_movement_id=corrects, reverses_movement_id=reverses, filled_by_movement_id=filled_by,
                count_position=position, currency='USD')


# Posted as the walk without the backdated purchase decided it.
POSTED = [
    row('R1', 'receipt', 10, 10000, '2027-01-02', 1),
    row('R2', 'receipt', 10, 12000, '2027-01-05', 2),
    row('R3', 'receipt', 10, 15000, '2027-01-10', 3),
    row('S1', 'issue', -15, -16000, '2027-01-12', 4),           # R1 10 = 100.00, R2 5 of 120.00 = 60.00
    row('C1', 'receipt', 3, 3200, '2027-01-13', 5, returns='S1'),  # floor(160.00 * 3/15) = 32.00
    row('V1', 'vendor_return', -4, -6000, '2027-01-14', 6, returns='R3'),
    row('V2', 'vendor_return', -2, -2200, '2027-01-15', 7),
    row('L1', 'landed', 0, 2000, '2027-01-16', 8, landed_on='R2'),
    # The landed 20.00 on R2 makes S1 170.00 and C1 34.00 from 16 January: forward true-ups.
    row('F1', 'recost', 0, -1000, '2027-01-16', 9, corrects='S1', filled_by='L1'),
    row('F2', 'recost', 0, 200, '2027-01-16', 10, corrects='C1', filled_by='L1'),
    row('K1', 'issue', -2, -2900, '2027-01-20', 11, position='count'),
    row('S2', 'issue', -6, -9200, '2027-01-22', 12),
]


def test_the_hand_worked_example_without_the_backdated_purchase_owes_nothing():
    # R1 10/100.00, R2 10/140.00 (with its landed 20.00), R3 10/150.00.
    # S1 15: R1 100.00 + R2 5 of 140.00 = 70.00 -> 170.00 (160.00 at its date, 10.00 forward).
    # C1 3 of S1: floor(170.00*3/15) = 34.00, a new layer.
    # V1 4 naming R3: 4 of 150.00 = 60.00, credited 60.00: no difference.
    # V2 2 naming none: oldest, R2 2 of 5/70.00 = 28.00, credited 22.00: 6.00 stays, spread over R2 3, R3 6,
    #   C1 3 by units -> 1.50, 3.00, 1.50: R2 43.50, R3 93.00, C1 35.50.
    # K1 2: R2 2 of 3/43.50 = 29.00. S2 6: R2 1/14.50 + R3 5 of 6/93.00 = 77.50 -> 92.00.
    state = replay(POSTED, FIFO)
    assert state.corrections == ()
    assert (state.quantity_microunits, state.value_minor_units) == (4 * MICRO, 5100)
    assert [(layer.source, layer.quantity_microunits // MICRO, layer.value_minor_units) for layer in state.layers] == [
        ('R3', 1, 1550), ('C1', 3, 3550)]
    assert state.method == 'fifo'


def test_the_hand_worked_example_with_a_backdated_purchase_matches_to_the_cent():
    # B0 5 at 8.00 dated 3 January, between R1 and R2.
    # S1 15: R1 100.00 + B0 40.00 = 140.00, all before the landed cost, so its forward true-up falls to 0.
    # C1: floor(140.00*3/15) = 28.00.  V1: 60.00 of R3, no difference.
    # V2: R2 2 of 10/140.00 = 28.00, credited 22.00: 6.00 over R2 8, R3 6, C1 3 (17 units):
    #   floor(600*8/17) = 282, floor(600*14/17) = 494 -> 212, 600 - 494 = 106: R2 114.82, R3 92.12, C1 29.06.
    # K1 2: R2 2 of 8/114.82 = 28.705 -> 28.70 (half even).  S2 6: R2's last 6 = 86.12.
    state = replay(POSTED + [row('B0', 'receipt', 5, 4000, '2027-01-03', 13)], FIFO)
    assert [(c.effective_date, c.target_movement['id'], c.filled_by and c.filled_by['id'], c.delta_minor_units)
            for c in state.corrections] == [
        ('2027-01-12', 'S1', None, 2000),
        ('2027-01-13', 'C1', None, -400),
        ('2027-01-16', 'S1', 'L1', 1000),
        ('2027-01-16', 'C1', 'L1', -200),
        ('2027-01-20', 'K1', None, 30),
        ('2027-01-22', 'S2', None, 588),
    ]
    assert (state.quantity_microunits, state.value_minor_units) == (9 * MICRO, 12118)
    assert [(layer.source, layer.quantity_microunits // MICRO, layer.value_minor_units) for layer in state.layers] == [
        ('R3', 6, 9212), ('C1', 3, 2906)]


def test_the_same_rows_under_average_are_the_running_average():
    state = replay(POSTED + [row('B0', 'receipt', 5, 4000, '2027-01-03', 13)])
    assert state.method == 'average' and len(state.layers) == 1
    assert state.layers[0].value_minor_units == state.value_minor_units


def test_a_vendor_return_naming_its_receipt_takes_that_layer_and_one_naming_none_the_oldest():
    rows = [row('A', 'receipt', 5, 5000, '2027-01-02', 1), row('B', 'receipt', 5, 10000, '2027-01-03', 2)]
    named = replay(rows + [row('V', 'vendor_return', -2, -4000, '2027-01-04', 3, returns='B')], FIFO)
    assert [(l.source, l.value_minor_units) for l in named.layers] == [('A', 5000), ('B', 6000)]
    oldest = replay(rows + [row('V', 'vendor_return', -2, -2000, '2027-01-04', 3)], FIFO)
    assert [(l.source, l.value_minor_units) for l in oldest.layers] == [('A', 3000), ('B', 10000)]
    # Credited 30.00 for 20.00 of A: 10.00 leaves more than those units were worth, taken off what stays.
    over = replay(rows + [row('V', 'vendor_return', -2, -3000, '2027-01-04', 3)], FIFO)
    assert over.value_minor_units == 12000 and sum(l.value_minor_units for l in over.layers) == 12000
    # A named receipt with too few units left takes the rest from the oldest.
    spill = replay(rows + [row('V', 'vendor_return', -7, -11000, '2027-01-04', 3, returns='A')], FIFO)
    assert [(l.source, l.quantity_microunits // MICRO) for l in spill.layers] == [('B', 3)]


def test_emptying_the_stock_by_vendor_return_must_credit_exactly_its_worth():
    rows = [row('A', 'receipt', 5, 5000, '2027-01-02', 1)]
    with pytest.raises(StockRefusal) as refusal:
        replay(rows + [row('V', 'vendor_return', -5, -4000, '2027-01-04', 2)], FIFO)
    assert refusal.value.reason == 'residual_value'


def test_a_switch_revalues_nothing_and_corrects_only_dates_on_or_after_it():
    rows = [
        row('A', 'receipt', 10, 10000, '2026-12-01', 1),
        row('X', 'issue', -2, -2400, '2026-12-20', 2),     # average of A and B
        row('B', 'receipt', 10, 14000, '2026-12-10', 3),
        row('Y', 'issue', -4, -4800, '2027-01-05', 4),
    ]
    average = replay(rows)
    assert average.corrections == ()
    switched = replay(rows, (('2027-01-01', 'fifo'),))
    # On 1 January 18 units worth 216.00 become one opening layer at 12.00: Y takes 48.00 either way.
    assert switched.corrections == ()
    assert switched.layers[0].source is None and switched.layers[0].effective_date == '2027-01-01'
    after = rows + [row('C', 'receipt', 10, 20000, '2027-01-03', 5)]
    a, f = replay(after), replay(after, (('2027-01-01', 'fifo'),))
    # Average: Y takes 4 of 28/416.00 = 59.43; FIFO: 4 of the opening layer = 48.00. Nothing before the switch moves.
    assert [(c.target_movement['id'], c.delta_minor_units) for c in a.corrections] == [('Y', -1143)]
    assert [(c.target_movement['id'], c.delta_minor_units) for c in f.corrections] == []


def test_an_issue_on_the_switch_date_is_costed_by_the_new_method():
    rows = [row('A', 'receipt', 1, 1000, '2026-12-01', 1), row('B', 'receipt', 1, 3000, '2026-12-02', 2),
            row('Y', 'issue', -1, -2000, '2027-01-01', 3)]
    # The opening layer is the average, so the switch date's issue takes 20.00 either way; a receipt that day
    # placed after it does not reach it.
    assert replay(rows, FIFO).corrections == ()
    later = rows + [row('C', 'receipt', 2, 10000, '2027-01-02', 4), row('Z', 'issue', -2, -6000, '2027-01-03', 5)]
    fifo = replay(later, FIFO)
    # Z takes the opening layer's last unit (20.00) and one of C (50.00) = 70.00.
    assert [(c.target_movement['id'], c.delta_minor_units) for c in fifo.corrections] == [('Z', -1000)]


def test_a_switch_while_a_shortfall_is_open_is_trued_up_by_the_receipt_that_fills_it():
    rows = [row('A', 'receipt', 2, 2000, '2026-12-01', 1),
            row('S', 'issue', -5, -5000, '2026-12-05', 2),       # 2 on hand + 3 provisional at 10.00
            row('R', 'receipt', 10, 15000, '2027-01-03', 3),
            row('T', 'issue', -4, -6000, '2027-01-04', 4)]
    state = replay(rows, FIFO)
    # R fills S's 3 units at 15.00: true-up -15.00 dated at R; 7 left at 105.00; T takes 4 of them = 60.00.
    assert [(c.target_movement['id'], c.filled_by and c.filled_by['id'], c.effective_date, c.delta_minor_units)
            for c in state.corrections] == [('S', 'R', '2027-01-03', -1500)]
    assert [(l.source, l.quantity_microunits // MICRO, l.value_minor_units) for l in state.layers] == [('R', 3, 4500)]


def test_switching_back_merges_the_layers_and_continues_at_the_average():
    rows = [row('A', 'receipt', 10, 10000, '2027-01-02', 1), row('B', 'receipt', 10, 20000, '2027-01-03', 2),
            row('X', 'issue', -5, -5000, '2027-01-04', 3),          # FIFO: 5 of A
            row('Y', 'issue', -5, -8333, '2027-02-02', 4)]          # average of 15/250.00: 83.33
    timeline = (('2027-01-01', 'fifo'), ('2027-02-01', 'average'))
    state = replay(rows, timeline)
    assert state.corrections == () and state.method == 'average'
    assert (state.quantity_microunits, state.value_minor_units) == (10 * MICRO, 16667)


def test_a_value_adjustment_spreads_over_the_layers_by_units():
    rows = [row('A', 'receipt', 1, 1000, '2027-01-02', 1), row('B', 'receipt', 2, 2000, '2027-01-03', 2),
            row('W', 'value', 0, 100, '2027-01-04', 3)]
    state = replay(rows, FIFO)
    # floor(100*1/3) = 33 to A, 100 - 33 = 67 to B.
    assert [l.value_minor_units for l in state.layers] == [1033, 2067]


def test_a_found_quantity_is_a_layer_worth_the_average_over_all_layers():
    rows = [row('A', 'receipt', 1, 1000, '2027-01-02', 1), row('B', 'receipt', 1, 3000, '2027-01-03', 2),
            row('F', 'found', 2, 4000, '2027-01-04', 3)]
    state = replay(rows, FIFO)
    assert state.targets['F'] == 4000 and state.layers[-1].source == 'F'


def test_a_shortfall_under_fifo_is_costed_at_the_newest_layer():
    rows = [row('A', 'receipt', 1, 1000, '2027-01-02', 1), row('B', 'receipt', 1, 3000, '2027-01-03', 2),
            row('S', 'issue', -3, -7000, '2027-01-04', 3)]
    state = replay(rows, FIFO)
    # 40.00 on hand, plus one unit short at the newest layer's 30.00.
    assert state.targets['S'] == -7000 and state.shortfalls[0].provisional_minor_units == 3000


def _random_rows(rng, cost=None):
    rows, sequence, issues, receipts = [], 0, [], []
    quantity = 0
    for day in range(1, 40):
        date = f'2027-01-{day:02d}' if day < 32 else f'2027-02-{day - 31:02d}'
        for _ in range(rng.randint(0, 2)):
            sequence += 1
            choice = rng.random()
            if choice < 0.4 or quantity <= 0:
                units = rng.randint(1, 9)
                unit_cost = cost if cost is not None else rng.randint(100, 900)
                rows.append(row(f'M{sequence}', 'receipt', units, units * unit_cost, date, sequence))
                receipts.append(f'M{sequence}')
                quantity += units
            elif choice < 0.75:
                units = rng.randint(1, quantity + 1)
                rows.append(row(f'M{sequence}', 'issue', -units, 0, date, sequence))
                issues.append((f'M{sequence}', units))
                quantity -= units
            elif choice < 0.85 and issues:
                issue, units = rng.choice(issues)
                rows.append(row(f'M{sequence}', 'receipt', 1, 0, date, sequence, returns=issue))
                quantity += 1
            elif choice < 0.95 and quantity > 1:
                unit_cost = cost if cost is not None else rng.randint(100, 900)
                rows.append(row(f'M{sequence}', 'vendor_return', -1, -unit_cost, date, sequence,
                                returns=rng.choice(receipts)))
                quantity -= 1
            else:
                rows.append(row(f'M{sequence}', 'found', 1, 0, date, sequence))
                quantity += 1
    return rows


def test_fifo_equals_average_when_every_receipt_has_the_same_unit_cost():
    for seed in range(60):
        rng = random.Random(seed)
        rows = _random_rows(rng, cost=rng.randint(100, 900))
        try:
            average = replay(rows)
        except StockRefusal:
            continue
        fifo = replay(rows, (('2027-01-01', 'fifo'),))
        assert (fifo.quantity_microunits, fifo.value_minor_units) == (average.quantity_microunits, average.value_minor_units)
        assert fifo.targets == average.targets, seed


def test_layers_always_sum_to_the_replay_value_and_quantity():
    for seed in range(60):
        rng = random.Random(seed)
        rows = _random_rows(rng)
        switches = sorted({(f'2027-01-{rng.randint(1, 31):02d}', rng.choice(['fifo', 'average'])) for _ in range(3)})
        try:
            state = replay(rows, switches)
        except StockRefusal:
            continue
        if state.quantity_microunits > 0:
            assert sum(l.quantity_microunits for l in state.layers) == state.quantity_microunits, seed
            assert sum(l.value_minor_units for l in state.layers) == state.value_minor_units, seed
        else:
            assert state.layers == ()


def test_a_shortfall_under_fifo_names_its_basis_newest_layer():
    from bookflow.company.inventory_costing import shortfall_warning
    rows = [row('A', 'receipt', 1, 1000, '2027-01-02', 1), row('S', 'issue', -2, -2000, '2027-01-04', 2)]
    fifo, average = replay(rows, FIFO).shortfalls[0], replay(rows).shortfalls[0]
    assert (fifo.basis, average.basis) == ('newest_layer', 'average')
    assert "newest layer's unit cost" in shortfall_warning('Widget', fifo)
    assert replay(POSTED + [row('B0', 'receipt', 5, 4000, '2027-01-03', 13)], FIFO).corrections[0].method == 'fifo'
