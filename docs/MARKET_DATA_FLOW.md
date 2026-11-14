# Market data: collected once, stored, analysed; executed on many accounts

## Short answer

COREI does **not** fetch prices on demand for analysis. Every price is **collected continuously, stored, and
analysed from the store** (and from the live stream as it is stored). Analysis never logs in to a broker.

```
 Quality sources (one per instrument, a backup ready)         Store                         Use
 ───────────────────────────────────────────────────          ─────                         ───
 MetaQuotes / VT Markets (MT5) ── XAUUSD, EURUSD ─┐
 Upstox (API) ─────────────────── NIFTY, BANKNIFTY ┼─▶ collector ─▶ journal (raw, every message, gz)
 Fast feed / LMAX (FIX) ───────── XAUUSD (backup) ─┘              ─▶ md_latest (last price per account × symbol)
                                                                  ─▶ md_ticks  (PostgreSQL, daily partitions, "hot" days)
                                                                  ─▶ md_bars   (S1 … D1 built from ticks; broker history backfill)
                                                                  ─▶ md_quality (per minute: latency, gaps, spread, spikes, deviation)
                                                                  ─▶ Parquet archive (older days, SHA-256 fingerprints) ─▶ DuckDB queries
                                                                                    │
                                                    strategy · signal · portfolio ◀─┘  (reads stored bars/ticks of the instrument's ACTIVE feed)
                                                                                    │
                                    one parent signal per instrument ──▶ allocation ──▶ child orders per account (lot sized per account)
                                                                                    │
            each execution account's own quote (md_latest of that account) ──▶ pre-send price check: fresh? spread? deviation from the reference?
```

## How the pieces map to what we decided

- **Instrument, not account.** A strategy works on an *instrument* (XAUUSD, NIFTY). Its prices come from the
  instrument's **feed set**: collection plans with role PRIMARY and BACKUP on chosen data-source accounts. The
  collector uses the primary; when quality rules fail (stale, gaps, deviation) it switches to the backup and
  logs FEED_SWITCH. `md_feed_state` records which plan is feeding each instrument now.
- **Stored, then analysed.** Ticks are written once (journal first, then PostgreSQL); bars are built from them.
  Analysis, back-tests, dashboards and quality reports read the store, so the broker is never asked twice for the
  same price, results are reproducible, and the history survives restarts. Recent days stay in PostgreSQL
  (`hot_days` per plan); older days move to Parquet files queried with DuckDB.
- **Executed on many accounts.** One signal per instrument becomes child orders for every account the portfolio
  allocates to (each with its own lot size and symbol mapping, e.g. XAUUSD → `XAUUSD.r` at VT Markets).
- **Each execution account still needs its own quote**, because fills happen at *that broker's* price. The
  pre-send price check (`md/store.price_check`) refuses an order when that account's latest price is stale or its
  spread too wide. So execution accounts run a light **MONITORING** plan for the symbols they trade (latest price
  and quality; history can be kept short), while the analysis uses the reference source. The dashboard's
  *Sources* panel shows the same instrument from every account side by side, with the difference in points.

## Status

Built: collection, storage, bars, quality, feed switching, archive, price check, dashboard.
Not yet: the strategy engine reading stored prices (it runs on synthetic prices today, A-34), and order
adapters (A-32).
