# -*- coding: utf-8 -*-

import re

from markupsafe import Markup

from odoo import api, fields, models

import logging

_logger = logging.getLogger(__name__)


class SmsGatewayInbound(models.Model):
    _name = 'sms.gateway.inbound'
    _description = 'Inbound SMS'
    _order = 'received_at desc'

    from_number = fields.Char(string='From', required=True, index=True)
    to_number = fields.Char(string='To')
    message = fields.Text(string='Message')
    received_at = fields.Datetime(string='Received', default=fields.Datetime.now, index=True)
    phone_id = fields.Many2one('sms.gateway.phone', string='Gateway Phone', ondelete='set null')
    partner_id = fields.Many2one('res.partner', string='Partner', ondelete='set null')
    is_stop = fields.Boolean(string='STOP', default=False, index=True)
    blacklisted = fields.Boolean(string='Blacklisted', default=False)

    @api.model
    def _phone_digits_tail(self, phone):
        """Return the last 9 digits of a phone number for comparison."""
        if not phone:
            return ''
        digits = re.sub(r'\D', '', phone)
        if digits.startswith('420'):
            digits = digits[3:]
        return digits[-9:] if len(digits) >= 9 else digits

    @api.model
    def _match_partner(self, number):
        """Find a partner by phone number, tolerant to formatting
        differences (spaces, dashes, +420 vs 00420 vs national format).

        Numbers are compared digit-only so '+420 777 123 456' stored on
        the partner matches '+420777123456' reported by the phone.
        """
        Partner = self.env['res.partner'].sudo()
        if not number:
            return Partner
        partner = Partner.search([
            '|',
            ('mobile', '=', number),
            ('phone', '=', number),
        ], limit=1)
        if partner:
            return partner
        tail = self._phone_digits_tail(number)
        if len(tail) >= 9:
            self.env.cr.execute(
                """
                SELECT id FROM res_partner
                WHERE active = TRUE
                  AND (regexp_replace(COALESCE(mobile, ''), '[^0-9]', '', 'g') LIKE %s
                       OR regexp_replace(COALESCE(phone, ''), '[^0-9]', '', 'g') LIKE %s)
                ORDER BY id
                LIMIT 1
                """,
                ['%' + tail, '%' + tail],
            )
            row = self.env.cr.fetchone()
            if row:
                partner = Partner.browse(row[0])
        return partner

    def _post_partner_chatter(self):
        """Post the inbound SMS into the matched partner's chatter."""
        for rec in self:
            if not rec.partner_id:
                continue
            stop_html = Markup(
                '<br/><span style="color: #dc2626; font-weight: bold;">'
                '&#9940; Číslo přidáno na blacklist (STOP)</span>'
            ) if rec.blacklisted else Markup('')
            body = Markup(
                '<b>&#128233; Příchozí SMS od {from_number}</b>'
                '<br/><blockquote style="border-left: 3px solid #3B82F6; '
                'padding-left: 8px; margin: 4px 0; color: #374151;">'
                '{message}</blockquote>{stop}'
            ).format(
                from_number=rec.from_number,
                message=rec.message or '',
                stop=stop_html,
            )
            try:
                rec.partner_id.message_post(
                    body=body,
                    message_type='comment',
                    subtype_xmlid='mail.mt_note',
                )
            except Exception as e:
                _logger.error('SMS Gateway: Failed to post chatter for %s: %s',
                              rec.from_number, e)

    def action_reprocess(self):
        """Re-match partner, blacklist STOP numbers and post chatter.

        Used to backfill records created before partner matching was
        fixed, and callable manually from the Inbound SMS list view.
        """
        Blacklist = self.env['phone.blacklist'].sudo()
        for rec in self:
            vals = {}
            is_stop = 'STOP' in (rec.message or '').upper()
            if is_stop != rec.is_stop:
                vals['is_stop'] = is_stop
            if is_stop and not rec.blacklisted:
                try:
                    Blacklist.add(rec.from_number)
                    vals['blacklisted'] = True
                    _logger.info('SMS Gateway: Reprocess blacklisted %s (STOP)',
                                 rec.from_number)
                except Exception as e:
                    _logger.error('SMS Gateway: Failed to blacklist %s: %s',
                                  rec.from_number, e)
            newly_matched = False
            if not rec.partner_id:
                partner = self._match_partner(rec.from_number)
                if partner:
                    vals['partner_id'] = partner.id
                    newly_matched = True
            if vals:
                rec.write(vals)
            # Only post chatter when the partner was matched just now —
            # records that already had a partner got their chatter post
            # when they were first processed
            if newly_matched:
                rec._post_partner_chatter()
        return True
