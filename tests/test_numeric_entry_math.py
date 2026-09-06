"""Browser calculator checked against Python's independent rational arithmetic."""
import json
from pathlib import Path
import random
import shutil
import subprocess
from fractions import Fraction

import pytest

SCRIPT = Path(__file__).parents[1] / 'src/bookflow/adapters/workbench/static/numeric-entry.js'
pytestmark = pytest.mark.skipif(not shutil.which('node'), reason='Node unavailable')


def evaluate(cases):
    program = """const {evaluate} = require(process.argv[1]);
    const fs = require('fs');
    console.log(JSON.stringify(JSON.parse(fs.readFileSync(0,'utf8')).map(([text,scale]) => {
      try { return evaluate(text,scale); } catch(e) { return {error:e.message}; }
    })));"""
    result = subprocess.run(['node', '-e', program, str(SCRIPT)], input=json.dumps(cases),
                            capture_output=True, text=True, check=True, timeout=10)
    return json.loads(result.stdout)


def expected(value, scale):
    units = round(value * 10**scale)  # Fraction.__round__ is independent half-even.
    negative = units < 0
    digits = str(abs(units)).zfill(scale + 1)
    output = (digits[:-scale] + '.' + digits[-scale:]).rstrip('0').rstrip('.') if scale else digits
    return ('-' if negative and units else '') + output


def test_decimal_arithmetic_precedence_rounding_and_limits():
    cases = [('.1 + .2', 2, '0.3'), ('(.5 + 1.25) * 4', 6, '7'),
             ('10 / 3', 2, '3.33'), ('1 / 3', 6, '0.333333'),
             ('1 / 8', 2, '0.12'), ('3 / 8', 2, '0.38'),
             ('-1 / 8', 2, '-0.12'), ('-3 / 8', 2, '-0.38'),
             ('100 * (1 + 10%)', 2, '110'), ('100 + 10%', 2, '100.1'),
             ('(2 + 3) × 4 ÷ 2 =', 2, '10'), ('6/3', 0, '2'),
             ('9007199254740993 + 2', 2, '9007199254740995'),
             ('0.005 * 1000', 2, '5'), ('1 / 2000000', 6, '0')]
    results = evaluate([(s, scale) for s, scale, _ in cases])
    assert [r.get('value') for r in results] == [v for _, _, v in cases]
    assert results[2]['needsRounding'] and not results[0]['needsRounding']
    invalid = ['1/0', '1 +', '1..5', '1e3', 'Math.random()', 'alert(1)',
               '1;2', '1/**/2', '1 2', '(1+2', '2(3)', '10%%', '=',
               '(' * 25 + '1' + ')' * 25, '1+' * 128 + '1', '9' * 257]
    assert all('error' in r for r in evaluate([(s, 6) for s in invalid]))
    assert 'error' in evaluate([('1/2', 0)])[0]
    assert evaluate([('1/3', None)])[0]['value'] is None


def test_random_exact_expressions_against_fraction_oracle():
    randomizer = random.Random(107)
    cases, answers = [], []
    for _ in range(350):
        a, b, c = [randomizer.randint(-9999, 9999) for _ in range(3)]
        denominator = randomizer.randint(1, 99)
        scale = randomizer.choice([2, 3, 6, 9])
        text = f'({a}/100 + {b}/100) * ({c}/100) / {denominator}'
        cases.append((text, scale))
        answers.append(expected((Fraction(a, 100) + Fraction(b, 100)) * Fraction(c, 100) / denominator, scale))
    assert [r.get('value') for r in evaluate(cases)] == answers
