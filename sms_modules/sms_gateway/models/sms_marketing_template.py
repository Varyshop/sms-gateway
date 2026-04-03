# -*- coding: utf-8 -*-

from odoo import fields, models


class SmsMarketingTemplate(models.Model):
    _name = 'sms.marketing.template'
    _description = 'SMS Marketing Template'
    _order = 'sequence, name'

    name = fields.Char(required=True, translate=True)
    body = fields.Text(
        required=True,
        help='SMS text. Supports placeholders: {{object.name}}, {{object.email}}, etc.',
    )
    phone_id = fields.Many2one(
        'sms.gateway.phone', required=True, ondelete='cascade',
        string='Gateway Phone',
    )
    segment_ids = fields.Many2many(
        'sms.marketing.segment', string='Available Segments',
    )
    active = fields.Boolean(default=True)
    sequence = fields.Integer(default=10)
    default_limit = fields.Integer(
        default=100, string='Default Recipient Limit',
        help='Suggested number of recipients shown in the app.',
    )
    max_limit = fields.Integer(
        default=500, string='Max Recipient Limit',
        help='Hard cap on number of recipients.',
    )
