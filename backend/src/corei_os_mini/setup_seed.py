"""Correct values for the owner's test setup (14 Nov 2026), applied through COREI's own record rules.

Dry run by default: prints every create and change. With --apply it writes them; LIVE changes that need a
second person go to Setup › Approvals as usual; everything is in the audit log under the operator's name.
Idempotent: running it again changes nothing that is already right. Never touches passwords or credentials.

    sudo bash scripts/apply-test-setup.sh            # show what would change
    sudo bash scripts/apply-test-setup.sh --apply    # change it
"""
from __future__ import annotations

import argparse
import os
import sys
from decimal import Decimal
from typing import Any

from pydantic import BaseModel

from . import config_store as store
from .config_models import AccountGroupIn, AccountIn, ConnectionIn, InstrumentIn, MappingIn, ProviderIn
from .md import plans

REASON = 'Test setup values (scripts/apply-test-setup.sh)'

# Facts checked 14 Nov 2026: VT Markets (Pty) Ltd, FSCA FSP 50865 (the entity the VTMarkets-Live servers name);
# Upstox = RKSV Securities India Pvt Ltd, SEBI INZ000185137; MetaQuotes Ltd makes MT5 and runs MetaQuotes-Demo.
PROVIDERS = {
    'VTMARKET': {'name': 'VT Markets', 'legal_entity': 'VT Markets (Pty) Ltd', 'regulator': 'FSCA (South Africa)', 'licence_number': 'FSP 50865',
                 'jurisdiction': 'South Africa', 'execution_model': 'MARKET_MAKER', 'website': 'https://www.vtmarkets.com'},
    'METAQUOTES': {'name': 'MetaQuotes', 'legal_entity': 'MetaQuotes Ltd', 'regulator': 'Not a broker (MT5 developer; demo server)', 'licence_number': '',
                   'jurisdiction': 'Cyprus', 'execution_model': 'MARKET_MAKER', 'website': 'https://www.metaquotes.net'},
    'UPSTOK': {'name': 'Upstox', 'legal_entity': 'RKSV Securities India Pvt Ltd', 'regulator': 'SEBI (India)', 'licence_number': 'INZ000185137',
               'jurisdiction': 'India', 'execution_model': 'EXCHANGE', 'website': 'https://upstox.com', 'exposure_currency': 'INR'},
}
# Names only (no facts claimed): brokers added earlier with their code as name.
PROVIDER_NAMES = {'EXNESS': 'Exness', 'ICMARKETS': 'IC Markets'}
# A technology entered as a provider: archived (kept in the audit log; restore any time).
PROVIDERS_TO_ARCHIVE = {'META_TRADER': 'MetaTrader is a technology (MT4/MT5), not a provider: choose it as a server type under a broker.'}
# Standard connection names: "<Provider> · <technology> <environment> · <server or endpoint>".
CONNECTION_NAMES = {'METAQUOTES_MT5_DEMO': 'MetaQuotes · MT5 demo · MetaQuotes-Demo', 'VTMARKET_MT5_LIVE': 'VT Markets · MT5 live · VTMarkets-Live 2',
                    'UPSTOK_API_LIVE': 'Upstox · API live · api.upstox.com'}
