# Calculation Graphs

**Purpose.** How every calculation becomes a graph of typed nodes that a desk can see with live numbers, copy, change,
test and approve — the n8n pattern (nodes, wires, an execution view), built in our own code (n8n's code is not used;
its licence does not allow it, and we need none of its server). Decision: DEC-93. Inventory and phases:
[CALCULATION_INVENTORY.md](CALCULATION_INVENTORY.md).

## The model (`calc/model.py`)

A **graph** has a purpose, nodes, wires (`node.port → node.port`), exposed parameters and notes.

| Purpose | Ends in | Contract (checked on every run) |
|---|---|---|
| FEATURE | `output.feature` | a number per bar |
| SIGNAL | `output.signal` | a score per bar within −100…+100, plus the values shown with it |
| STRATEGY_RULE | `output.rule` | LONG / SHORT / FLAT / HOLD per bar, strength 0…100, reason |
| SIZING | `output.sizing` | an exact decimal quantity ≥ 0 and a note |
| PROTECTIVE | `output.protective` | stop and/or target as exact decimal prices |
| RISK_CHECK | `output.risk_check` | refuse + reason only; a value that cannot be computed refuses (fails closed) |
| ALLOCATION | `output.allocation` | non-negative weights that sum to 1 |
| MEASURE | `output.measure` | a named number with its unit |

**Parameters** are knobs a graph exposes (`fast`, `slow`, `scale`…), bounded by min/max; a node parameter refers to one
as `{{name}}`. Changing a parameter per use never needs a new graph.

**Limits**: 200 nodes, 400 wires, 50 steps deep, 20 000 bars per run; expressions ≤ 400 characters, 200 parts,
40 levels.

## Port types (`calc/types.py`)

`series<float>`, `series<bool>`, `series<side>`, `float`, `bool`, `side`, `text`, `decimal:money`, `decimal:price`,
`decimal:quantity`. A wire joins equal types; a single value may feed a series (repeated on every bar). A number
**never** feeds money, a price or a quantity except through **To decimal**, which names the kind, places and rounding.
Decimal kinds do not mix (a price cannot feed a quantity).

## Nodes (`calc/nodes/`)

| Family | Nodes |
|---|---|
| Sources | Bars, Feature (any platform feature), Signal score (any library formula), Number, True/false, Side, Quote, Position, Account, Instrument, Setting |
| Indicators | SMA, EMA, RSI, True range, ATR, Bollinger, ADX, Efficiency ratio, Realised volatility, Range position, MACD, Stochastic, Donchian, Z-score, VWAP, OBV, Rate of change — wrapping `intel/indicators.py` |
| Series | Shift, Rolling (mean, sum, min, max, stdev, stdev_pop, rank; this bar included or the n before it), Difference, Percent change, Log return, Cumulative, Normalise by ATR |
| Maths | Add, Subtract, Multiply, Divide, Function, Min/max, Clamp, Round, **Expression** |
| Logic | Compare, Crosses, And, Or, Not, If, Between, Side from score, Side with hysteresis |
| Combine | Weighted sum (Σ w·x ÷ Σ\|w\|), Vote, Consensus, Rank |
| Convert | Last value (number, true/false, side), To decimal |
| Outputs | one per purpose (above) |

Each node has typed ports, checked parameters, a pure `run`, a `formula` (shown on screen), an `explain` (the formula
with the numbers of the bar being looked at) and a `lookback` (warm-up). The catalogue is served at `GET /api/calc/nodes`.

## The expression language (`calc/expr.py`)

One line over the node's named inputs: `+ − * / ^`, comparisons, `and or not`, `true false`, `x[t-k]` (k bars ago) and
the functions `abs min max clamp sqrt log exp round sign if`. Our own tokenizer and recursive-descent parser compile it
into closures. There is no `eval`, `exec`, Python AST, attribute, string, loop, assignment or import; `x[t+1]` (a
future bar) cannot be written. A missing value makes the result missing; division by zero, √ of a negative and log of
zero or less give a missing value.

