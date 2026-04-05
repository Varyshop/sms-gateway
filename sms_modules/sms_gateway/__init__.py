import logging

_logger = logging.getLogger(__name__)

try:
    import firebase_admin  # noqa: F401
except ImportError:
    import subprocess
    import sys
    try:
        subprocess.check_call([
            sys.executable, '-m', 'pip', 'install',
            'firebase-admin', '--quiet', '--no-input',
        ])
        _logger.info('sms_gateway: firebase-admin auto-installed for FCM support')
    except Exception:
        _logger.info(
            'sms_gateway: firebase-admin not available, FCM disabled (polling still works). '
            'To enable FCM: pip install firebase-admin'
        )

from . import tools
from . import models
from . import controllers
from . import wizard


def post_init_hook(env):
    """Create partial index on mailing_trace for the just-in-time duplicate
    exclusion check in /sms-gateway/pending.

    The check runs on every phone poll — for 100k+ partner databases with
    10k+ campaigns, a parciální index restricted to SMS traces makes the
    EXISTS correlated subquery an index-only scan (<1 ms per candidate row).
    """
    env.cr.execute("""
        CREATE INDEX IF NOT EXISTS mailing_trace_sms_res_id_write_date_idx
        ON mailing_trace (res_id, write_date)
        WHERE trace_type = 'sms'
          AND trace_status IN ('pending', 'sent', 'open', 'reply')
          AND res_id IS NOT NULL
    """)
    _logger.info(
        'sms_gateway: ensured mailing_trace_sms_res_id_write_date_idx index '
        'for just-in-time duplicate exclusion',
    )
