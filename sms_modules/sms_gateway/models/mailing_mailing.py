# -*- coding: utf-8 -*-

import logging
import random

from datetime import timedelta

from odoo import api, fields, models, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class Mailing(models.Model):
    _inherit = 'mailing.mailing'

    sms_provider = fields.Selection(
        [('gateway', 'SMS Gateway')],
        string='SMS Provider',
        help='Select which SMS provider to use for this campaign. Leave empty for default.',
    )
    paused = fields.Boolean(
        string='Paused',
        default=False,
        help='When active, SMS in the queue for this campaign will not be sent.'
    )
    gateway_phone_forced_id = fields.Many2one(
        'sms.gateway.phone', string='Forced Gateway Phone',
        help='When set, all SMS in this mailing are sent through this phone.',
    )
    recipient_limit = fields.Integer(
        string='Recipient Limit',
        help='When set, randomly sample this many recipients from the filtered set.',
    )
    marketing_template_id = fields.Many2one(
        'sms.marketing.template', string='Marketing Template',
    )
    sms_allow_unsubscribe = fields.Boolean(default=True)
    exclude_contacted_days = fields.Integer(
        default=0, string='Exclude Contacted (days)',
        help='Exclude partners who received an SMS in the last N days. '
             'Applied at send time, not stored in the domain filter.',
    )
    created_from_app = fields.Boolean(default=False)

    def _get_recipients(self):
        res_ids = super()._get_recipients()
        # Exclude partners contacted by any SMS campaign in the last N days
        if self.exclude_contacted_days and self.exclude_contacted_days > 0:
            cutoff = fields.Datetime.now() - timedelta(days=self.exclude_contacted_days)
            self.env.cr.execute("""
                SELECT DISTINCT res_id
                FROM mailing_trace
                WHERE model = 'res.partner'
                  AND trace_type = 'sms'
                  AND trace_status = 'sent'
                  AND write_date >= %s
                  AND res_id IS NOT NULL
            """, (cutoff,))
            excluded = {r[0] for r in self.env.cr.fetchall()}
            if excluded:
                res_ids = [rid for rid in res_ids if rid not in excluded]
        if self.recipient_limit and 0 < self.recipient_limit < len(res_ids):
            res_ids = random.sample(res_ids, self.recipient_limit)
        return res_ids

    def action_force_create_sms_queue(self):
        """Force create SMS records for recipients without existing mailing.trace."""
        self.ensure_one()

        if self.mailing_type != 'sms':
            raise UserError(_('This action is only available for SMS mailings.'))

        all_recipients = self._get_recipients()
        res_ids = self._get_remaining_recipients()
        already_contacted = len(all_recipients) - len(res_ids)

        _logger.info('FORCE CREATE SMS - Mailing %s: Total=%d, Remaining=%d, Already=%d',
                     self.id, len(all_recipients), len(res_ids), already_contacted)

        if not res_ids:
            if not all_recipients:
                raise UserError(_(
                    'No recipients found matching the mailing filter.\n\n'
                    'Please check your mailing domain/filter configuration.'
                ))
            else:
                trace_count = self.env['mailing.trace'].search_count([
                    ('mass_mailing_id', '=', self.id)
                ])
                sms_count = self.env['sms.sms'].search_count([
                    ('mailing_id', '=', self.id)
                ])
                raise UserError(_(
                    'All recipients have already been contacted in this campaign.\n\n'
                    'Recipients in filter: %(total)s\n'
                    'Already contacted: %(contacted)s\n'
                    'Existing mailing.trace records: %(traces)s\n'
                    'Existing sms.sms records: %(sms)s\n\n'
                    'If you want to resend, please delete the existing mailing.trace records first.'
                ) % {
                    'total': len(all_recipients),
                    'contacted': already_contacted,
                    'traces': trace_count,
                    'sms': sms_count,
                })

        try:
            if self.state == 'in_queue':
                self.state = 'sending'

            composer_vals = self._send_sms_get_composer_values(res_ids)
            composer = self.env['sms.composer'].with_context(active_id=False).create(composer_vals)
            sms_records = composer._action_send_sms()

            if sms_records:
                sms_count = len(sms_records)
                outgoing_count = len(sms_records.filtered(lambda s: s.state == 'outgoing'))
                pending_count = len(sms_records.filtered(lambda s: s.state == 'pending'))
                canceled_count = len(sms_records.filtered(lambda s: s.state == 'canceled'))

                message = _(
                    'Successfully created %(created)s SMS records:\n\n'
                    '- Ready to send (outgoing): %(outgoing)s\n'
                    '- In gateway queue (pending): %(pending)s\n'
                    '- Canceled (invalid/blacklisted): %(canceled)s\n\n'
                    'Total in filter: %(total)s\n'
                    'Already contacted: %(already)s'
                ) % {
                    'created': sms_count,
                    'outgoing': outgoing_count,
                    'pending': pending_count,
                    'canceled': canceled_count,
                    'total': len(all_recipients),
                    'already': already_contacted,
                }
            else:
                message = _('No SMS records were created. Check the logs for details.')

            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('SMS Queue Created'),
                    'message': message,
                    'type': 'success' if sms_records else 'warning',
                    'sticky': True,
                }
            }

        except Exception as e:
            _logger.error('FORCE CREATE SMS - Error for mailing %s: %s', self.id, e, exc_info=True)
            raise UserError(_(
                'Failed to create SMS records.\n\nError: %s'
            ) % str(e))
