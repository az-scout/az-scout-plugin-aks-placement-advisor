# Copilot Instructions for az-scout-plugin-aks-placement-advisor

## Project overview

This is an **az-scout plugin** — a Python package that extends [az-scout](https://github.com/az-scout/az-scout) with custom API routes, MCP tools, UI tabs, and chat modes. Plugins are auto-discovered via the `az_scout.plugins` entry-point group.

## Tech stack

- **Backend:** Python 3.11+, FastAPI (APIRouter), az-scout plugin API
- **Frontend:** Vanilla JavaScript (no framework, no npm), CSS custom properties
- **Packaging:** hatchling + hatch-vcs, CalVer (`YYYY.MM.MICRO`), src-layout
- **Tools:** uv (package manager), ruff (lint + format), mypy, pytest

## Project structure

```
src/az_scout_aks_placement_advisor/
├── __init__.py          # Plugin class + module-level `plugin` instance
├── models.py            # SkuRecommendation dataclass + DISCLAIMER
├── service.py           # Core logic: SKU fetching, filtering, scoring
├── routes.py            # FastAPI APIRouter (mounted at /plugins/aks-placement-advisor/)
├── tools.py             # MCP tool functions (exposed on the az-scout MCP server)
└── static/
    ├── css/
    │   └── aks-placement-advisor.css
    ├── html/
    │   └── aks-placement-advisor-tab.html
    └── js/
        └── aks-placement-advisor-tab.js
```

## Code conventions

- **Python:** All functions must have type annotations. Follow ruff rules: `E, F, I, W, UP, B, SIM`. Line length is 100.
- **JavaScript:** Vanilla JS only — no npm, no bundler, no frameworks. Use `const`/`let` (never `var`). Functions and variables use `camelCase`.
- **CSS:** Use CSS custom properties for theming. Support both light and dark modes using `[data-theme="dark"]` selectors.

## Quality checks

```bash
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/
uv run mypy src/
uv run pytest
```

## Versioning

- Version is derived from git tags via `hatch-vcs` — never hardcode a version.
- `_version.py` is auto-generated and excluded from linting.
- Tags follow CalVer: `v2026.2.0`, `v2026.2.1`, etc.
