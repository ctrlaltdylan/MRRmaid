# MRRmaid

RevOps dashboard for founders to track key subscription metrics from Shopify Partner and Stripe.

## Features

- **MRR (Monthly Recurring Revenue)**: Track your recurring revenue from both Shopify apps and Stripe subscriptions
- **NRR (Net Revenue Retention)**: Measure expansion and contraction in your customer base
- **Churn**: Monitor customer and revenue churn rates
- **Multi-source**: Combine data from Shopify Partner API and Stripe API
- **Filtering**: Filter by date range, source, and more
- **Export**: Export data to CSV for further analysis

## Installation

```bash
# Clone the repository
git clone https://github.com/your-org/MRRmaid.git
cd MRRmaid

# Install dependencies
pip install -e .

# Or install from requirements
pip install -r requirements.txt
```

## Configuration

### Option 1: Interactive Configuration

```bash
mrrmaid configure
```

### Option 2: Environment Variables

Copy `.env.example` to `.env` and fill in your credentials:

```bash
cp .env.example .env
```

### Shopify Partner API

1. Go to [Shopify Partners Dashboard](https://partners.shopify.com)
2. Navigate to **Settings** → **Partner API clients**
3. Create a new API client with "View financials" permission
4. Copy the access token and your organization ID (from the URL)

### Stripe API

1. Go to [Stripe Dashboard](https://dashboard.stripe.com/apikeys)
2. Copy your Secret Key (starts with `sk_live_` or `sk_test_`)

## Usage

### Check Configuration Status

```bash
mrrmaid status
```

### Sync Data

```bash
# Sync all sources (last 90 days by default)
mrrmaid sync

# Sync specific source
mrrmaid sync --source shopify
mrrmaid sync --source stripe

# Sync more history
mrrmaid sync --days 365
```

### View Dashboard

```bash
# Full dashboard
mrrmaid dashboard

# Filter by source
mrrmaid dashboard --source stripe
```

### View MRR Trends

```bash
# Monthly MRR trend for the last year
mrrmaid mrr

# Custom date range and granularity
mrrmaid mrr --start 2025-01-01 --end 2025-12-31 --granularity month

# Weekly granularity
mrrmaid mrr --granularity week
```

### View Churn Metrics

```bash
# Last month's churn
mrrmaid churn

# Quarterly churn
mrrmaid churn --period quarter

# Yearly churn
mrrmaid churn --period year
```

### View NRR (Net Revenue Retention)

```bash
# Monthly NRR
mrrmaid nrr

# Quarterly NRR
mrrmaid nrr --period quarter
```

### View Transactions

```bash
# Recent transactions
mrrmaid transactions

# Filter by source and date
mrrmaid transactions --source stripe --start 2025-01-01

# Export to CSV
mrrmaid transactions --export transactions.csv
```

### View Subscriptions

```bash
# All subscriptions
mrrmaid subscriptions

# Filter by status
mrrmaid subscriptions --status active

# Export to CSV
mrrmaid subscriptions --export subscriptions.csv
```

### Save Snapshots

```bash
# Save current metrics as a snapshot
mrrmaid snapshot --save

# View historical snapshots
mrrmaid snapshot
```

## Metrics Explained

### MRR (Monthly Recurring Revenue)

The sum of all recurring revenue normalized to a monthly amount. Annual subscriptions are divided by 12, weekly subscriptions are multiplied by ~4.33.

### NRR (Net Revenue Retention)

```
NRR = (Starting MRR + Expansion - Contraction - Churn) / Starting MRR × 100
```

- **> 100%**: Revenue from existing customers is growing (excellent)
- **90-100%**: Slight revenue decline from existing customers
- **< 90%**: Significant churn problem

### GRR (Gross Revenue Retention)

```
GRR = (Starting MRR - Contraction - Churn) / Starting MRR × 100
```

Unlike NRR, GRR doesn't count expansion revenue. It measures how much revenue you retain without upsells.

### Churn Rate

```
Churn Rate = Churned MRR / Starting MRR × 100
```

Healthy churn rates vary by industry, but generally:
- **< 5%**: Good
- **5-10%**: Moderate
- **> 10%**: High, needs attention

## Data Sources

### Shopify Partner API

Fetches:
- App subscription sales
- App usage sales
- One-time app sales
- Service sales
- Referral transactions

**Note**: Shopify Partner API transaction data is for analytics only and should not be used for financial reporting.

### Stripe API

Fetches:
- Subscriptions (with full item details)
- Invoices (for historical MRR calculation)
- Customer information

## Architecture

```
src/mrrmaid/
├── api/
│   ├── shopify.py      # Shopify Partner GraphQL client
│   └── stripe_client.py # Stripe REST API client
├── models/
│   ├── database.py     # SQLAlchemy configuration
│   └── transaction.py  # Data models
├── services/
│   ├── metrics.py      # MRR, NRR, Churn calculations
│   └── sync.py         # Data synchronization
├── utils/
│   └── config.py       # Configuration management
└── cli.py              # Command-line interface
```

## Development

```bash
# Install dev dependencies
pip install -e ".[dev]"

# Run tests
pytest

# Format code
black src/

# Lint
ruff src/
```

## License

MIT
