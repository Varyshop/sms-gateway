# -*- coding: utf-8 -*-

import logging
import re

from odoo import api, fields, models

from ..tools.sms_utils import sms_segment_count

_logger = logging.getLogger(__name__)

# Pattern to match the full unsubscribe line: "\nSTOP SMS: https://…/sms/…"
UNSUBSCRIBE_LINE_PATTERN = re.compile(r'\n?STOP SMS:\s*https?://\S+', re.IGNORECASE)


class SmsSms(models.Model):
    _inherit = 'sms.sms'

    sms_provider = fields.Selection(
        [('gateway', 'SMS Gateway')],
        string='SMS Provider', index=True,
        help='When set to SMS Gateway, this SMS will be sent via a gateway phone device.',
    )
    gateway_phone_id = fields.Many2one(
        'sms.gateway.phone', string='Gateway Phone', index=True,
    )
    gateway_sim_number = fields.Char(
        string='Send From Number',
        help='Which SIM phone number to use for sending this SMS. '
             'Leave empty to let the gateway phone choose automatically.',
    )
    gateway_state = fields.Selection([
        ('pending', 'Waiting in Queue'),
        ('processing', 'Processing'),
        ('sending', 'Sending'),
        ('sent', 'Sent'),
        ('error', 'Error'),
    ], string='Gateway State', readonly=True)

    @api.onchange('gateway_phone_id')
    def _onchange_gateway_phone_id(self):
        if self.gateway_phone_id:
            self.sms_provider = 'gateway'
            # Don't auto-fill gateway_sim_number — empty means app picks SIM
        else:
            self.gateway_sim_number = False

    def write(self, vals):
        res = super().write(vals)
        if 'state' in vals:
            self._update_mailing_trace_status(vals)
        return res

    def _check_mailing_completion(self, traces):
        """Check if all SMS for a mailing have been sent and update mailing status."""
        for trace in traces:
            if not trace.mass_mailing_id:
                continue
            mailing = trace.mass_mailing_id
            if mailing.state != 'sending':
                continue
            if hasattr(mailing, 'paused') and mailing.paused:
                _logger.info('Mailing %s is paused, skipping completion check', mailing.id)
                continue
            pending_count = self.env['sms.sms'].sudo().search_count([
                ('mailing_id', '=', mailing.id),
                ('state', 'in', ['outgoing', 'pending']),
            ])
            if pending_count == 0:
                _logger.info('All SMS for mailing %s processed. Marking as done.', mailing.id)
                mailing.sudo().write({'state': 'done', 'sent_date': fields.Datetime.now()})

    def _update_mailing_trace_status(self, vals):
        """Update mailing.trace records when SMS state changes."""
        for sms in self:
            traces = self.env['mailing.trace'].sudo().search([('sms_id_int', '=', sms.id)])
            if not traces:
                continue
            trace_vals = {}
            if vals['state'] == 'sent':
                trace_vals['trace_status'] = 'sent'
                trace_vals['sent_datetime'] = fields.Datetime.now()
            elif vals['state'] == 'error':
                trace_vals['trace_status'] = 'failed'
                if sms.failure_type:
                    trace_vals['failure_type'] = sms.failure_type
            if trace_vals:
                traces.write(trace_vals)
                self._check_mailing_completion(traces)

    @staticmethod
    def _replace_unsubscribe_url(body):
        """Replace the full unsubscribe line with a short opt-out notice."""
        if not body:
            return body
        return UNSUBSCRIBE_LINE_PATTERN.sub('\nodhl. sms: STOP', body)

    def _phone_remaining_capacity(self, phone):
        """Return remaining segment capacity for a gateway phone.

        Counters reflect only confirmed sends. We also count segments
        currently in the queue to avoid over-assigning.
        """
        in_queue = 0
        queued_sms = self.env['sms.sms'].sudo().search([
            ('gateway_phone_id', '=', phone.id),
            ('gateway_state', 'in', ['pending', 'processing', 'sending']),
        ])
        for sms in queued_sms:
            in_queue += sms_segment_count(sms.body)

        used_d = phone.sent_today + in_queue
        daily = (phone.daily_limit - used_d) if phone.daily_limit else 999_999
        used_m = phone.sent_month + in_queue
        monthly = (phone.monthly_limit - used_m) if phone.monthly_limit else 999_999
        return max(0, min(daily, monthly))

    def _send(self, unlink_failed=False, unlink_sent=True, raise_exception=False):
        """Override _send to assign SMS to gateway phones instead of sending via IAP.

        Only processes SMS explicitly tagged with sms_provider='gateway'.
        Everything else (False, 'odoo', or any other provider) passes to super().
        """
        # Filter: only handle explicitly gateway-tagged SMS
        gateway_sms = self.filtered(lambda s: s.sms_provider == 'gateway')
        other_sms = self - gateway_sms
        if other_sms:
            super(SmsSms, other_sms)._send(
                unlink_failed=unlink_failed,
                unlink_sent=unlink_sent,
                raise_exception=raise_exception,
            )
        if not gateway_sms:
            return

        related_mailings = gateway_sms.mapped('mailing_id').filtered(lambda m: m.state == 'sending')

        # Track in-memory capacity deltas per phone (for loop-internal limit checks)
        capacity_used = {}  # phone_id → segments already assigned in this batch

        for sms in gateway_sms:
            # Check if mailing is paused
            if sms.mailing_id and hasattr(sms.mailing_id, 'paused') and sms.mailing_id.paused:
                _logger.info('Skipping SMS %s - mailing %s is paused', sms.id, sms.mailing_id.id)
                continue

            sms_sudo = sms.sudo().with_context(sms_skip_msg_notification=True)

            # Calculate segments first (needed for limit check)
            body = self._replace_unsubscribe_url(sms.body)
            segments = sms_segment_count(body)

            # Use pre-assigned phone or auto-assign
            if sms.gateway_phone_id:
                phone = sms.gateway_phone_id
                sim_number = sms.gateway_sim_number or False

                # Check limit even for pre-assigned phones
                remaining = self._phone_remaining_capacity(phone) - capacity_used.get(phone.id, 0)
                if remaining < segments:
                    _logger.warning(
                        'SMS %s: pre-assigned phone %s has insufficient capacity '
                        '(%d remaining, %d needed). Unassigning.',
                        sms.id, phone.name, remaining, segments,
                    )
                    sms_sudo.write({
                        'state': 'error',
                        'failure_type': 'sms_server',
                        'gateway_state': 'error',
                    })
                    if sms_sudo.sms_tracker_id:
                        sms_sudo.sms_tracker_id._action_update_from_sms_state(
                            'error', failure_type='sms_server')
                    self.env.cr.commit()
                    continue
            else:
                # Find the partner for domain filtering
                partner = None
                if sms.partner_id:
                    partner = sms.partner_id
                elif sms.number:
                    partner = self.env['res.partner'].sudo().search([
                        '|',
                        ('mobile', '=', sms.number),
                        ('phone', '=', sms.number),
                    ], limit=1)

                available_phones = self.env['sms.gateway.phone']._get_available_phones(partner=partner)

                # Find first phone with enough capacity (including batch usage)
                phone = None
                for candidate in available_phones:
                    remaining = self._phone_remaining_capacity(candidate) - capacity_used.get(candidate.id, 0)
                    if remaining >= segments:
                        phone = candidate
                        break

                if not phone:
                    _logger.warning('No gateway phone with capacity for SMS %s to %s (%d segments)',
                                    sms.id, sms.number, segments)
                    sms_sudo.write({
                        'state': 'error',
                        'failure_type': 'sms_server',
                        'gateway_state': 'error',
                    })
                    if sms_sudo.sms_tracker_id:
                        sms_sudo.sms_tracker_id._action_update_from_sms_state(
                            'error', failure_type='sms_server')
                    self.env.cr.commit()
                    continue
                # Empty — mobile app decides which SIM to use
                sim_number = False

            # Assign to gateway phone queue
            write_vals = {
                'gateway_phone_id': phone.id,
                'gateway_state': 'pending',
                'state': 'pending',
                'failure_type': False,
                'body': body,
            }
            if sim_number:
                write_vals['gateway_sim_number'] = sim_number
            sms_sudo.write(write_vals)

            # Track in-memory for this batch; DB counters update on confirm
            capacity_used[phone.id] = capacity_used.get(phone.id, 0) + segments

            # Update tracker
            if sms_sudo.sms_tracker_id:
                sms_sudo.sms_tracker_id._action_update_from_sms_state('pending')

            _logger.info('Assigned SMS %s (%d seg) to gateway phone %s (%s)',
                         sms.id, segments, phone.name, phone.phone_number)

            # Commit after each SMS to prevent rollback issues
            self.env.cr.commit()

        # Check mailing completion
        for mailing in related_mailings:
            if hasattr(mailing, 'paused') and mailing.paused:
                continue
            pending_count = self.env['sms.sms'].sudo().search_count([
                ('mailing_id', '=', mailing.id),
                ('state', 'in', ['outgoing', 'pending']),
            ])
            if pending_count == 0:
                _logger.info('All SMS for mailing %s processed. Marking as done.', mailing.id)
                mailing.sudo().write({'state': 'done', 'sent_date': fields.Datetime.now()})
                self.env.cr.commit()

    def _update_gateway_status(self, sms_id, status, error_message=None):
        """Update SMS status from gateway phone confirmation."""
        sms = self.sudo().browse(sms_id)
        if not sms.exists():
            _logger.warning('SMS Gateway: SMS %s not found for status update', sms_id)
            return False

        try:
            if status == 'sending':
                sms.write({'gateway_state': 'sending'})
            elif status == 'sent':
                if sms.sms_tracker_id:
                    sms.sms_tracker_id._action_update_from_sms_state('sent')
                sms.write({
                    'state': 'sent',
                    'gateway_state': 'sent',
                    'failure_type': False,
                })
                # Increment counters only on confirmed delivery
                if sms.gateway_phone_id:
                    segments = sms_segment_count(sms.body)
                    self.env.cr.execute(
                        "UPDATE sms_gateway_phone "
                        "SET sent_today = sent_today + %s, "
                        "    sent_month = sent_month + %s, "
                        "    sent_total = sent_total + %s "
                        "WHERE id = %s",
                        (segments, segments, segments, sms.gateway_phone_id.id),
                    )
                    sms.gateway_phone_id.invalidate_recordset(
                        ['sent_today', 'sent_month', 'sent_total'])
            elif status == 'error':
                if sms.sms_tracker_id:
                    sms.sms_tracker_id._action_update_from_sms_state(
                        'error', failure_type='sms_server')
                sms.write({
                    'state': 'error',
                    'gateway_state': 'error',
                    'failure_type': 'sms_server',
                })

            _logger.info('SMS Gateway: Updated SMS %s to status %s', sms_id, status)
            if sms.mail_message_id:
                sms.mail_message_id._notify_message_notification_update()
        except Exception:
            _logger.exception('SMS Gateway: Error updating SMS %s status to %s', sms_id, status)
            return False
        return True
