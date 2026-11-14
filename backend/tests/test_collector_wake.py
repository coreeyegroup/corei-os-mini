"""A failed source retries as soon as its setup changes, instead of waiting out the back-off."""
import threading
import time
import unittest
from unittest import mock

from corei_os_mini.md import collector


class Wake(unittest.TestCase):
    def worker(self, stamps):
        w = collector.SourceWorker.__new__(collector.SourceWorker)
        w.stopping, w.events = threading.Event(), []
        w._config_stamp = mock.Mock(side_effect=stamps)
        w._event = lambda kind, *a, **k: w.events.append(kind)
        return w

    def test_returns_when_setup_changes(self):
        w = self.worker([('a',), ('b',)])
        real_wait = w.stopping.wait
        with mock.patch.object(w.stopping, 'wait', side_effect=lambda t: real_wait(0)):
            started = time.monotonic()
            w._wait_or_config_change(3600)
        self.assertLess(time.monotonic() - started, 5)
        self.assertEqual(w.events, ['CONFIG_CHANGED'])

    def test_waits_full_delay_when_unchanged(self):
        w = self.worker(lambda: ('a',))
        w._config_stamp = mock.Mock(return_value=('a',))
        started = time.monotonic()
        w._wait_or_config_change(0.2)
        self.assertGreaterEqual(time.monotonic() - started, 0.2)
        self.assertEqual(w.events, [])
