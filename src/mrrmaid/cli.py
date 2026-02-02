"""Command-line interface for MRRmaid."""

from datetime import datetime, timedelta
from typing import Optional

import pandas as pd
import typer
from rich import print as rprint
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.table import Table

from mrrmaid import __version__
from mrrmaid.api.shopify import ShopifyPartnerClient
from mrrmaid.api.stripe_client import StripeClient
from mrrmaid.models.database import init_db
from mrrmaid.models.transaction import TransactionSource
from mrrmaid.services.metrics import MetricsCalculator
from mrrmaid.services.sync import DataSyncService
from mrrmaid.utils.config import Settings, get_settings

app = typer.Typer(
    name="mrrmaid",
    help="RevOps dashboard for tracking MRR, NRR, and Churn from Shopify Partner and Stripe",
    add_completion=False,
)
console = Console()


def version_callback(value: bool) -> None:
    """Print version and exit."""
    if value:
        rprint(f"[bold blue]MRRmaid[/bold blue] v{__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(
        None,
        "--version",
        "-v",
        help="Show version and exit",
        callback=version_callback,
        is_eager=True,
    ),
) -> None:
    """MRRmaid - RevOps dashboard for founders."""
    pass


# ============================================================================
# Configuration Commands
# ============================================================================


@app.command()
def configure(
    shopify_token: Optional[str] = typer.Option(
        None,
        "--shopify-token",
        help="Shopify Partner API access token",
        prompt="Shopify Partner API token (leave empty to skip)",
    ),
    shopify_org_id: Optional[str] = typer.Option(
        None,
        "--shopify-org-id",
        help="Shopify Partner organization ID",
        prompt="Shopify organization ID (leave empty to skip)",
    ),
    stripe_key: Optional[str] = typer.Option(
        None,
        "--stripe-key",
        help="Stripe API secret key",
        prompt="Stripe API secret key (leave empty to skip)",
    ),
) -> None:
    """Configure API credentials."""
    import os
    from pathlib import Path

    env_path = Path.cwd() / ".env"
    lines = []

    if env_path.exists():
        lines = env_path.read_text().splitlines()

    def update_or_add(key: str, value: str) -> None:
        if not value:
            return
        for i, line in enumerate(lines):
            if line.startswith(f"{key}="):
                lines[i] = f"{key}={value}"
                return
        lines.append(f"{key}={value}")

    if shopify_token:
        update_or_add("SHOPIFY_PARTNER_TOKEN", shopify_token)
    if shopify_org_id:
        update_or_add("SHOPIFY_ORGANIZATION_ID", shopify_org_id)
    if stripe_key:
        update_or_add("STRIPE_API_KEY", stripe_key)

    env_path.write_text("\n".join(lines) + "\n")
    rprint("[green]Configuration saved to .env[/green]")


@app.command()
def status() -> None:
    """Check configuration and connection status."""
    settings = get_settings()

    table = Table(title="Configuration Status")
    table.add_column("Service", style="cyan")
    table.add_column("Configured", style="magenta")
    table.add_column("Connection", style="green")

    # Check Shopify
    shopify_configured = settings.shopify_configured
    shopify_connected = False
    if shopify_configured:
        try:
            client = ShopifyPartnerClient(
                access_token=settings.shopify_partner_token,
                organization_id=settings.shopify_organization_id,
                api_version=settings.shopify_api_version,
            )
            shopify_connected = client.test_connection()
        except Exception:
            pass

    table.add_row(
        "Shopify Partner",
        "[green]Yes[/green]" if shopify_configured else "[red]No[/red]",
        "[green]OK[/green]" if shopify_connected else "[red]Failed[/red]" if shopify_configured else "[dim]N/A[/dim]",
    )

    # Check Stripe
    stripe_configured = settings.stripe_configured
    stripe_connected = False
    if stripe_configured:
        try:
            client = StripeClient(api_key=settings.stripe_api_key)
            stripe_connected = client.test_connection()
        except Exception:
            pass

    table.add_row(
        "Stripe",
        "[green]Yes[/green]" if stripe_configured else "[red]No[/red]",
        "[green]OK[/green]" if stripe_connected else "[red]Failed[/red]" if stripe_configured else "[dim]N/A[/dim]",
    )

    console.print(table)

    # Database status
    try:
        init_db(settings.database_url)
        rprint(f"\n[green]Database:[/green] {settings.database_url}")
    except Exception as e:
        rprint(f"\n[red]Database error:[/red] {e}")


