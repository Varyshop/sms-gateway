# -*- coding: utf-8 -*-

import ast
import logging
from datetime import timedelta

from odoo import api, fields, models
from odoo.tools import SQL

_logger = logging.getLogger(__name__)


class SmsMarketingSegment(models.Model):
    _name = 'sms.marketing.segment'
    _description = 'SMS Marketing Segment'
    _order = 'sequence, name'

    name = fields.Char(required=True, translate=True)
    code = fields.Char(required=True, index=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    description = fields.Text(translate=True)
    res_model_name = fields.Char(
        default='res.partner', store=False, readonly=True,
    )
    domain_filter = fields.Char(
        string='Custom Domain Filter',
        help='Custom Odoo domain for res.partner, e.g. [("country_id.code", "=", "CZ")]. '
             'When set, this overrides the built-in code-based segment logic.',
    )

    _sql_constraints = [
        ('code_unique', 'UNIQUE(code)', 'Segment code must be unique.'),
    ]

    @api.constrains('domain_filter')
    def _check_domain_filter(self):
        for seg in self:
            if seg.domain_filter:
                try:
                    domain = ast.literal_eval(seg.domain_filter)
                    if not isinstance(domain, list):
                        raise ValueError('Domain must be a list')
                    self.env['res.partner'].sudo().search(domain, limit=1)
                except Exception as e:
                    raise models.ValidationError(
                        'Invalid domain filter: %s' % e
                    )

    def _get_domain(self):
        """Return Odoo domain for res.partner.

        If domain_filter is set, use it directly.
        Otherwise dispatch to code-based builder.
        """
        self.ensure_one()
        if self.domain_filter:
            try:
                return ast.literal_eval(self.domain_filter)
            except Exception:
                _logger.warning('Invalid domain_filter on segment %s', self.code)
                return [('id', '=', 0)]
        today = fields.Date.today()
        method = getattr(self, '_domain_%s' % self.code, None)
        if method:
            return method(today)
        _logger.warning('No domain builder for segment code: %s', self.code)
        return [('id', '=', 0)]  # empty result for unknown codes

    def _domain_no_order_3m(self, today):
        """Partners who ordered before but not in the last 90 days."""
        cutoff = today - timedelta(days=90)
        # Partners who have at least one sale.order
        # but none with date_order >= cutoff
        cr = self.env.cr
        cr.execute("""
            SELECT DISTINCT partner_id FROM sale_order
            WHERE state IN ('sale', 'done')
            EXCEPT
            SELECT DISTINCT partner_id FROM sale_order
            WHERE state IN ('sale', 'done') AND date_order >= %s
        """, (cutoff,))
        partner_ids = [r[0] for r in cr.fetchall()]
        if not partner_ids:
            return [('id', '=', 0)]
        return [('id', 'in', partner_ids)]

    def _domain_one_order_only(self, today):
        """Partners with exactly 1 confirmed order."""
        cr = self.env.cr
        cr.execute("""
            SELECT partner_id FROM sale_order
            WHERE state IN ('sale', 'done')
            GROUP BY partner_id
            HAVING COUNT(*) = 1
        """)
        partner_ids = [r[0] for r in cr.fetchall()]
        if not partner_ids:
            return [('id', '=', 0)]
        return [('id', 'in', partner_ids)]

    def _domain_new_customers_30d(self, today):
        """Partners created in the last 30 days."""
        cutoff = today - timedelta(days=30)
        return [('create_date', '>=', cutoff)]

    def _get_recipient_count(self, phone=None):
        """Count matching partners, optionally intersected with phone domain_filter."""
        domain = self._get_domain()
        # Exclude blacklisted numbers
        domain += [
            ('phone_sanitized_blacklisted', '=', False),
            '|', ('mobile', '!=', False), ('phone', '!=', False),
        ]
        if phone and phone.domain_filter:
            try:
                phone_domain = ast.literal_eval(phone.domain_filter)
                domain += phone_domain
            except Exception:
                pass
        return self.env['res.partner'].sudo().search_count(domain)
