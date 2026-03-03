# -*- coding: utf-8 -*-

from odoo import models


class SmsComposer(models.TransientModel):
    _inherit = 'sms.composer'

    def _prepare_mass_sms_values(self, records):
        result = super()._prepare_mass_sms_values(records)
        if self.composition_mode == 'mass' and self.mailing_id:
            provider = getattr(self.mailing_id, 'sms_provider', False)
            if provider:
                for record in records:
                    result[record.id]['sms_provider'] = provider
        return result
