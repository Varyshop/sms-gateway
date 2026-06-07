{
    'name': 'SMS Campaign Scheduler',
    'version': '18.0.1.0.0',
    'category': 'Marketing/SMS',
    'summary': 'Schedule recurring SMS campaigns with timezone-aware timing',
    'description': """
SMS Campaign Scheduler
======================
Create automated, recurring SMS campaigns:

* Select template, segment, and optional domain filter
* Schedule: daily / weekly (pick day) / monthly (pick day of month)
* Timezone-aware execution time
* Each run creates a real mailing.mailing campaign via sms_gateway
* Execution log with links to created campaigns
    """,
    'author': 'VaryShop',
    'depends': [
        'sms_gateway',
    ],
    'data': [
        'security/ir.model.access.csv',
        'data/ir_cron_data.xml',
        'views/campaign_schedule_views.xml',
        'views/menu_views.xml',
    ],
    'installable': True,
    'application': False,
    'auto_install': False,
    'license': 'LGPL-3',
}