# ============================================================================
# Sync Commands
# ============================================================================


@app.command()
def sync(
    source: Optional[str] = typer.Option(
        None,
        "--source",
        "-s",
        help="Source to sync: 'shopify', 'stripe', or 'all'",
    ),
    days: int = typer.Option(
        90,
        "--days",
        "-d",
        help="Number of days of history to sync",
    ),
    all_history: bool = typer.Option(
        False,
        "--all",
        "-a",
        help="Sync entire history (ignores --days)",
    ),
    fresh: bool = typer.Option(
        False,
        "--fresh",
        "-f",
        help="Start fresh (ignore saved progress)",
    ),
) -> None:
    """Sync data from configured sources."""
    from mrrmaid.services.sync import SyncState

    settings = get_settings()
    init_db(settings.database_url)

    shopify_client = None
    stripe_client = None

    if settings.shopify_configured and source in (None, "all", "shopify"):
        shopify_client = ShopifyPartnerClient(
            access_token=settings.shopify_partner_token,
            organization_id=settings.shopify_organization_id,
            api_version=settings.shopify_api_version,
        )

    if settings.stripe_configured and source in (None, "all", "stripe"):
        stripe_client = StripeClient(api_key=settings.stripe_api_key)

    if not shopify_client and not stripe_client:
        rprint("[red]No sources configured. Run 'mrrmaid configure' first.[/red]")
        raise typer.Exit(1)

    # Initialize sync state for resumable syncing
    sync_state = SyncState()

    # Clear state if fresh flag is set
    if fresh:
        sync_state.clear_all()
        rprint("[yellow]Starting fresh sync (cleared saved progress).[/yellow]")

    sync_service = DataSyncService(
        shopify_client=shopify_client,
        stripe_client=stripe_client,
        sync_state=sync_state,
    )

    # Check for resumable state
    resume_info = []
    if not fresh:
        for key in ["shopify_transactions", "stripe_subscriptions", "stripe_invoices"]:
            saved_count = sync_state.get_count(key)
            if saved_count > 0:
                resume_info.append(f"{key.replace('_', ' ')}: {saved_count} records")

    if resume_info:
        rprint("[cyan]Resuming sync from saved progress:[/cyan]")
        for info in resume_info:
            rprint(f"  [cyan]• {info}[/cyan]")

    # Determine start date
    if all_history:
        start_date = None
        rprint("[yellow]Syncing entire history. This may take a while...[/yellow]")
    else:
        start_date = datetime.utcnow() - timedelta(days=days)

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        task = progress.add_task("Syncing data...", total=None)

        def update_progress(count: int) -> None:
            progress.update(task, description=f"Synced {count} records...")

        results = sync_service.sync_all(
            created_at_min=start_date,
            progress_callback=update_progress,
        )

    # Display results
    table = Table(title="Sync Results")
    table.add_column("Source", style="cyan")
    table.add_column("Records", style="green")

    for key, count in results.items():
        table.add_row(key.replace("_", " ").title(), str(count))

    console.print(table)


# ============================================================================
# Dashboard Commands
# ============================================================================


