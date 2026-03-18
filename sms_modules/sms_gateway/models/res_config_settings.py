# -*- coding: utf-8 -*-

from odoo import api, fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    sms_gateway_force = fields.Boolean(
        string='Force SMS Gateway',
        config_parameter='sms_gateway.force_gateway',
        help='When enabled, all SMS will be routed through the Gateway phones '
             'instead of the default SMS provider.',
    )
    sms_gateway_fcm_enabled = fields.Boolean(
        string='FCM Push Enabled',
        config_parameter='sms_gateway.fcm_enabled',
        help='Enable Firebase Cloud Messaging push notifications to wake '
             'gateway phones instantly when new SMS are queued. '
             'Requires firebase-admin Python package and service account credentials.',
    )
    sms_gateway_fcm_credentials_json = fields.Char(
        string='FCM Service Account JSON',
        config_parameter='sms_gateway.fcm_credentials_json',
        help='Paste the full Firebase service account JSON here. '
             'This is the preferred method — no file needed on server.',
    )
    sms_gateway_fcm_credentials_path = fields.Char(
        string='FCM Credentials Path (alternative)',
        config_parameter='sms_gateway.fcm_credentials_path',
        help='Alternative: absolute path to Firebase service account JSON file on the server. '
             'Used only if the inline JSON above is empty.',
    )
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