ACCOUNTS = {  # by account code
    'VTMARKET_LIVE_35822869': {'name': 'VT Markets live 2 · 35822869'},
    'METAQUOTES_DEMO_5056977914': {'name': 'MetaQuotes demo · 5056977914'},
    'UPSTOX_AL3696': {'name': 'Upstox · AL3696'},
}
D = Decimal
INSTRUMENTS = [
    dict(symbol='XAUUSD', name='Gold vs US dollar', asset_class='METAL', base_currency='XAU', quote_currency='USD', price_precision=2, quantity_precision=2,
         tick_size=D('0.01'), min_quantity=D('0.01'), quantity_step=D('0.01'), contract_size=D('100'), contract_unit='troy ounce', trading_session='Sun 23:00 – Fri 22:00 UTC, daily break 22:00–23:00'),
    dict(symbol='EURUSD', name='Euro vs US dollar', asset_class='FX', base_currency='EUR', quote_currency='USD', price_precision=5, quantity_precision=2,
         tick_size=D('0.00001'), min_quantity=D('0.01'), quantity_step=D('0.01'), contract_size=D('100000'), contract_unit='EUR', trading_session='Sun 22:00 – Fri 22:00 UTC'),
    dict(symbol='GBPUSD', name='British pound vs US dollar', asset_class='FX', base_currency='GBP', quote_currency='USD', price_precision=5, quantity_precision=2,
         tick_size=D('0.00001'), min_quantity=D('0.01'), quantity_step=D('0.01'), contract_size=D('100000'), contract_unit='GBP', trading_session='Sun 22:00 – Fri 22:00 UTC'),
    dict(symbol='USDJPY', name='US dollar vs Japanese yen', asset_class='FX', base_currency='USD', quote_currency='JPY', price_precision=3, quantity_precision=2,
         tick_size=D('0.001'), min_quantity=D('0.01'), quantity_step=D('0.01'), contract_size=D('100000'), contract_unit='USD', trading_session='Sun 22:00 – Fri 22:00 UTC'),
    dict(symbol='NIFTY50', name='NIFTY 50 index (NSE)', asset_class='INDEX', base_currency='INR', quote_currency='INR', price_precision=2, quantity_precision=0,
         tick_size=D('0.05'), min_quantity=D('1'), quantity_step=D('1'), contract_size=D('1'), contract_unit='index point', trading_session='Mon–Fri 09:15–15:30 IST'),
    dict(symbol='NIFTYBANK', name='NIFTY Bank index (NSE)', asset_class='INDEX', base_currency='INR', quote_currency='INR', price_precision=2, quantity_precision=0,
         tick_size=D('0.05'), min_quantity=D('1'), quantity_step=D('1'), contract_size=D('1'), contract_unit='index point', trading_session='Mon–Fri 09:15–15:30 IST'),
]
GROUPS = [
    {'code': 'SRC_FX_METALS', 'name': 'Price sources · FX and metals', 'description': 'MT5 accounts that give FX and gold prices', 'members': ['METAQUOTES_DEMO_5056977914', 'VTMARKET_LIVE_35822869']},
    {'code': 'SRC_INDIA', 'name': 'Price sources · India', 'description': 'NSE/BSE/MCX prices', 'members': ['UPSTOX_AL3696']},
    {'code': 'TEST_DEMO', 'name': 'Test · demo accounts', 'description': 'Demo accounts for testing the full flow', 'members': ['METAQUOTES_DEMO_5056977914']},
]
# What to collect, per account: (broker symbol, instrument, feed role). MetaQuotes-Demo uses plain symbols;
# VT Markets symbols are picked from its Available list once its symbol list has loaded.
COLLECT = {
    'METAQUOTES_DEMO_5056977914': [('XAUUSD', 'XAUUSD', 'PRIMARY'), ('EURUSD', 'EURUSD', 'PRIMARY'), ('GBPUSD', 'GBPUSD', 'PRIMARY'), ('USDJPY', 'USDJPY', 'PRIMARY')],
    'UPSTOX_AL3696': [('NSE_INDEX|Nifty 50', 'NIFTY50', 'PRIMARY'), ('NSE_INDEX|Nifty Bank', 'NIFTYBANK', 'PRIMARY')],
}


class Run:
    def __init__(self, actor: str, apply: bool):
        self.actor, self.apply, self.changes = actor, apply, 0

    def say(self, verb: str, what: str, detail: str = '') -> None:
        self.changes += 1
        print(f"{'  ' if self.apply else '  would '}{verb} {what}{(': ' + detail) if detail else ''}")

    def upsert(self, entity: str, model: type[BaseModel], existing: dict | None, wanted: dict[str, Any], label: str) -> dict | None:
        fields = model.model_fields
        if existing is None:
            self.say('create', label)
            return store.create(self.actor, entity, model.model_validate(wanted), reason=REASON) if self.apply else None
        current = {k: existing[k] for k in fields if k in existing}
        diff = {k: (current.get(k), v) for k, v in wanted.items() if k in fields and _norm(current.get(k)) != _norm(v)}
        if not diff:
            return existing
        self.say('update', label, ', '.join(f'{k} {a!r} → {b!r}' for k, (a, b) in diff.items()))
        if not self.apply:
            return existing
        result = store.update(self.actor, entity, existing['id'], model.model_validate({**current, **wanted}), existing['version'], reason=REASON)
        if result.get('change_request'):
            print(f"    → waiting for approval: {result['change_request']['summary']}")
        return result


