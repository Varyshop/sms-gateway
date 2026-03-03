# -*- coding: utf-8 -*-

from odoo import api, fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    sms_gateway_enabled = fields.Boolean(
        string='SMS Gateway Enabled',
        compute='_compute_sms_gateway_enabled',
    )
    sms_gateway_phone_count = fields.Integer(
        string='Gateway Phones',
        compute='_compute_sms_gateway_phone_count',
    )
    sms_gateway_online_count = fields.Integer(
        string='Online Phones',
        compute='_compute_sms_gateway_phone_count',
    )

    def _compute_sms_gateway_enabled(self):
        for record in self:
            record.sms_gateway_enabled = bool(
                self.env['sms.gateway.phone'].sudo().search_count([
                    ('active', '=', True),
                    ('state', '=', 'online'),
                ])
            )

    def _compute_sms_gateway_phone_count(self):
        for record in self:
            record.sms_gateway_phone_count = self.env['sms.gateway.phone'].sudo().search_count([
                ('active', '=', True),
            ])
            record.sms_gateway_online_count = self.env['sms.gateway.phone'].sudo().search_count([
                ('active', '=', True),
                ('state', '=', 'online'),
            ])

    def action_open_gateway_phones(self):
        return {
            'type': 'ir.actions.act_window',
            'name': 'SMS Gateway Phones',
            'res_model': 'sms.gateway.phone',
            'view_mode': 'list,form',
            'target': 'current',
        }
