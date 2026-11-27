"""Integration catalogue: every permission (scope), event and connection profile the gateway knows, with its status.

Why it exists: external applications must discover what COREI offers instead of hard-coding it, and administrators
must see what each permission allows. One list feeds the admin screen, capability discovery and the backend checks.
Purpose: SCOPES (name → group, label, status, note), EVENTS, PROFILES (recommended scope sets per client type),
ALERT_EVENT_MAP (existing alert events → integration events).
Must never: list a capability as AVAILABLE that the gateway does not implement; offer a wildcard; let any profile
include LIVE order authority.
Dependencies: none. Consumers: integration.auth, integration.gateway, integration.admin, integration.webhooks.
Architectural position: integration gateway, reference data.
"""
from __future__ import annotations

AVAILABLE, PARTIAL, UNAVAILABLE = 'AVAILABLE', 'PARTIAL', 'UNAVAILABLE'

# name: (group, label, status, note)
SCOPES: dict[str, tuple[str, str, str, str]] = {
    'system.read': ('System', 'Platform health and version', AVAILABLE, ''),
    'capabilities.read': ('System', 'Capability discovery', AVAILABLE, ''),
    'accounts.read': ('Accounts', 'Accounts in scope: environment, permission, balance, equity', AVAILABLE, 'No credentials are ever returned.'),
    'instruments.read': ('Instruments', 'Instrument catalogue, collection and tradability', AVAILABLE, ''),
    'market_data.read': ('Market data', 'Latest quotes with age and source', AVAILABLE, 'Quotes are polled observations, not an exchange tick stream.'),
    'market_data.history': ('Market data', 'Historical candles (closed bars)', AVAILABLE, ''),
    'market_data.snapshot': ('Market data', 'Multi-instrument, multi-timeframe analysis snapshot', AVAILABLE, ''),
    'market_data.stream': ('Market data', 'Real-time push stream (WebSocket/SSE)', UNAVAILABLE, 'Not built; use webhooks or polling.'),
    'market_intelligence.read': ('Market intelligence', 'Indicators, regime, volatility, sessions, events', AVAILABLE, ''),
    'market_intelligence.screen': ('Market intelligence', 'Screener ranking across instruments', AVAILABLE, ''),
    'strategies.read': ('Strategies', 'Strategy catalogue, versions, parameters, status', AVAILABLE, ''),
    'strategies.evaluate': ('Strategies', 'Evaluate one strategy on one instrument (job)', AVAILABLE, ''),
    'strategies.scan': ('Strategies', 'Instrument- or strategy-centric scans (job)', AVAILABLE, ''),
    'strategies.backtest': ('Strategies', 'Backtests through the gateway', UNAVAILABLE, 'Backtests run from the workstation; gateway access is a later release.'),
    'signals.read': ('Signals', 'Read candidate signals submitted by this connection', AVAILABLE, ''),
    'signals.propose': ('Signals', 'Submit candidate signals with provenance (never an order)', AVAILABLE, ''),
    'signals.validate': ('Signals', 'Run a submitted signal through the risk checks', AVAILABLE, ''),
    'risk.read': ('Risk', 'Limits, kill switches, recent decisions', AVAILABLE, ''),
    'risk.assess': ('Risk', 'Risk assessment of a candidate order (no order is created)', AVAILABLE, ''),
    'portfolio.read': ('Portfolio', 'Equity, positions, exposure, P&L', AVAILABLE, ''),
    'portfolio.analyse': ('Portfolio', 'Portfolio analysis endpoints', PARTIAL, 'Read data is available; proposals are kept by the caller.'),
    'orders.read': ('Orders', 'Orders and fills of accounts in scope', AVAILABLE, ''),
    'orders.preview': ('Orders', 'Validate and risk-check an order without placing it', AVAILABLE, ''),
    'orders.paper': ('Orders', 'Place paper orders (simulated; never reaches a broker)', AVAILABLE, ''),
    'orders.demo': ('Orders', 'Place orders on DEMO broker accounts', AVAILABLE, 'Same risk checks, setup gates and order sending switch as the workstation.'),
    'orders.cancel': ('Orders', 'Cancel orders this connection placed', AVAILABLE, ''),
    'orders.live_request': ('Orders', 'Request LIVE orders', UNAVAILABLE, 'LIVE orders through integrations are not enabled in this release.'),
    'orders.approve': ('Orders', 'Approve held orders (four-eyes)', UNAVAILABLE, 'Approvals stay with people in the workstation.'),
    'orders.execute': ('Orders', 'Release orders to a broker', UNAVAILABLE, 'Not offered to integrations.'),
    'orders.modify': ('Orders', 'Amend working orders', UNAVAILABLE, 'Later release.'),
    'post_trade.read': ('Post-trade', 'Fills, costs, realised P&L', AVAILABLE, ''),
    'reports.read': ('Post-trade', 'Reports', PARTIAL, 'Use post-trade and portfolio data.'),
    'jobs.read': ('Jobs', 'Read job status and results', AVAILABLE, ''),
    'jobs.cancel': ('Jobs', 'Cancel own jobs', AVAILABLE, ''),
    'events.subscribe': ('Events', 'Receive event webhooks', AVAILABLE, ''),
    'webhooks.manage': ('Events', 'Create and delete own webhooks', AVAILABLE, ''),
}
GRANTABLE = frozenset(s for s, v in SCOPES.items() if v[2] != UNAVAILABLE)

