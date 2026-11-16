"""Stage 2 · Market intelligence: the feature store (indicators computed once from stored bars), sessions, volatility
and regime classification, and the instrument screener. Reads Stage 1's bars and quality; writes only intel_* tables;
never touches orders."""
