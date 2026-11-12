# AutoBench UI handoff

A runnable frontend reference for the AutoBench evaluation console. The overview pairs a Swiss type hierarchy with an Apple-inspired bento grid. Rounded cards use a neutral palette and a blue accent in both light and dark modes.

## Run the preview

From the extracted directory, run:

    python -m http.server 8080 --directory dist

On Windows, use py instead of python if needed. Open http://localhost:8080 in a browser. The preview loads ES modules over HTTP and needs no package installation or API key.

## Source map

| File | Responsibility |
| --- | --- |
| dist/index.html | Accessible shell, navigation mount points, dialog, and announcements |
| dist/overview.mjs | Overview composition, active run, alerts, trend, queue, budget, and recent runs |
| dist/overview.css | Bento grid, responsive breakpoints, and overview-specific layout |
| dist/ui.mjs | Shared buttons, links, status labels, tables, panels, and safe text rendering |
| dist/style.css | Theme tokens, typography, navigation, forms, tables, and keyboard focus |
| dist/app.js | Routes, filtering, dialogs, run details, trial inspector, and UI events |
| dist/demo-data.mjs | Demo runs, task outcomes, alerts, budgets, and trend series |
| tests/overview.test.mjs | Checks for dashboard labels, count links, chart ranges, and empty state |
| CODEX_HANDOFF.md | Instructions for adapting the UI to the real application |

## Overview behavior

| Component | Data and interaction |
| --- | --- |
| Active run | Shows a running evaluation and its completed and total trial counts. Open run opens its detail page. When no evaluation is running, the card links to the Runs screen. |
| Needs attention | Links to the affected release gate, invalid run, or task. Harness health opens the operations screen. |
| Benchmark trend | The 7-day and 30-day choices show the sample core-50 series with 95% confidence intervals. View data shows the scores and intervals in a table. |
| Run queue | Running and Queued open the corresponding run filters. |
| Weekly spend | Shows spend against the weekly budget. Selecting the spend card opens Settings with the same totals. |
| Recent runs | Run names and suites open their details. All runs clears the status filter. |

The preview uses demo data. Run creation, cancellation, and task publishing are local simulations that reset when the page reloads. The theme preference is stored in the browser.

## Check the package

    node --test tests/overview.test.mjs
    node --input-type=module --check < dist/app.js
