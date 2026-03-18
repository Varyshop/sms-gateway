# -*- coding: utf-8 -*-

import ast
import base64
import calendar
import io
import json
import logging
import secrets
from datetime import date, timedelta

try:
    import qrcode
    HAS_QRCODE = True
except ImportError:
    HAS_QRCODE = False

from odoo import api, fields, models, _
from odoo.exceptions import ValidationError

_logger = logging.getLogger(__name__)


class SmsGatewayPhone(models.Model):
    _name = 'sms.gateway.phone'
    _description = 'SMS Gateway Phone'
    _order = 'name'

    name = fields.Char(string='Name', required=True)
    phone_number = fields.Char(string='Phone Number', required=True,
                               help='Primary phone number in international format (e.g. +420123456789)')
    phone_number_2 = fields.Char(string='Phone Number 2 (Dual SIM)',
                                 help='Secondary phone number for dual SIM devices')

    api_key = fields.Char(string='API Key', copy=False)
    qr_code = fields.Binary(string='QR Code', compute='_compute_qr_code',
                            help='QR code for pairing with the mobile app')

    active = fields.Boolean(default=True)
    state = fields.Selection([
        ('offline', 'Offline'),
        ('online', 'Online'),
    ], string='Status', default='offline', readonly=True)
    last_heartbeat = fields.Datetime(string='Last Heartbeat', readonly=True)
    heartbeat_timeout = fields.Integer(string='Heartbeat Timeout (min)', default=5,
                                       help='Mark phone as offline after this many minutes without heartbeat')

    # Limits
    daily_limit = fields.Integer(string='Daily Limit', default=500,
                                 help='Maximum SMS per day for this phone')
    sent_today = fields.Integer(string='Sent Today', readonly=True)
    monthly_limit = fields.Integer(string='Monthly Limit', default=0,
                                   help='Maximum SMS per billing period. 0 = unlimited.')
    sent_month = fields.Integer(string='Sent This Period', readonly=True)
    month_start_day = fields.Integer(string='Billing Period Start Day', default=1,
                                     help='Day of month when the billing period resets (1-28).')
    next_month_reset = fields.Date(string='Next Period Reset')
    sent_total = fields.Integer(string='Total Sent', readonly=True)
    rate_limit = fields.Integer(string='SMS per Minute', default=100,
                                help='Maximum SMS per minute (controls queue timing)')

    # Domain filter
    domain_filter = fields.Char(
        string='Partner Domain Filter',
        help='Optional Odoo domain filter for partner matching. '
             'E.g. [("category_id", "in", [10])]. '
             'Leave empty to accept all SMS.'
    )

    # FCM push notifications
    fcm_token = fields.Char(string='FCM Token', copy=False,
                             help='Firebase Cloud Messaging token for push notifications. '
                                  'Set automatically by the mobile app.')
    fcm_token_updated = fields.Datetime(string='FCM Token Updated', readonly=True)

    # Device info (updated via heartbeat)
    battery_level = fields.Integer(string='Battery Level', readonly=True)
    signal_strength = fields.Integer(string='Signal Strength (dBm)', readonly=True)

    # Statistics
    sms_ids = fields.One2many('sms.sms', 'gateway_phone_id', string='SMS Messages')
    pending_count = fields.Integer(string='Pending SMS', compute='_compute_counts')
    error_count = fields.Integer(string='Error SMS', compute='_compute_counts')

    @api.model
    def _get_next_reset_date(self, month_start_day, after_date=None):
        """Calculate the next monthly reset date after the given date.

        Args:
            month_start_day: day of month (1-28) when billing period starts
            after_date: calculate next reset after this date (default: today)
        Returns:
            date object for the next reset
        """
        ref = after_date or date.today()
        day = min(month_start_day or 1, 28)
        if ref.day < day:
            # Reset is still this month
            return ref.replace(day=day)
        # Reset is next month
        if ref.month == 12:
            return ref.replace(year=ref.year + 1, month=1, day=day)
        max_day = calendar.monthrange(ref.year, ref.month + 1)[1]
        return ref.replace(month=ref.month + 1, day=min(day, max_day))

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        for rec in records:
            if not rec.next_month_reset:
                rec.next_month_reset = self._get_next_reset_date(rec.month_start_day)
        return records

    def write(self, vals):
        res = super().write(vals)
        if 'month_start_day' in vals:
            for rec in self:
                rec.next_month_reset = self._get_next_reset_date(rec.month_start_day)
        return res

    @api.depends('sms_ids.state', 'sms_ids.gateway_state')
    def _compute_counts(self):
        for phone in self:
            sms_data = self.env['sms.sms'].sudo().read_group(
                [('gateway_phone_id', '=', phone.id), ('state', 'in', ['pending', 'outgoing'])],
                ['gateway_state'],
                ['gateway_state'],
            )
            counts = {d['gateway_state']: d['gateway_state_count'] for d in sms_data}
            phone.pending_count = counts.get('pending', 0) + counts.get('processing', 0) + counts.get('sending', 0)
            phone.error_count = self.env['sms.sms'].sudo().search_count([
                ('gateway_phone_id', '=', phone.id),
                ('state', '=', 'error'),
            ])

    @api.depends('api_key')
    def _compute_qr_code(self):
        for phone in self:
            if phone.api_key and HAS_QRCODE:
                base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url', '')
                qr_data = json.dumps({
                    'url': base_url,
                    'api_key': phone.api_key,
                    'type': 'sms_gateway',
                })
                qr = qrcode.QRCode(
                    version=1,
                    error_correction=qrcode.constants.ERROR_CORRECT_L,
                    box_size=10,
                    border=4,
                )
                qr.add_data(qr_data)
                qr.make(fit=True)
                img = qr.make_image(fill_color="black", back_color="white")
                buffer = io.BytesIO()
                img.save(buffer, format='PNG')
                phone.qr_code = base64.b64encode(buffer.getvalue())
            else:
                phone.qr_code = False

    def action_generate_api_key(self):
        for phone in self:
            phone.api_key = secrets.token_urlsafe(32)
        return True

    @api.constrains('domain_filter')
    def _check_domain_filter(self):
        for phone in self:
            if phone.domain_filter:
                try:
                    domain = ast.literal_eval(phone.domain_filter)
                    if not isinstance(domain, list):
                        raise ValidationError(_('Domain filter must be a valid Odoo domain (list of tuples).'))
                    # Try to use it
                    self.env['res.partner'].sudo().search(domain, limit=1)
                except (ValueError, SyntaxError):
                    raise ValidationError(_('Domain filter must be a valid Python expression (list of tuples).'))
                except Exception as e:
                    raise ValidationError(_('Invalid domain filter: %s') % str(e))

    @api.model
    def _cron_check_heartbeat(self):
        """Check heartbeat timeout and mark phones as offline."""
        now = fields.Datetime.now()
        online_phones = self.sudo().search([('state', '=', 'online')])
        for phone in online_phones:
            if phone.last_heartbeat:
                timeout = timedelta(minutes=phone.heartbeat_timeout)
                if now - phone.last_heartbeat > timeout:
                    phone.state = 'offline'
                    _logger.info('Gateway phone %s (%s) marked as offline (heartbeat timeout)',
                                 phone.name, phone.phone_number)

    @api.model
    def _cron_reset_daily_counters(self):
        """Reset daily SMS counters at midnight."""
        all_phones = self.sudo().search([])
        all_phones.write({'sent_today': 0})
        _logger.info('Reset daily SMS counters for %d gateway phones', len(all_phones))

    @api.model
    def _cron_reset_monthly_counters(self):
        """Reset monthly SMS counters when billing period starts."""
        today = date.today()

        # Initialize next_month_reset for phones that don't have it yet
        unset_phones = self.sudo().search([('next_month_reset', '=', False)])
        for phone in unset_phones:
            phone.next_month_reset = self._get_next_reset_date(phone.month_start_day)

        phones_to_reset = self.sudo().search([
            ('next_month_reset', '<=', today),
        ])
        for phone in phones_to_reset:
            # Advance next_month_reset BEFORE writing sent_month,
            # so the cron doesn't reset again tomorrow.
            next_reset = self._get_next_reset_date(phone.month_start_day, after_date=today)
            phone.write({
                'sent_month': 0,
                'next_month_reset': next_reset,
            })
            _logger.info(
                'Reset monthly SMS counter for phone %s (%s), next reset: %s',
                phone.name, phone.phone_number, next_reset,
            )
        if phones_to_reset:
            _logger.info('Reset monthly SMS counters for %d gateway phones', len(phones_to_reset))

    def action_recalculate_counters(self):
        """Recalculate sent_today, sent_month, sent_total from sms.sms records.

        Useful when counters got out of sync (e.g. due to tracker errors
        preventing counter increment).
        """
        from ..tools.sms_utils import sms_segment_count
        today = date.today()
        for phone in self:
            # sent_today: SMS sent today
            today_sms = self.env['sms.sms'].sudo().search([
                ('gateway_phone_id', '=', phone.id),
                ('state', '=', 'sent'),
                ('gateway_state', '=', 'sent'),
                ('write_date', '>=', fields.Datetime.to_string(
                    fields.Datetime.from_string(today.isoformat() + ' 00:00:00'))),
            ])
            today_segments = sum(sms_segment_count(s.body) for s in today_sms)

            # sent_month: SMS sent since last reset date
            if phone.next_month_reset:
                # Current period started one billing cycle before next_month_reset
                period_start_day = phone.month_start_day or 1
                # Work backwards from next_month_reset to find period start
                nr = phone.next_month_reset
                if nr.month == 1:
                    period_start = nr.replace(year=nr.year - 1, month=12, day=min(period_start_day, 31))
                else:
                    max_day = calendar.monthrange(nr.year, nr.month - 1)[1]
                    period_start = nr.replace(month=nr.month - 1, day=min(period_start_day, max_day))
            else:
                # Fallback: assume period started on month_start_day of current month
                period_start_day = phone.month_start_day or 1
                if today.day >= period_start_day:
                    period_start = today.replace(day=period_start_day)
                else:
                    if today.month == 1:
                        period_start = today.replace(year=today.year - 1, month=12, day=period_start_day)
                    else:
                        max_day = calendar.monthrange(today.year, today.month - 1)[1]
                        period_start = today.replace(month=today.month - 1, day=min(period_start_day, max_day))

            month_sms = self.env['sms.sms'].sudo().search([
                ('gateway_phone_id', '=', phone.id),
                ('state', '=', 'sent'),
                ('gateway_state', '=', 'sent'),
                ('write_date', '>=', fields.Datetime.to_string(
                    fields.Datetime.from_string(period_start.isoformat() + ' 00:00:00'))),
            ])
            month_segments = sum(sms_segment_count(s.body) for s in month_sms)

            # sent_total: all sent SMS ever
            all_sms = self.env['sms.sms'].sudo().search([
                ('gateway_phone_id', '=', phone.id),
                ('state', '=', 'sent'),
                ('gateway_state', '=', 'sent'),
            ])
            total_segments = sum(sms_segment_count(s.body) for s in all_sms)

            phone.write({
                'sent_today': today_segments,
                'sent_month': month_segments,
                'sent_total': total_segments,
            })
            _logger.info(
                'Recalculated counters for phone %s: today=%d, month=%d, total=%d',
                phone.name, today_segments, month_segments, total_segments,
            )

    @api.model
    def _get_available_phones(self, partner=None):
        """Get available gateway phones for sending SMS.

        Args:
            partner: optional res.partner record to filter by domain_filter

        Returns:
            sms.gateway.phone recordset sorted by pending count (least-loaded first)
        """
        phones = self.sudo().search([
            ('state', '=', 'online'),
            ('active', '=', True),
        ])

        available = self.env['sms.gateway.phone']
        for phone in phones:
            # Check daily limit
            if phone.daily_limit and phone.sent_today >= phone.daily_limit:
                continue

            # Check monthly limit
            if phone.monthly_limit and phone.sent_month >= phone.monthly_limit:
                continue

            # Check domain filter
            if phone.domain_filter and partner:
                try:
                    domain = ast.literal_eval(phone.domain_filter)
                    matching = self.env['res.partner'].sudo().search_count(
                        [('id', '=', partner.id)] + domain
                    )
                    if not matching:
                        continue
                except Exception:
                    _logger.warning('Invalid domain filter on gateway phone %s: %s',
                                    phone.name, phone.domain_filter)
                    continue

            available |= phone

        # Sort by pending count (least-loaded first)
        if available:
            pending_data = self.env['sms.sms'].sudo().read_group(
                [('gateway_phone_id', 'in', available.ids),
                 ('state', '=', 'pending'),
                 ('gateway_state', 'in', ['pending', 'processing', 'sending'])],
                ['gateway_phone_id'],
                ['gateway_phone_id'],
            )
            pending_map = {d['gateway_phone_id'][0]: d['gateway_phone_id_count'] for d in pending_data}
            available = available.sorted(key=lambda p: pending_map.get(p.id, 0))

        return available

    def _get_phones_by_number(self, phone_numbers):
        """Find gateway phones matching the given phone numbers."""
        domain = []
        for num in phone_numbers:
            if domain:
                domain = ['|'] + domain
            domain += ['|',
                        ('phone_number', '=', num),
                        ('phone_number_2', '=', num)]
        return self.sudo().search(domain) if domain else self.env['sms.gateway.phone']

    def _update_heartbeat(self, battery_level=None, signal_strength=None):
        """Update heartbeat timestamp and device info."""
        vals = {
            'last_heartbeat': fields.Datetime.now(),
            'state': 'online',
        }
        if battery_level is not None:
            vals['battery_level'] = battery_level
        if signal_strength is not None:
            vals['signal_strength'] = signal_strength
        self.sudo().write(vals)

    def action_view_pending_sms(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Pending SMS',
            'res_model': 'sms.sms',
            'view_mode': 'list,form',
            'domain': [('gateway_phone_id', '=', self.id), ('state', '=', 'pending')],
        }

    def action_view_error_sms(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Error SMS',
            'res_model': 'sms.sms',
            'view_mode': 'list,form',
            'domain': [('gateway_phone_id', '=', self.id), ('state', '=', 'error')],
        }
