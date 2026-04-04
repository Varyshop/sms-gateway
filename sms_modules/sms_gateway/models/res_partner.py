# -*- coding: utf-8 -*-

from odoo import fields, models


class ResPartner(models.Model):
    _inherit = 'res.partner'

    stats_id = fields.One2many(
        'res.partner.stats', 'partner_id', string='Order Stats',
    )
    stats_order_count = fields.Integer(
        related='stats_id.order_count', string='Order Count', readonly=True,
    )
    stats_first_order = fields.Date(
        related='stats_id.first_order_date', string='First Order', readonly=True,
    )
    stats_last_order = fields.Date(
        related='stats_id.last_order_date', string='Last Order', readonly=True,
    )
    stats_bought_last_3m = fields.Boolean(
        related='stats_id.bought_last_3m', string='Bought Last 3M', readonly=True,
    )
    stats_is_new_customer = fields.Boolean(
        related='stats_id.is_new_customer', string='New Customer', readonly=True,
    )
    stats_did_not_buy_3m = fields.Boolean(
        related='stats_id.did_not_buy_last_3m', string='Did Not Buy 3M', readonly=True,
    )
    stats_returning = fields.Boolean(
        related='stats_id.is_returning_customer', string='Returning Customer', readonly=True,
    )
    stats_last_sms_sent = fields.Date(
        related='stats_id.last_sms_sent_date', string='Last SMS Sent', readonly=True,
    )
    stats_last_order_days = fields.Integer(
        related='stats_id.last_order_days', string='Days Since Last Order', readonly=True,
    )
    stats_first_order_days = fields.Integer(
        related='stats_id.first_order_days', string='Days Since First Order', readonly=True,
    )
    stats_last_sms_days = fields.Integer(
        related='stats_id.last_sms_sent_days', string='Days Since Last SMS', readonly=True,
    )
