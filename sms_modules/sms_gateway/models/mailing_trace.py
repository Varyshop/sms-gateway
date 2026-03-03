# -*- coding: utf-8 -*-
# Fix for Odoo 18 core bug in mass_mailing_sms where
# mailing_trace.create() references undefined 'vals_list' instead of 'values_list'.
# We replicate the buggy method's logic (fixed) and skip it via super(BuggyClass, self).

import logging

from odoo import api, fields, models, _
from odoo.exceptions import UserError

from odoo.addons.mass_mailing_sms.models.mailing_trace import \
    MailingTrace as BuggyMailingTrace

_logger = logging.getLogger(__name__)


class MailingTrace(models.Model):
    _inherit = 'mailing.trace'

    @api.model_create_multi
    def create(self, values_list):
        for values in values_list:
            if values.get('trace_type') == 'sms' and not values.get('sms_code'):
                values['sms_code'] = self._get_random_code()
        # super(BuggyMailingTrace, self) skips the buggy create and calls mass_mailing's
        return super(BuggyMailingTrace, self).create(values_list)

    def action_send_now(self):
        """Bulk send SMS for selected mailing traces."""
        sms_traces = self.filtered(lambda t: t.trace_type == 'sms' and t.sms_id_int)
        if not sms_traces:
            raise UserError(_('No SMS traces selected.'))

        sms_ids = sms_traces.mapped('sms_id_int')
        sms_records = self.env['sms.sms'].sudo().search([
            ('id', 'in', sms_ids),
            ('state', '=', 'outgoing'),
        ])
        if not sms_records:
            raise UserError(_('No outgoing SMS found for the selected traces.'))

        _logger.info('Mailing trace Send Now: %d traces → %d outgoing SMS', len(sms_traces), len(sms_records))
        sms_records.send(auto_commit=True, raise_exception=False)

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('SMS Sending Started'),
                'message': _('%s SMS message(s) are being sent.', len(sms_records)),
                'type': 'success',
                'sticky': False,
            },
        }

    def action_send_now_gateway(self):
        """Open the Gateway Send wizard for SMS linked to selected traces."""
        sms_traces = self.filtered(lambda t: t.trace_type == 'sms' and t.sms_id_int)
        if not sms_traces:
            raise UserError(_('No SMS traces selected.'))

        sms_ids = sms_traces.mapped('sms_id_int')
        sms_records = self.env['sms.sms'].sudo().search([
            ('id', 'in', sms_ids),
            ('state', '=', 'outgoing'),
        ])
        if not sms_records:
            raise UserError(_('No outgoing SMS found for the selected traces.'))

        return {
            'type': 'ir.actions.act_window',
            'name': _('Send via Gateway'),
            'res_model': 'sms.gateway.send.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'active_ids': sms_records.ids,
                'active_model': 'sms.sms',
            },
        }
