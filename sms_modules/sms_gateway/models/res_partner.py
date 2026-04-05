# -*- coding: utf-8 -*-

from datetime import timedelta

from odoo import api, fields, models


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
        string='Days Since Last SMS', readonly=True,
        compute='_compute_stats_last_sms_days',
        search='_search_stats_last_sms_days',
        help='Days since last SMS — 0 if never contacted. Search operator '
             '> N matches partners with no stats row OR with last SMS older '
             'than N days, so campaigns can exclude recently-contacted '
             'partners without filtering out never-contacted ones.',
    )

    def _compute_stats_last_sms_days(self):
        today = fields.Date.today()
        for rec in self:
            stats = rec.stats_id[:1]
            if stats and stats.last_sms_sent_date:
                rec.stats_last_sms_days = (today - stats.last_sms_sent_date).days
            else:
                rec.stats_last_sms_days = 0

    @api.model
    def _search_stats_last_sms_days(self, operator, value):
        """NULL-aware search: a partner with no stats row (or NULL
        ``last_sms_sent_date``) counts as "never contacted" and must be
        matched by ``> N`` / ``>= N`` so campaigns don't accidentally exclude
        brand-new recipients. Otherwise falls back to a date comparison.
        """
        value = int(value or 0)
        ref_date = fields.Date.today() - timedelta(days=value)
        # Invert operator: more days ago ⇔ earlier date
        op_map = {'>': '<', '>=': '<=', '<': '>', '<=': '>=', '=': '=', '!=': '!='}
        date_op = op_map.get(operator, operator)

        # Partners with a matching stats row
        stats = self.env['res.partner.stats'].sudo().search(
            [('last_sms_sent_date', date_op, ref_date)],
        )
        partner_ids = stats.mapped('partner_id').ids

        if operator in ('>', '>='):
            # Include partners with no stats row at all (never contacted)
            return ['|',
                    ('id', 'in', partner_ids),
                    ('stats_id', '=', False)]
        return [('id', 'in', partner_ids)]
