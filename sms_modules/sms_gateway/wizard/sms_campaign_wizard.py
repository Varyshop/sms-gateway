# -*- coding: utf-8 -*-

import ast
import logging

from odoo import api, fields, models, _
from odoo.exceptions import UserError

from ..tools.sms_utils import sms_segment_count

_logger = logging.getLogger(__name__)


class SmsCampaignWizardPhone(models.TransientModel):
    _name = 'sms.campaign.wizard.phone'
    _description = 'SMS Campaign Wizard – Phone Line'

    wizard_id = fields.Many2one('sms.campaign.wizard', ondelete='cascade')
    phone_id = fields.Many2one('sms.gateway.phone', string='Device')
    phone_name = fields.Char(string='Device Name')
    phone_number = fields.Char(string='Phone Number')
    phone_number_2 = fields.Char(string='Phone Number 2')
    state = fields.Selection([('offline', 'Offline'), ('online', 'Online')], string='Status')
    sent_today = fields.Integer(string='Sent Today')
    daily_limit = fields.Integer(string='Daily Limit')
    sent_month = fields.Integer(string='Sent This Period')
    monthly_limit = fields.Integer(string='Monthly Limit')
    remaining_capacity = fields.Integer(string='Remaining')
    selected = fields.Boolean(string='Use', default=False)
    use_sim = fields.Selection([
        ('auto', 'Auto'),
        ('sim1', 'SIM 1'),
        ('sim2', 'SIM 2'),
        ('split', 'Split (both)'),
    ], string='SIM', default='auto')


