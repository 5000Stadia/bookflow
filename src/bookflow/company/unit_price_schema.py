"""The nullable fraction column beside each stored unit price or cost (co0091, R150)."""
import sqlalchemy as sa


def fraction_check_sql(name: str) -> str:
    return f"{name} IS NULL OR (typeof({name}) = 'integer' AND {name} BETWEEN -500 AND 500 AND {name} <> 0)"


def fraction_column(prefix: str, label: str) -> sa.Column:
    name = f'{prefix}_fraction'
    return sa.Column(name, sa.BigInteger, sa.CheckConstraint(fraction_check_sql(name), name=f'ck_{name}'),
                     nullable=True, info={'description': (
                         f'{label} beyond its rounded minor units, in signed thousandths of a minor unit '
                         f'(price = {prefix}_minor_units + fraction/1000); null when the price is whole minor units.')})
