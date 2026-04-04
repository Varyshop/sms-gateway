# -*- coding: utf-8 -*-

import ast
import logging
import re
import unicodedata
from datetime import timedelta

from odoo import api, fields, models

_logger = logging.getLogger(__name__)


class SmsMarketingSegment(models.Model):
    _name = 'sms.marketing.segment'
    _description = 'SMS Marketing Segment'
    _order = 'sequence, name'

    name = fields.Char(required=True, translate=True)
    code = fields.Char(index=True, readonly=True, copy=False)
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

    @staticmethod
    def _slugify(text):
        """Convert text to a slug suitable for code field."""
        text = unicodedata.normalize('NFKD', text).encode('ascii', 'ignore').decode('ascii')
        text = text.lower().strip()
        text = re.sub(r'[^a-z0-9]+', '_', text)
        return text.strip('_')

    def copy(self, default=None):
        default = dict(default or {})
        if 'name' not in default:
            default['name'] = '%s (kopie)' % self.name
        if 'code' not in default:
            base = self._slugify(default['name'])
            code = base
            suffix = 2
            while self.search_count([('code', '=', code)]):
                code = '%s_%d' % (base, suffix)
                suffix += 1
            default['code'] = code
        return super().copy(default)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get('code') and vals.get('name'):
                vals['code'] = self._slugify(vals['name'])
        return super().create(vals_list)

    def write(self, vals):
        if 'name' in vals and 'code' not in vals:
            for rec in self:
                if not rec.code or rec.code == self._slugify(rec.name):
                    vals_copy = dict(vals)
                    vals_copy['code'] = self._slugify(vals['name'])
                    super(SmsMarketingSegment, rec).write(vals_copy)
                    return True
        return super().write(vals)

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

    def _get_exclusion_domain(self, days):
        """Return a domain leaf that excludes partners contacted in the last N days.

        Uses a SQL subquery to avoid materialising thousands of IDs in the
        domain, which previously produced huge ``('id', 'not in', [...])``
        clauses sent to PostgreSQL.
        """
        if not days or days <= 0:
            return []
        cutoff = fields.Datetime.now() - timedelta(days=days)
        cr = self.env.cr
        # Use a single SQL query to get the set of excluded IDs and let
        # Odoo intersect it.  We still fetch IDs, but we pick the smaller
        # side: if there are fewer candidates than excluded, we fetch
        # candidates and use ``id in``; otherwise ``id not in``.
        cr.execute("""
            SELECT DISTINCT res_id
            FROM mailing_trace
            WHERE model = 'res.partner'
              AND trace_type = 'sms'
              AND trace_status = 'sent'
              AND write_date >= %s
              AND res_id IS NOT NULL
        """, (cutoff,))
        excluded_ids = [r[0] for r in cr.fetchall()]
        if not excluded_ids:
            return []
        return [('id', 'not in', excluded_ids)]

    def _get_full_domain(self, phone=None, exclude_contacted_days=0):
        """Build the complete recipient domain (segment + blacklist + phone + exclusion).

        This is the single source of truth for recipient filtering — used by
        both ``_get_recipient_count`` and the campaign create endpoint so the
        logic is never duplicated.
        """
        self.ensure_one()
        domain = self._get_domain()
        domain += [
            ('phone_sanitized_blacklisted', '=', False),
            '|',
            '&', ('mobile', '!=', False), ('mobile', '!=', ''),
            '&', ('phone', '!=', False), ('phone', '!=', ''),
        ]
        if phone and phone.domain_filter:
            try:
                phone_domain = ast.literal_eval(phone.domain_filter)
                domain += phone_domain
            except Exception:
                pass
        domain += self._get_exclusion_domain(exclude_contacted_days)
        return domain

    def _is_domain_storable(self):
        """Check if this segment produces a purely declarative domain.

        Code-based segments (no_order_3m, one_order_only) use SQL and return
        ``('id', 'in', [...])``, which cannot be stored as a reusable domain.
        Segments with ``domain_filter`` are purely declarative and safe to store.
        """
        self.ensure_one()
        return bool(self.domain_filter)

    def _get_storable_domain(self, phone=None, exclude_contacted_days=0):
        """Return a domain suitable for storing in ``mailing_domain``.

        For segments with a declarative ``domain_filter``, composes the full
        domain from segment + phone filters (no runtime IDs).  The
        ``exclude_contacted_days`` exclusion is **not** baked into the stored
        domain — it is applied at send time by ``mailing.mailing._get_recipients()``
        via the ``exclude_contacted_days`` field on the mailing record.  This
        keeps the stored domain clean and readable.

        For SQL-based segments (code dispatched), falls back to pre-resolving
        recipient IDs into ``('id', 'in', [...])``.
        """
        self.ensure_one()
        # Start with base filters (blacklist + phone required)
        base = [
            ('phone_sanitized_blacklisted', '=', False),
            '|',
            '&', ('mobile', '!=', False), ('mobile', '!=', ''),
            '&', ('phone', '!=', False), ('phone', '!=', ''),
        ]

        # Phone domain filter (declarative)
        phone_extra = []
        if phone and phone.domain_filter:
            try:
                phone_extra = ast.literal_eval(phone.domain_filter)
            except Exception:
                pass

        # Declarative segments → always store the pure domain (exclusion
        # is handled at send time, not stored in the domain)
        if self._is_domain_storable():
            domain = self._get_domain() + base + phone_extra
            return domain

        # SQL-based segments → pre-resolve to IDs (exclude_contacted_days
        # is still applied here for accurate recipient set)
        domain = self._get_full_domain(phone, exclude_contacted_days)
        partner_ids = self.env['res.partner'].sudo().search(domain).ids
        return [('id', 'in', partner_ids)] if partner_ids else [('id', '=', 0)]

    def _resolve_recipient_ids(self, phone=None, exclude_contacted_days=0, limit=None):
        """Resolve full domain to a list of partner IDs.

        Returns the resolved IDs — suitable for storing as ``[('id', 'in', ids)]``
        in ``mailing_domain`` to avoid huge ``not in`` clauses.
        """
        domain = self._get_full_domain(phone, exclude_contacted_days)
        return self.env['res.partner'].sudo().search(domain, limit=limit).ids

    def _get_recipient_count(self, phone=None, exclude_contacted_days=0):
        """Count matching partners."""
        domain = self._get_full_domain(phone, exclude_contacted_days)
        return self.env['res.partner'].sudo().search_count(domain)