@app.command()
def dashboard(
    source: Optional[str] = typer.Option(
        None,
        "--source",
        "-s",
        help="Filter by source: 'shopify' or 'stripe'",
    ),
) -> None:
    """Show the main RevOps dashboard."""
    settings = get_settings()
    init_db(settings.database_url)

    source_filter = None
    if source == "shopify":
        source_filter = TransactionSource.SHOPIFY
    elif source == "stripe":
        source_filter = TransactionSource.STRIPE

    calculator = MetricsCalculator()
    summary = calculator.calculate_current_mrr(source=source_filter)

    # Main metrics panel
    mrr_display = f"${summary.total_mrr:,.2f}"
    if summary.shopify_mrr > 0 or summary.stripe_mrr > 0:
        breakdown = []
        if summary.shopify_mrr > 0:
            breakdown.append(f"Shopify: ${summary.shopify_mrr:,.2f}")
        if summary.stripe_mrr > 0:
            breakdown.append(f"Stripe: ${summary.stripe_mrr:,.2f}")
        mrr_display += f"\n[dim]({' | '.join(breakdown)})[/dim]"

    console.print(Panel(
        f"[bold green]{mrr_display}[/bold green]",
        title="[bold]Monthly Recurring Revenue (MRR)[/bold]",
        border_style="green",
    ))

    # Subscription stats
    stats_table = Table(show_header=False, box=None)
    stats_table.add_column("Metric", style="cyan")
    stats_table.add_column("Value", style="white")

    stats_table.add_row("Total Subscriptions", str(summary.total_subscriptions))
    stats_table.add_row("Active", f"[green]{summary.active_subscriptions}[/green]")
    stats_table.add_row("Trials", f"[yellow]{summary.trial_subscriptions}[/yellow]")
    stats_table.add_row("Canceled", f"[red]{summary.canceled_subscriptions}[/red]")

    console.print(Panel(stats_table, title="Subscriptions", border_style="blue"))

    # Rates
    if summary.churn_rate is not None or summary.net_revenue_retention is not None:
        rates_table = Table(show_header=False, box=None)
        rates_table.add_column("Metric", style="cyan")
        rates_table.add_column("Value", style="white")

        if summary.churn_rate is not None:
            churn_color = "green" if summary.churn_rate < 5 else "yellow" if summary.churn_rate < 10 else "red"
            rates_table.add_row("Churn Rate", f"[{churn_color}]{summary.churn_rate:.1f}%[/{churn_color}]")

        if summary.net_revenue_retention is not None:
            nrr_color = "green" if summary.net_revenue_retention >= 100 else "yellow" if summary.net_revenue_retention >= 90 else "red"
            rates_table.add_row("Net Revenue Retention", f"[{nrr_color}]{summary.net_revenue_retention:.1f}%[/{nrr_color}]")

        if summary.gross_revenue_retention is not None:
            grr_color = "green" if summary.gross_revenue_retention >= 90 else "yellow" if summary.gross_revenue_retention >= 80 else "red"
            rates_table.add_row("Gross Revenue Retention", f"[{grr_color}]{summary.gross_revenue_retention:.1f}%[/{grr_color}]")

        console.print(Panel(rates_table, title="Retention Metrics", border_style="magenta"))


@app.command()
def mrr(
    source: Optional[str] = typer.Option(
        None,
        "--source",
        "-s",
        help="Filter by source: 'shopify' or 'stripe'",
    ),
    start_date: Optional[str] = typer.Option(
        None,
        "--start",
        help="Start date (YYYY-MM-DD)",
    ),
    end_date: Optional[str] = typer.Option(
        None,
        "--end",
        help="End date (YYYY-MM-DD)",
    ),
    granularity: str = typer.Option(
        "month",
        "--granularity",
        "-g",
        help="Time granularity: 'day', 'week', or 'month'",
    ),
) -> None:
    """View MRR metrics and trends."""
    settings = get_settings()
    init_db(settings.database_url)

    source_filter = None
    if source == "shopify":
        source_filter = TransactionSource.SHOPIFY
    elif source == "stripe":
        source_filter = TransactionSource.STRIPE

    calculator = MetricsCalculator()

    # Parse dates
    if start_date:
        start = datetime.strptime(start_date, "%Y-%m-%d")
    else:
        start = datetime.utcnow() - timedelta(days=365)

    if end_date:
        end = datetime.strptime(end_date, "%Y-%m-%d")
    else:
        end = datetime.utcnow()

    # Get trend data
    trend_df = calculator.get_mrr_trend(
        start_date=start,
        end_date=end,
        granularity=granularity,
        source=source_filter,
    )

    if trend_df.empty:
        rprint("[yellow]No MRR data found for the specified period.[/yellow]")
        return

    # Display trend table
    table = Table(title=f"MRR Trend ({granularity.title()})")
    table.add_column("Period", style="cyan")
    table.add_column("Total MRR", style="green", justify="right")
    table.add_column("Shopify", style="yellow", justify="right")
    table.add_column("Stripe", style="blue", justify="right")

    for _, row in trend_df.iterrows():
        table.add_row(
            row["date"].strftime("%Y-%m-%d"),
            f"${row['total_mrr']:,.2f}",
            f"${row['shopify_mrr']:,.2f}",
            f"${row['stripe_mrr']:,.2f}",
        )

    console.print(table)


