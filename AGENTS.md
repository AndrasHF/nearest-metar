# Repository Guidelines

## Project Structure & Module Organization

- `app.py` contains the Flask routes, METAR parsing, station metadata caching, reverse geocoding, and distance calculations.
- `templates/index.html` is the page template; `static/app.js` handles browser geolocation and rendering; `static/style.css` provides styling.
- `tests/test_app.py` covers routes and runtime settings, `tests/test_services.py` covers caches and geocoding concurrency, and `tests/test_frontend.py` covers wind rendering.
- `requirements.txt` lists runtime dependencies; `requirements-dev.txt` adds QuickJS for tests. `README.md` documents configuration and usage.

## Build, Test, and Development Commands

Run commands from the repository root:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
python app.py
```

The development server runs at `http://127.0.0.1:5050`. Use `NEAREST_METAR_PORT=5051 python app.py` to select another port. For runtime dependencies alone, install `requirements.txt`. Static assets require no build step.

- `python -m unittest discover -s tests -v`: run the complete suite.
- `python -m unittest discover -s tests -p test_services.py -v`: run service tests.
- `git diff --check`: check whitespace before committing.

## Coding Style & Naming Conventions

Use four-space indentation in Python and two spaces in JavaScript and HTML. Follow existing compact CSS formatting. Use `snake_case` for Python functions and variables, `PascalCase` for classes, `UPPER_CASE` for constants, and `camelCase` for JavaScript functions and variables. Prefix internal Python helpers with `_`. Annotate service helpers; explain behavior in comments. No formatter or linter is configured.

## Testing Guidelines

Use `unittest`, `unittest.mock`, and `test_*.py` files with descriptive `test_*` methods. QuickJS executes the actual frontend script with a minimal DOM. Mock network responses and clocks; use temporary directories for persisted caches and events for concurrent tests. Socket tests need local bind access.

Add regression tests for bug fixes and confirm they fail before the fix. Run the full suite before submitting changes. No numeric coverage threshold is configured.

## Commit & Pull Request Guidelines

Use concise, imperative commit subjects, matching history such as `Fix query handling for configured geocoding endpoints`. For substantive changes, explain behavior and validation in the body.

Keep changes focused. PR descriptions should state the problem, resulting behavior, test command and result, and relevant issue links. Include screenshots for visible UI changes.

## Configuration & Cache Invariants

Use `NEAREST_METAR_` environment variables and update `README.md` when configuration changes. Preserve weather availability during metadata or geocoding failures. Keep coordinates and place names in memory only. Share the cache directory across local workers for SQLite throttling, and keep cache hits independent of download locks.
