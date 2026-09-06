"""Independent rational witnesses for progress billing; also runnable with unittest."""

from fractions import Fraction
from itertools import combinations
import unittest

from bookflow.company.billing_math import (
    AllocationRangeError,
    FragmentationError,
    allocate,
    canonical_spans,
    coordinate_hex,
    coordinate_int,
    denominator,
    exact_microunits,
    format_fraction,
    fraction,
    free_spans,
    portion,
    recommended_net,
    spans_available,
)


def oracle_net(total, spans, scale):
    return sum(round(Fraction(total * b, scale)) - round(Fraction(total * a, scale))
               for a, b in spans)


def points(spans):
    return {point for a, b in spans for point in range(a, b)}


class ProgressBillingMathTests(unittest.TestCase):
    def test_denominator_is_the_least_common_multiple(self):
        # Prime-factor witnesses independent of math.lcm.
        for q, n, expected in ((1, 0, 100_000_000), (3, 7, 2_100_000_000),
                               (25, 100, 100_000_000), (9, 27, 2_700_000_000)):
            with self.subTest(q=q, n=n):
                self.assertEqual(denominator(q, n), expected)

    def test_endpoint_ties_and_full_coverage(self):
        # Four identical scope lengths have the deliberately unequal cents 1/1/0/1.
        self.assertEqual([portion(3, a, a + 2, 8) for a in range(0, 8, 2)],
                         [1, 1, 0, 1])
        self.assertEqual([portion(4, a, a + 1, 8) for a in range(8)],
                         [0, 1, 1, 0, 0, 1, 1, 0])
        for total in range(10):
            for scale in range(1, 25):
                for a in range(scale + 1):
                    for b in range(a, scale + 1):
                        with self.subTest(total=total, scale=scale, a=a, b=b):
                            self.assertEqual(portion(total, a, b, scale),
                                             oracle_net(total, [(a, b)], scale))
                self.assertEqual(sum(portion(total, a, a + 1, scale)
                                     for a in range(scale)), total)

    def test_one_microunit_source_can_bill_forty_of_one_hundred_cents(self):
        scale = denominator(1, 100)
        spans = allocate([(0, scale)], denominator=scale, source_net=100, net_amount=40)
        self.assertEqual(spans, ((0, 40_000_000),))
        self.assertEqual(oracle_net(100, spans, scale), 40)
        quantity = fraction(1, spans, scale)
        self.assertEqual(quantity, Fraction(1, 2_500_000))
        self.assertEqual(format_fraction(quantity), "1/2500000")
        self.assertIsNone(exact_microunits(quantity))

    def test_nonterminating_quantity_preserves_the_source_on_completion(self):
        scale = denominator(1_000_000, 3)
        first = allocate([(0, scale)], denominator=scale, source_net=3, net_amount=1)
        second = allocate(free_spans(first, scale), denominator=scale,
                          source_net=3, net_amount=2)
        self.assertEqual(fraction(1_000_000, first, scale), Fraction(1, 3))
        self.assertEqual(format_fraction(fraction(1_000_000, second, scale)), "2/3")
        self.assertEqual(fraction(1_000_000, first, scale) +
                         fraction(1_000_000, second, scale), 1)
        self.assertEqual(oracle_net(3, first + second, scale), 3)

    def test_maximum_signed64_sources_and_unsigned160_storage(self):
        maximum = (1 << 63) - 1
        scale = denominator(maximum, maximum - 1)
        self.assertEqual(scale % maximum, 0)
        self.assertEqual(scale % (maximum - 1), 0)
        self.assertEqual(scale % 100_000_000, 0)
        self.assertLessEqual(scale.bit_length(), 160)
        self.assertEqual(coordinate_int(coordinate_hex(scale)), scale)
        first = allocate([(0, scale)], denominator=scale, source_net=maximum - 1,
                         net_amount=maximum - 2)
        rest = allocate(free_spans(first, scale), denominator=scale,
                        source_net=maximum - 1, net_amount=1)
        self.assertEqual(oracle_net(maximum - 1, first, scale), maximum - 2)
        self.assertEqual(oracle_net(maximum - 1, rest, scale), 1)
        self.assertEqual(fraction(maximum, first, scale) + fraction(maximum, rest, scale),
                         Fraction(maximum, 1_000_000))
        self.assertEqual(portion(maximum, 0, scale, scale), maximum)

    def test_percent_is_original_scope_with_millionths_of_one_percent(self):
        q, net = 7_000_003, 103
        scale = denominator(q, net)
        for percent_micro in (1, 12_345_678, 25_000_000, 100_000_000):
            length = percent_micro * scale // 100_000_000
            spans = allocate([(0, scale)], denominator=scale, source_net=net, length=length)
            self.assertEqual(fraction(q, spans, scale) / Fraction(q, 1_000_000),
                             Fraction(percent_micro, 100_000_000))
        first = allocate([(0, scale)], denominator=scale, source_net=net, length=scale // 4)
        second = allocate(free_spans(first, scale), denominator=scale,
                          source_net=net, length=scale // 4)
        self.assertEqual(second, ((scale // 4, scale // 2),))

    def test_quantity_length_crosses_occupied_gaps(self):
        free = [(0, 2), (4, 5), (8, 12)]
        for length in range(1, 8):
            selected = allocate(iter(free), denominator=12, source_net=3, length=length)
            self.assertEqual(sorted(points(selected)), sorted(points(free))[:length])
        with self.assertRaises(AllocationRangeError):
            allocate(free, denominator=12, source_net=3, length=8)

    def test_net_inverse_across_all_small_free_interval_pairs(self):
        # Compare capacity/selection to rational rounding and discrete free points.
        # A partial endpoint must land at an exact integer cumulative source net;
        # this distinguishes the specified inverse from a first-rounded-cent search.
        for net in range(1, 7):
            scale = net * 4
            for a, b, c, d in combinations(range(scale + 1), 4):
                free = [(a, b), (c, d)]
                positive = [span for span in free if oracle_net(net, [span], scale)]
                capacity = oracle_net(net, free, scale)
                for amount in range(1, capacity + 1):
                    selected = allocate(iter(free), denominator=scale,
                                        source_net=net, net_amount=amount)
                    self.assertEqual(oracle_net(net, selected, scale), amount)
                    self.assertTrue(points(selected) <= points(free))
                    self.assertEqual([a for a, _ in selected],
                                     [a for a, _ in positive[:len(selected)]])
                    self.assertEqual(selected[:-1], tuple(positive[:len(selected) - 1]))
                    if selected[-1][1] != positive[len(selected) - 1][1]:
                        self.assertEqual(Fraction(net * selected[-1][1], scale).denominator, 1)
                        self.assertLess(selected[-1][1], positive[len(selected) - 1][1])
                with self.assertRaises(AllocationRangeError):
                    allocate(free, denominator=scale, source_net=net, net_amount=capacity + 1)

    def test_net_exact_capacity_consumes_whole_gap_including_zero_net_tail(self):
        self.assertEqual(allocate([(0, 14)], denominator=20, source_net=2, net_amount=1),
                         ((0, 14),))
        self.assertEqual(allocate([(6, 20)], denominator=20, source_net=2, net_amount=1),
                         ((6, 20),))
        self.assertEqual(allocate([(0, 20)], denominator=20, source_net=2, net_amount=1),
                         ((0, 10),))

    def test_net_skips_zero_capacity_but_quantity_consumes_it(self):
        free = [(0, 1), (2, 3), (4, 8)]
        self.assertEqual(allocate(free, denominator=8, source_net=1, net_amount=1),
                         ((4, 8),))
        self.assertEqual(allocate(free, denominator=8, source_net=1, length=2),
                         ((0, 1), (2, 3)))
        self.assertEqual(allocate([(0, 8)], denominator=8, source_net=0, length=8),
                         ((0, 8),))
        with self.assertRaises(AllocationRangeError):
            allocate([(0, 8)], denominator=8, source_net=0, net_amount=1)

    def test_more_than_two_hundred_zero_capacity_gaps_do_not_hide_chargeable_work(self):
        scale = 100_000_000
        free = [(2 * n, 2 * n + 1) for n in range(501)] + [(scale // 2, scale)]
        self.assertEqual(recommended_net(iter(free), denominator=scale, source_net=1), 1)
        self.assertEqual(allocate(iter(free), denominator=scale, source_net=1, net_amount=1),
                         ((scale // 2, scale),))
        with self.assertRaises(FragmentationError):
            allocate(iter(free), denominator=scale, source_net=1, length=201)

    def test_fragmentation_never_returns_a_truncated_selection(self):
        free = [(2 * n, 2 * n + 1) for n in range(201)]
        for selection in ({"length": 201}, {"net_amount": 201}):
            with self.assertRaises(FragmentationError):
                allocate(iter(free), denominator=402, source_net=402, **selection)
        self.assertEqual(len(allocate(free, denominator=402, source_net=402, length=200)), 200)
        self.assertEqual(recommended_net(free, denominator=402, source_net=402), 200)
        self.assertEqual(len(canonical_spans(free, 402, max_spans=201)), 201)
        with self.assertRaises(FragmentationError):
            canonical_spans(free, 402)

    def test_recommendations_provide_finite_bounded_completion(self):
        remaining = [(4 * n, 4 * n + 1) for n in range(605)]
        original = oracle_net(2420, remaining, 2420)
        billed = 0
        installment_count = 0
        while amount := recommended_net(iter(remaining), denominator=2420, source_net=2420):
            chosen = allocate(iter(remaining), denominator=2420, source_net=2420, net_amount=amount)
            self.assertGreater(amount, 0)
            self.assertLessEqual(len(chosen), 200)
            self.assertEqual(oracle_net(2420, chosen, 2420), amount)
            self.assertEqual(tuple(remaining[:len(chosen)]), chosen)
            remaining = remaining[len(chosen):]
            billed += amount
            installment_count += 1
            self.assertLessEqual(installment_count, 4)
        self.assertEqual(billed, original)
        self.assertEqual(installment_count, 4)
        self.assertEqual(remaining, [])

    def test_custom_span_bound_and_recommendation_only_read_a_bounded_prefix(self):
        read = []

        def stream():
            for i in range(1000):
                read.append(i)
                yield 2 * i, 2 * i + 1

        self.assertEqual(recommended_net(stream(), denominator=2000,
                                         source_net=2000, max_spans=3), 3)
        self.assertLessEqual(len(read), 4)  # One lookahead establishes coalescing.
        self.assertEqual(allocate(stream(), denominator=2000, source_net=2000,
                                  net_amount=3, max_spans=3), ((0, 1), (2, 3), (4, 5)))
        with self.assertRaises(FragmentationError):
            allocate(stream(), denominator=2000, source_net=2000, net_amount=4, max_spans=3)

    def test_late_void_exact_rebill_preserves_one_two_one_installments(self):
        scale = denominator(3_000_000, 4)
        issued = []
        for _ in range(3):
            spans = allocate(free_spans(issued, scale), denominator=scale,
                             source_net=4, length=scale // 3)
            issued.extend(spans)
        self.assertEqual([oracle_net(4, [span], scale) for span in issued], [1, 2, 1])
        saved_middle = (issued[1],)
        # Late release of the first and middle bills, while the third remains issued.
        free = tuple(free_spans([issued[2]], scale))
        self.assertTrue(spans_available(saved_middle, iter(free), scale))
        fresh = allocate(iter(free), denominator=scale, source_net=4, length=scale // 3)
        self.assertNotEqual(fresh, saved_middle)
        self.assertEqual(oracle_net(4, fresh, scale), 1)
        self.assertEqual(oracle_net(4, saved_middle, scale), 2)
        self.assertFalse(spans_available(saved_middle,
                                         free_spans([issued[1], issued[2]], scale), scale))
        self.assertEqual(oracle_net(4, [issued[2]], scale), 1)

    def test_subset_proof_compared_to_discrete_sets(self):
        scale = 7
        for a, b in combinations(range(scale + 1), 2):
            for c, d in combinations(range(scale + 1), 2):
                requested = [(a, b)]
                free = [(c, d)]
                self.assertEqual(spans_available(requested, free, scale),
                                 points(requested) <= points(free))
        self.assertTrue(spans_available([(1, 3), (5, 6)], [(0, 2), (2, 7)], scale))
        self.assertFalse(spans_available([(1, 6)], [(0, 3), (4, 7)], scale))
        self.assertTrue(spans_available([], [], scale))
        self.assertFalse(spans_available([(0, 1)], [], scale))

    def test_free_spans_coalesces_and_matches_set_complement(self):
        self.assertEqual(tuple(free_spans([(0, 1), (1, 2), (4, 5), (5, 6)], 8)),
                         ((2, 4), (6, 8)))
        self.assertEqual(tuple(free_spans([], 8)), ((0, 8),))
        self.assertEqual(tuple(free_spans([(0, 8)], 8)), ())
        for mask in range(256):
            occupied = [(i, i + 1) for i in range(8) if mask & (1 << i)]
            free = tuple(free_spans(iter(occupied), 8))
            self.assertEqual(points(free), set(range(8)) - points(occupied))
            self.assertEqual(canonical_spans(free, 8), free)
        self.assertEqual(allocate([(0, 1), (1, 2)], denominator=8, source_net=8,
                                  length=2, max_spans=1), ((0, 2),))

    def test_fraction_display_and_exact_microunits(self):
        for value, display, micros in (
            (Fraction(0), "0", 0), (Fraction(3), "3", 3_000_000),
            (Fraction(1, 8), "0.125", 125_000),
            (Fraction(1, 1_000_000), "0.000001", 1),
            (Fraction(123456789, 1_000_000), "123.456789", 123456789),
            (Fraction(1, 128), "1/128", None),
            (Fraction(1, 3), "1/3", None),
            (Fraction(2, 6), "1/3", None),
        ):
            self.assertEqual(format_fraction(value), display)
            self.assertEqual(exact_microunits(value), micros)
            self.assertEqual(Fraction(display), value)
        self.assertEqual(fraction(3_000_000, [(0, 2), (4, 6)], 12), 1)
        self.assertEqual(fraction(3_000_000, [], 12), 0)

    def test_coordinate_encoding_is_canonical_and_lexically_ordered(self):
        values = [0, 1, 15, 16, 255, 256, (1 << 63) - 1, (1 << 160) - 1]
        encoded = [coordinate_hex(value) for value in values]
        self.assertEqual(encoded, sorted(encoded))
        self.assertEqual(encoded[0], "0" * 40)
        self.assertEqual(encoded[-1], "f" * 40)
        self.assertEqual([coordinate_int(value) for value in encoded], values)
        for bad in ("0", "0" * 39, "0" * 41, "F" * 40, "g" * 40,
                    "0x" + "0" * 38, "+" + "0" * 39, " " + "0" * 39,
                    "0" * 40 + "\n", "０" * 40, b"0" * 40, 0, None):
            with self.subTest(bad=bad), self.assertRaises(AllocationRangeError):
                coordinate_int(bad)
        for bad in (-1, 1 << 160, True, 1.0, "1", None):
            with self.subTest(bad=bad), self.assertRaises(AllocationRangeError):
                coordinate_hex(bad)

    def test_reject_noncanonical_proof_instead_of_repairing_it(self):
        self.assertEqual(canonical_spans([[0, 2], [4, 8]], 8), ((0, 2), (4, 8)))
        for spans in ([(0, 2), (2, 4)], [(0, 3), (2, 4)], [(3, 4), (0, 2)],
                      [(0, 2), (0, 2)]):
            with self.subTest(spans=spans), self.assertRaises(AllocationRangeError):
                canonical_spans(spans, 8)

    def test_corrupt_span_shapes_bounds_and_stream_overlap_reject(self):
        bad_inputs = [None, 3, "", b"", {}, "01", [(0,)], [(0, 1, 2)], ["01"],
                      [{"start": 0, "end": 1}], [(True, 2)], [(0, False)],
                      [(0.0, 1)], [(0, "1")], [(-1, 2)], [(0, 9)], [(3, 3)],
                      [(4, 3)], [(0, 3), (2, 4)], [(5, 6), (0, 1)]]
        for spans in bad_inputs:
            for operation in (
                lambda: canonical_spans(spans, 8),
                lambda: tuple(free_spans(spans, 8)),
                lambda: allocate(spans, denominator=8, source_net=8, length=8),
                lambda: recommended_net(spans, denominator=8, source_net=8),
                lambda: spans_available([], spans, 8),
                lambda: fraction(1, spans, 8),
            ):
                with self.subTest(spans=spans), self.assertRaises(AllocationRangeError):
                    operation()

    def test_invalid_numeric_arguments_reject_bool_float_and_out_of_bounds(self):
        for bad in (True, 1.0, "1", None, -1, 1 << 63):
            with self.subTest(bad=bad), self.assertRaises(AllocationRangeError):
                denominator(bad, 1)
            with self.subTest(bad=bad), self.assertRaises(AllocationRangeError):
                denominator(1, bad)
        for bad in (0, True, 1.0, "1", None, -1, 1 << 160):
            for operation in (
                lambda: portion(1, 0, 1, bad),
                lambda: canonical_spans([], bad),
                lambda: tuple(free_spans([], bad)),
                lambda: allocate([], denominator=bad, source_net=1, length=1),
                lambda: recommended_net([], denominator=bad, source_net=1),
                lambda: spans_available([], [], bad),
                lambda: fraction(1, [], bad),
                lambda: canonical_spans([], 8, max_spans=bad),
                lambda: allocate([], denominator=8, source_net=1, length=1, max_spans=bad),
                lambda: recommended_net([], denominator=8, source_net=1, max_spans=bad),
            ):
                with self.subTest(bad=bad), self.assertRaises(AllocationRangeError):
                    operation()
        for selection in ({}, {"length": 1, "net_amount": 1}, {"length": 0},
                          {"length": True}, {"net_amount": True}, {"length": 9},
                          {"net_amount": 3}, {"net_amount": -1}, {"net_amount": 1.0}):
            with self.subTest(selection=selection), self.assertRaises(AllocationRangeError):
                allocate([(0, 8)], denominator=8, source_net=2, **selection)
        with self.assertRaises(AllocationRangeError):
            denominator(0, 0)
        with self.assertRaises(AllocationRangeError):
            allocate([(0, 8)], denominator=8, source_net=3, net_amount=1)
        for args in ((True, 0, 1, 8), (1, 2, 1, 8), (1, -1, 1, 8), (1, 0, 9, 8)):
            with self.assertRaises(AllocationRangeError):
                portion(*args)
        for bad in (1, 0.5, True, Fraction(-1, 3)):
            with self.assertRaises(AllocationRangeError):
                format_fraction(bad)
            with self.assertRaises(AllocationRangeError):
                exact_microunits(bad)


if __name__ == "__main__":
    unittest.main()