EVENTS: dict[str, str] = {
    'signal.created': 'An external candidate signal was submitted',
    'signal.validated': 'A candidate signal passed the risk checks',
    'signal.rejected': 'A candidate signal failed the risk checks',
    'job.completed': 'A scan or evaluation job finished',
    'job.failed': 'A scan or evaluation job failed or was cancelled',
    'order.submitted': 'An order placed through the gateway was accepted for processing',
    'order.rejected': 'An order placed through the gateway was refused',
    'order.event': 'Orders: refused, unknown outcome, filled (workstation alert)',
    'risk.kill_switch': 'A kill switch was engaged (by a person or a loss limit)',
    'approval.needed': 'A change or order waits for a second person',
    'system.order_sending': 'Order sending was switched on or off',
    'market.data.alert': 'Market data outage, quality problem or feed switch',
    'connection.failed': 'A broker connection or login failed',
    'system.alert': 'Any other workstation alert',
    'webhook.test': 'Test delivery',
}
# Existing workstation alert events (cfg_alert_channels.events) → integration events.
ALERT_EVENT_MAP = {'TRADING': 'order.event', 'KILL_SWITCH': 'risk.kill_switch', 'APPROVAL_NEEDED': 'approval.needed',
                   'ORDER_SENDING': 'system.order_sending', 'MARKET_DATA': 'market.data.alert', 'LOGIN_FAILED': 'connection.failed'}

_READ = ['system.read', 'capabilities.read', 'instruments.read', 'market_data.read', 'market_data.history', 'market_data.snapshot',
         'market_intelligence.read', 'strategies.read', 'risk.read', 'jobs.read']
PROFILES: dict[str, dict] = {
    'CORE_AI': {'label': 'CORE-AI', 'scopes': _READ + ['market_intelligence.screen', 'strategies.evaluate', 'strategies.scan', 'signals.read',
                                                        'signals.propose', 'signals.validate', 'risk.assess', 'portfolio.read', 'jobs.cancel']},
    'N8N': {'label': 'n8n', 'scopes': _READ + ['market_intelligence.screen', 'strategies.scan', 'signals.read', 'portfolio.read', 'orders.read',
                                                'post_trade.read', 'jobs.cancel', 'events.subscribe', 'webhooks.manage']},
    'AI_AGENT': {'label': 'External AI agent', 'scopes': _READ + ['signals.read', 'signals.propose']},
    'PYTHON': {'label': 'Python application', 'scopes': list(_READ)},
    'JAVASCRIPT': {'label': 'JavaScript application', 'scopes': list(_READ)},
    'RESEARCH': {'label': 'Research platform', 'scopes': list(_READ)},
    'DASHBOARD': {'label': 'External dashboard', 'scopes': ['system.read', 'capabilities.read', 'instruments.read', 'market_data.read',
                                                           'portfolio.read', 'orders.read', 'post_trade.read', 'risk.read']},
    'CUSTOM': {'label': 'Custom integration', 'scopes': ['system.read', 'capabilities.read']},
}
assert all(set(p['scopes']) <= GRANTABLE for p in PROFILES.values())
assert not any(s.startswith('orders.') and s not in ('orders.read',) for p in PROFILES.values() for s in p['scopes']), \
    'profiles never include order authority; an administrator adds it explicitly'
