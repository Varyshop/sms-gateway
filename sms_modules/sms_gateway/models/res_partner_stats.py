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
    last_sms_sent_date = fields.Date(
        string='Last SMS Sent',
        help='Date of the last successfully sent SMS to this partner.',
    )
    last_order_days = fields.Integer(
        string='Days Since Last Order',
        compute='_compute_order_days', search='_search_last_order_days',
    )
    first_order_days = fields.Integer(
        string='Days Since First Order',
        compute='_compute_order_days', search='_search_first_order_days',
    )
    last_sms_sent_days = fields.Integer(
        string='Days Since Last SMS',
        compute='_compute_sms_days', search='_search_last_sms_sent_days',
    )

    @api.depends('last_order_date', 'first_order_date')
    def _compute_order_days(self):
        today = fields.Date.today()
        for rec in self:
            rec.last_order_days = (today - rec.last_order_date).days if rec.last_order_date else 0
            rec.first_order_days = (today - rec.first_order_date).days if rec.first_order_date else 0

    @api.depends('last_sms_sent_date')
    def _compute_sms_days(self):
        today = fields.Date.today()
        for rec in self:
            # -1 sentinel = never contacted; distinguishes UI from "today" (0).
            rec.last_sms_sent_days = (
                (today - rec.last_sms_sent_date).days
                if rec.last_sms_sent_date else -1
            )

    @staticmethod
    def _days_to_date(operator, value):
        """Convert a 'days ago' comparison to a date comparison.

        ``("last_order_days", ">", 10)``  means last order was MORE than
        10 days ago → ``last_order_date < today - 10``.

        The operator is inverted because more days ago = earlier date.
        """
        ref_date = fields.Date.today() - timedelta(days=int(value))
        op_map = {'>': '<', '>=': '<=', '<': '>', '<=': '>=', '=': '=', '!=': '!='}
        return ref_date, op_map.get(operator, operator)

    def _search_last_order_days(self, operator, value):
        ref_date, date_op = self._days_to_date(operator, value)
        return [('last_order_date', date_op, ref_date)]

    def _search_first_order_days(self, operator, value):
        ref_date, date_op = self._days_to_date(operator, value)
        return [('first_order_date', date_op, ref_date)]

    def _search_last_sms_sent_days(self, operator, value):
        ref_date, date_op = self._days_to_date(operator, value)
        # "More than N days ago" must also include partners that have never
        # received an SMS (last_sms_sent_date IS NULL) — they trivially
        # satisfy "not contacted in the last N days".
        if operator in ('>', '>='):
            return ['|',
                    ('last_sms_sent_date', '=', False),
                    ('last_sms_sent_date', date_op, ref_date)]
        return [('last_sms_sent_date', date_op, ref_date)]

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

        # Update last_sms_sent_date from mailing_trace
        self._update_last_sms_sent()

    @api.model
    def _touch_last_sms_sent(self, partner_id, sent_date=None):
        """Upsert ``last_sms_sent_date`` for a single partner in real time.

        Called from ``sms.sms._update_gateway_status`` on every successful
        send so the exclusion filter (``stats_last_sms_days``) reflects
        today's sends immediately, without waiting for the nightly cron.

        Creates the stats row if missing — partners without orders would
        otherwise have no row and the filter could miss them.
        """
        if not partner_id:
            return
        sent_date = sent_date or fields.Date.today()
        rec = self.sudo().search([('partner_id', '=', partner_id)], limit=1)
        if rec:
            if not rec.last_sms_sent_date or rec.last_sms_sent_date < sent_date:
                rec.write({'last_sms_sent_date': sent_date})
        else:
            self.sudo().create({
                'partner_id': partner_id,
                'last_sms_sent_date': sent_date,
            })

    @api.model
    def _update_last_sms_sent(self):
        """Recompute ``last_sms_sent_date`` only for partners that actually
        have SMS traces — never touches the millions of partners who never
        received an SMS.

        Uses bulk SQL operations (INSERT ... ON CONFLICT DO UPDATE) to stay
        fast even for tens of thousands of SMS recipients. Falls back to
        ORM ``create`` for new stats rows so _sql_constraints and hooks
        still fire.
        """
        cr = self.env.cr
        # NOTE: In Odoo 18 mailing.trace.trace_status:
        #   'pending' = Sent (provider accepted, delivery unconfirmed)
        #   'sent'    = Delivered (confirmed by provider/gateway)
        #   'open'    = Clicked
        #   'reply'   = Replied
        # All of these count as "SMS has been sent to this partner" for
        # exclusion purposes — we must NOT re-contact them.
        cr.execute("""
            SELECT res_id, MAX(write_date)::date
            FROM mailing_trace
            WHERE model = 'res.partner'
              AND trace_type = 'sms'
              AND trace_status IN ('pending', 'sent', 'open', 'reply')
              AND res_id IS NOT NULL
            GROUP BY res_id
        """)
        sms_dates = cr.fetchall()
        if not sms_dates:
            _logger.info('res.partner.stats: no SMS traces to process')
            return

        partner_ids = [row[0] for row in sms_dates]

        # Bulk fetch existing stats rows keyed by partner_id
        cr.execute("""
            SELECT partner_id, last_sms_sent_date
            FROM res_partner_stats
            WHERE partner_id = ANY(%s)
        """, (partner_ids,))
        existing = {pid: last for pid, last in cr.fetchall()}

        # Split into updates (row exists, date differs) and creates (no row)
        to_update = []  # list of (partner_id, last_date)
        to_create = []  # list of vals dicts
        for pid, last_date in sms_dates:
            if pid in existing:
                if existing[pid] != last_date:
                    to_update.append((last_date, pid))
            else:
                to_create.append({
                    'partner_id': pid,
                    'last_sms_sent_date': last_date,
                })

        # Bulk UPDATE via execute_values — one round-trip for all changes
        if to_update:
            from psycopg2.extras import execute_values
            execute_values(
                cr,
                """
                UPDATE res_partner_stats AS s
                SET last_sms_sent_date = v.last_date
                FROM (VALUES %s) AS v(last_date, partner_id)
                WHERE s.partner_id = v.partner_id
                """,
                to_update,
                template='(%s::date, %s)',
            )

        if to_create:
            self.sudo().create(to_create)

        _logger.info(
            'res.partner.stats: last_sms_sent_date — %d updated, %d created '
            '(total SMS recipients: %d)',
            len(to_update), len(to_create), len(sms_dates),
        )
