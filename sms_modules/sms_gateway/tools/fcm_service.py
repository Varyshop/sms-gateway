# -*- coding: utf-8 -*-

import json
import logging
import time

_logger = logging.getLogger(__name__)

try:
    import firebase_admin
    from firebase_admin import credentials, messaging
    HAS_FIREBASE = True
except ImportError:
    HAS_FIREBASE = False

_app = None


def _get_firebase_app(env):
    """Initialize Firebase Admin SDK lazily from Odoo config parameter.

    Supports two modes:
    - Inline JSON: store the full service account JSON in
      ir.config_parameter 'sms_gateway.fcm_credentials_json'
    - File path: store a path to the JSON file on the server in
      ir.config_parameter 'sms_gateway.fcm_credentials_path'

    Inline JSON is checked first (no file needed on server).
    """
    global _app
    if _app is not None:
        return _app
    if not HAS_FIREBASE:
        return None

    ICP = env['ir.config_parameter'].sudo()

    # 1) Try inline JSON from config parameter
    cred_json = ICP.get_param('sms_gateway.fcm_credentials_json', '')
    if cred_json:
        try:
            cred = credentials.Certificate(json.loads(cred_json))
            _app = firebase_admin.initialize_app(cred)
            _logger.info('Firebase Admin SDK initialized from inline JSON config')
            return _app
        except Exception:
            _logger.exception('Failed to initialize Firebase from inline JSON')
            return None

    # 2) Fall back to file path
    cred_path = ICP.get_param('sms_gateway.fcm_credentials_path', '')
    if not cred_path:
        return None

    try:
        cred = credentials.Certificate(cred_path)
        _app = firebase_admin.initialize_app(cred)
        _logger.info('Firebase Admin SDK initialized from %s', cred_path)
        return _app
    except Exception:
        _logger.exception('Failed to initialize Firebase Admin SDK')
        return None


def send_fcm_wake(env, phone):
    """Send a data-only FCM message to wake a gateway phone.

    Returns True if the message was sent, False otherwise.
    Silently returns False when FCM is not configured or the phone
    has no FCM token — this preserves backward compatibility with
    old app versions that use polling.
    """
    if not phone.fcm_token:
        return False

    fcm_enabled = env['ir.config_parameter'].sudo().get_param(
        'sms_gateway.fcm_enabled', 'False'
    )
    if fcm_enabled in ('False', '0', 'false', ''):
        return False

    app = _get_firebase_app(env)
    if not app:
        return False

    try:
        message = messaging.Message(
            data={
                'type': 'sms_pending',
                'phone_id': str(phone.id),
                'timestamp': str(int(time.time())),
            },
            token=phone.fcm_token,
            android=messaging.AndroidConfig(
                priority='high',
                ttl=60,
            ),
        )
        response = messaging.send(message, app=app)
        _logger.info('FCM wake sent to phone %s (%s): %s',
                      phone.name, phone.phone_number, response)
        return True
    except (messaging.UnregisteredError, messaging.SenderIdMismatchError):
        _logger.warning('FCM token invalid for phone %s, clearing', phone.name)
        phone.sudo().write({'fcm_token': False})
        return False
    except Exception:
        _logger.exception('FCM send failed for phone %s', phone.name)
        return False
