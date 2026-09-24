"""AI Strategy and Tools (plan §7): pure computation, no database.

states       the 8-state portfolio space and `portfolio_state`, its one definition
monte_carlo  the account-level Monte Carlo engine (task E02)
backtest     coverage of the engine's bands against history (task E04 core)
synthetic    SYNTHETIC inputs for benchmarks and tests

Nothing here imports the ORM; callers pass arrays. Import submodules directly.
"""
