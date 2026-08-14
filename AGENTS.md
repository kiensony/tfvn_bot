# Repository Guidelines

## Documentation Map

Read [README.md](README.md) for setup and operation, [CODEBASE.md](CODEBASE.md) for runtime flow and file ownership, [FUNCTIONS.md](FUNCTIONS.md) for the full command and feature catalog, and [CODING_CONVENSION.md](CODING_CONVENSION.md) for detailed implementation rules. Update the relevant document when structure, configuration, or conventions change.

## Prospective Target Specification

`FUTURE_DEVELOPMENT.md` is an unapproved, deferred, and non-operational prospective target-system specification. Its identified `SHALL` and `SHALL NOT` statements apply only to a hypothetical future TrapNet baseline; they do not describe current behavior, authorize implementation, or create repository acceptance criteria.

Do not implement, scaffold, refactor toward, test against, procure for, or deploy any item from that document unless the user's current request explicitly names a bounded work package, stage, or requirement ID, explicitly supersedes its `IDR-0001` deferment for that scope, and authorizes implementation after the specification's entry gates are satisfied. A generic request to “follow,” “build,” or “implement the specification” does not activate the whole document; establish explicit scope first. An explicit request to edit the document authorizes documentation changes only and never supersedes `IDR-0001`.

For all other work, ignore its target architecture and treat the current code, tests, [README.md](README.md), [CODEBASE.md](CODEBASE.md), [FUNCTIONS.md](FUNCTIONS.md), and [CODING_CONVENSION.md](CODING_CONVENSION.md) as authoritative. When a proposed item is actually delivered, update the authoritative documentation in the same change. `FUTURE_DEVELOPMENT.md` is never evidence that a feature exists.

## Project Structure & Module Organization

`main.py` configures Discord intents, loads datasets, and discovers extensions. `db.py` creates the MongoDB client, while `dataloader.py` handles files under `data/`. Bot features live in domain-based packages under `cogs/` (`mod/`, `booster/`, `minigames/`, `interaction/`, and others). Every loadable cog must expose `async def setup(bot)`. Prefix helper modules with `_` so production discovery skips them.

Static media constants belong in `assets/`, maintenance utilities in `scripts/`, and automated tests in `test/`. Docker deployment files are at the repository root.

## Build, Test, and Development Commands

- `python -m venv venv` and `.\venv\Scripts\Activate.ps1`: create and activate the Windows development environment.
- `python -m pip install -r requirements.txt`: install the pinned Python dependencies.
- `python main.py`: run the bot from the repository root so relative data paths resolve.
- `python -m unittest discover -s test -p "test_*.py"`: run the unit-test suite.
- `docker compose up --build -d`: build and start the production-style container. MongoDB is external to this Compose file.

For focused development, set `ENVIRONMENT=development` and list dotted cog paths such as `cogs.mod.*` in the ignored `dev_cogs.txt` file.

## Coding Style & Naming Conventions

Target Python 3.11 and use four-space indentation. Follow existing conventions: `snake_case` for modules, functions, and variables; `PascalCase` for cog/view classes; and `UPPER_SNAKE_CASE` for constants. Keep Discord callbacks asynchronous, add type hints to new public helpers, and group standard-library, third-party, then local imports. No formatter or linter is configured, so match surrounding code and avoid unrelated reformatting.

## Testing Guidelines

Tests use the standard `unittest` framework. Name discovered files `test_*.py`, classes `Test...`, and methods `test_...`. Keep unit tests deterministic and isolated from live Discord, MongoDB, and external APIs. There is no enforced coverage threshold; add focused regression tests for changed parsing, persistence, or game logic.

## Commit & Pull Request Guidelines

Recent history favors short, imperative subjects such as `Add giveaway cog` and `Improve data prep`. Keep each commit scoped to one behavior. Pull requests should explain user-visible changes, configuration or schema impacts, and verification performed; link relevant issues. Include screenshots for changed Discord embeds or interactive views. Never commit tokens, API credentials, `.env` files, production IDs, or database exports.