def _norm(v: Any) -> Any:
    if isinstance(v, Decimal):
        return v.normalize()
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return Decimal(str(v)).normalize()
    if isinstance(v, str):
        try:
            return Decimal(v).normalize()
        except Exception:  # noqa: BLE001 - not a number
            return v
    if isinstance(v, list):
        return sorted(str(x) for x in v)
    return v


def run(actor: str, apply: bool) -> int:
    r = Run(actor, apply)
    print('Providers')
    providers = {p['code']: p for p in store.list_records('providers', {}, '')}
    for code, values in PROVIDERS.items():
        if code in providers:
            r.upsert('providers', ProviderIn, providers[code], values, f"provider {code}")
        else:
            print(f'  provider {code} not found: skipped')
    for code, name in PROVIDER_NAMES.items():
        if code in providers:
            r.upsert('providers', ProviderIn, providers[code], {'name': name}, f'provider {code}')
    for code, why in PROVIDERS_TO_ARCHIVE.items():
        p = providers.get(code)
        if p and p['status'] != 'ARCHIVED':
            try:
                r.upsert('providers', ProviderIn, p, {'status': 'ARCHIVED'}, f'provider {code} ({why})')
            except Exception as exc:  # noqa: BLE001 - e.g. still has active servers: report and go on
                print(f"    not archived: {getattr(exc, 'detail', exc)}")
    print('Servers and sessions')
    for c in store.list_records('connections', {}, ''):
        if c['code'] in CONNECTION_NAMES:
            r.upsert('connections', ConnectionIn, c, {'name': CONNECTION_NAMES[c['code']]}, f"server {c['code']}")
    print('Instruments')
    instruments = {i['symbol']: i for i in store.list_records('instruments', {}, '')}
    for spec in INSTRUMENTS:
        made = r.upsert('instruments', InstrumentIn, instruments.get(spec['symbol']), spec, f"instrument {spec['symbol']}")
        if made:
            instruments[spec['symbol']] = made
    print('Account groups')
    groups = {g['code']: g for g in store.list_records('groups', {}, '')}
    for g in GROUPS:
        made = r.upsert('groups', AccountGroupIn, groups.get(g['code']), {k: g[k] for k in ('code', 'name', 'description')}, f"group {g['code']}")
        if made:
            groups[g['code']] = made
    print('Accounts')
    accounts = {a['code']: a for a in store.list_records('accounts', {}, '')}
    for code, values in ACCOUNTS.items():
        a = accounts.get(code)
        if not a:
            print(f'  account {code} not found: skipped')
            continue
        wanted = dict(values)
        member_of = [str(groups[g['code']]['id']) for g in GROUPS if code in g['members'] and g['code'] in groups]
        if member_of or not apply:
            wanted['group_ids'] = sorted(set(map(str, a.get('group_ids') or [])) | set(member_of))
        made = r.upsert('accounts', AccountIn, a, wanted, f"account {code}")
        if made and 'id' in made:
            accounts[code] = made
    print('Symbol mappings and collection plans')
    existing_plans = plans.list_plans(None, False)
    mappings = store.list_records('mappings', {}, '')
    for code, items in COLLECT.items():
        a = accounts.get(code)
        if not a:
            continue
        for broker_symbol, symbol, role in items:
            inst = instruments.get(symbol)
            if not inst or 'id' not in inst:
                print(f'  {code} {broker_symbol}: instrument {symbol} not created yet (dry run): mapping and plan follow on --apply')
                r.changes += 2
                continue
            if not any(m['account_id'] == a['id'] and m['broker_symbol'] == broker_symbol for m in mappings):
                r.upsert('mappings', MappingIn, None, {'connection_id': a['connection_id'], 'account_id': a['id'], 'instrument_id': inst['id'],
                                                       'broker_symbol': broker_symbol, 'usage': 'MARKET_DATA'}, f'mapping {code} {broker_symbol} → {symbol}')
            if any(p['account_id'] == a['id'] and p['broker_symbol'] == broker_symbol for p in existing_plans):
                continue
            plan_code = f"{code}_{symbol}"[:64]
            r.say('create and start', f'collection plan {plan_code}', f'{broker_symbol} → {symbol}, trading feed {role.lower()}, quotes + bars M1/M5/H1')
            if apply:
                plan = plans.create(actor, plans.PlanIn(code=plan_code, account_id=a['id'], broker_symbol=broker_symbol, instrument_id=inst['id'],
                                                        purpose='TRADING_FEED', feed_role=role, data_types=['QUOTES', 'BARS'], bar_timeframes=['M1', 'M5', 'H1'],
                                                        stale_seconds=30 if code.startswith('UPSTOX') else 10))
                started = plans.set_status(actor, plan['id'], plans.StatusIn(status='ACTIVE', version=plan['version'], reason=REASON))
                if isinstance(started, dict) and started.get('change_request'):
                    print(f"    → waiting for approval (LIVE trading feed): {started['change_request']['summary']}")
    print(f"\n{r.changes} change(s) {'made' if apply else 'to make'}." + ('' if apply else ' Run again with --apply to make them.'))
    return 0


