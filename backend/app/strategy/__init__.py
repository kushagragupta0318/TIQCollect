"""AI Strategy and Tools (plan §7): pure computation, no database access.

states       the 8-state space, read from models/loan; `portfolio_state`, made strict
monte_carlo  the account-level Monte Carlo engine (task E02)
backtest     coverage of the engine's bands against history (E04 core; inert, ADR 0013)
synthetic    SYNTHETIC inputs for benchmarks and tests

`states` imports `models/loan` for the state space and the DPD ladder, so importing this
package builds SQLAlchemy metadata and needs the app's settings env. It opens no
connection and runs no query: callers pass arrays. Import submodules directly.
"""
