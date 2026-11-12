# Rebuild the index

`store/` holds a set of `*.rec` data files and an `index.json` that maps each
record key to its file and byte offset. The index is corrupt: some offsets are
wrong, some keys are missing, and one entry points at a file that no longer exists.

The data files are intact and authoritative.

Write `/app/rebuild.py` so that running it regenerates `/app/store/index.json`
from the record files. Each `.rec` file contains one record per line in the form:

```
<key>\t<payload>
```

The rebuilt index must map every key present in the data to
`{"file": "<name>.rec", "offset": <byte offset of the line start>}`.
Keys must be unique; if a key appears twice, the last occurrence wins.
