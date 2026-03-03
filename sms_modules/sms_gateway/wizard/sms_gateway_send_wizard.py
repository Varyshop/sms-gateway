# -*- coding: utf-8 -*-

import logging

from odoo import api, fields, models, _
from odoo.exceptions import UserError

from ..tools.sms_utils import sms_segment_count

_logger = logging.getLogger(__name__)


def _sim_remaining_capacity(phone):
    """Return effective remaining segment capacity for a gateway phone.

    Capacity is per-device (shared across both SIMs).
    Counters reflect confirmed sends only; we also count queued segments.
    """
    if phone.state != 'online':
        return 0
    in_queue = 0
    queued_sms = phone.env['sms.sms'].sudo().search([
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


class SmsGatewaySendWizardLine(models.TransientModel):
    _name = 'sms.gateway.send.wizard.line'
    _description = 'Gateway Send Wizard – SIM Line'

    wizard_id = fields.Many2one('sms.gateway.send.wizard', ondelete='cascade')
    # NOTE: no readonly=True here — Odoo 18 web_save strips readonly fields
    # from One2many command payloads, causing phone_id to become False.
    # Read-only appearance is enforced in the XML view instead.
    phone_id = fields.Many2one('sms.gateway.phone', string='Device')
    sim_number = fields.Char(string='Phone Number')
    sim_slot = fields.Char(string='SIM')
    selected = fields.Boolean(string='Use', default=False)

    # Stored snapshot values (populated in default_get, independent of related)
    phone_name = fields.Char(string='Device Name')
    state = fields.Selection([('offline', 'Offline'), ('online', 'Online')],
                             string='Status')
    sent_today = fields.Integer(string='Sent Today')
    daily_limit = fields.Integer(string='Daily Limit')
    sent_month = fields.Integer(string='Sent This Period')
    monthly_limit = fields.Integer(string='Monthly Limit')
    remaining_capacity = fields.Integer(string='Remaining')


class SmsGatewaySendWizard(models.TransientModel):
    _name = 'sms.gateway.send.wizard'
    _description = 'Send SMS via Gateway Phones'

    sms_ids = fields.Many2many(
        'sms.sms', 'sms_gateway_send_wizard_sms_rel',
        'wizard_id', 'sms_id',
        string='SMS Records', readonly=True,
    )
    line_ids = fields.One2many(
        'sms.gateway.send.wizard.line', 'wizard_id', string='SIM Numbers',
    )
    sms_count = fields.Integer(compute='_compute_summary')
    total_segments = fields.Integer(string='Total Segments', compute='_compute_summary')
    total_capacity = fields.Integer(string='Selected Capacity', compute='_compute_summary')
    can_fit_all = fields.Boolean(compute='_compute_summary')

    @api.depends('sms_ids', 'line_ids.selected', 'line_ids.remaining_capacity')
    def _compute_summary(self):
        for wiz in self:
            segs = sum(sms_segment_count(s.body) for s in wiz.sms_ids)
            # Capacity is per-device; avoid double-counting when both SIMs of same device are selected
            seen_phones = set()
            cap = 0
            for line in wiz.line_ids.filtered('selected'):
                pid = line.phone_id.id
                if pid and pid not in seen_phones:
                    cap += line.remaining_capacity
                    seen_phones.add(pid)
            wiz.sms_count = len(wiz.sms_ids)
            wiz.total_segments = segs
            wiz.total_capacity = cap
            wiz.can_fit_all = cap >= segs

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        active_ids = self.env.context.get('active_ids', [])
        if active_ids and 'sms_ids' in fields_list:
            valid = self.env['sms.sms'].browse(active_ids).filtered(
                lambda s: s.state in ('outgoing', 'error')
            )
            res['sms_ids'] = [fields.Command.set(valid.ids)]

        # Pre-populate SIM lines from all active online phones
        if 'line_ids' in fields_list:
            phones = self.env['sms.gateway.phone'].sudo().search([
                ('active', '=', True),
                ('state', '=', 'online'),
            ])
            lines = []
            for phone in phones:
                remaining = _sim_remaining_capacity(phone)
                base_vals = {
                    'phone_id': phone.id,
                    'phone_name': phone.name,
                    'state': phone.state,
                    'sent_today': phone.sent_today,
                    'daily_limit': phone.daily_limit,
                    'sent_month': phone.sent_month,
                    'monthly_limit': phone.monthly_limit,
                    'remaining_capacity': remaining,
                }
                lines.append(fields.Command.create({
                    **base_vals,
                    'sim_number': phone.phone_number,
                    'sim_slot': 'SIM 1',
                    'selected': True,
                }))
                if phone.phone_number_2:
                    lines.append(fields.Command.create({
                        **base_vals,
                        'sim_number': phone.phone_number_2,
                        'sim_slot': 'SIM 2',
                        'selected': False,
                    }))
            res['line_ids'] = lines
        return res

    def action_send(self):
        self.ensure_one()
        selected_lines = self.line_ids.filtered('selected')
        if not selected_lines:
            raise UserError(_('Please select at least one SIM number.'))

        # Re-read SMS records — accept both outgoing and error (for resend)
        sms_records = self.env['sms.sms'].browse(self.sms_ids.ids).filtered(
            lambda s: s.state in ('outgoing', 'error')
        )
        if not sms_records:
            raise UserError(_('No SMS records to assign (only outgoing or error SMS can be sent).'))

        # Build mutable capacity slots for round-robin.
        # Limits are per-device, so if both SIMs of the same device are selected,
        # they share the same capacity pool.
        # Invalidate phone records to get fresh counter values from DB
        phone_ids = selected_lines.mapped('phone_id')
        phone_ids.invalidate_recordset(['sent_today', 'sent_month'])

        device_capacity = {}  # phone_id → remaining segments
        slots = []
        for line in selected_lines:
            pid = line.phone_id.id
            if pid not in device_capacity:
                device_capacity[pid] = _sim_remaining_capacity(line.phone_id)
            slots.append({
                'phone': line.phone_id,
                'sim_number': line.sim_number,
                'device_cap_key': pid,
            })

        _logger.info(
            'Gateway wizard: %d SMS, %d slots, capacity=%s',
            len(sms_records), len(slots), device_capacity,
        )

        slot_count = len(slots)
        slot_idx = 0
        assigned = 0
        skipped = 0

        for sms in sms_records:
            body = self.env['sms.sms']._replace_unsubscribe_url(sms.body)
            segments = sms_segment_count(body)
            _logger.info(
                'Gateway wizard: SMS %s, body_len=%d, segments=%d, state=%s',
                sms.id, len(body or ''), segments, sms.state,
            )

            found = False
            for _try in range(slot_count):
                slot = slots[slot_idx % slot_count]
                slot_idx += 1
                cap_key = slot['device_cap_key']
                _logger.info(
                    'Gateway wizard: trying slot %s (phone %s), cap=%d, need=%d',
                    slot['sim_number'], cap_key, device_capacity[cap_key], segments,
                )
                if device_capacity[cap_key] >= segments:
                    phone = slot['phone']
                    sms.sudo().with_context(sms_skip_msg_notification=True).write({
                        'sms_provider': 'gateway',
                        'gateway_phone_id': phone.id,
                        'gateway_sim_number': slot['sim_number'],
                        'gateway_state': 'pending',
                        'state': 'pending',
                        'failure_type': False,
                        'body': body,
                    })
                    # DB counters update on confirm from device
                    device_capacity[cap_key] -= segments
                    assigned += 1
                    found = True
                    break

            if not found:
                skipped += 1

        self.env.cr.commit()

        msg = _('Assigned %s SMS to gateway phones.', assigned)
        ntype = 'success'
        if skipped:
            msg += '\n' + _('%s SMS could not be assigned (capacity exceeded).', skipped)
            ntype = 'warning'

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Gateway Send'),
                'message': msg,
                'type': ntype,
                'sticky': bool(skipped),
                'next': {'type': 'ir.actions.act_window_close'},
            },
        }
