"""Market data: collection plans, the collector, quality, archive and backups.

Layers (DEC-41): journal file (every message as received) → live prices in memory →
recent ticks and bars in PostgreSQL (split by day) → nightly fingerprinted Parquet
history read with DuckDB. Open source only: PostgreSQL, Parquet, DuckDB.
"""
