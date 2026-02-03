# MRRmaid 🧜‍♀️

A CLI dashboard for indie hackers and founders to track MRR, NRR, churn, and customer concentration from Shopify Partner and Stripe.

```
╭────────────────────── Monthly Recurring Revenue (MRR) ───────────────────────╮
│ $72,793.48                                                                   │
│ (Shopify: $64,384.18 | Stripe: $8,409.30)                                    │
╰──────────────────────────────────────────────────────────────────────────────╯
```

## Why MRRmaid?

- **No SaaS fees** - Your data stays local in SQLite
- **Multi-source** - Combines Shopify Partner + Stripe in one view
- **Cohort analysis** - Track retention by customer vintage
- **Concentration risk** - Identify over-reliance on top customers
- **Resumable sync** - Full history backfills that survive interruptions

## Installation

```bash
# With pip
pip install mrrmaid

# Or clone and install locally
git clone https://github.com/ctrlaltdylan/MRRmaid.git
cd MRRmaid
pip install -e .
```

## Quick Start

```bash
# 1. Configure your API credentials
mrrmaid configure

# 2. Sync your data
mrrmaid sync

# 3. View your dashboard
mrrmaid dashboard
```

## Configuration

### Interactive Setup

```bash
mrrmaid configure
```

### Environment Variables

Create a `.env` file:

```bash
# Shopify Partner API
SHOPIFY_PARTNER_ACCESS_TOKEN=your_token_here
SHOPIFY_ORGANIZATION_ID=your_org_id

# Stripe API
STRIPE_API_KEY=sk_live_xxx
```

### Getting API Credentials

**Shopify Partner API:**
1. Go to [Shopify Partners](https://partners.shopify.com) → Settings → Partner API clients
2. Create a client with "View financials" permission
3. Copy the access token and organization ID (from URL: `partners.shopify.com/ORG_ID/...`)

**Stripe API:**
1. Go to [Stripe Dashboard](https://dashboard.stripe.com/apikeys)
2. Copy your Secret Key (`sk_live_...` or `sk_test_...`)

## Commands

### Dashboard

```bash
mrrmaid dashboard                    # Full overview
mrrmaid dashboard --source shopify   # Filter by source
```

### MRR Trends

```bash
mrrmaid mrr                          # Monthly MRR trend
mrrmaid mrr --granularity week       # Weekly breakdown
mrrmaid mrr --start 2025-01-01       # Custom date range
```

### Net Revenue Retention

```bash
mrrmaid nrr                          # Monthly NRR
mrrmaid nrr --period quarter         # Quarterly comparison
```

Calculates NRR using cohort analysis:
```
NRR = (Starting MRR + Expansion - Contraction - Churn) / Starting MRR × 100
```

### Cohort Analysis

Track retention by customer vintage:

```bash
mrrmaid cohort                       # Revenue retention by cohort
mrrmaid cohort --metric customers    # Customer count retention
mrrmaid cohort --periods 12          # Track 12 months
mrrmaid cohort --export cohorts.csv  # Export for spreadsheets
```

Output:
```
┏━━━━━━━━━━┳━━━━━━┳━━━━━━━━━━┳━━━━━━━┳━━━━━━━┳━━━━━━━┳━━━━━━━━┓
┃ Cohort   ┃ Cust ┃  Revenue ┃    M0 ┃    M1 ┃    M2 ┃     M3 ┃
┡━━━━━━━━━━╇━━━━━━╇━━━━━━━━━━╇━━━━━━━╇━━━━━━━╇━━━━━━━╇━━━━━━━━┩
│ 2025-11  │  263 │ $159,021 │  100% │  124% │  135% │     9% │
│ 2025-10  │    3 │   $1,016 │  100% │  102% │  189% │     0% │
│ 2025-09  │    2 │   $1,752 │  100% │   65% │   70% │    88% │
```

### Customer Concentration

Identify revenue concentration risk:

```bash
mrrmaid customers                    # Top customers by revenue
mrrmaid customers --period all       # All-time analysis
mrrmaid customers --limit 50         # Show more customers
mrrmaid customers --export cust.csv  # Export to CSV
```

Output:
```
⚠ High concentration risk: Top 10% of customers = 57.8% of revenue
Top 20% = 70.5% of revenue

┏━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━┳━━━━━━━┳━━━━━━━━━┓
┃   # ┃ Customer                   ┃   Revenue ┃ % Rev ┃ Cumul % ┃
┡━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━╇━━━━━━━╇━━━━━━━━━┩
│   1 │ bigcustomer.myshopify.com  │ $12,912   │ 17.6% │   17.6% │
│   2 │ another.myshopify.com      │  $2,858   │  3.9% │   21.5% │
```

### Churn

```bash
mrrmaid churn                        # Monthly churn rate
mrrmaid churn --period quarter       # Quarterly churn
```

### Sync Data

```bash
mrrmaid sync                         # Last 90 days (default)
mrrmaid sync --days 365              # Last year
mrrmaid sync --all                   # Full history (resumable)
mrrmaid sync --all --fresh           # Full history, start over
mrrmaid sync --source shopify        # Single source only
```

Syncs are **resumable** - if interrupted, just run again to continue from where you left off.

### Transactions & Subscriptions

```bash
mrrmaid transactions                 # Recent transactions
mrrmaid transactions --export tx.csv # Export to CSV
mrrmaid subscriptions                # View subscriptions
mrrmaid subscriptions --status active
```

### Snapshots

```bash
mrrmaid snapshot --save              # Save current metrics
mrrmaid snapshot                     # View historical snapshots
```

## Metrics Explained

| Metric | Formula | Good | Warning |
|--------|---------|------|---------|
| **NRR** | (Start + Expansion - Contraction - Churn) / Start | >100% | <90% |
| **GRR** | (Start - Contraction - Churn) / Start | >90% | <80% |
| **Churn** | Churned MRR / Starting MRR | <5% | >10% |
| **Concentration** | Top 10% customer revenue / Total | <30% | >50% |

## Data Sources

### Shopify Partner API

- App subscription charges
- App usage charges (metered billing)
- One-time app charges
- Service revenue
- Referral commissions

### Stripe API

- Subscriptions (with line items)
- Invoices (for historical MRR)
- Metered billing usage

## Architecture

```
src/mrrmaid/
├── api/
│   ├── shopify.py         # Shopify Partner GraphQL client
│   └── stripe_client.py   # Stripe REST client
├── models/
│   ├── database.py        # SQLite via SQLAlchemy
│   └── transaction.py     # Data models
├── services/
│   ├── metrics.py         # MRR, NRR, Churn, Cohort calculations
│   └── sync.py            # Resumable data sync
├── utils/
│   └── config.py          # Configuration management
└── cli.py                 # Typer CLI
```

Data is stored locally in `mrrmaid.db` (SQLite).

## Development

```bash
# Install with dev dependencies
pip install -e ".[dev]"

# Run tests
pytest

# Format
black src/

# Lint
ruff src/
```

## Contributing

Contributions welcome! Please open an issue first to discuss what you'd like to change.

## License

MIT

---

Built for indie hackers who want to understand their revenue without paying for expensive analytics tools.