class SmsCampaignWizard(models.TransientModel):
    _name = 'sms.campaign.wizard'
    _description = 'Create SMS Campaign'

    segment_id = fields.Many2one(
        'sms.marketing.segment', string='Segment',
        help='Customer segment to target.',
    )
    template_id = fields.Many2one(
        'sms.marketing.template', string='Template',
        help='Pre-built SMS template. Fills in body text and settings.',
    )
    body = fields.Text(string='SMS Text', required=True)
    sms_allow_unsubscribe = fields.Boolean(string='Add STOP message', default=True)
    exclude_contacted_days = fields.Integer(
        string='Exclude Contacted (days)', default=0,
        help='Exclude partners who received SMS in the last N days.',
    )
    recipient_limit = fields.Integer(
        string='Recipient Limit', default=0,
        help='Max recipients. 0 = no limit.',
    )
    phone_line_ids = fields.One2many(
        'sms.campaign.wizard.phone', 'wizard_id', string='Gateway Phones',
    )
    recipient_count = fields.Integer(string='Matching Recipients', compute='_compute_preview')
    effective_count = fields.Integer(string='Will Send To', compute='_compute_preview')
    total_capacity = fields.Integer(string='Selected Capacity', compute='_compute_preview')
    sms_segment_count_preview = fields.Integer(string='≈ SMS Segments', compute='_compute_preview')
    can_fit_all = fields.Boolean(compute='_compute_preview')
    send_now = fields.Boolean(string='Send Immediately', default=True,
                              help='Uncheck to create the campaign paused.')

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        if 'phone_line_ids' in fields_list:
            phones = self.env['sms.gateway.phone'].sudo().search([('active', '=', True)])
            lines = []
            for phone in phones:
                remaining = self._phone_remaining_capacity(phone)
                lines.append(fields.Command.create({
                    'phone_id': phone.id,
                    'phone_name': phone.name,
                    'phone_number': phone.phone_number,
                    'phone_number_2': phone.phone_number_2 or '',
                    'state': phone.state,
                    'sent_today': phone.sent_today,
                    'daily_limit': phone.daily_limit,
                    'sent_month': phone.sent_month,
                    'monthly_limit': phone.monthly_limit,
                    'remaining_capacity': remaining,
                    'selected': phone.state == 'online',
                    'use_sim': 'auto',
                }))
            res['phone_line_ids'] = lines
        return res

    @staticmethod
    def _phone_remaining_capacity(phone):
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

    @api.onchange('template_id')
    def _onchange_template_id(self):
        if self.template_id:
            self.body = self.template_id.body
            self.exclude_contacted_days = self.template_id.exclude_contacted_days
            self.recipient_limit = self.template_id.default_limit
            if self.template_id.segment_ids and len(self.template_id.segment_ids) == 1:
                self.segment_id = self.template_id.segment_ids[0]
            if self.template_id.phone_id:
                for line in self.phone_line_ids:
                    line.selected = (line.phone_id == self.template_id.phone_id)

    @api.depends('segment_id', 'exclude_contacted_days', 'recipient_limit',
                 'body', 'phone_line_ids.selected', 'phone_line_ids.remaining_capacity')
    def _compute_preview(self):
        for wiz in self:
            # Recipient count
            count = 0
            if wiz.segment_id:
                selected_phones = wiz.phone_line_ids.filtered('selected').mapped('phone_id')
                phone = selected_phones[:1] if selected_phones else None
                count = wiz.segment_id._get_recipient_count(
                    phone=phone,
                    exclude_contacted_days=wiz.exclude_contacted_days,
                )
            wiz.recipient_count = count

            limit = wiz.recipient_limit or 0
            wiz.effective_count = min(count, limit) if limit > 0 else count

            segs_per_msg = sms_segment_count(wiz.body) if wiz.body else 1
            wiz.sms_segment_count_preview = wiz.effective_count * segs_per_msg

            seen_phones = set()
            cap = 0
            for line in wiz.phone_line_ids.filtered('selected'):
                pid = line.phone_id.id
                if pid and pid not in seen_phones:
                    cap += line.remaining_capacity
                    seen_phones.add(pid)
            wiz.total_capacity = cap
            wiz.can_fit_all = cap >= wiz.sms_segment_count_preview

    def action_create_campaign(self):
        self.ensure_one()

        if not self.segment_id:
            raise UserError(_('Please select a customer segment.'))
        if not self.body or not self.body.strip():
            raise UserError(_('Please enter SMS text.'))

        selected_lines = self.phone_line_ids.filtered('selected')
        if not selected_lines:
            raise UserError(_('Please select at least one gateway phone.'))

        selected_phones = selected_lines.mapped('phone_id')
        primary_phone = selected_phones[0]

        # Build storable domain
        exclude_days = self.exclude_contacted_days or 0
        limit = self.recipient_limit if self.recipient_limit > 0 else None
        stored_domain = self.segment_id._get_storable_domain(
            phone=primary_phone,
            exclude_contacted_days=exclude_days,
            limit=limit,
        )

        count = self.env['res.partner'].sudo().search_count(stored_domain)
        if not count:
            raise UserError(_('No recipients match the selected segment and filters.'))

        partner_model = self.env['ir.model'].sudo().search([
            ('model', '=', 'res.partner'),
        ], limit=1)

        company = self.env.company.sudo()
        email_from = company.email or company.partner_id.email or 'noreply@example.com'

        subject = '%s - %s' % (
            self.segment_id.name,
            fields.Datetime.now().strftime('%d.%m.%Y %H:%M'),
        )

        mailing = self.env['mailing.mailing'].sudo().create({
            'subject': subject,
            'mailing_type': 'sms',
            'body_plaintext': self.body.strip(),
            'email_from': email_from,
            'sms_provider': 'gateway',
            'mailing_model_id': partner_model.id,
            'mailing_domain': repr(stored_domain),
            'gateway_phone_forced_id': primary_phone.id,
            'recipient_limit': self.recipient_limit if self.recipient_limit > 0 else 0,
            'marketing_template_id': self.template_id.id if self.template_id else False,
            'sms_allow_unsubscribe': self.sms_allow_unsubscribe,
            'exclude_contacted_days': exclude_days,
        })

        # Generate SMS queue
        mailing.state = 'in_queue'
        mailing.action_force_create_sms_queue()

        if not self.send_now:
            mailing.write({'state': 'in_queue', 'paused': True})
        else:
            # Assign SIM numbers based on phone line selections
            self._assign_sims_to_sms(mailing, selected_lines)

        sms_count = self.env['sms.sms'].sudo().search_count([
            ('mailing_id', '=', mailing.id),
            ('state', 'in', ('pending', 'outgoing')),
        ])

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('SMS Campaign Created'),
                'message': _(
                    'Campaign "%(name)s" created with %(count)s SMS.%(paused)s'
                ) % {
                    'name': subject,
                    'count': sms_count,
                    'paused': _('\nCampaign is PAUSED — resume it manually.') if not self.send_now else '',
                },
                'type': 'success',
                'sticky': True,
                'next': {
                    'type': 'ir.actions.act_window',
                    'res_model': 'mailing.mailing',
                    'res_id': mailing.id,
                    'views': [(False, 'form')],
                    'target': 'current',
                },
            },
        }

    def _assign_sims_to_sms(self, mailing, selected_lines):
        """Re-assign SMS across selected phones/SIMs with round-robin."""
        sms_records = self.env['sms.sms'].sudo().search([
            ('mailing_id', '=', mailing.id),
            ('state', 'in', ('pending', 'outgoing')),
        ])
        if not sms_records:
            return

        # Build slot list: [(phone, sim_number), ...]
        slots = []
        for line in selected_lines:
            phone = line.phone_id
            if line.use_sim == 'sim1' and phone.phone_number:
                slots.append((phone, phone.phone_number))
            elif line.use_sim == 'sim2' and phone.phone_number_2:
                slots.append((phone, phone.phone_number_2))
            elif line.use_sim == 'split':
                if phone.phone_number:
                    slots.append((phone, phone.phone_number))
                if phone.phone_number_2:
                    slots.append((phone, phone.phone_number_2))
            else:
                slots.append((phone, False))

        if not slots:
            return

        slot_count = len(slots)
        for i, sms in enumerate(sms_records):
            phone, sim_number = slots[i % slot_count]
            vals = {
                'sms_provider': 'gateway',
                'gateway_phone_id': phone.id,
                'gateway_state': 'pending',
                'state': 'pending',
                'failure_type': False,
            }
            if sim_number:
                vals['gateway_sim_number'] = sim_number
            sms.sudo().with_context(sms_skip_msg_notification=True).write(vals)

        # Wake phones via FCM
        for line in selected_lines:
            try:
                from ..tools.fcm_service import send_fcm_wake
                send_fcm_wake(self.env, line.phone_id)
            except Exception:
                pass
