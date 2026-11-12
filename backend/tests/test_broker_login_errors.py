"""A refused broker login says what the broker said: JSON error codes, an edge block, an empty body."""
import io
import json
import unittest
import urllib.error
from email.message import Message
from unittest.mock import patch

from corei_os_mini import broker_login


def http_error(code: int, body: bytes, content_type: str = 'application/json') -> urllib.error.HTTPError:
    headers = Message()
    headers['Content-Type'] = content_type
    return urllib.error.HTTPError('https://api.upstox.com/v2/x', code, 'err', headers, io.BytesIO(body))


class BrokerLoginErrors(unittest.TestCase):
    def test_upstox_error_code_and_message_are_shown(self):
        exc = http_error(400, json.dumps({'status': 'error', 'errors': [{'errorCode': 'UDAPI100069',
                                           'message': "Check your 'client_id' and 'client_secret'; one or both are incorrect."}]}).encode())
        refused = broker_login._explain_http_error(exc)
        self.assertEqual(refused.status_code, 502)
        self.assertIn('UDAPI100069', refused.detail)
        self.assertIn('client_secret', refused.detail)
        self.assertFalse(refused.blocked)

    def test_cloudflare_block_is_named_with_the_egress_ip(self):
        html = b'<html><head><title>Attention Required! | Cloudflare</title></head><body>Sorry, you have been blocked</body></html>'
        with patch('corei_os_mini.network.egress_ip', return_value='203.0.113.9'):
            refused = broker_login._explain_http_error(http_error(403, html, 'text/html'))
        self.assertTrue(refused.blocked)
        self.assertIn('Cloudflare', refused.detail)
        self.assertIn('203.0.113.9', refused.detail)
        self.assertIn('Indian server', refused.detail)

    def test_unknown_body_shows_status_and_snippet(self):
        refused = broker_login._explain_http_error(http_error(502, b'Bad gateway from somewhere', 'text/plain'))
        self.assertIn('HTTP 502', refused.detail)
        self.assertIn('Bad gateway', refused.detail)

    def test_api_check_tells_reachable_from_blocked(self):
        settings = {'api_base_url': 'https://api.upstox.com'}
        with patch('urllib.request.urlopen', side_effect=http_error(401, json.dumps({'errors': [{'message': 'Invalid token'}]}).encode())):
            self.assertTrue(broker_login.api_check(settings)['reachable'])
        with patch('urllib.request.urlopen', side_effect=http_error(403, b'<html>Sorry, you have been blocked cf-ray</html>', 'text/html')):
            result = broker_login.api_check(settings)
        self.assertFalse(result['reachable'])
        self.assertTrue(result['blocked'])
        with patch('urllib.request.urlopen', side_effect=OSError('timed out')):
            self.assertIn('No route', broker_login.api_check(settings)['detail'])