@app.command()
def churn(
    source: Optional[str] = typer.Option(
        None,
        "--source",
        "-s",
        help="Filter by source: 'shopify' or 'stripe'",
    ),
    period: str = typer.Option(
        "month",
        "--period",
        "-p",
        help="Analysis period: 'week', 'month', 'quarter', 'year'",
    ),
) -> None:
    """View churn metrics."""
    settings = get_settings()
    init_db(settings.database_url)

    source_filter = None
    if source == "shopify":
        source_filter = TransactionSource.SHOPIFY
    elif source == "stripe":
        source_filter = TransactionSource.STRIPE

    # Calculate period
    now = datetime.utcnow()
    if period == "week":
        start = now - timedelta(weeks=1)
    elif period == "month":
        start = now - timedelta(days=30)
    elif period == "quarter":
        start = now - timedelta(days=90)
    else:  # year
        start = now - timedelta(days=365)

    calculator = MetricsCalculator()
    summary = calculator.calculate_mrr_for_period(
        start_date=start,
        end_date=now,
        source=source_filter,
    )

    # Display churn panel
    console.print(Panel(
        f"[bold]Period:[/bold] {start.strftime('%Y-%m-%d')} to {now.strftime('%Y-%m-%d')}",
        title="Churn Analysis",
        border_style="red",
    ))

    table = Table(show_header=False, box=None)
    table.add_column("Metric", style="cyan", width=30)
    table.add_column("Value", style="white")

    table.add_row("Churned MRR", f"[red]${summary.churned_mrr:,.2f}[/red]")
    table.add_row("Churned Subscriptions", f"[red]{summary.churned_subscriptions}[/red]")

    if summary.churn_rate is not None:
        churn_color = "green" if summary.churn_rate < 5 else "yellow" if summary.churn_rate < 10 else "red"
        table.add_row("Churn Rate", f"[{churn_color}]{summary.churn_rate:.2f}%[/{churn_color}]")

    table.add_row("", "")
    table.add_row("[bold]MRR Movement[/bold]", "")
    table.add_row("New MRR", f"[green]+${summary.new_mrr:,.2f}[/green]")
    table.add_row("Expansion MRR", f"[green]+${summary.expansion_mrr:,.2f}[/green]")
    table.add_row("Contraction MRR", f"[red]-${summary.contraction_mrr:,.2f}[/red]")
    table.add_row("Churned MRR", f"[red]-${summary.churned_mrr:,.2f}[/red]")
    table.add_row("", "")
    net_color = "green" if summary.net_new_mrr >= 0 else "red"
    table.add_row("[bold]Net New MRR[/bold]", f"[{net_color}]${summary.net_new_mrr:,.2f}[/{net_color}]")

    console.print(table)


