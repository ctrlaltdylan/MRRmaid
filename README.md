# MRRmaid 🧜‍♀️

Track MRR, NRR, churn, and customer concentration from Shopify Partner and Stripe.

## Why MRRmaid?

**Local-first.** Your revenue data stays on your machine in SQLite. No third-party analytics service storing your financials.

**CLI-driven.** No browser, no dashboards to click through. Just run `mrrmaid dashboard` and get your numbers.

**Agent-friendly.** Built for automation and AI agents. Structured output, CSV exports, and scriptable commands.

**Free.** No SaaS fees, no tiers, no "contact sales." MIT licensed, run it forever.

```
$ mrrmaid dashboard

╭────────────────────── Monthly Recurring Revenue (MRR) ───────────────────────╮
│ $72,793.48                                                                   │
│ (Shopify: $64,384.18 | Stripe: $8,409.30)                                    │
╰──────────────────────────────────────────────────────────────────────────────╯
```

## Features

- **Multi-source** - Combines Shopify Partner + Stripe in one view
- **Cohort analysis** - Track retention by customer vintage
- **Concentration risk** - Identify over-reliance on top customers
- **Resumable sync** - Full history backfills that survive interruptions
- **Export everything** - CSV exports for all data

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

## Command Reference

### `mrrmaid dashboard`

Overview of MRR and subscriptions.

```bash
$ mrrmaid dashboard
╭────────────────────── Monthly Recurring Revenue (MRR) ───────────────────────╮
│ $72,793.48                                                                   │
│ (Shopify: $64,384.18 | Stripe: $8,409.30)                                    │
╰──────────────────────────────────────────────────────────────────────────────╯
╭─────────────────────────────── Subscriptions ────────────────────────────────╮
│  Total Subscriptions  308                                                    │
│  Active               308                                                    │
│  Trials               0                                                      │
│  Canceled             0                                                      │
╰──────────────────────────────────────────────────────────────────────────────╯

$ mrrmaid dashboard --source shopify   # Filter by source
```

### `mrrmaid mrr`

MRR breakdown and movement.

```bash
$ mrrmaid mrr
╭──────────────────────────── MRR Breakdown ─────────────────────────────╮
│  Shopify MRR    $64,384.18                                             │
│  Stripe MRR     $8,409.30                                              │
│  Total MRR      $72,793.48                                             │
╰────────────────────────────────────────────────────────────────────────╯
                        MRR Movement
┏━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ Component         ┃ Amount                                 ┃
┡━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ New MRR           │ +$8,234.00                             │
│ Expansion MRR     │ +$2,891.50                             │
│ Contraction MRR   │ -$1,205.00                             │
│ Churned MRR       │ -$3,420.00                             │
│ Net New MRR       │ +$6,500.50                             │
└───────────────────┴────────────────────────────────────────┘

$ mrrmaid mrr --granularity week       # Weekly breakdown
$ mrrmaid mrr --start 2025-01-01       # Custom date range
```

### `mrrmaid nrr`

Net Revenue Retention from existing customers.

```bash
$ mrrmaid nrr
╭─────────────────────── Net Revenue Retention (Month) ────────────────────────╮
│ 110.9%                                                                       │
╰────────────────────────── 2025-01-03 to 2025-02-02 ──────────────────────────╯
 Formula Components
 Starting MRR               (base for calculation)
 + Expansion MRR            +$17,091.90
 - Contraction MRR          -$5,904.86
 - Churned MRR              -$4,360.58

 Gross Revenue Retention    83.6%

Good! Revenue from existing customers is growing.

$ mrrmaid nrr --period quarter         # Quarterly NRR
$ mrrmaid nrr --period year            # Annual NRR
```

### `mrrmaid cohort`

Cohort-based retention analysis.

```bash
$ mrrmaid cohort
╭──────────────────────────────────────────────────────────────────────────────╮
│ Cohort Analysis - Revenue Retention                                          │
│ Shows % retention relative to first month (M0 = 100%)                        │
╰──────────────────────────────────────────────────────────────────────────────╯
┏━━━━━━━━━━┳━━━━━━┳━━━━━━━━━━┳━━━━━━━┳━━━━━━━┳━━━━━━━┳━━━━━━━━┓
┃ Cohort   ┃ Cust ┃  Revenue ┃    M0 ┃    M1 ┃    M2 ┃     M3 ┃
┡━━━━━━━━━━╇━━━━━━╇━━━━━━━━━━╇━━━━━━━╇━━━━━━━╇━━━━━━━╇━━━━━━━━┩
│ 2025-11  │  263 │ $159,021 │  100% │  124% │  135% │     9% │
│ 2025-10  │   45 │  $12,016 │  100% │  102% │   89% │    85% │
│ 2025-09  │   38 │   $8,752 │  100% │   95% │   90% │    88% │
│ 2025-08  │   52 │  $15,737 │  100% │   89% │   87% │    80% │
└──────────┴──────┴──────────┴───────┴───────┴───────┴────────┘

Average M1 Retention: 78.6%
Average M3 Retention: 53.8%

$ mrrmaid cohort --metric customers    # Customer count retention
$ mrrmaid cohort --periods 12          # Track 12 months
$ mrrmaid cohort --export cohorts.csv  # Export to CSV
```

### `mrrmaid customers`

Customer concentration and revenue distribution.