## The executor (`calc/run.py`)

1. **Check** (`compile_graph`): schema, graph parameters and overrides within bounds, node types, node parameters,
   wires (both ends exist, one wire per input, types compatible — the message names both ports and types), required
   inputs, cycles, depth, exactly one output node of the graph's purpose. A graph that fails is never run.
2. **Run** (`execute`): nodes in a fixed topological order (ties by id), so the same graph, parameters and bars always
   give the same result. A node that fails records its reason; only the nodes that depend on it are skipped.
3. **Trace**: for the chosen bar (default the last), every node's inputs, outputs, formula with numbers and time.
4. **Hash**: SHA-256 of the canonical definition without node positions (moving a node is not a change).

Measured (tests/test_calc.py, `test_time_budgets`): a signal graph on 600 bars ≈ 1.3 ms (budget 20 ms);
30 instruments × 5 timeframes × 15 graphs ≈ 3 s (budget 10 s).

## Storage (migration 038)

`calc_graphs`, `calc_graph_versions` (definition JSONB, hash, parity reference; an APPROVED or RETIRED version cannot
be changed or deleted — enforced by a trigger), `calc_bindings` (where an approved version is used; one active per
target), `calc_runs` (every preview, parity check and engine run — insert-only, enforced by a trigger). Built-in graphs
come from code (`calc/builtin.py`), are APPROVED and read-only.

## API (`/api/calc`)

Life cycle: `POST /graphs` (new draft), `POST /graphs/{code}/copy`, `PUT /graphs/{code}/draft`,
`POST /graphs/{code}/versions/{n}/{propose|approve|reject|retire}` (approve, reject and retire need the Risk approver
role; approval is never by the person who proposed it), `GET|POST /bindings`, `DELETE /bindings/{id}`,
`GET /graphs/{code}/usage`, `POST /parity`. Reads: `GET /nodes`, `GET /graphs`, `GET /graphs/{code}`, `GET /graphs/{code}/versions/{n}`, `POST /validate`,
`POST /preview` (stored bars ending at the newest stored bar — market time, never the machine clock; the latest stored
quote fills Quote; money values sent by the caller must be decimal strings; every preview is recorded in `calc_runs` in
the same transaction), `GET /runs`. Proposing, approving, retiring and binding arrive with the editor (phase 3) and
the signal phase (phase 2).

## On screen (ui/src/calc)

`GraphWorkbench`: the library, the canvas (@xyflow/react) with each node's value on the chosen bar, the selected
node's formula with its numbers, the result; *Plain* lists the steps in the order they run (also used where no canvas
can be drawn). Edit mode adds the node palette, wiring with an immediate type check, parameters, check, save,
propose, approve, retire, *Put to use* (signal definitions) and *Check parity*.

## In the stages

* **Signals**: a signal definition bound to an approved SIGNAL graph is scored by it everywhere (`signal_library.score_series`).
* **Strategies**: algorithm `GRAPH` pins an approved STRATEGY_RULE version (definition + hash) inside the strategy
  version; runtime and backtest run it with the algorithms' target state machine (HOLD keeps the target).
* Node `logic.latch` is that state machine as a node; Expression inputs may be true/false (`name:bool`); function
  `coalesce(x, y)`.

## Guardrails

* No Docker, Kubernetes, Kafka, Redis or workflow server: a library inside the backend.
* No user code: our own grammar and a function whitelist; no network, file, clock or database access inside graphs
  (a test scans the package).
* Money is exact decimal; a float reaches a money port only through To decimal.
* Point in time: every node is tested by truncating the bars — its output on bar i must not change when later bars exist.
* Risk cannot be bypassed: a risk graph can only add a refusal and fails closed; the built-in limits always run.
* Versions and their audit are written in one transaction; deployments will pin a version; four-eyes for approval;
  live trading stays OFF by default.