def use_bridge(actor: str, account_code: str, port: int, state: str, apply: bool) -> int:
    """Point an MT5 account's server at the official terminal installed by scripts/setup-mt5-terminal.sh on this
    machine: method = terminal bridge, 127.0.0.1:port, pinned certificate, token saved write-only."""
    import hashlib
    import ssl
    from pathlib import Path
    from .config_models import BridgeTokenCredential, ConnectionIn
    folder = Path(state)
    cert, token_file = folder / 'corei-bridge.crt', folder / 'corei-bridge.token'
    if not cert.exists() or not token_file.exists():
        print(f'No bridge certificate/token in {folder}: run scripts/setup-mt5-terminal.sh first.')
        return 1
    digest = hashlib.sha256(ssl.PEM_cert_to_DER_cert(cert.read_text())).hexdigest().upper()
    fingerprint = ':'.join(digest[i:i + 2] for i in range(0, 64, 2))
    account = next((a for a in store.list_records('accounts', {}, '') if a['code'] == account_code), None)
    if not account:
        print(f'Account {account_code} not found.')
        return 1
    conn = next(c for c in store.list_records('connections', {}, '') if c['id'] == account['connection_id'])
    settings = {**conn['settings'], 'bridge': {'host': '127.0.0.1', 'port': port, 'fingerprint': fingerprint}}
    r = Run(actor, apply)
    r.upsert('connections', ConnectionIn, conn, {'adapter': 'TERMINAL_BRIDGE', 'settings': settings}, f"connection {conn['code']} (method → MT5 terminal bridge 127.0.0.1:{port})")
    r.say('save', f"bridge token for {conn['code']}", 'write-only, encrypted')
    if apply:
        token = token_file.read_text().strip()
        result = store.set_credential(actor, 'connections', conn['id'], BridgeTokenCredential(kind='BRIDGE_TOKEN', token=token), reason=REASON)
        token = ''
        if isinstance(result, dict) and result.get('change_request'):
            print(f"    → waiting for approval: {result['change_request']['summary']}")
    print(f"\n{r.changes} change(s) {'made' if apply else 'to make'}." + ('' if apply else ' Run again with --apply to make them.'))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Apply the test setup values (dry run unless --apply).')
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--actor', default=os.environ.get('COREI_OPERATOR_USER') or 'setup-script')
    parser.add_argument('--bridge', metavar='ACCOUNT_CODE', help='switch this MT5 account to the terminal installed on this machine')
    parser.add_argument('--port', type=int, default=9443)
    parser.add_argument('--state', help='the terminal instance state folder (printed by setup-mt5-terminal.sh)')
    args = parser.parse_args(argv)
    if args.bridge:
        if not args.state:
            parser.error('--bridge needs --state')
        return use_bridge(args.actor, args.bridge, args.port, args.state, args.apply)
    return run(args.actor, args.apply)


if __name__ == '__main__':
    sys.exit(main())