```bash
$ mrrmaid customers
╭──────────────────────────────────────────────────────────────────────────────╮
│ Customer Revenue Analysis - Month                                            │
│ Total Revenue: $73,289.99 from 357 customers                                 │
╰──────────────────────────────────────────────────────────────────────────────╯
⚠ High concentration risk: Top 10% of customers = 57.8% of revenue
Top 20% = 70.5% of revenue

┏━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━┳━━━━━━━┳━━━━━━━━━┳━━━━━━┓
┃   # ┃ Customer                       ┃   Revenue ┃ % Rev ┃ Cumul % ┃ Txns ┃
┡━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━╇━━━━━━━╇━━━━━━━━━╇━━━━━━┩
│   1 │ acme-store.myshopify.com       │ $12,912.19│ 17.6% │   17.6% │ 8839 │
│   2 │ widgets-inc.myshopify.com      │  $2,858.77│  3.9% │   21.5% │ 1142 │
│   3 │ example-shop.myshopify.com     │  $2,260.49│  3.1% │   24.6% │ 1493 │
└─────┴────────────────────────────────┴───────────┴───────┴─────────┴──────┘

$ mrrmaid customers --period all       # All-time analysis
$ mrrmaid customers --period quarter   # Last 90 days
$ mrrmaid customers --limit 50         # Show more customers
$ mrrmaid customers --export cust.csv  # Export to CSV
```

### `mrrmaid churn`

Churn rate analysis.

```bash
$ mrrmaid churn
╭───────────────────────── Churn Rate (Month) ─────────────────────────╮
│ 5.2%                                                                 │
╰──────────────────────────────────────────────────────────────────────╯
 Churned MRR         $3,420.00
 Churned Customers   12

$ mrrmaid churn --period quarter       # Quarterly churn
$ mrrmaid churn --period year          # Annual churn
```

### `mrrmaid sync`

Sync data from Shopify Partner and Stripe APIs.

```bash
$ mrrmaid sync
⠋ Synced 1200 records...
           Sync Results
┏━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━┓
┃ Source               ┃ Records ┃
┡━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━┩
│ Shopify Transactions │ 63598   │
│ Stripe Subscriptions │ 174     │
│ Stripe Invoices      │ 6389    │
└──────────────────────┴─────────┘

$ mrrmaid sync --days 365              # Last year
$ mrrmaid sync --all                   # Full history (resumable)
$ mrrmaid sync --all --fresh           # Start over from scratch
$ mrrmaid sync --source shopify        # Single source only
```

Syncs are **resumable** - if interrupted, run again to continue where you left off.

### `mrrmaid transactions`

View and export transaction history.

```bash
$ mrrmaid transactions
                    Recent Transactions (20)
┏━━━━━━━━━━━━┳━━━━━━━━━┳━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ Date       ┃ Source  ┃   Amount ┃ Customer                       ┃
┡━━━━━━━━━━━━╇━━━━━━━━━╇━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ 2025-02-01 │ shopify │   $45.00 │ acme-store.myshopify.com       │
│ 2025-02-01 │ stripe  │  $299.00 │ cus_abc123                     │
│ 2025-01-31 │ shopify │   $12.50 │ widgets-inc.myshopify.com      │
└────────────┴─────────┴──────────┴────────────────────────────────┘

$ mrrmaid transactions --limit 100     # Show more
$ mrrmaid transactions --source stripe # Filter by source
$ mrrmaid transactions --start 2025-01-01 --end 2025-01-31
$ mrrmaid transactions --export tx.csv # Export to CSV
```

### `mrrmaid subscriptions`

View active subscriptions.

```bash
$ mrrmaid subscriptions
                            Subscriptions (174 total)
┏━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━┳━━━━━━━━┳━━━━━━━━━┳━━━━━━━━━━━━┓
┃ ID                 ┃ Source ┃ Status ┃     MRR ┃ Created    ┃
┡━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━╇━━━━━━━━╇━━━━━━━━━╇━━━━━━━━━━━━┩
│ sub_1abc123...     │ stripe │ active │ $299.00 │ 2025-01-15 │
│ sub_2def456...     │ stripe │ active │  $99.00 │ 2025-01-10 │
│ sub_3ghi789...     │ stripe │ trial  │   $0.00 │ 2025-02-01 │
└────────────────────┴────────┴────────┴─────────┴────────────┘

$ mrrmaid subscriptions --status active
$ mrrmaid subscriptions --status canceled
$ mrrmaid subscriptions --export subs.csv
```

### `mrrmaid snapshot`

Save and view historical metric snapshots.

```bash
$ mrrmaid snapshot --save
Snapshot saved for 2025-02-01

$ mrrmaid snapshot
                     MRR Snapshots
┏━━━━━━━━━━━━┳━━━━━━━━━━━━┳━━━━━━━━━━━━━┳━━━━━━━━┓
┃ Date       ┃  Total MRR ┃ Active Subs ┃    NRR ┃
┡━━━━━━━━━━━━╇━━━━━━━━━━━━╇━━━━━━━━━━━━━╇━━━━━━━━┩
│ 2025-02-01 │ $72,793.48 │         308 │ 110.9% │
│ 2025-01-01 │ $68,421.00 │         295 │ 108.2% │
│ 2024-12-01 │ $65,102.33 │         280 │ 105.4% │
└────────────┴────────────┴─────────────┴────────┘
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
