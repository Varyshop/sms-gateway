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
