from odoo import fields, models


class CampaignScheduleLog(models.Model):
    _name = 'campaign.schedule.log'
    _description = 'Campaign Schedule Execution Log'
    _order = 'create_date DESC'

    schedule_id = fields.Many2one(
        'campaign.schedule', string='Schedule',
        required=True, ondelete='cascade', index=True,
    )
    mailing_id = fields.Many2one(
        'mailing.mailing', string='Created Campaign',
        readonly=True,
    )
    state = fields.Selection([
        ('ok', 'Success'),
        ('error', 'Error'),
    ], string='Result', required=True, readonly=True)

    note = fields.Text(string='Details', readonly=True)
    recipient_count = fields.Integer(string='Recipients', readonly=True)
