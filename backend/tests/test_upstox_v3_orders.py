import io
import json
import unittest
import urllib.error
from contextlib import nullcontext
from decimal import Decimal
from unittest.mock import MagicMock, patch
from uuid import uuid4

from fastapi import HTTPException

from corei_os_mini.trading import brokers, oms


class UpstoxV3Tests(unittest.TestCase):
    def setUp(self):
        self.client = object.__new__(brokers.Upstox)
        self.client._auth = 'Bearer test-fixture'
        self.client.algo_name = 'Approved fixture'
        self.order = {'order_type':'MARKET', 'quantity':Decimal('25'), 'broker_tag':'tag-1',
                      'side':'BUY', 'time_in_force':'DAY', 'broker_order_id':'order-1'}

    def test_place_uses_hft_v3_and_one_protected_order(self):
        answer = io.BytesIO(b'{"status":"success","data":{"order_ids":["order-1"]}}')
        with patch.object(brokers.urllib.request, 'build_opener') as make_opener:
            call = make_opener.return_value.open
            call.return_value = nullcontext(answer)
            self.assertEqual(self.client.place(self.order,'NSE_EQ|TEST','D'), {'order_id':'order-1'})
            request = call.call_args.args[0]
        self.assertEqual(request.full_url,'https://api-hft.upstox.com/v3/order/place')
        self.assertEqual(request.get_method(),'POST')
        self.assertEqual(request.get_header('X-algo-name'),'Approved fixture')
        body = json.loads(request.data)
        self.assertEqual(body['market_protection'],-1)
        self.assertEqual(body['quantity'],25)
        self.assertFalse(body['slice'])

    def test_fractional_size_and_unexpected_ticket_count(self):
        self.order['quantity'] = Decimal('1.25')
        with patch.object(self.client,'call') as call:
            with self.assertRaises(brokers.NotSent):
                self.client.place(self.order,'NSE_EQ|TEST','D')
            call.assert_not_called()
        self.order['quantity'] = Decimal('25')
        with patch.object(self.client,'call',return_value={'order_ids':['order-1','order-2']}):
            with self.assertRaises(brokers.Lost):
                self.client.place(self.order,'NSE_EQ|TEST','D')

    def test_v3_cancel_and_modify_price(self):
        with patch.object(self.client,'call',return_value={'order_id':'order-1'}) as call:
            self.client.cancel('order-1')
            call.assert_called_once_with('DELETE','/v3/order/cancel',params={'order_id':'order-1'},sending=True)
            self.order['order_type']='LIMIT'
            self.client.modify_price(self.order,{'limit_price':Decimal('18.50')})
            method,path=call.call_args.args
            self.assertEqual((method,path),('PUT','/v3/order/modify'))
            body=call.call_args.kwargs['body']
            self.assertEqual(body['price'],18.5)
            self.assertNotIn('quantity',body)

    def test_gateway_errors_are_uncertain_for_writes_and_never_retried(self):
        for code in (302,400,408,409,429,500,502):
            with self.subTest(code=code), patch.object(brokers.urllib.request,'build_opener') as create:
                opener=create.return_value.open
                opener.side_effect=urllib.error.HTTPError('https://api-hft.upstox.com',code,'fixture',{},io.BytesIO(b'{}'))
                with self.assertRaises(brokers.Lost):
                    self.client.place(self.order,'NSE_EQ|TEST','D')
                opener.assert_called_once()
        with patch.object(brokers.urllib.request,'build_opener') as create:
            create.return_value.open.side_effect=urllib.error.HTTPError(
                'https://api-hft.upstox.com',400,'fixture',{},io.BytesIO(b'{"errors":[{"errorCode":"UDAPI1158"}]}'))
            with self.assertRaisesRegex(brokers.Refused,'UDAPI1158'):
                self.client.place(self.order,'NSE_EQ|TEST','D')

    def test_official_host_boundary(self):
        self.assertTrue(brokers.upstox_host('https://api.upstox.com/v2/'))
        self.assertFalse(brokers.upstox_host('https://upstox.com.example.org'))
        self.assertFalse(brokers.upstox_host('https://api.upstox.com@untrusted.example.org'))
        self.assertFalse(brokers.upstox_host('https://sandbox.upstox.com'))


class UpstoxAmendmentTests(unittest.TestCase):
    def test_price_change_is_booked_only_on_upstox_acceptance(self):
        order_id = uuid4()
        order = {'id':order_id,'state':'ACKNOWLEDGED','mode':'BROKER','adapter':'UPSTOX',
                 'order_type':'LIMIT','account_id':uuid4(),'instrument_id':uuid4(),'broker_order_id':'order-1',
                 'quantity':Decimal('25'),'filled_qty':Decimal('0')}
        change = oms.ModifyIn(limit_price=Decimal('10.05'),reason='Verify amended price')
        database = MagicMock()
        database.execute.return_value.fetchone.return_value = order

        with patch.object(oms.store,'transaction',return_value=nullcontext(database)), \
             patch.object(oms,'instrument',return_value={'tick_size':'0.05'}), \
             patch.object(oms,'quote',return_value={}), \
             patch.object(oms.risk,'check',return_value={'reasons':[]}), \
             patch.object(brokers,'Upstox') as adapter, \
             patch.object(oms,'_modified',return_value={'state':'ACKNOWLEDGED'}) as update:
            broker = adapter.return_value
            self.assertEqual(oms.modify('operator',order_id,change), {'state':'ACKNOWLEDGED'})
            broker.modify_price.assert_called_once()
            update.assert_called_once()
            update.reset_mock()
            broker.modify_price.side_effect = brokers.Lost('fixture timeout')
            with self.assertRaises(HTTPException) as error:
                oms.modify('operator',order_id,change)
            self.assertEqual(error.exception.status_code,504)
            update.assert_not_called()
            self.assertIn('modify_unanswered',database.execute.call_args.args[1][3])
