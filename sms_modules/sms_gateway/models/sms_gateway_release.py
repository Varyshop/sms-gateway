# -*- coding: utf-8 -*-

from odoo import api, fields, models


class SmsGatewayRelease(models.Model):
    _name = 'sms.gateway.release'
    _description = 'SMS Gateway App Release'
    _order = 'version_code desc'

    version = fields.Char(string='Version', required=True, help='Semver string, e.g. 1.6.0')
    version_code = fields.Integer(string='Version Code', required=True,
                                  help='Android versionCode (integer) for numeric comparison')
    apk_file = fields.Binary(string='APK File', required=True, attachment=True)
    apk_filename = fields.Char(string='APK Filename')
    file_size = fields.Integer(string='File Size (bytes)', compute='_compute_file_size', store=True)
    release_notes = fields.Text(string='Release Notes', help='Changelog shown to users in the app')
    force_update = fields.Boolean(string='Force Update', default=False,
                                  help='Force users to update before using the app')
    active = fields.Boolean(default=True)
    release_date = fields.Datetime(string='Release Date', default=fields.Datetime.now)

    _sql_constraints = [
        ('version_code_unique', 'UNIQUE(version_code)',
         'Version code must be unique.'),
    ]

    @api.depends('apk_file')
    def _compute_file_size(self):
        for rec in self:
            if rec.apk_file:
                att = self.env['ir.attachment'].sudo().search([
                    ('res_model', '=', self._name),
                    ('res_id', '=', rec.id),
                    ('res_field', '=', 'apk_file'),
                ], limit=1)
                rec.file_size = att.file_size if att else 0
            else:
                rec.file_size = 0

    @api.model
    def get_latest_release(self):
        return self.search([('active', '=', True)], limit=1, order='version_code desc')
