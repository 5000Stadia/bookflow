"""Report selector declarations; references may include retired master records."""
from dataclasses import dataclass

from bookflow.company.lists import ReferenceDefinition


@dataclass(frozen=True)
class ReportReference(ReferenceDefinition):
    include_inactive: bool = True


@dataclass(frozen=True)
class ReportFormDefinition:
    references: tuple[ReferenceDefinition, ...] = (ReportReference('account', 'account'),)


FORM = ReportFormDefinition()
