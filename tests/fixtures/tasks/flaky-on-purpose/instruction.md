# Deliberate flake

Write `/app/coin.txt` containing the result of a coin flip seeded from
`AUTOBENCH_SEED`. This fixture exists so the statistics layer has a task
with a *known* pass rate to validate pass@k and interval coverage against.
