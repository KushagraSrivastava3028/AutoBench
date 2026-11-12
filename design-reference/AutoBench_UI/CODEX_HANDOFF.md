# Apply this UI to the AutoBench app

Use this folder as a working design reference while editing the existing AutoBench repository.

1. Locate the app shell, overview route, existing UI components, and the data sources for runs, trials, alerts, budgets, and benchmark statistics.
2. Match the preview’s visual hierarchy. Keep one Overview heading and a left-aligned Swiss type hierarchy. Use the rounded bento grid from dist/overview.css, with a wide active-run tile, compact queue and spend tiles, and a wider chart beside alerts. Use the theme and radius tokens in dist/style.css.
3. Map the reference components to the project’s own component system. Use the existing routing, authentication, APIs, permissions, and run and trial actions.
4. Bind tiles and counts to project-scoped data. Open run should navigate to that run. Running and Queued should open the corresponding list filters. Alerts should open their evidence, and the budget tile should agree with the budget screen.
5. Use the backend’s actual time series and confidence intervals for the headline scores, chart bands, and View data table. Preserve separate states for agent failures, harness errors, warnings, and passes. Pair every color with a readable label or symbol.
6. Handle loading, error, empty, and missing-permission states with the app’s existing patterns. Keep tables scrollable within their cards and verify keyboard focus, theme contrast, and the single-column layout.
7. Run the project’s existing build and tests. Manually check overview navigation, the period selector, run filters, run creation, and trial drill-down against the app’s own data.

The standalone preview isolates its simulated data in dist/demo-data.mjs. Treat those values and local mutations as examples for visual verification. The real app remains the source of truth for behavior, calculations, and persistence.
