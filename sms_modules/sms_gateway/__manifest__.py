{
    'name': 'SMS Gateway',
    'version': '18.0.2.2.0',
    'category': 'Marketing/SMS',
    'summary': 'Send SMS via physical Android phones as gateway devices',
    'description': """
        Replaces Odoo IAP SMS with a mobile phone gateway system.
        Multiple phones can be registered as SMS gateways with load balancing,
        rate limiting, domain filtering, and integration with mass mailing campaigns.
    """,
    'author': 'VaryShop',
    'depends': ['base_setup', 'sms', 'mass_mailing_sms', 'phone_validation', 'sale', 'point_of_sale'],
    'external_dependencies': {'python': ['qrcode']},
    'data': [
        'security/ir.model.access.csv',
        'data/ir_cron_data.xml',
        'data/sms_marketing_segment_data.xml',
        'views/sms_gateway_phone_views.xml',
        'views/mailing_mailing_views.xml',
        'views/res_config_settings_views.xml',
        'views/sms_sms_views.xml',
        'views/sms_gateway_send_wizard_views.xml',
        'views/sms_marketing_template_views.xml',
        'views/res_partner_views.xml',
    ],
    'installable': True,
    'application': False,
    'auto_install': False,
    'license': 'LGPL-3',
}
