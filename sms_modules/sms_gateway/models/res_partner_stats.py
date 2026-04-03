# -*- coding: utf-8 -*-

import logging
from datetime import timedelta

from odoo import api, fields, models

_logger = logging.getLogger(__name__)

CONFIRMED_SALE_STATES = ('sale',)
CONFIRMED_POS_STATES = ('paid', 'done', 'invoiced')


class ResPartnerStats(models.Model):
    _name = 'res.partner.stats'
    _description = 'Partner Order Statistics'
    _rec_name = 'partner_id'

    partner_id = fields.Many2one(
        'res.partner', required=True, ondelete='cascade', index=True,
    )
    order_count = fields.Integer(string='Total Orders', default=0)
    first_order_date = fields.Date(string='First Order')
    last_order_date = fields.Date(string='Last Order')
    bought_last_3m = fields.Boolean(string='Bought Last 3 Months', default=False)
    is_new_customer = fields.Boolean(
        string='New Customer',
        help='First purchase was in the last 3 months.',
        default=False,
    )
    did_not_buy_last_3m = fields.Boolean(
        string='Did Not Buy Last 3 Months',
        help='Has orders before 3 months ago but none in the last 3 months.',
        default=False,
    )
    is_returning_customer = fields.Boolean(
        string='Returning Customer',
        help='Bought >1 year ago, had a gap of 6+ months, then bought again in last 3 months.',
        default=False,
    )

    _sql_constraints = [
        ('partner_unique', 'UNIQUE(partner_id)', 'Only one stats record per partner.'),
    ]

    @api.model
    def _cron_recompute_all(self):
        """Recompute stats for all partners with orders. Called by scheduled action."""
        _logger.info('res.partner.stats: starting full recomputation')
        cr = self.env.cr
        today = fields.Date.today()
        cutoff_3m = today - timedelta(days=90)
        cutoff_6m = today - timedelta(days=180)
        cutoff_1y = today - timedelta(days=365)

        # Gather all order data per partner in one query
        cr.execute("""
            WITH all_orders AS (
                SELECT partner_id, date_order::date AS order_date
                FROM sale_order
                WHERE state IN %s AND partner_id IS NOT NULL
                UNION ALL
                SELECT partner_id, date_order::date AS order_date
                FROM pos_order
                WHERE state IN %s AND partner_id IS NOT NULL
            ),
            partner_agg AS (
                SELECT
                    partner_id,
                    COUNT(*) AS order_count,
                    MIN(order_date) AS first_order_date,
                    MAX(order_date) AS last_order_date,
                    BOOL_OR(order_date >= %s) AS bought_last_3m,
                    BOOL_OR(order_date < %s) AS has_old_orders,
                    BOOL_OR(order_date >= %s AND order_date < %s) AS bought_6m_to_3m,
                    BOOL_OR(order_date < %s) AS bought_before_1y
                FROM all_orders
                GROUP BY partner_id
            )
            SELECT
                partner_id,
                order_count,
                first_order_date,
                last_order_date,
                bought_last_3m,
                -- is_new_customer: first order is within last 3 months
                (first_order_date >= %s) AS is_new_customer,
                -- did_not_buy_last_3m: has old orders but none recent
                (has_old_orders AND NOT bought_last_3m) AS did_not_buy_last_3m,
                -- is_returning_customer: bought >1y ago, gap of 6m+, bought again last 3m
                (bought_before_1y AND NOT bought_6m_to_3m AND bought_last_3m) AS is_returning_customer
            FROM partner_agg
        """, (
            CONFIRMED_SALE_STATES, CONFIRMED_POS_STATES,
            cutoff_3m,   # bought_last_3m
            cutoff_3m,   # has_old_orders
            cutoff_6m, cutoff_3m,  # bought_6m_to_3m
            cutoff_1y,   # bought_before_1y
            cutoff_3m,   # is_new_customer
        ))
        rows = cr.fetchall()
        if not rows:
            _logger.info('res.partner.stats: no orders found')
            return

        partner_ids = [r[0] for r in rows]

        # Load existing stats
        existing = {
            s.partner_id.id: s
            for s in self.sudo().search([('partner_id', 'in', partner_ids)])
        }

        to_create = []
        for row in rows:
            pid, count, first_dt, last_dt, b3m, is_new, no_buy, returning = row
            vals = {
                'order_count': count,
                'first_order_date': first_dt,
                'last_order_date': last_dt,
                'bought_last_3m': b3m,
                'is_new_customer': is_new,
                'did_not_buy_last_3m': no_buy,
                'is_returning_customer': returning,
            }
            if pid in existing:
                existing[pid].sudo().write(vals)
            else:
                vals['partner_id'] = pid
                to_create.append(vals)

        if to_create:
            self.sudo().create(to_create)

        # Clean up stats for partners who no longer have orders
        stale = self.sudo().search([('partner_id', 'not in', partner_ids)])
        if stale:
            stale.unlink()

        _logger.info(
            'res.partner.stats: recomputed %d partners (%d created, %d updated, %d removed)',
            len(rows), len(to_create), len(rows) - len(to_create), len(stale),
        )
