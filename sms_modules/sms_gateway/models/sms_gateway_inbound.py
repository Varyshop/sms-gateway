# -*- coding: utf-8 -*-

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