@app.command()
def nrr(
    period: str = typer.Option(
        "month",
        "--period",
        "-p",
        help="Analysis period: 'month', 'quarter', 'year'",
    ),
    source: Optional[str] = typer.Option(
        None,
        "--source",
        "-s",
        help="Filter by source: 'shopify' or 'stripe'",
    ),
) -> None:
    """View Net Revenue Retention metrics."""
    settings = get_settings()
    init_db(settings.database_url)

    source_filter = None
    if source == "shopify":
        source_filter = TransactionSource.SHOPIFY
    elif source == "stripe":
        source_filter = TransactionSource.STRIPE

    now = datetime.utcnow()
    if period == "month":
        start = now - timedelta(days=30)
    elif period == "quarter":
        start = now - timedelta(days=90)
    else:
        start = now - timedelta(days=365)

    calculator = MetricsCalculator()
    summary = calculator.calculate_mrr_for_period(
        start_date=start,
        end_date=now,
        source=source_filter,
    )

    # NRR Panel
    nrr_value = summary.net_revenue_retention or 0
    nrr_color = "green" if nrr_value >= 100 else "yellow" if nrr_value >= 90 else "red"

    console.print(Panel(
        f"[bold {nrr_color}]{nrr_value:.1f}%[/bold {nrr_color}]",
        title=f"Net Revenue Retention ({period.title()})",
        subtitle=f"{start.strftime('%Y-%m-%d')} to {now.strftime('%Y-%m-%d')}",
        border_style=nrr_color,
    ))

    # Explanation
    table = Table(show_header=False, box=None)
    table.add_column("Component", style="cyan", width=25)
    table.add_column("Value", style="white")

    table.add_row("[bold]Formula Components[/bold]", "")
    table.add_row("Starting MRR", "[dim](base for calculation)[/dim]")
    table.add_row("+ Expansion MRR", f"[green]+${summary.expansion_mrr:,.2f}[/green]")
    table.add_row("- Contraction MRR", f"[yellow]-${summary.contraction_mrr:,.2f}[/yellow]")
    table.add_row("- Churned MRR", f"[red]-${summary.churned_mrr:,.2f}[/red]")
    table.add_row("", "")

    if summary.gross_revenue_retention is not None:
        grr_color = "green" if summary.gross_revenue_retention >= 90 else "yellow" if summary.gross_revenue_retention >= 80 else "red"
        table.add_row("Gross Revenue Retention", f"[{grr_color}]{summary.gross_revenue_retention:.1f}%[/{grr_color}]")

    console.print(table)

    # Interpretation
    if nrr_value >= 120:
        interpretation = "[green]Excellent! Strong expansion revenue exceeds churn.[/green]"
    elif nrr_value >= 100:
        interpretation = "[green]Good! Revenue from existing customers is growing.[/green]"
    elif nrr_value >= 90:
        interpretation = "[yellow]Moderate churn. Consider retention strategies.[/yellow]"
    else:
        interpretation = "[red]High churn. Revenue from existing customers is declining.[/red]"

    console.print(f"\n{interpretation}")


