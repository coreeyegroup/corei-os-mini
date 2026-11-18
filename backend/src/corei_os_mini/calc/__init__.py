"""Calculation Graphs: every calculation COREI makes, as a graph of typed nodes (the n8n pattern, our own code).

Why it exists: a desk must see, check, copy and change every formula that produces a number used for a decision —
features, signal scores, strategy rules, sizing, risk checks, measures — with live numbers at every step, and put a
change into use only through versions and approval. One graph model and one executor serve every stage (DEC-93).
Purpose: the package root. model (graphs, nodes, edges, purposes and limits), types (port types and what may connect),
expr (the safe expression language), nodes (the catalogue), run (validation and execution with a trace), store (the
graphs in PostgreSQL, versioned and audited).
Must never: execute user code (no eval, exec or Python AST execution), read the clock, the network, files or the
database from inside a graph, carry money in float, or look at a bar after the one being computed.
Dependencies: intel.indicators, intel.engine (features), trading.signal_library (library scores) — wrapped, never
re-implemented. Consumers: calc_api, and (from phase 2) the stages that bind graph versions.
Architectural position: a library inside the backend, beside the stages it serves; no separate server.
"""
