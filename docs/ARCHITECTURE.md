# COREI-OS-MINI · How the system is built

COREI turns market data into controlled trades in **eight stages**. Each stage answers one question, owns one kind of
record and hands one thing to the next. Setup feeds every stage; Monitor and the audit log watch every stage.

```
            SETUP: providers · accounts · servers · bridge nodes · instruments · strategies · deployments
               │
 1 Market data ─► 2 Market intelligence ─► 3 Signals ─► 5 Risk ─► 7 Execution ─► 6 Portfolio ─► 8 Post-trade
   ticks, bars      features, regimes         ▲  intents     sized      orders, fills     positions,      reconciliation,
   quality          screens, calendar         │             approval                     P&L, equity     costs, audit
                                         4 Strategies ◄──────────────────────────────── (P&L and exposure feed back into risk)
                                           runtime, backtest
               │
            MONITOR: problems, health, alerts · AUDIT: every change, password use, approval and order event
```

| # | Stage | Question | Owns | Code | Screen |
|---|---|---|---|---|---|
| 1 | Market data | What is the market doing, from which source, can we trust it? | ticks, bars, quality | `md/` | Market data |
| 2 | Market intelligence | What state is each market in? | features, regimes, screens | `intel/` | Intelligence |
| 3 | Signal intelligence | Is there a trade? | signals, intents | `trading/signals.py` | Signals |
| 4 | Strategy intelligence | Which rules run where, are they working? | versions, deployments, runtime, backtests | `strategy_registry.py`, `trading/algorithms.py`, `runtime.py`, `backtest.py` | Strategies |
| 5 | Risk intelligence | Allowed? How big? Exposure? | limits, kill switches, decisions | `trading/risk.py` | Risk |
| 6 | Portfolio intelligence | What do we hold and what is it worth? | positions, equity | `trading/portfolio.py` | Portfolio |
| 7 | Execution intelligence | How does an order reach the broker, what happened? | orders, order events, fills | `trading/oms.py` | Orders |
| 8 | Post-trade | Does our record match the broker's? | breaks, costs | (planned), audit log | Post-trade |

## One trade, end to end

1. The collector stores ticks and closes bars (source time, receipt time, quality).
2. The feature engine computes indicators and the regime on each closed bar.
3. The runtime evaluates each active deployment on its newly closed bar (once per bar); a change in the strategy's
   target is a **signal** (side, strength, reasons, the features behind it).
4. **Validation**: fresh price, spread against ATR, market open, event risk, duplicate, minimum strength.
5. **Decision**: the target (latest signal, or N agreeing) against the position held → the order needed.
6. **Risk**: kill switches → order sending (real orders) → market and price → lot rules → limits (tightest of firm,
   account, strategy, instrument, deployment); sizing (fixed, % of equity with an ATR stop, fixed value).
7. **Order engine**: client order ID from the intent (a repeat is the same order); NEW → APPROVED → SUBMITTED →
   ACKNOWLEDGED → FILLED, or REJECTED / CANCELLED / EXPIRED / UNKNOWN; every change is an append-only event.
8. **Paper adapter** fills at the live bid/ask plus slippage (broker adapter: next build).
9. **Portfolio** updates the position in the fill's transaction; P&L at the live mid; equity and drawdown feed risk.
10. **Post-trade** compares with the broker (planned) — differences are raised, never hidden.

## Rules every stage follows

- One owner per record; others read it.
- Decisions on market time (closed bars, quote times) → the same data gives the same decision.
- Exact numbers (NUMERIC / Decimal) for prices, quantities, P&L and exposure.
- Audit in the same transaction; order events append-only.
- Risk can always say no; every refusal names its reasons.
- UNKNOWN is a state, resolved by a check or a person, never by sending again.
- Paper first; real orders need the adapter and the environment's order-sending switch (off by default).
- Every stage screen: Overview · Work · Monitor · Configure, plus its stage map; plain words; blocked actions say why.

## Where things run

| Process | What | Where |
|---|---|---|
| Workstation API (`corei-os-mini-workstation`) | All screens and APIs; the feature engine and the trading cycle (every minute) | COREI server |
| Collector (`corei-os-mini-collector`) | Market data from MT5 (bridge, EA push, SDK), Upstox, FIX | COREI server |
| Nightly jobs | Archive, verify, backup | COREI server |
| MT5 bridge nodes | Official MT5 terminals behind TLS + token, a terminal pool | Windows VPS / PC |
| PostgreSQL | Every record | COREI server |