@app.command()
def cohort(
    metric: str = typer.Option(
        "revenue",
        "--metric",
        "-m",
        help="Metric type: 'revenue' or 'customers'",
    ),
    source: Optional[str] = typer.Option(
        None,
        "--source",
        "-s",
        help="Filter by source: 'shopify' or 'stripe'",
    ),
    periods: int = typer.Option(
        12,
        "--periods",
        "-p",
        help="Number of months to track",
    ),
    export: Optional[str] = typer.Option(
        None,
        "--export",
        "-e",
        help="Export to CSV file",
    ),
) -> None:
    """View cohort-based retention analysis."""
    settings = get_settings()
    init_db(settings.database_url)

    source_filter = None
    if source == "shopify":
        source_filter = TransactionSource.SHOPIFY
    elif source == "stripe":
        source_filter = TransactionSource.STRIPE

    calculator = MetricsCalculator()
    df = calculator.calculate_cohort_analysis(
        metric=metric,
        source=source_filter,
        num_periods=periods,
    )

    if df.empty:
        rprint("[yellow]No cohort data available. Run 'mrrmaid sync' first.[/yellow]")
        raise typer.Exit(1)

    # Export if requested
    if export:
        df.to_csv(export)
        rprint(f"[green]Exported cohort analysis to {export}[/green]")
        return

    # Display cohort table
    metric_label = "Revenue Retention" if metric == "revenue" else "Customer Retention"
    source_label = f" ({source.title()})" if source else ""

    console.print(Panel(
        f"[bold]Cohort Analysis - {metric_label}{source_label}[/bold]\n"
        f"[dim]Shows % retention relative to first month (M0 = 100%)[/dim]",
        border_style="blue",
    ))

    # Build the table
    table = Table(show_header=True, header_style="bold cyan")
    table.add_column("Cohort", style="white", width=10)
    table.add_column("Cust", justify="right", width=6)
    table.add_column("Revenue", justify="right", width=10)

    # Add period columns
    period_cols = [c for c in df.columns if c.startswith("M")]
    for col in period_cols:
        table.add_column(col, justify="right", width=7)

    # Add rows (most recent cohorts first)
    for cohort_name in reversed(df.index.tolist()):
        row = df.loc[cohort_name]
        values = [cohort_name]
        values.append(f"[dim]{int(row['Customers'])}[/dim]")
        values.append(f"[dim]${row['Revenue']:,.0f}[/dim]")

        for col in period_cols:
            val = row[col]
            if pd.isna(val):
                values.append("[dim]-[/dim]")
            else:
                # Color code retention percentages
                if val >= 90:
                    values.append(f"[green]{val:.0f}%[/green]")
                elif val >= 70:
                    values.append(f"[yellow]{val:.0f}%[/yellow]")
                else:
                    values.append(f"[red]{val:.0f}%[/red]")

        table.add_row(*values)

    console.print(table)

    # Summary stats
    summary = calculator.get_cohort_summary(source=source_filter)
    if summary:
        avg_retention = summary.get("average_retention_by_period", {})
        if "M1" in avg_retention:
            console.print(f"\n[dim]Average M1 Retention: {avg_retention['M1']:.1f}%[/dim]")
        if "M3" in avg_retention:
            console.print(f"[dim]Average M3 Retention: {avg_retention['M3']:.1f}%[/dim]")
        if "M6" in avg_retention:
            console.print(f"[dim]Average M6 Retention: {avg_retention['M6']:.1f}%[/dim]")


# ============================================================================
# Data Commands
# ============================================================================


@app.command()
def transactions(
    source: Optional[str] = typer.Option(
        None,
        "--source",
        "-s",
        help="Filter by source: 'shopify' or 'stripe'",
    ),
    start_date: Optional[str] = typer.Option(
        None,
        "--start",
        help="Start date (YYYY-MM-DD)",
    ),
    end_date: Optional[str] = typer.Option(
        None,
        "--end",
        help="End date (YYYY-MM-DD)",
    ),
    limit: int = typer.Option(
        20,
        "--limit",
        "-n",
        help="Number of transactions to show",
    ),
    export: Optional[str] = typer.Option(
        None,
        "--export",
        "-e",
        help="Export to CSV file",
    ),
) -> None:
    """View transaction history."""
    settings = get_settings()
    init_db(settings.database_url)

    source_filter = None
    if source == "shopify":
        source_filter = TransactionSource.SHOPIFY
    elif source == "stripe":
        source_filter = TransactionSource.STRIPE

    start = datetime.strptime(start_date, "%Y-%m-%d") if start_date else None
    end = datetime.strptime(end_date, "%Y-%m-%d") if end_date else None

    calculator = MetricsCalculator()
    df = calculator.get_transactions_summary(
        start_date=start,
        end_date=end,
        source=source_filter,
    )

    if df.empty:
        rprint("[yellow]No transactions found.[/yellow]")
        return

    if export:
        df.to_csv(export, index=False)
        rprint(f"[green]Exported {len(df)} transactions to {export}[/green]")
        return

    # Display table
    table = Table(title=f"Transactions (showing {min(limit, len(df))} of {len(df)})")
    table.add_column("Date", style="cyan")
    table.add_column("Source", style="magenta")
    table.add_column("Type", style="yellow")
    table.add_column("Amount", style="green", justify="right")
    table.add_column("Customer/Shop", style="white")

    for _, row in df.head(limit).iterrows():
        customer = row.get("shop_domain") or row.get("customer_id") or "-"
        table.add_row(
            row["created_at"].strftime("%Y-%m-%d") if row["created_at"] else "-",
            row["source"] or "-",
            row["type"] or "-",
            f"${row['net_amount']:,.2f}" if row["net_amount"] else "-",
            str(customer)[:30],
        )

    console.print(table)


