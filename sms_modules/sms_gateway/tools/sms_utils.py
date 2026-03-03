# -*- coding: utf-8 -*-

import math
import re

# GSM-7 basic character set — exact copy of the JS regex from
# addons/sms/static/src/components/sms_widget/fields_sms_widget.js:70
_GSM7_PATTERN = re.compile(
    r"^[@£$¥èéùìòÇ\nØø\rÅåΔ_ΦΓΛΩΠΨΣΘΞ"
    r"ÆæßÉ !\"#¤%&'()*+,\-./0123456789:;<=>?"
    r"¡ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÑÜ§¿"
    r"abcdefghijklmnopqrstuvwxyzäöñüà]*$"
)


def _extract_encoding(content):
    """Return 'GSM7' if content uses only GSM-7 charset, else 'UNICODE'."""
    if _GSM7_PATTERN.match(content or ''):
        return 'GSM7'
    return 'UNICODE'


def _count_sms_parts(nbr_char, encoding):
    """Return number of SMS segments for *nbr_char* characters."""
    if nbr_char == 0:
        return 0
    if encoding == 'UNICODE':
        return 1 if nbr_char <= 70 else math.ceil(nbr_char / 67)
    return 1 if nbr_char <= 160 else math.ceil(nbr_char / 153)


def sms_segment_count(body):
    """Return how many SMS billing segments a message body will consume.

    Replicates the Odoo JS widget logic exactly:
    - Newlines count as 2 characters
    - Non-GSM7 characters (Czech diacritics, emoji, etc.) force UNICODE
    - UNICODE: 1 segment <= 70 chars, multipart = ceil(n/67)
    - GSM7:    1 segment <= 160 chars, multipart = ceil(n/153)
    """
    if not body:
        return 1
    nbr_char = len(body) + body.count('\n')
    encoding = _extract_encoding(body)
    return max(1, _count_sms_parts(nbr_char, encoding))
