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
| 4 | Strategy intelligence | Which rules run where, are they working? | versions, deployments, runtime, backtests, optimisations, drift | `strategy_registry.py`, `trading/algorithms.py`, `runtime.py`, `backtest.py`, `optimise.py`, `strategy_health.py` | Strategies |
| 5 | Risk intelligence | Allowed? How big? Exposure? What could we lose? | limits, kill switches, decisions, stress scenarios | `trading/risk.py`, `var.py` | Risk |
| 6 | Portfolio intelligence | What do we hold, what is it worth, how is capital split? | positions, equity, partitions, copy links | `trading/portfolio.py`, `partitions.py`, `copytrade.py` | Portfolio |
| 7 | Execution intelligence | How does an order reach the broker, what happened? | orders, order events, fills | `trading/oms.py`, `execution.py`, `brokers.py`, `routing.py` | Orders |
| 8 | Post-trade | Does our record match the broker's? What did it cost? | broker book and deals, breaks, recon runs | `trading/posttrade.py` | Post-trade |

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
8. **Paper** fills at the live bid/ask plus slippage, in the same transaction. **Real (demo or live)**: the order stops
   at APPROVED (held for a second person on LIVE when four-eyes applies); the **dispatcher** re-checks kill switches
   and order sending, pre-checks at the broker, commits SUBMITTED, then sends — MT5 through the terminal bridge (routes
   in priority order), Upstox through its API — and records the answer: fills per deal, ACKNOWLEDGED, REJECTED, or
   UNKNOWN when no answer came. UNKNOWN and working orders are looked up by their tag; nothing is ever sent twice.
9. **Portfolio** updates the position in the fill's transaction; P&L at the live mid; equity and drawdown feed risk;
   copy links turn a master fill into follower orders; partitions and weights set the next order's size.
10. **Post-trade** reads the broker's book every few minutes and reconciles it with COREI's — differences are raised
   as breaks, never hidden; TCA, journal and statements are built from the fills and the broker's deals.

## Rules every stage follows

- One owner per record; others read it.
- Decisions on market time (closed bars, quote times) → the same data gives the same decision.
- Exact numbers (NUMERIC / Decimal) for prices, quantities, P&L and exposure.
- Audit in the same transaction; order events append-only.
- Risk can always say no; every refusal names its reasons.
- UNKNOWN is a state, resolved by a check or a person, never by sending again.
- Paper first; real orders need an account that can take them, the environment's order-sending switch (off by
  default) and, on LIVE, a second person for manual orders.
- A broker call never holds a database transaction; SUBMITTED is on record before anything is sent.
- Every stage screen: Overview · Work · Monitor · Configure, plus its stage map; plain words; blocked actions say why.

## Where things run

| Process | What | Where |
|---|---|---|
| Workstation API (`corei-os-mini-workstation`) | All screens and APIs; the feature engine and the trading cycle (every minute); the order dispatcher and broker look-ups (every 2 s); reconciliation (every few minutes) | COREI server |
| Collector (`corei-os-mini-collector`) | Market data from MT5 (bridge, EA push, SDK), Upstox, FIX | COREI server |
| Nightly jobs | Archive, verify, backup | COREI server |
| MT5 bridge nodes | Official MT5 terminals behind TLS + token, a terminal pool; market data, account reads and (0.43+) orders | Windows VPS / PC |
| PostgreSQL | Every record | COREI server |
