"""Private bridge response families. Business completion always requires frames."""
from bookflow.core.errors import BookflowError
from .envelopes import intent_reference
from .framing import invalid

STATES = {'preparing', 'receiving', 'ready', 'queued', 'started', 'delivering', 'completed', 'cleanup_failed'}
NOT_SUBMITTED = {'expired_before_submission', 'released_before_submission', 'rejected_before_submission'}
LIMIT_FIELDS = {'active_host', 'active_principal', 'preexecution_idle_seconds', 'preexecution_absolute_seconds',
    'prepared_host_bytes', 'prepared_principal_bytes', 'receipt_bytes', 'completed_host', 'completed_principal',
    'completed_host_bytes', 'completed_principal_bytes', 'completed_idle_seconds', 'completed_absolute_seconds', 'json_delivery_seconds'}


def observation(value, reference=None, *, kind='state'):
    """Validate a transport observation; it is never a command output document."""
    try:
        if kind == 'digest':
            import re
            if set(value) != {'sha256', 'size_bytes'} or type(value['size_bytes']) is not int or value['size_bytes'] < 0 or not isinstance(value['sha256'], str) or not re.fullmatch('[0-9a-f]{64}', value['sha256']):
                raise ValueError
            return value
        ref = intent_reference(value['operation_ref'])
        if reference is not None and ref != reference:
            raise ValueError
        if kind == 'release':
            if set(value) != {'operation_ref', 'released'} or value['released'] is not True:
                raise ValueError
            return value
        if set(value) != {'operation_ref', 'state', 'reason', 'receipt_available', 'inspection_available', 'limits', 'outcome'}:
            raise ValueError
        if kind == 'admission' and value['state'] != 'preparing':
            raise ValueError
        if not isinstance(value['state'], str) or value['state'] not in STATES:
            raise ValueError
        if value['reason'] is not None and not isinstance(value['reason'], str):
            raise ValueError
        if any(type(value[key]) is not bool for key in ('receipt_available', 'inspection_available')):
            raise ValueError
        if not isinstance(value['limits'], dict) or set(value['limits']) != LIMIT_FIELDS or any(type(v) is not int or v <= 0 for v in value['limits'].values()):
            raise ValueError
        if value['outcome'] != ('not_submitted' if value['reason'] in NOT_SUBMITTED else 'unknown'):
            raise ValueError
        if value['state'] not in {'completed', 'delivering'} and (value['receipt_available'] or value['inspection_available']):
            raise ValueError
        return value
    except (ValueError, TypeError, KeyError, BookflowError):
        raise invalid('invalid_observation') from None


def annotate(error, reference, *, submitted):
    """Add adapter provenance only; genuine framed command errors do not raise here."""
    error.details.setdefault('operation', 'mcp_result')
    error.details.setdefault('stage', 'post_submission' if submitted else 'pre_submission')
    error.details.setdefault('outcome', 'unknown' if submitted else 'not_submitted')
    if reference is not None:
        error.details['operation_ref'] = reference
    return error
