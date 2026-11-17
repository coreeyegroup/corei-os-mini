"""Market hours: NSE, MCX and FX open and close at their own local times; holidays close a market; the collector
expects no prices (and raises no stale alarm) while the plan's market is closed."""
import unittest
from datetime import date, datetime, timezone

from corei_os_mini.md import collector, markets

UTC = timezone.utc


class MarketHours(unittest.TestCase):
    def test_nse(self):
        tue = datetime(2026, 11, 17, 4, 0, tzinfo=UTC)  # 09:30 IST
        self.assertTrue(markets.is_open('NSE', tue))
        self.assertFalse(markets.is_open('NSE', datetime(2026, 11, 17, 10, 5, tzinfo=UTC)), '15:35 IST: closed')
        self.assertFalse(markets.is_open('NSE', datetime(2026, 11, 17, 3, 35, tzinfo=UTC)), '09:05 IST: pre-open, not trading')
        self.assertTrue(markets.is_open('NSE', datetime(2026, 11, 17, 3, 35, tzinfo=UTC), pre_open=True))
        self.assertFalse(markets.is_open('NSE', datetime(2026, 11, 21, 5, 0, tzinfo=UTC)), 'Saturday')
        self.assertFalse(markets.is_open('NSE', tue, {date(2026, 11, 17)}), 'holiday')
        nxt = markets.next_change('NSE', tue)
        self.assertEqual((nxt['event'], nxt['at']), ('closes', '2026-11-17T10:00:00+00:00'))

    def test_mcx_and_fx(self):
        self.assertTrue(markets.is_open('MCX', datetime(2026, 7, 15, 17, 50, tzinfo=UTC)), '23:20 IST in US summer')
        self.assertFalse(markets.is_open('MCX', datetime(2026, 7, 15, 18, 10, tzinfo=UTC)), '23:40 IST in US summer: closed')
        self.assertTrue(markets.is_open('MCX', datetime(2026, 12, 15, 18, 10, tzinfo=UTC)), '23:40 IST in US winter: open to 23:55')
        self.assertFalse(markets.is_open('FX', datetime(2026, 11, 21, 12, 0, tzinfo=UTC)), 'Saturday')
        self.assertTrue(markets.is_open('FX', datetime(2026, 11, 22, 23, 0, tzinfo=UTC)), 'Sunday evening New York')
        self.assertTrue(markets.is_open(None, datetime(2026, 11, 21, 12, 0, tzinfo=UTC)), 'unknown market: always')

    def test_inference(self):
        self.assertEqual(markets.market_for(None, 'NSE_INDEX|Nifty 50'), 'NSE')
        self.assertEqual(markets.market_for(None, 'NSE_FO|60001'), 'NSE_FO')
        self.assertEqual(markets.market_for(None, 'MCX_FO|440000'), 'MCX')
        self.assertEqual(markets.market_for(None, 'BSE_INDEX|SENSEX'), 'BSE')
        self.assertEqual(markets.market_for('MCX', 'NSE_EQ|X'), 'MCX', "the instrument's own setting wins")
        self.assertEqual(markets.market_for(None, 'XAUUSD', 'MT5', 'METAL'), 'FX')
        self.assertIsNone(markets.market_for(None, 'US30.cash', 'MT5', 'INDEX'), 'CFD index hours vary by broker: unknown')

    def test_collector_schedule(self):
        plan = {'schedule': 'SESSIONS', 'broker_symbol': 'NSE_INDEX|Nifty 50', 'platform': 'REST_API', 'holidays': [{'date': '2026-11-24'}]}
        self.assertTrue(collector.scheduled(plan, datetime(2026, 11, 17, 3, 35, tzinfo=UTC)), 'from pre-open')
        self.assertFalse(collector.scheduled(plan, datetime(2026, 11, 17, 11, 0, tzinfo=UTC)))
        self.assertFalse(collector.scheduled(plan, datetime(2026, 11, 24, 5, 0, tzinfo=UTC)), 'provider holiday')
        self.assertFalse(collector.scheduled(plan, datetime(2026, 11, 18, 5, 0, tzinfo=UTC), {'INR': frozenset({date(2026, 11, 18)})}), 'calendar holiday in INR')
        self.assertFalse(collector.in_session({**plan, 'schedule': 'ALWAYS'}, datetime(2026, 11, 17, 11, 0, tzinfo=UTC)), 'no stale alarm after 15:30 IST')
