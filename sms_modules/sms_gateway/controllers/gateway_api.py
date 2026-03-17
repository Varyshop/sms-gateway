# -*- coding: utf-8 -*-

import json
import logging
import re

from odoo import http, fields
from odoo.http import request

_logger = logging.getLogger(__name__)


class SmsGatewayController(http.Controller):

    def _get_api_key(self):
        """Extract API key from X-API-Key header or JSON params."""
        api_key = request.httprequest.headers.get('X-API-Key')
        if api_key:
            return api_key
        try:
            data = request.get_json_data()
            return data.get('params', {}).get('api_key') or data.get('api_key')
        except Exception:
            return None

    def _validate_api_key(self, api_key):
        """Validate API key against gateway phone records.

        Returns:
            sms.gateway.phone recordset matching the API key, or empty recordset.
        """
        if not api_key:
            return request.env['sms.gateway.phone']
        return request.env['sms.gateway.phone'].sudo().search([
            ('api_key', '=', api_key),
            ('active', '=', True),
        ])

    def _json_response(self, data, status=200):
        return request.make_response(
            json.dumps(data),
            headers=[('Content-Type', 'application/json')],
            status=status,
        )

    def _error_response(self, message, status=400):
        return self._json_response({'success': False, 'error': message}, status=status)

    def _get_json_data(self):
        """Parse JSON from request body."""
        try:
            body = request.httprequest.get_data(as_text=True)
            return json.loads(body) if body else {}
        except json.JSONDecodeError:
            return {}

    def _normalize_phone(self, phone):
        """Normalize phone for comparison - return last 9 digits."""
        if not phone:
            return ''
        digits = re.sub(r'\D', '', phone)
        if digits.startswith('420'):
            digits = digits[3:]
        return digits[-9:] if len(digits) >= 9 else digits

    # ---- Heartbeat ----

    @http.route('/sms-gateway/heartbeat', type='http', auth='public',
                methods=['POST'], csrf=False, cors='*')
    def heartbeat(self, **kwargs):
        """Receive heartbeat from mobile app."""
        try:
            api_key = self._get_api_key()
            phones = self._validate_api_key(api_key)
            if not phones:
                return self._error_response('Invalid API key', 401)

            data = self._get_json_data()
            battery_level = data.get('battery_level')
            signal_strength = data.get('signal_strength')

            phones._update_heartbeat(
                battery_level=battery_level,
                signal_strength=signal_strength,
            )

            pending_count = {}
            for phone in phones:
                for num in [phone.phone_number, phone.phone_number_2]:
                    if num:
                        count = request.env['sms.sms'].sudo().search_count([
                            ('gateway_phone_id', '=', phone.id),
                            ('state', '=', 'pending'),
                            ('gateway_state', '=', 'pending'),
                        ])
                        pending_count[num] = count

            # Include counter stats so the app always has fresh data
            phone_stats = {}
            for phone in phones:
                phone_stats[phone.phone_number] = {
                    'sent_today': phone.sent_today,
                    'daily_limit': phone.daily_limit,
                    'sent_month': phone.sent_month,
                    'monthly_limit': phone.monthly_limit,
                    'sent_total': phone.sent_total,
                }
                if phone.phone_number_2:
                    phone_stats[phone.phone_number_2] = phone_stats[phone.phone_number]

            return self._json_response({
                'success': True,
                'pending_count': pending_count,
                'rate_limit': phones[0].rate_limit if phones else 100,
                'phone_stats': phone_stats,
            })
        except Exception as e:
            _logger.exception('SMS Gateway heartbeat error')
            return self._error_response(str(e), 500)

    # ---- Pending SMS ----

    @http.route('/sms-gateway/pending', type='http', auth='public',
                methods=['POST'], csrf=False, cors='*')
    def get_pending_sms(self, **kwargs):
        """Get pending SMS for the gateway phone."""
        try:
            api_key = self._get_api_key()
            phones = self._validate_api_key(api_key)
            if not phones:
                return self._error_response('Invalid API key', 401)

            data = self._get_json_data()
            limit = min(data.get('limit', 20), 100)

            phone_ids = tuple(phones.ids)
            request.env.cr.execute("""
                UPDATE sms_sms
                SET gateway_state = 'processing'
                WHERE id IN (
                    SELECT id FROM sms_sms
                    WHERE gateway_phone_id IN %s
                      AND state = 'pending'
                      AND gateway_state = 'pending'
                      AND sms_provider = 'gateway'
                    ORDER BY id ASC
                    LIMIT %s
                    FOR UPDATE SKIP LOCKED
                )
                RETURNING id, number, body, uuid, gateway_sim_number
            """, (phone_ids, limit))

            rows = request.env.cr.fetchall()

            sms_list = []
            for row in rows:
                sms_list.append({
                    'id': row[0],
                    'phone_number': row[1],
                    'message': row[2],
                    'uuid': row[3],
                    'gateway_phone_number': row[4] or '',
                })

            if sms_list:
                request.env.cr.commit()

            return self._json_response({
                'success': True,
                'sms_list': sms_list,
            })
        except Exception as e:
            _logger.exception('SMS Gateway pending error')
            request.env.cr.rollback()
            return self._error_response(str(e), 500)

    # ---- Confirm SMS Status ----

    @http.route('/sms-gateway/confirm/<int:sms_id>', type='http', auth='public',
                methods=['POST'], csrf=False, cors='*')
    def confirm_sms(self, sms_id, **kwargs):
        """Confirm SMS sending status from mobile app."""
        try:
            api_key = self._get_api_key()
            phones = self._validate_api_key(api_key)
            if not phones:
                return self._error_response('Invalid API key', 401)

            data = self._get_json_data()
            status = data.get('status')
            error_message = data.get('error_message')

            _logger.info('SMS Gateway confirm: sms_id=%s status=%s error=%s',
                         sms_id, status, error_message)

            if status not in ('sending', 'sent', 'error'):
                return self._error_response('Invalid status. Must be: sending, sent, or error')

            sms = request.env['sms.sms'].sudo().browse(sms_id)
            if not sms.exists() or sms.gateway_phone_id.id not in phones.ids:
                _logger.warning('SMS Gateway confirm: SMS %s not found or phone %s not authorized (allowed: %s)',
                                sms_id, sms.gateway_phone_id.id if sms.exists() else 'N/A', phones.ids)
                return self._error_response('SMS not found or unauthorized', 404)

            result = request.env['sms.sms']._update_gateway_status(sms_id, status, error_message)
            if result:
                request.env.cr.commit()
            else:
                _logger.warning('SMS Gateway confirm: _update_gateway_status returned False for SMS %s', sms_id)

            # Return updated counters after confirmation
            response_data = {'success': bool(result)}
            if result and status == 'sent' and sms.gateway_phone_id:
                phone = sms.gateway_phone_id
                # Re-read from DB to get the incremented values
                phone.invalidate_recordset(['sent_today', 'sent_month', 'sent_total'])
                response_data['sent_today'] = phone.sent_today
                response_data['sent_month'] = phone.sent_month
                response_data['sent_total'] = phone.sent_total

            return self._json_response(response_data)
        except Exception as e:
            _logger.exception('SMS Gateway confirm error for SMS %s', sms_id)
            request.env.cr.rollback()
            return self._error_response(str(e), 500)

    # ---- Inbound SMS (STOP detection) ----

    @http.route('/sms-gateway/inbound', type='http', auth='public',
                methods=['POST'], csrf=False, cors='*')
    def inbound_sms(self, **kwargs):
        """Handle inbound SMS received by the phone."""
        try:
            api_key = self._get_api_key()
            phones = self._validate_api_key(api_key)
            if not phones:
                return self._error_response('Invalid API key', 401)

            data = self._get_json_data()
            from_number = data.get('from_number', '')
            message = data.get('message', '')
            to_number = data.get('to_number', '')

            if not from_number or not message:
                return self._error_response('from_number and message are required')

            blacklisted = False

            if 'STOP' in message.upper():
                try:
                    request.env['phone.blacklist'].sudo().add(from_number)
                    blacklisted = True
                    _logger.info('SMS Gateway: Added %s to phone blacklist (STOP received)', from_number)
                except Exception as e:
                    _logger.error('SMS Gateway: Failed to blacklist %s: %s', from_number, e)

            partner = request.env['res.partner'].sudo().search([
                '|',
                ('mobile', '=', from_number),
                ('phone', '=', from_number),
            ], limit=1)

            if not partner:
                sanitized = from_number.replace(' ', '').replace('-', '')
                partner = request.env['res.partner'].sudo().search([
                    '|',
                    ('mobile', 'ilike', sanitized),
                    ('phone', 'ilike', sanitized),
                ], limit=1)

            if partner:
                body = f"Prichozi SMS: {message}"
                if blacklisted:
                    body += "\n[Cislo pridano na blacklist - STOP]"
                try:
                    partner.message_post(
                        body=body,
                        subject=f"SMS od {from_number}",
                        message_type='comment',
                        subtype_xmlid='mail.mt_note',
                    )
                except Exception as e:
                    _logger.error('SMS Gateway: Failed to post chatter message: %s', e)

            request.env.cr.commit()

            return self._json_response({
                'success': True,
                'blacklisted': blacklisted,
                'partner_found': bool(partner),
            })
        except Exception as e:
            _logger.exception('SMS Gateway inbound error')
            return self._error_response(str(e), 500)

    # ---- Stats ----

    @http.route('/sms-gateway/stats', type='http', auth='public',
                methods=['POST'], csrf=False, cors='*')
    def stats(self, **kwargs):
        """Get statistics for the gateway phone."""
        try:
            api_key = self._get_api_key()
            phones = self._validate_api_key(api_key)
            if not phones:
                return self._error_response('Invalid API key', 401)

            phone_stats = []
            for phone in phones:
                phone_stats.append({
                    'id': phone.id,
                    'name': phone.name,
                    'phone_number': phone.phone_number,
                    'phone_number_2': phone.phone_number_2,
                    'state': phone.state,
                    'sent_today': phone.sent_today,
                    'daily_limit': phone.daily_limit,
                    'sent_month': phone.sent_month,
                    'monthly_limit': phone.monthly_limit,
                    'sent_total': phone.sent_total,
                    'pending_count': phone.pending_count,
                    'rate_limit': phone.rate_limit,
                })

            return self._json_response({
                'success': True,
                'phones': phone_stats,
            })
        except Exception as e:
            _logger.exception('SMS Gateway stats error')
            return self._error_response(str(e), 500)
