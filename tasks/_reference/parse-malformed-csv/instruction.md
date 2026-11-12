# Parse the malformed export

`orders.csv` came out of a legacy system and is a mess: ragged rows, a stray
BOM, mixed quoting, and a few rows with the delimiter inside an unquoted field.

Write `/app/summarise.py` so that running `python3 /app/summarise.py` writes
`/app/summary.json` containing:

```json
{"rows": <int>, "total_cents": <int>, "currencies": ["..."]}
```

* `rows` counts only rows with a parseable integer `amount_cents`.
* `total_cents` is the sum of those amounts.
* `currencies` is the sorted set of currency codes seen on those rows.

Rows that cannot be parsed are skipped, not guessed at.
