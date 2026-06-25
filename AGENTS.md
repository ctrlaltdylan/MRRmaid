# AGENTS.md

This file provides instructions for AI agents working in this repository.

## Project Overview

MRRmaid is a CLI tool for tracking MRR (Monthly Recurring Revenue), NRR, churn, and customer concentration from Shopify Partner and Stripe APIs. It's a Python 3.10+ project using modern Python tooling.

## Build/Development Commands

**Package Management:**
- Uses `uv` for dependency management (see `uv.lock`)
- `uv pip install -e .` - Install package in editable mode
- `uv pip install -e '.[dev]'` - Install with dev dependencies

**Testing:**
- `pytest` - Run all tests
- `pytest path/to/test_file.py` - Run specific test file
- `pytest path/to/test_file.py::test_function` - Run single test
- `pytest -xvs` - Run with verbose output and stop on first failure
- `pytest --cov=mrrmaid` - Run tests with coverage

**Linting & Formatting:**
- `ruff check .` - Run linter
- `ruff check --fix .` - Run linter with auto-fix
- `ruff check src/mrrmaid/` - Lint specific directory
- `black --check .` - Check formatting
- `black .` - Apply formatting

**Running the CLI:**
- `python -m mrrmaid --help` - Run the CLI
- `python -m mrrmaid dashboard` - Show dashboard
- `python -m mrrmaid sync` - Sync data from APIs

## Code Style Guidelines

**General:**
- Python 3.10+ with modern type annotations (`list[str]`, `dict[str, Any]`, `X | Y`)
- Line length: 100 characters (configured in pyproject.toml)
- Use type hints everywhere (functions, methods, dataclass fields)
- Follow PEP 8 naming conventions

**Imports:**
- Order: stdlib → third-party → local
- Use absolute imports: `from mrrmaid.models.database import Base`
- Group imports with blank lines between groups
- Use `from typing import ...` for type hints

**Types:**
- Use `Optional[X]` or `X | None` for nullable types
- Use explicit return types: `-> None` for procedures
- Prefer `list[dict[str, Any]]` over `List[Dict[str, Any]]`
- Use `dataclass` for data structures with `@dataclass`

**Naming:**
- Classes: `PascalCase` (e.g., `MetricsCalculator`, `StripeSubscription`)
- Functions/variables: `snake_case` (e.g., `calculate_mrr`, `customer_id`)
- Constants: `UPPER_SNAKE_CASE` (e.g., `ACTIVE_STATUSES`)
- Private methods: `_leading_underscore` (e.g., `_parse_subscription`)
- Boolean properties: `is_` prefix (e.g., `is_trial`, `is_canceled`)

**Docstrings:**
- Use triple double quotes `"""`
- Include Args/Returns sections for public functions
- Keep module-level docstrings concise
- Example:
  ```python
  def get_subscriptions(
      self,
      status: Optional[str] = None,
      limit: int = 100,
  ) -> list[stripe.Subscription]:
      """Fetch subscriptions from Stripe.

      Args:
          status: Filter by subscription status
          limit: Number of subscriptions to fetch (max 100)

      Returns:
          List of Stripe Subscription objects
      """
  ```

**Error Handling:**
- Use specific exceptions, catch broadly only when needed
- Handle API errors gracefully (return False for `test_connection()`)
- Use `typer.BadParameter` for CLI validation errors
- Log errors appropriately, don't silently swallow exceptions

**SQLAlchemy Models:**
- Define `__tablename__` explicitly
- Use `Column(..., index=True)` for query-able fields
- Define composite indexes in `__table_args__`
- Add `__repr__` methods for debugging
- Use `SQLEnum` for enum columns

**Pydantic Models:**
- Inherit from `BaseModel` for API responses
- Use `Optional[X] = None` for optional fields with defaults
- Use `Field(...)` for validation and descriptions in Settings

**CLI Commands:**
- Use `typer.Option()` for optional parameters
- Use type-safe option types with defaults
- Add help text to all options
- Organize commands with section comments (# === ...)

## Project Structure

```
src/mrrmaid/
├── __init__.py          # Version info
├── __main__.py          # Entry point
├── cli.py               # Typer CLI commands
├── api/                 # API clients
│   ├── shopify.py       # Shopify Partner API client
│   └── stripe_client.py # Stripe API client
├── models/              # Database models
│   ├── database.py      # SQLAlchemy setup
│   └── transaction.py   # Transaction models
├── services/            # Business logic
│   ├── metrics.py       # Metrics calculation
│   └── sync.py          # Data synchronization
└── utils/               # Utilities
    └── config.py        # Pydantic settings
```

## Key Dependencies

- **typer**: CLI framework
- **rich**: Terminal formatting/tables
- **sqlalchemy**: ORM for SQLite
- **pydantic/pydantic-settings**: Settings management
- **pandas**: Data analysis
- **requests**: HTTP client
- **stripe**: Stripe API SDK
- **pytest/black/ruff**: Dev tools

## Testing

- Tests should mirror the `src/` structure under `tests/`
- Use fixtures for database setup
- Mock external API calls in tests
- No tests currently exist - create as needed following existing patterns

## Database

- Uses SQLite by default (`mrrmaid.db` or configured via `DATABASE_URL`)
- Models in `src/mrrmaid/models/transaction.py`
- Initialize with `init_db(settings.database_url)`

## Environment Variables

Create a `.env` file:
```
SHOPIFY_PARTNER_TOKEN=xxx
SHOPIFY_ORGANIZATION_ID=xxx
STRIPE_API_KEY=sk_live_xxx
DATABASE_URL=sqlite:///mrrmaid.db
```

## Important Notes

- Never commit API keys or `.env` files
- Use `datetime.utcnow()` for timestamps (aware datetime)
- Normalize all amounts to monthly MRR
- Currency is assumed USD but tracked per-transaction
- External IDs should be unique per source (enforced by DB constraint)
- The project uses `uv` for fast Python package management
