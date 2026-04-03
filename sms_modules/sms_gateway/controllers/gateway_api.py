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

    # ---- Batch Confirm SMS Status ----

    @http.route('/sms-gateway/confirm-batch', type='http', auth='public',
                methods=['POST'], csrf=False, cors='*')
    def confirm_batch(self, **kwargs):
        """Batch-confirm SMS statuses from mobile app.

        Expects JSON body:
        {
            "results": [
                {"id": 123, "status": "sent"},
                {"id": 124, "status": "error", "error_message": "No signal"}
            ]
        }

        Returns updated counters once for the whole batch.
        """
        try:
            api_key = self._get_api_key()
            phones = self._validate_api_key(api_key)
            if not phones:
                return self._error_response('Invalid API key', 401)

            data = self._get_json_data()
            results = data.get('results')
            if not results or not isinstance(results, list):
                return self._error_response('results list is required')

            phone_ids = set(phones.ids)
            processed = 0
            errors = []

            for item in results:
                sms_id = item.get('id')
                status = item.get('status')
                error_message = item.get('error_message')

                if not sms_id or status not in ('sent', 'error'):
                    errors.append({'id': sms_id, 'error': 'Invalid id or status'})
                    continue

                sms = request.env['sms.sms'].sudo().browse(sms_id)
                if not sms.exists() or sms.gateway_phone_id.id not in phone_ids:
                    errors.append({'id': sms_id, 'error': 'Not found or unauthorized'})
                    continue

                ok = request.env['sms.sms']._update_gateway_status(sms_id, status, error_message)
                if ok:
                    processed += 1
                else:
                    errors.append({'id': sms_id, 'error': 'Update failed'})

            if processed > 0:
                request.env.cr.commit()

            # Return fresh counters
            response_data = {
                'success': True,
                'processed': processed,
            }
            if errors:
                response_data['errors'] = errors

            phone = phones[0]
            phone.invalidate_recordset(['sent_today', 'sent_month', 'sent_total'])
            response_data['sent_today'] = phone.sent_today
            response_data['sent_month'] = phone.sent_month
            response_data['sent_total'] = phone.sent_total
            response_data['daily_limit'] = phone.daily_limit
            response_data['monthly_limit'] = phone.monthly_limit

            return self._json_response(response_data)
        except Exception as e:
            _logger.exception('SMS Gateway confirm-batch error')
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
                stop_html = (
                    '<br/><span style="color: #dc2626; font-weight: bold;">'
                    '&#9940; Cislo pridano na blacklist (STOP)</span>'
                ) if blacklisted else ''
                body = (
                    f'<b>&#128233; Prichozi SMS od {from_number}</b>'
                    f'<br/><blockquote style="border-left: 3px solid #3B82F6; '
                    f'padding-left: 8px; margin: 4px 0; color: #374151;">'
                    f'{message}</blockquote>{stop_html}'
                )
                try:
                    partner.message_post(
                        body=body,
                        message_type='comment',
                        subtype_xmlid='mail.mt_note',
                    )
                except Exception as e:
                    _logger.error('SMS Gateway: Failed to post chatter message: %s', e)

            # Persist inbound SMS
            is_stop = 'STOP' in message.upper()
            try:
                request.env['sms.gateway.inbound'].sudo().create({
                    'from_number': from_number,
                    'to_number': to_number,
                    'message': message,
                    'phone_id': phones[0].id if phones else False,
                    'partner_id': partner.id if partner else False,
                    'is_stop': is_stop,
                    'blacklisted': blacklisted,
                })
            except Exception as e:
                _logger.error('SMS Gateway: Failed to save inbound SMS: %s', e)

            request.env.cr.commit()

            return self._json_response({
                'success': True,
                'blacklisted': blacklisted,
                'partner_found': bool(partner),
            })
        except Exception as e:
            _logger.exception('SMS Gateway inbound error')
            return self._error_response(str(e), 500)

    # ---- Batch Inbound (retroactive inbound check) ----

    @http.route('/sms-gateway/inbound-batch', type='http', auth='public',
                methods=['POST'], csrf=False, cors='*')
    def inbound_batch(self, **kwargs):
        """Batch-process inbound SMS from device inbox.

        Records all messages into sms.gateway.inbound (deduplicating by
        from_number + message text). For messages containing STOP, also
        blacklists the number and posts to partner chatter.
        """
        try:
            api_key = self._get_api_key()
            phones = self._validate_api_key(api_key)
            if not phones:
                return self._error_response('Invalid API key', 401)

            data = self._get_json_data()
            messages = data.get('messages')
            if not messages or not isinstance(messages, list):
                return self._error_response('messages list is required')

            Inbound = request.env['sms.gateway.inbound'].sudo()
            blacklisted_count = 0
            already_blacklisted = 0
            recorded = 0
            skipped = 0

            for msg in messages:
                from_number = msg.get('from_number', '')
                message = msg.get('message', '')
                to_number = msg.get('to_number', '')

                if not from_number or not message:
                    skipped += 1
                    continue

                # Deduplicate: skip if already recorded with same from+message
                existing_inbound = Inbound.search([
                    ('from_number', '=', from_number),
                    ('message', '=', message),
                    ('phone_id', 'in', phones.ids),
                ], limit=1)
                if existing_inbound:
                    # Already recorded — but still ensure STOP is blacklisted
                    is_stop = 'STOP' in message.upper()
                    if is_stop:
                        bl = request.env['phone.blacklist'].sudo().search([
                            '|',
                            ('number', '=', self._normalize_phone(from_number)),
                            ('number', '=', from_number),
                        ], limit=1)
                        if bl:
                            already_blacklisted += 1
                        else:
                            try:
                                request.env['phone.blacklist'].sudo().add(from_number)
                                blacklisted_count += 1
                                _logger.info('SMS Gateway: Retroactive blacklist %s (STOP, dedup)', from_number)
                            except Exception as e:
                                _logger.error('SMS Gateway: Failed to blacklist %s: %s', from_number, e)
                    skipped += 1
                    continue

                # Find partner
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

                is_stop = 'STOP' in message.upper()
                blacklisted = False

                # Blacklist STOP messages
                if is_stop:
                    bl = request.env['phone.blacklist'].sudo().search([
                        '|',
                        ('number', '=', self._normalize_phone(from_number)),
                        ('number', '=', from_number),
                    ], limit=1)
                    if bl:
                        already_blacklisted += 1
                    else:
                        try:
                            request.env['phone.blacklist'].sudo().add(from_number)
                            blacklisted = True
                            blacklisted_count += 1
                            _logger.info('SMS Gateway: Retroactive blacklist %s (STOP)', from_number)
                        except Exception as e:
                            _logger.error('SMS Gateway: Failed to blacklist %s: %s', from_number, e)

                # Record inbound SMS
                try:
                    Inbound.create({
                        'from_number': from_number,
                        'to_number': to_number,
                        'message': message,
                        'phone_id': phones[0].id if phones else False,
                        'partner_id': partner.id if partner else False,
                        'is_stop': is_stop,
                        'blacklisted': blacklisted,
                    })
                    recorded += 1
                except Exception as e:
                    _logger.error('SMS Gateway: Failed to save inbound SMS: %s', e)

                # Post to partner chatter
                if partner:
                    stop_html = (
                        '<br/><span style="color: #dc2626; font-weight: bold;">'
                        '&#9940; Cislo pridano na blacklist (STOP)</span>'
                    ) if blacklisted else ''
                    body_html = (
                        f'<b>&#128233; Prichozi SMS od {from_number}</b>'
                        f'<br/><blockquote style="border-left: 3px solid #3B82F6; '
                        f'padding-left: 8px; margin: 4px 0; color: #374151;">'
                        f'{message}</blockquote>{stop_html}'
                    )
                    try:
                        partner.message_post(
                            body=body_html,
                            message_type='comment',
                            subtype_xmlid='mail.mt_note',
                        )
                    except Exception as e:
                        _logger.error('SMS Gateway: Failed to post chatter: %s', e)

            if recorded > 0 or blacklisted_count > 0:
                request.env.cr.commit()

            return self._json_response({
                'success': True,
                'recorded': recorded,
                'blacklisted': blacklisted_count,
                'already_blacklisted': already_blacklisted,
                'skipped': skipped,
            })
        except Exception as e:
            _logger.exception('SMS Gateway inbound-batch error')
            return self._error_response(str(e), 500)

    # ---- Inbound SMS History ----

    @http.route('/sms-gateway/inbound-history', type='http', auth='public',
                methods=['POST'], csrf=False, cors='*')
    def inbound_history(self, **kwargs):
        """Return paginated inbound SMS history for the gateway phone."""
        try:
            api_key = self._get_api_key()
            phones = self._validate_api_key(api_key)
            if not phones:
                return self._error_response('Invalid API key', 401)

            data = self._get_json_data()
            limit = min(int(data.get('limit', 50)), 200)
            offset = int(data.get('offset', 0))
            stop_only = data.get('stop_only', False)
            stop_not_blacklisted = data.get('stop_not_blacklisted', False)
            search = data.get('search', '').strip()

            domain = [('phone_id', 'in', phones.ids)]
            if stop_not_blacklisted:
                domain.extend([('is_stop', '=', True), ('blacklisted', '=', False)])
            elif stop_only:
                domain.append(('is_stop', '=', True))
            if search:
                domain.append('|')
                domain.append(('from_number', 'ilike', search))
                domain.append(('message', 'ilike', search))

            records = request.env['sms.gateway.inbound'].sudo().search(
                domain, limit=limit, offset=offset, order='received_at desc',
            )
            total = request.env['sms.gateway.inbound'].sudo().search_count(domain)

            messages = []
            for rec in records:
                messages.append({
                    'id': rec.id,
                    'from_number': rec.from_number,
                    'to_number': rec.to_number or '',
                    'message': rec.message or '',
                    'received_at': fields.Datetime.to_string(rec.received_at),
                    'is_stop': rec.is_stop,
                    'blacklisted': rec.blacklisted,
                    'partner_name': rec.partner_id.name if rec.partner_id else '',
                })

            return self._json_response({
                'success': True,
                'messages': messages,
                'total': total,
                'limit': limit,
                'offset': offset,
            })
        except Exception as e:
            _logger.exception('SMS Gateway inbound-history error')
            return self._error_response(str(e), 500)

    # ---- Blacklist from Inbound ----

    @http.route('/sms-gateway/inbound-blacklist', type='http', auth='public',
                methods=['POST'], csrf=False, cors='*')
    def inbound_blacklist(self, **kwargs):
        """Blacklist one or more phone numbers from inbound SMS records.

        Expects JSON body:
        {
            "ids": [1, 2, 3]  // sms.gateway.inbound record IDs
        }
        """
        try:
            api_key = self._get_api_key()
            phones = self._validate_api_key(api_key)
            if not phones:
                return self._error_response('Invalid API key', 401)

            data = self._get_json_data()
            ids = data.get('ids', [])
            if not ids or not isinstance(ids, list):
                return self._error_response('ids list is required')

            Inbound = request.env['sms.gateway.inbound'].sudo()
            records = Inbound.search([
                ('id', 'in', ids),
                ('phone_id', 'in', phones.ids),
                ('blacklisted', '=', False),
            ])

            blacklisted_count = 0
            for rec in records:
                try:
                    request.env['phone.blacklist'].sudo().add(rec.from_number)
                    rec.blacklisted = True
                    blacklisted_count += 1

                    if rec.partner_id:
                        rec.partner_id.message_post(
                            body=(
                                f'<b>&#9940; Cislo {rec.from_number} pridano na blacklist</b>'
                                f'<br/><span style="color: #6B7280;">Rucne z mobilni aplikace</span>'
                            ),
                            message_type='comment',
                            subtype_xmlid='mail.mt_note',
                        )
                except Exception as e:
                    _logger.error('SMS Gateway: Failed to blacklist %s: %s', rec.from_number, e)

            if blacklisted_count > 0:
                request.env.cr.commit()

            return self._json_response({
                'success': True,
                'blacklisted': blacklisted_count,
            })
        except Exception as e:
            _logger.exception('SMS Gateway inbound-blacklist error')
            return self._error_response(str(e), 500)

    # ---- Register FCM Token ----

    @http.route('/sms-gateway/register-fcm', type='http', auth='public',
                methods=['POST'], csrf=False, cors='*')
    def register_fcm_token(self, **kwargs):
        """Register or update the FCM token for a gateway phone.

        Called by the mobile app after pairing and on every FCM token refresh.
        Old app versions that don't support FCM will never call this endpoint,
        preserving full backward compatibility.
        """
        try:
            api_key = self._get_api_key()
            phones = self._validate_api_key(api_key)
            if not phones:
                return self._error_response('Invalid API key', 401)

            data = self._get_json_data()
            fcm_token = data.get('fcm_token')
            if not fcm_token:
                return self._error_response('fcm_token is required')

            phones.sudo().write({
                'fcm_token': fcm_token,
                'fcm_token_updated': fields.Datetime.now(),
            })

            _logger.info('SMS Gateway: FCM token registered for phone(s) %s',
                         ', '.join(phones.mapped('name')))

            return self._json_response({'success': True})
        except Exception as e:
            _logger.exception('SMS Gateway register-fcm error')
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

    # ──────────────────────────────────────────────
    # Campaign / Marketing Template endpoints
    # ──────────────────────────────────────────────

    @http.route('/sms-gateway/campaign/templates', type='http', auth='public', methods=['POST'], csrf=False, cors='*')
    def campaign_templates(self, **kw):
        api_key = self._get_api_key()
        if not api_key:
            return self._error_response('Missing API key', 401)
        phones = self._validate_api_key(api_key)
        if not phones:
            return self._error_response('Invalid API key', 401)

        try:
            templates = request.env['sms.marketing.template'].sudo().search([
                ('phone_id', 'in', phones.ids),
                ('active', '=', True),
            ], order='sequence, name')

            result = []
            for t in templates:
                result.append({
                    'id': t.id,
                    'name': t.name,
                    'body': t.body,
                    'default_limit': t.default_limit,
                    'max_limit': t.max_limit,
                    'exclude_contacted_days': t.exclude_contacted_days,
                    'segments': [{
                        'id': s.id,
                        'name': s.name,
                        'code': s.code,
                    } for s in t.segment_ids],
                })

            return self._json_response({'success': True, 'templates': result})
        except Exception as e:
            _logger.exception('SMS Gateway campaign/templates error')
            return self._error_response(str(e), 500)

    @http.route('/sms-gateway/campaign/filters', type='http', auth='public', methods=['POST'], csrf=False, cors='*')
    def campaign_filters(self, **kw):
        api_key = self._get_api_key()
        if not api_key:
            return self._error_response('Missing API key', 401)
        phones = self._validate_api_key(api_key)
        if not phones:
            return self._error_response('Invalid API key', 401)

        try:
            data = self._get_json_data()
            template_id = data.get('template_id')

            exclude_days = 0
            if template_id:
                template = request.env['sms.marketing.template'].sudo().browse(template_id)
                if not template.exists() or template.phone_id.id not in phones.ids:
                    return self._error_response('Template not found', 404)
                segments = template.segment_ids
                exclude_days = template.exclude_contacted_days or 0
            else:
                segments = request.env['sms.marketing.segment'].sudo().search([
                    ('active', '=', True),
                ])

            phone = phones[0]
            result = []
            for seg in segments:
                count = seg._get_recipient_count(
                    phone=phone, exclude_contacted_days=exclude_days,
                )
                result.append({
                    'id': seg.id,
                    'code': seg.code,
                    'name': seg.name,
                    'description': seg.description or '',
                    'recipient_count': count,
                })

            return self._json_response({'success': True, 'filters': result})
        except Exception as e:
            _logger.exception('SMS Gateway campaign/filters error')
            return self._error_response(str(e), 500)

    @http.route('/sms-gateway/campaign/preview', type='http', auth='public', methods=['POST'], csrf=False, cors='*')
    def campaign_preview(self, **kw):
        api_key = self._get_api_key()
        if not api_key:
            return self._error_response('Missing API key', 401)
        phones = self._validate_api_key(api_key)
        if not phones:
            return self._error_response('Invalid API key', 401)

        try:
            data = self._get_json_data()
            template_id = data.get('template_id')
            segment_id = data.get('segment_id')
            limit = data.get('limit', 100)

            template = request.env['sms.marketing.template'].sudo().browse(template_id)
            if not template.exists() or template.phone_id.id not in phones.ids:
                return self._error_response('Template not found', 404)

            segment = request.env['sms.marketing.segment'].sudo().browse(segment_id)
            if not segment.exists():
                return self._error_response('Segment not found', 404)

            phone = phones[0]
            exclude_days = template.exclude_contacted_days or 0
            count = segment._get_recipient_count(
                phone=phone, exclude_contacted_days=exclude_days,
            )
            effective_count = min(count, limit, template.max_limit)

            # Render preview with a sample partner
            preview_text = template.body
            try:
                import ast
                domain = segment._get_domain()
                domain += [
                    ('phone_sanitized_blacklisted', '=', False),
                    '|',
                    '&', ('mobile', '!=', False), ('mobile', '!=', ''),
                    '&', ('phone', '!=', False), ('phone', '!=', ''),
                ]
                if phone.domain_filter:
                    domain += ast.literal_eval(phone.domain_filter)
                excluded_ids = segment._get_excluded_partner_ids(exclude_days)
                if excluded_ids:
                    domain += [('id', 'not in', excluded_ids)]
                sample = request.env['res.partner'].sudo().search(domain, limit=1)
                if sample:
                    preview_text = preview_text.replace('{{object.name}}', sample.name or '')
                    preview_text = preview_text.replace('{{object.email}}', sample.email or '')
                    preview_text = preview_text.replace('{{object.phone}}', sample.phone or sample.mobile or '')
            except Exception:
                pass  # preview with raw placeholders is fine

            return self._json_response({
                'success': True,
                'recipient_count': effective_count,
                'preview_text': preview_text,
                'template_name': template.name,
                'segment_name': segment.name,
            })
        except Exception as e:
            _logger.exception('SMS Gateway campaign/preview error')
            return self._error_response(str(e), 500)

    @http.route('/sms-gateway/campaign/create', type='http', auth='public', methods=['POST'], csrf=False, cors='*')
    def campaign_create(self, **kw):
        api_key = self._get_api_key()
        if not api_key:
            return self._error_response('Missing API key', 401)
        phones = self._validate_api_key(api_key)
        if not phones:
            return self._error_response('Invalid API key', 401)

        try:
            import ast

            data = self._get_json_data()
            template_id = data.get('template_id')
            segment_id = data.get('segment_id')
            limit = data.get('limit', 100)
            custom_body = data.get('custom_body')

            template = request.env['sms.marketing.template'].sudo().browse(template_id)
            if not template.exists() or template.phone_id.id not in phones.ids:
                return self._error_response('Template not found', 404)

            segment = request.env['sms.marketing.segment'].sudo().browse(segment_id)
            if not segment.exists():
                return self._error_response('Segment not found', 404)

            phone = phones[0]
            effective_limit = min(limit, template.max_limit)

            # Use custom body from app if provided, otherwise template body
            sms_body = custom_body.strip() if custom_body else template.body

            # Build combined domain
            exclude_days = template.exclude_contacted_days or 0
            domain = segment._get_domain()
            domain += [
                ('phone_sanitized_blacklisted', '=', False),
                '|',
                '&', ('mobile', '!=', False), ('mobile', '!=', ''),
                '&', ('phone', '!=', False), ('phone', '!=', ''),
            ]
            if phone.domain_filter:
                try:
                    domain += ast.literal_eval(phone.domain_filter)
                except Exception:
                    pass
            excluded_ids = segment._get_excluded_partner_ids(exclude_days)
            if excluded_ids:
                domain += [('id', 'not in', excluded_ids)]

            # Create mailing
            partner_model = request.env['ir.model'].sudo().search([
                ('model', '=', 'res.partner'),
            ], limit=1)

            # email_from is required (NOT NULL) even for SMS mailings
            company = request.env.company.sudo()
            email_from = company.email or company.partner_id.email or 'noreply@example.com'

            mailing = request.env['mailing.mailing'].sudo().create({
                'subject': '%s - %s' % (template.name, fields.Datetime.now().strftime('%d.%m.%Y %H:%M')),
                'mailing_type': 'sms',
                'body_plaintext': sms_body,
                'email_from': email_from,
                'sms_provider': 'gateway',
                'mailing_model_id': partner_model.id,
                'mailing_domain': repr(domain),
                'gateway_phone_forced_id': phone.id,
                'recipient_limit': effective_limit,
                'marketing_template_id': template.id,
                'created_from_app': True,
            })

            # Generate SMS queue
            mailing.state = 'in_queue'
            mailing.action_force_create_sms_queue()

            # Count created SMS
            sms_count = request.env['sms.sms'].sudo().search_count([
                ('mailing_id', '=', mailing.id),
                ('state', 'in', ('pending', 'outgoing')),
            ])

            return self._json_response({
                'success': True,
                'campaign_id': mailing.id,
                'recipient_count': sms_count,
            })
        except Exception as e:
            _logger.exception('SMS Gateway campaign/create error')
            return self._error_response(str(e), 500)

    @http.route('/sms-gateway/campaign/assign-sim', type='http', auth='public', methods=['POST'], csrf=False, cors='*')
    def campaign_assign_sim(self, **kw):
        """Assign SIM number(s) to pending SMS in a campaign and optionally trigger immediate send."""
        api_key = self._get_api_key()
        if not api_key:
            return self._error_response('Missing API key', 401)
        phones = self._validate_api_key(api_key)
        if not phones:
            return self._error_response('Invalid API key', 401)

        try:
            data = self._get_json_data()
            campaign_id = data.get('campaign_id')
            # mode: 'single' (one SIM for all) or 'split' (alternate between SIMs)
            mode = data.get('mode', 'single')
            # sim_number: required for 'single' mode
            sim_number = data.get('sim_number')
            # sim_numbers: list of SIM numbers for 'split' mode
            sim_numbers = data.get('sim_numbers', [])

            mailing = request.env['mailing.mailing'].sudo().browse(campaign_id)
            if not mailing.exists() or mailing.gateway_phone_forced_id.id not in phones.ids:
                return self._error_response('Campaign not found', 404)

            phone = mailing.gateway_phone_forced_id

            # Get all pending SMS for this campaign assigned to this phone
            pending_sms = request.env['sms.sms'].sudo().search([
                ('mailing_id', '=', mailing.id),
                ('gateway_phone_id', '=', phone.id),
                ('gateway_state', '=', 'pending'),
                ('state', '=', 'pending'),
            ])

            if not pending_sms:
                return self._json_response({
                    'success': True,
                    'assigned': 0,
                    'message': 'No pending SMS to assign',
                })

            assigned = 0
            if mode == 'split' and len(sim_numbers) >= 2:
                # Round-robin assignment across SIMs
                for i, sms in enumerate(pending_sms):
                    sim = sim_numbers[i % len(sim_numbers)]
                    sms.sudo().write({'gateway_sim_number': sim})
                    assigned += 1
            elif sim_number:
                # Single SIM assignment
                pending_sms.sudo().write({'gateway_sim_number': sim_number})
                assigned = len(pending_sms)
            else:
                return self._error_response('sim_number required for single mode', 400)

            return self._json_response({
                'success': True,
                'assigned': assigned,
            })
        except Exception as e:
            _logger.exception('SMS Gateway campaign/assign-sim error')
            return self._error_response(str(e), 500)

    @http.route('/sms-gateway/campaign/list', type='http', auth='public', methods=['POST'], csrf=False, cors='*')
    def campaign_list(self, **kw):
        api_key = self._get_api_key()
        if not api_key:
            return self._error_response('Missing API key', 401)
        phones = self._validate_api_key(api_key)
        if not phones:
            return self._error_response('Invalid API key', 401)

        try:
            mailings = request.env['mailing.mailing'].sudo().search([
                ('gateway_phone_forced_id', 'in', phones.ids),
                ('created_from_app', '=', True),
            ], order='create_date desc', limit=50)

            result = []
            for m in mailings:
                traces = request.env['mailing.trace'].sudo().search_read(
                    [('mass_mailing_id', '=', m.id)],
                    ['trace_status'],
                )
                total = len(traces)
                sent = sum(1 for t in traces if t['trace_status'] == 'sent')
                pending = sum(1 for t in traces if t['trace_status'] in ('outgoing', 'pending', 'process'))
                error = sum(1 for t in traces if t['trace_status'] in ('error', 'cancel', 'bounce'))

                result.append({
                    'id': m.id,
                    'name': m.subject or m.name,
                    'state': m.state,
                    'date_created': m.create_date.isoformat() if m.create_date else '',
                    'total': total,
                    'sent': sent,
                    'pending': pending,
                    'error': error,
                })

            return self._json_response({'success': True, 'campaigns': result})
        except Exception as e:
            _logger.exception('SMS Gateway campaign/list error')
            return self._error_response(str(e), 500)

    @http.route('/sms-gateway/campaign/status/<int:mailing_id>', type='http', auth='public', methods=['POST'], csrf=False, cors='*')
    def campaign_status(self, mailing_id, **kw):
        api_key = self._get_api_key()
        if not api_key:
            return self._error_response('Missing API key', 401)
        phones = self._validate_api_key(api_key)
        if not phones:
            return self._error_response('Invalid API key', 401)

        try:
            mailing = request.env['mailing.mailing'].sudo().browse(mailing_id)
            if not mailing.exists() or mailing.gateway_phone_forced_id.id not in phones.ids:
                return self._error_response('Campaign not found', 404)

            traces = request.env['mailing.trace'].sudo().search_read(
                [('mass_mailing_id', '=', mailing.id)],
                ['trace_status'],
            )
            total = len(traces)
            sent = sum(1 for t in traces if t['trace_status'] == 'sent')
            pending = sum(1 for t in traces if t['trace_status'] in ('outgoing', 'pending', 'process'))
            error = sum(1 for t in traces if t['trace_status'] in ('error', 'cancel', 'bounce'))

            return self._json_response({
                'success': True,
                'id': mailing.id,
                'name': mailing.subject or mailing.name,
                'state': mailing.state,
                'total': total,
                'sent': sent,
                'pending': pending,
                'error': error,
                'created_at': mailing.create_date.isoformat() if mailing.create_date else '',
            })
        except Exception as e:
            _logger.exception('SMS Gateway campaign/status error')
            return self._error_response(str(e), 500)