@app.command()
def subscriptions(
    source: Optional[str] = typer.Option(
        None,
        "--source",
        "-s",
        help="Filter by source: 'shopify' or 'stripe'",
    ),
    status_filter: Optional[str] = typer.Option(
        None,
        "--status",
        help="Filter by status: 'active', 'trialing', 'canceled', etc.",
    ),
    export: Optional[str] = typer.Option(
        None,
        "--export",
        "-e",
        help="Export to CSV file",
    ),
) -> None:
    """View subscriptions."""
    settings = get_settings()
    init_db(settings.database_url)

    source_filter = None
    if source == "shopify":
        source_filter = TransactionSource.SHOPIFY
    elif source == "stripe":
        source_filter = TransactionSource.STRIPE

    calculator = MetricsCalculator()
    df = calculator.get_subscriptions_summary(
        source=source_filter,
        status=status_filter,
    )

    if df.empty:
        rprint("[yellow]No subscriptions found.[/yellow]")
        return

    if export:
        df.to_csv(export, index=False)
        rprint(f"[green]Exported {len(df)} subscriptions to {export}[/green]")
        return

    # Display table
    table = Table(title=f"Subscriptions ({len(df)} total)")
    table.add_column("ID", style="dim")
    table.add_column("Source", style="magenta")
    table.add_column("Status", style="cyan")
    table.add_column("MRR", style="green", justify="right")
    table.add_column("Customer", style="white")
    table.add_column("Created", style="dim")

    for _, row in df.iterrows():
        status_color = {
            "active": "green",
            "trialing": "yellow",
            "canceled": "red",
            "past_due": "red",
        }.get(row["status"], "white")

        customer = row.get("customer_email") or row.get("shop_domain") or row.get("customer_id") or "-"

        table.add_row(
            str(row["external_id"])[:20] + "..." if len(str(row["external_id"])) > 20 else str(row["external_id"]),
            row["source"] or "-",
            f"[{status_color}]{row['status']}[/{status_color}]",
            f"${row['monthly_amount']:,.2f}" if row["monthly_amount"] else "-",
            str(customer)[:25],
            row["created_at"].strftime("%Y-%m-%d") if row["created_at"] else "-",
        )

    console.print(table)


# ============================================================================
# Snapshot Commands
# ============================================================================


@app.command()
def snapshot(
    save: bool = typer.Option(
        False,
        "--save",
        help="Save current metrics as a snapshot",
    ),
) -> None:
    """View or save MRR snapshots."""
    settings = get_settings()
    init_db(settings.database_url)

    calculator = MetricsCalculator()

    if save:
        summary = calculator.calculate_current_mrr()
        calculator.save_snapshot(summary)
        rprint("[green]Snapshot saved successfully![/green]")
        return

    # Display historical snapshots
    df = calculator.get_snapshots()

    if df.empty:
        rprint("[yellow]No snapshots found. Use --save to create one.[/yellow]")
        return

    table = Table(title="MRR Snapshots")
    table.add_column("Date", style="cyan")
    table.add_column("Source", style="magenta")
    table.add_column("Total MRR", style="green", justify="right")
    table.add_column("Active", style="blue", justify="right")
    table.add_column("Churn %", style="yellow", justify="right")
    table.add_column("NRR %", style="green", justify="right")

    for _, row in df.iterrows():
        table.add_row(
            row["date"].strftime("%Y-%m-%d") if row["date"] else "-",
            row["source"],
            f"${row['total_mrr']:,.2f}",
            str(row.get("active_subscriptions", "-")),
            f"{row['churn_rate']:.1f}%" if row.get("churn_rate") else "-",
            f"{row['nrr']:.1f}%" if row.get("nrr") else "-",
        )

    console.print(table)


if __name__ == "__main__":
    app()
