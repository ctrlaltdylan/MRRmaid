"""Command-line interface for MRRmaid."""

import calendar
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
        "[green]OK[/green]"
        if shopify_connected
        else "[red]Failed[/red]"
        if shopify_configured
        else "[dim]N/A[/dim]",
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
        "[green]OK[/green]"
        if stripe_connected
        else "[red]Failed[/red]"
        if stripe_configured
        else "[dim]N/A[/dim]",
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
    days: Optional[int] = typer.Option(
        None,
        "--days",
        "-d",
        help="Sync specific number of days (overrides incremental)",
    ),
    all_history: bool = typer.Option(
        False,
        "--all",
        "-a",
        help="Sync entire history",
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
    from mrrmaid.models.transaction import Transaction
    from mrrmaid.models.database import get_session
    from sqlalchemy import func

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
    # Priority: --all > --days > incremental (default)
    if all_history:
        start_date = None
        rprint("[yellow]Syncing entire history. This may take a while...[/yellow]")
    elif days is not None:
        start_date = datetime.utcnow() - timedelta(days=days)
        rprint(f"[cyan]Syncing last {days} days[/cyan]")
    else:
        # Default: incremental sync from last transaction
        with get_session() as session:
            latest = session.query(func.max(Transaction.created_at)).scalar()
            if latest:
                start_date = latest
                rprint(f"[cyan]Incremental sync from {latest.strftime('%Y-%m-%d %H:%M')}[/cyan]")
            else:
                # No existing data, sync last 90 days as initial sync
                start_date = datetime.utcnow() - timedelta(days=90)
                rprint("[yellow]No existing data, syncing last 90 days[/yellow]")

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

    # Main metrics panel - MRR and ARR
    arr = summary.total_mrr * 12
    mrr_display = f"MRR: ${summary.total_mrr:,.2f}  |  ARR: ${arr:,.2f}"
    if summary.shopify_mrr > 0 or summary.stripe_mrr > 0:
        breakdown = []
        if summary.shopify_mrr > 0:
            breakdown.append(f"Shopify: ${summary.shopify_mrr:,.2f}")
        if summary.stripe_mrr > 0:
            breakdown.append(f"Stripe: ${summary.stripe_mrr:,.2f}")
        mrr_display += f"\n[dim]({' | '.join(breakdown)})[/dim]"

    console.print(
        Panel(
            f"[bold green]{mrr_display}[/bold green]",
            title="[bold]Recurring Revenue[/bold]",
            border_style="green",
        )
    )

    # TTM (Trailing Twelve Months) Revenue
    ttm_start = datetime.utcnow() - timedelta(days=365)
    ttm_df = calculator.get_transactions_summary(start_date=ttm_start, source=source_filter)
    ttm_revenue = ttm_df["net_amount"].sum() if not ttm_df.empty else 0

    console.print(
        Panel(
            f"[bold cyan]${ttm_revenue:,.2f}[/bold cyan]",
            title="[bold]TTM Revenue (Last 12 Months Actual)[/bold]",
            border_style="cyan",
        )
    )

    # Subscription stats
    stats_table = Table(show_header=False, box=None)
    stats_table.add_column("Metric", style="cyan")
    stats_table.add_column("Value", style="white")

    stats_table.add_row("Total Subscriptions", str(summary.total_subscriptions))
    stats_table.add_row("Active", f"[green]{summary.active_subscriptions}[/green]")
    stats_table.add_row("Trials", f"[yellow]{summary.trial_subscriptions}[/yellow]")
    stats_table.add_row("Canceled", f"[red]{summary.canceled_subscriptions}[/red]")

    console.print(Panel(stats_table, title="Subscriptions", border_style="blue"))

    # Calculate LTV metrics for the dashboard
    ltv_summary = calculator.calculate_ltv_metrics(period="month", source=source_filter)

    # Rates and LTV
    rates_table = Table(show_header=False, box=None)
    rates_table.add_column("Metric", style="cyan")
    rates_table.add_column("Value", style="white")

    # Add ARPU
    if ltv_summary.arpu is not None:
        rates_table.add_row("ARPU", f"${ltv_summary.arpu:,.2f}")

    # Add LTV
    if ltv_summary.ltv is not None:
        ltv_color = (
            "green" if ltv_summary.ltv >= 1000 else "yellow" if ltv_summary.ltv >= 500 else "red"
        )
        rates_table.add_row("LTV", f"[{ltv_color}]${ltv_summary.ltv:,.2f}[/{ltv_color}]")
    elif ltv_summary.arpu is not None:
        rates_table.add_row("LTV", "[yellow]N/A (0% churn)[/yellow]")

    if ltv_summary.churn_rate is not None:
        churn_color = (
            "green"
            if ltv_summary.churn_rate < 5
            else "yellow"
            if ltv_summary.churn_rate < 10
            else "red"
        )
        rates_table.add_row(
            "Churn Rate", f"[{churn_color}]{ltv_summary.churn_rate:.1f}%[/{churn_color}]"
        )

    if ltv_summary.net_revenue_retention is not None:
        nrr_color = (
            "green"
            if ltv_summary.net_revenue_retention >= 100
            else "yellow"
            if ltv_summary.net_revenue_retention >= 90
            else "red"
        )
        rates_table.add_row(
            "Net Revenue Retention",
            f"[{nrr_color}]{ltv_summary.net_revenue_retention:.1f}%[/{nrr_color}]",
        )

    if ltv_summary.gross_revenue_retention is not None:
        grr_color = (
            "green"
            if ltv_summary.gross_revenue_retention >= 90
            else "yellow"
            if ltv_summary.gross_revenue_retention >= 80
            else "red"
        )
        rates_table.add_row(
            "Gross Revenue Retention",
            f"[{grr_color}]{ltv_summary.gross_revenue_retention:.1f}%[/{grr_color}]",
        )

    # Only show the panel if there's content
    if rates_table.row_count > 0:
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
    app_id: Optional[str] = typer.Option(
        None,
        "--app-id",
        "-a",
        help="Filter by specific app ID",
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
        app_id=app_id,
    )

    if trend_df.empty:
        rprint("[yellow]No MRR data found for the specified period.[/yellow]")
        return

    # Display trend table
    title = f"MRR Trend ({granularity.title()})"
    if app_id:
        title += f" - App: {app_id}"
    table = Table(title=title)
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
    console.print(
        Panel(
            f"[bold]Period:[/bold] {start.strftime('%Y-%m-%d')} to {now.strftime('%Y-%m-%d')}",
            title="Churn Analysis",
            border_style="red",
        )
    )

    table = Table(show_header=False, box=None)
    table.add_column("Metric", style="cyan", width=30)
    table.add_column("Value", style="white")

    table.add_row("Churned MRR", f"[red]${summary.churned_mrr:,.2f}[/red]")
    table.add_row("Churned Subscriptions", f"[red]{summary.churned_subscriptions}[/red]")

    if summary.churn_rate is not None:
        churn_color = (
            "green" if summary.churn_rate < 5 else "yellow" if summary.churn_rate < 10 else "red"
        )
        table.add_row("Churn Rate", f"[{churn_color}]{summary.churn_rate:.2f}%[/{churn_color}]")

    table.add_row("", "")
    table.add_row("[bold]MRR Movement[/bold]", "")
    table.add_row("New MRR", f"[green]+${summary.new_mrr:,.2f}[/green]")
    table.add_row("Expansion MRR", f"[green]+${summary.expansion_mrr:,.2f}[/green]")
    table.add_row("Contraction MRR", f"[red]-${summary.contraction_mrr:,.2f}[/red]")
    table.add_row("Churned MRR", f"[red]-${summary.churned_mrr:,.2f}[/red]")
    table.add_row("", "")
    net_color = "green" if summary.net_new_mrr >= 0 else "red"
    table.add_row(
        "[bold]Net New MRR[/bold]", f"[{net_color}]${summary.net_new_mrr:,.2f}[/{net_color}]"
    )

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

    console.print(
        Panel(
            f"[bold {nrr_color}]{nrr_value:.1f}%[/bold {nrr_color}]",
            title=f"Net Revenue Retention ({period.title()})",
            subtitle=f"{start.strftime('%Y-%m-%d')} to {now.strftime('%Y-%m-%d')}",
            border_style=nrr_color,
        )
    )

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
        grr_color = (
            "green"
            if summary.gross_revenue_retention >= 90
            else "yellow"
            if summary.gross_revenue_retention >= 80
            else "red"
        )
        table.add_row(
            "Gross Revenue Retention",
            f"[{grr_color}]{summary.gross_revenue_retention:.1f}%[/{grr_color}]",
        )

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
def ltv(
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
    """View Customer Lifetime Value (LTV) metrics."""
    settings = get_settings()
    init_db(settings.database_url)

    source_filter = None
    if source == "shopify":
        source_filter = TransactionSource.SHOPIFY
    elif source == "stripe":
        source_filter = TransactionSource.STRIPE

    calculator = MetricsCalculator()
    summary = calculator.calculate_ltv_metrics(
        period=period,
        source=source_filter,
    )

    # Check if we have enough data
    if summary.arpu is None:
        rprint("[yellow]No active subscriptions found. Cannot calculate LTV.[/yellow]")
        rprint("[dim]Run 'mrrmaid sync' to fetch subscription data.[/dim]")
        raise typer.Exit(1)

    # Main LTV panel
    if summary.ltv is not None:
        ltv_color = "green" if summary.ltv >= 1000 else "yellow" if summary.ltv >= 500 else "red"
        ltv_display = f"${summary.ltv:,.2f}"
    else:
        ltv_color = "yellow"
        ltv_display = "N/A (0% churn)"

    period_label = period.title()
    console.print(
        Panel(
            f"[bold {ltv_color}]{ltv_display}[/bold {ltv_color}]",
            title=f"Customer Lifetime Value ({period_label})",
            subtitle="LTV = ARPU / Monthly Churn Rate",
            border_style=ltv_color,
        )
    )

    # LTV Components table
    components_table = Table(show_header=False, box=None)
    components_table.add_column("Component", style="cyan", width=25)
    components_table.add_column("Value", style="white")

    components_table.add_row("[bold]LTV Components[/bold]", "")
    components_table.add_row("ARPU (Monthly)", f"${summary.arpu:,.2f}")

    if summary.churn_rate is not None:
        churn_color = (
            "green" if summary.churn_rate < 5 else "yellow" if summary.churn_rate < 10 else "red"
        )
        churn_label = f"{period_label} Churn Rate"
        components_table.add_row(
            churn_label, f"[{churn_color}]{summary.churn_rate:.2f}%[/{churn_color}]"
        )
    else:
        components_table.add_row(f"{period_label} Churn Rate", "[dim]N/A[/dim]")

    if summary.ltv is not None:
        components_table.add_row("LTV (ARPU/Churn)", f"[bold]${summary.ltv:,.2f}[/bold]")
    else:
        components_table.add_row("LTV (ARPU/Churn)", "[yellow]N/A (0% churn)[/yellow]")

    console.print(components_table)

    # Lifespan Analysis
    console.print()
    lifespan_table = Table(show_header=False, box=None)
    lifespan_table.add_column("Metric", style="cyan", width=25)
    lifespan_table.add_column("Value", style="white")

    lifespan_table.add_row("[bold]Lifespan Analysis[/bold]", "")

    if summary.average_lifespan_months is not None:
        lifespan_table.add_row(
            "Avg Customer Lifespan", f"{summary.average_lifespan_months:.1f} months"
        )
        if summary.ltv_lifespan is not None:
            lifespan_table.add_row("LTV (Lifespan-based)", f"${summary.ltv_lifespan:,.2f}")
    else:
        lifespan_table.add_row(
            "Avg Customer Lifespan", "[dim]Insufficient data (<10 customers)[/dim]"
        )
        lifespan_table.add_row("LTV (Lifespan-based)", "[dim]N/A[/dim]")

    console.print(lifespan_table)

    # Health Indicators
    console.print()
    health_table = Table(show_header=False, box=None)
    health_table.add_column("Indicator", style="cyan", width=25)
    health_table.add_column("Value", style="white")

    health_table.add_row("[bold]Health Indicators[/bold]", "")

    if summary.ltv is not None and summary.arpu > 0:
        ltv_arpu_ratio = summary.ltv / summary.arpu
        ratio_color = (
            "green" if ltv_arpu_ratio >= 12 else "yellow" if ltv_arpu_ratio >= 6 else "red"
        )
        health_table.add_row(
            "LTV / ARPU Ratio", f"[{ratio_color}]{ltv_arpu_ratio:.1f}x[/{ratio_color}]"
        )
    else:
        health_table.add_row("LTV / ARPU Ratio", "[dim]N/A[/dim]")

    health_table.add_row("Active Subscriptions", str(summary.active_subscriptions))

    console.print(health_table)

    # Interpretation
    console.print()
    if summary.ltv is None:
        interpretation = "[yellow]Zero churn is great, but LTV cannot be calculated. Monitor as customer base grows.[/yellow]"
    elif summary.ltv >= 3000:
        interpretation = "[green]Excellent! Customers provide strong long-term value.[/green]"
    elif summary.ltv >= 1000:
        interpretation = "[green]Good! Healthy customer lifetime value.[/green]"
    elif summary.ltv >= 500:
        interpretation = "[yellow]Moderate LTV. Consider improving retention or ARPU.[/yellow]"
    else:
        interpretation = "[red]Low LTV. Focus on reducing churn and/or increasing ARPU.[/red]"

    console.print(interpretation)


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

    console.print(
        Panel(
            f"[bold]Cohort Analysis - {metric_label}{source_label}[/bold]\n"
            f"[dim]Shows % retention relative to first month (M0 = 100%)[/dim]",
            border_style="blue",
        )
    )

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


@app.command()
def customers(
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
        help="Analysis period: 'month', 'quarter', 'year', 'all'",
    ),
    cohort: Optional[str] = typer.Option(
        None,
        "--cohort",
        "-c",
        help="Filter by cohort month (YYYY-MM), e.g., '2025-11'",
    ),
    limit: int = typer.Option(
        20,
        "--limit",
        "-n",
        help="Number of customers to show",
    ),
    sort: str = typer.Option(
        "revenue",
        "--sort",
        help="Sort by: 'revenue', 'transactions', 'name'",
    ),
    export: Optional[str] = typer.Option(
        None,
        "--export",
        "-e",
        help="Export to CSV file",
    ),
    app_id: Optional[str] = typer.Option(
        None,
        "--app-id",
        "-a",
        help="Filter by specific app ID",
    ),
) -> None:
    """Analyze customers by revenue concentration."""
    settings = get_settings()
    init_db(settings.database_url)

    source_filter = None
    if source == "shopify":
        source_filter = TransactionSource.SHOPIFY
    elif source == "stripe":
        source_filter = TransactionSource.STRIPE

    # Determine date range
    now = datetime.utcnow()
    if period == "month":
        start_date = now - timedelta(days=30)
    elif period == "quarter":
        start_date = now - timedelta(days=90)
    elif period == "year":
        start_date = now - timedelta(days=365)
    else:  # all
        start_date = None

    calculator = MetricsCalculator()
    df = calculator.get_customer_analysis(
        source=source_filter,
        start_date=start_date,
        end_date=now,
        cohort_month=cohort,
        app_id=app_id,
    )

    if df.empty:
        rprint("[yellow]No customer data available. Run 'mrrmaid sync' first.[/yellow]")
        raise typer.Exit(1)

    # Sort
    if sort == "revenue":
        df = df.sort_values("revenue", ascending=False)
    elif sort == "transactions":
        df = df.sort_values("transaction_count", ascending=False)
    elif sort == "name":
        df = df.sort_values("customer_id")

    # Export if requested
    if export:
        df.to_csv(export, index=False)
        rprint(f"[green]Exported customer analysis to {export}[/green]")
        return

    # Calculate concentration metrics
    total_revenue = df["revenue"].sum()
    df["pct_of_total"] = (df["revenue"] / total_revenue * 100).round(1)
    df["cumulative_pct"] = df["pct_of_total"].cumsum().round(1)

    # Find concentration thresholds
    top_10_pct = df.head(max(1, len(df) // 10))["revenue"].sum() / total_revenue * 100
    top_20_pct = df.head(max(1, len(df) // 5))["revenue"].sum() / total_revenue * 100

    # Header
    period_label = period.title() if period != "all" else "All Time"
    source_label = f" ({source.title()})" if source else ""
    app_label = f" - App: {app_id}" if app_id else ""
    console.print(
        Panel(
            f"[bold]Customer Revenue Analysis - {period_label}{source_label}{app_label}[/bold]\n"
            f"[dim]Total Revenue: ${total_revenue:,.2f} from {len(df)} customers[/dim]",
            border_style="blue",
        )
    )

    # Concentration warning
    if top_10_pct > 50:
        console.print(
            f"[red]⚠ High concentration risk: Top 10% of customers = {top_10_pct:.1f}% of revenue[/red]"
        )
    elif top_10_pct > 30:
        console.print(
            f"[yellow]⚠ Moderate concentration: Top 10% of customers = {top_10_pct:.1f}% of revenue[/yellow]"
        )
    else:
        console.print(
            f"[green]✓ Healthy distribution: Top 10% of customers = {top_10_pct:.1f}% of revenue[/green]"
        )

    console.print(f"[dim]Top 20% = {top_20_pct:.1f}% of revenue[/dim]\n")

    # Build table
    table = Table(show_header=True, header_style="bold cyan")
    table.add_column("#", justify="right", width=3)
    table.add_column("Customer", style="white", no_wrap=True, overflow="ellipsis")
    table.add_column("Revenue", justify="right")
    table.add_column("% Rev", justify="right")
    table.add_column("Cumul %", justify="right")
    table.add_column("Txns", justify="right")

    # Add rows
    for i, (_, row) in enumerate(df.head(limit).iterrows(), 1):
        # Color code by concentration
        if row["cumulative_pct"] <= 50:
            pct_style = "red"
        elif row["cumulative_pct"] <= 80:
            pct_style = "yellow"
        else:
            pct_style = "green"

        table.add_row(
            str(i),
            str(row["customer_id"])[:35],
            f"${row['revenue']:,.2f}",
            f"{row['pct_of_total']:.1f}%",
            f"[{pct_style}]{row['cumulative_pct']:.1f}%[/{pct_style}]",
            str(int(row["transaction_count"])),
        )

    console.print(table)

    if len(df) > limit:
        console.print(
            f"\n[dim]Showing top {limit} of {len(df)} customers. Use --limit to see more.[/dim]"
        )


@app.command()
def customer(
    identifier: str = typer.Argument(
        ...,
        help="Customer ID or shop domain (partial match supported)",
    ),
    source: Optional[str] = typer.Option(
        None,
        "--source",
        "-s",
        help="Filter by source: 'shopify' or 'stripe'",
    ),
    export: Optional[str] = typer.Option(
        None,
        "--export",
        "-e",
        help="Export monthly history to CSV file",
    ),
) -> None:
    """View detailed history for a specific customer."""
    settings = get_settings()
    init_db(settings.database_url)

    source_filter = None
    if source == "shopify":
        source_filter = TransactionSource.SHOPIFY
    elif source == "stripe":
        source_filter = TransactionSource.STRIPE

    calculator = MetricsCalculator()
    data = calculator.get_customer_history(
        customer_identifier=identifier,
        source=source_filter,
    )

    if not data:
        rprint(f"[yellow]No customer found matching '{identifier}'[/yellow]")
        raise typer.Exit(1)

    # Export if requested
    if export:
        import csv

        with open(export, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=["month", "revenue", "transactions"])
            writer.writeheader()
            writer.writerows(data["monthly_history"])
        rprint(f"[green]Exported monthly history to {export}[/green]")
        return

    # Customer header
    status_color = "green" if data["status"] == "active" else "red"
    status_label = (
        "Active" if data["status"] == "active" else f"Churned ({data['days_since_last']} days ago)"
    )

    console.print(
        Panel(
            f"[bold]{data['display_name']}[/bold]\n[{status_color}]{status_label}[/{status_color}]",
            title="Customer Details",
            border_style="blue",
        )
    )

    # Summary stats
    stats_table = Table(show_header=False, box=None)
    stats_table.add_column("Metric", style="cyan", width=25)
    stats_table.add_column("Value", style="white")

    stats_table.add_row(
        "[bold]Lifetime Value[/bold]", f"[bold green]${data['ltv']:,.2f}[/bold green]"
    )
    stats_table.add_row("Total Revenue", f"${data['total_revenue']:,.2f}")
    stats_table.add_row("Monthly ARPU", f"${data['monthly_arpu']:,.2f}")
    stats_table.add_row("Transactions", str(data["transaction_count"]))
    stats_table.add_row("Tenure", f"{data['tenure_months']:.1f} months")
    stats_table.add_row("First Seen", data["first_seen"].strftime("%Y-%m-%d"))
    stats_table.add_row("Last Seen", data["last_seen"].strftime("%Y-%m-%d"))

    # Always show Customer ID for consistency
    if data.get("customer_id"):
        stats_table.add_row("Customer ID", data["customer_id"])

    console.print(stats_table)

    # Monthly history table
    console.print()
    history_table = Table(
        title="Monthly Revenue History", show_header=True, header_style="bold cyan"
    )
    history_table.add_column("Month", style="white")
    history_table.add_column("Revenue", justify="right", style="green")
    history_table.add_column("Txns", justify="right")
    history_table.add_column("", width=30)  # Sparkline-style bar

    # Find max revenue for scaling bars
    max_revenue = max((m["revenue"] for m in data["monthly_history"]), default=1)

    for month_data in data["monthly_history"]:
        # Create a simple bar visualization
        bar_width = int((month_data["revenue"] / max_revenue) * 25) if max_revenue > 0 else 0
        bar = "█" * bar_width

        history_table.add_row(
            month_data["month"],
            f"${month_data['revenue']:,.2f}",
            str(month_data["transactions"]),
            f"[cyan]{bar}[/cyan]",
        )

    console.print(history_table)


# ============================================================================
# Comparison Commands
# ============================================================================


def _parse_period(period_str: str) -> tuple[datetime, datetime]:
    """Parse a period string into (start_date, end_date).

    Supports:
        YYYY-MM  -> first/last day of month
        YYYY-QN  -> first/last day of quarter
    """
    period_str = period_str.strip()

    # Quarter format: 2025-Q4
    if "-Q" in period_str.upper():
        parts = period_str.upper().split("-Q")
        year = int(parts[0])
        quarter = int(parts[1])
        if quarter < 1 or quarter > 4:
            raise typer.BadParameter(f"Invalid quarter: Q{quarter}. Must be Q1-Q4.")
        start_month = (quarter - 1) * 3 + 1
        end_month = start_month + 2
        start = datetime(year, start_month, 1)
        end = datetime(year, end_month, calendar.monthrange(year, end_month)[1], 23, 59, 59)
        return start, end

    # Month format: 2025-02
    try:
        dt = datetime.strptime(period_str, "%Y-%m")
        start = datetime(dt.year, dt.month, 1)
        last_day = calendar.monthrange(dt.year, dt.month)[1]
        end = datetime(dt.year, dt.month, last_day, 23, 59, 59)
        return start, end
    except ValueError:
        raise typer.BadParameter(f"Invalid period format: '{period_str}'. Use YYYY-MM or YYYY-QN.")


def _format_delta(old: float, new: float, is_pct: bool = False, invert: bool = False) -> str:
    """Format the change between two values with color and arrows.

    Args:
        old: The baseline value.
        new: The current value.
        is_pct: If True, show percentage-point delta (e.g. +2.1pp).
        invert: If True, a decrease is good (e.g. churn rate).
    """
    diff = new - old

    if diff == 0:
        return "[dim]—[/dim]"

    # Determine if this change is positive (good)
    is_good = diff > 0
    if invert:
        is_good = not is_good

    color = "green" if is_good else "red"
    arrow = "↑" if diff > 0 else "↓"
    sign = "+" if diff > 0 else ""

    if is_pct:
        return f"[{color}]{sign}{diff:.1f}pp {arrow}[/{color}]"

    # Dollar amount with percentage change
    if old != 0:
        pct_change = (diff / abs(old)) * 100
        return f"[{color}]{sign}${diff:,.2f} ({sign}{pct_change:.1f}%) {arrow}[/{color}]"

    # Old was zero — can't compute percentage
    return f"[{color}]{sign}${diff:,.2f} {arrow}[/{color}]"


def _format_count_delta(old: int, new: int, invert: bool = False) -> str:
    """Format an integer delta with color and arrows."""
    diff = new - old

    if diff == 0:
        return "[dim]—[/dim]"

    is_good = diff > 0
    if invert:
        is_good = not is_good

    color = "green" if is_good else "red"
    arrow = "↑" if diff > 0 else "↓"
    sign = "+" if diff > 0 else ""

    if old != 0:
        pct_change = (diff / abs(old)) * 100
        return f"[{color}]{sign}{diff} ({sign}{pct_change:.1f}%) {arrow}[/{color}]"

    return f"[{color}]{sign}{diff} {arrow}[/{color}]"


def _period_label(start: datetime, end: datetime) -> str:
    """Create a human-readable label for a period."""
    if start.month == end.month and start.year == end.year:
        return start.strftime("%b %Y")
    # Quarter or multi-month
    return f"{start.strftime('%b')}-{end.strftime('%b %Y')}"


@app.command()
def compare(
    period: Optional[str] = typer.Option(
        None,
        "--period",
        "-p",
        help="Target period: YYYY-MM or YYYY-QN (default: last complete month)",
    ),
    vs: Optional[str] = typer.Option(
        None,
        "--vs",
        help="Comparison period: YYYY-MM or YYYY-QN (default: same period one year prior)",
    ),
    source: Optional[str] = typer.Option(
        None,
        "--source",
        "-s",
        help="Filter by source: 'shopify' or 'stripe'",
    ),
) -> None:
    """Compare metrics between two periods (YoY, QoQ, etc)."""
    settings = get_settings()
    init_db(settings.database_url)

    source_filter = None
    if source == "shopify":
        source_filter = TransactionSource.SHOPIFY
    elif source == "stripe":
        source_filter = TransactionSource.STRIPE

    # Default period: last complete month
    if period is None:
        now = datetime.utcnow()
        # Go to last complete month
        if now.month == 1:
            last_month = datetime(now.year - 1, 12, 1)
        else:
            last_month = datetime(now.year, now.month - 1, 1)
        period = last_month.strftime("%Y-%m")

    period_start, period_end = _parse_period(period)

    # Default vs: same period one year prior
    if vs is None:
        vs_start = period_start.replace(year=period_start.year - 1)
        vs_end = period_end.replace(year=period_end.year - 1)
        # Fix end day for leap year edge cases
        last_day = calendar.monthrange(vs_end.year, vs_end.month)[1]
        if vs_end.day > last_day:
            vs_end = vs_end.replace(day=last_day)
    else:
        vs_start, vs_end = _parse_period(vs)

    calculator = MetricsCalculator()

    # Calculate metrics for both periods
    current = calculator.calculate_mrr_for_period(
        start_date=period_start,
        end_date=period_end,
        source=source_filter,
    )
    baseline = calculator.calculate_mrr_for_period(
        start_date=vs_start,
        end_date=vs_end,
        source=source_filter,
    )

    # Get revenue for both periods
    current_txns = calculator.get_transactions_summary(
        start_date=period_start,
        end_date=period_end,
        source=source_filter,
    )
    baseline_txns = calculator.get_transactions_summary(
        start_date=vs_start,
        end_date=vs_end,
        source=source_filter,
    )

    current_revenue = current_txns["net_amount"].sum() if not current_txns.empty else 0
    baseline_revenue = baseline_txns["net_amount"].sum() if not baseline_txns.empty else 0

    # Labels
    current_label = _period_label(period_start, period_end)
    baseline_label = _period_label(vs_start, vs_end)

    # Build comparison table
    table = Table(
        show_header=True,
        header_style="bold cyan",
        title=f"Period Comparison: {current_label} vs {baseline_label}",
        title_style="bold",
    )
    table.add_column("Metric", style="white", width=22)
    table.add_column(baseline_label, justify="right", width=12)
    table.add_column(current_label, justify="right", width=12)
    table.add_column("Change", justify="right", min_width=20)

    # MRR metrics
    table.add_row(
        "[bold]MRR[/bold]",
        f"${baseline.total_mrr:,.2f}",
        f"${current.total_mrr:,.2f}",
        _format_delta(baseline.total_mrr, current.total_mrr),
    )
    table.add_row(
        "New MRR",
        f"${baseline.new_mrr:,.2f}",
        f"${current.new_mrr:,.2f}",
        _format_delta(baseline.new_mrr, current.new_mrr),
    )
    table.add_row(
        "Expansion MRR",
        f"${baseline.expansion_mrr:,.2f}",
        f"${current.expansion_mrr:,.2f}",
        _format_delta(baseline.expansion_mrr, current.expansion_mrr),
    )
    table.add_row(
        "Contraction MRR",
        f"${baseline.contraction_mrr:,.2f}",
        f"${current.contraction_mrr:,.2f}",
        _format_delta(baseline.contraction_mrr, current.contraction_mrr, invert=True),
    )
    table.add_row(
        "Churned MRR",
        f"${baseline.churned_mrr:,.2f}",
        f"${current.churned_mrr:,.2f}",
        _format_delta(baseline.churned_mrr, current.churned_mrr, invert=True),
    )
    table.add_row(
        "[bold]Net New MRR[/bold]",
        f"${baseline.net_new_mrr:,.2f}",
        f"${current.net_new_mrr:,.2f}",
        _format_delta(baseline.net_new_mrr, current.net_new_mrr),
    )

    # Separator
    table.add_row("", "", "", "")

    # Customer metrics
    table.add_row(
        "Active Customers",
        str(baseline.active_subscriptions),
        str(current.active_subscriptions),
        _format_count_delta(baseline.active_subscriptions, current.active_subscriptions),
    )
    table.add_row(
        "New Customers",
        str(baseline.new_subscriptions),
        str(current.new_subscriptions),
        _format_count_delta(baseline.new_subscriptions, current.new_subscriptions),
    )
    table.add_row(
        "Churned Customers",
        str(baseline.churned_subscriptions),
        str(current.churned_subscriptions),
        _format_count_delta(
            baseline.churned_subscriptions, current.churned_subscriptions, invert=True
        ),
    )

    # Separator
    table.add_row("", "", "", "")

    # Rates (percentage-point deltas)
    baseline_churn = baseline.churn_rate
    current_churn = current.churn_rate
    if baseline_churn is not None and current_churn is not None:
        table.add_row(
            "Churn Rate",
            f"{baseline_churn:.1f}%",
            f"{current_churn:.1f}%",
            _format_delta(baseline_churn, current_churn, is_pct=True, invert=True),
        )
    else:
        table.add_row(
            "Churn Rate",
            f"{baseline_churn:.1f}%" if baseline_churn is not None else "[dim]N/A[/dim]",
            f"{current_churn:.1f}%" if current_churn is not None else "[dim]N/A[/dim]",
            "[dim]—[/dim]",
        )

    baseline_nrr = baseline.net_revenue_retention
    current_nrr = current.net_revenue_retention
    if baseline_nrr is not None and current_nrr is not None:
        table.add_row(
            "NRR",
            f"{baseline_nrr:.1f}%",
            f"{current_nrr:.1f}%",
            _format_delta(baseline_nrr, current_nrr, is_pct=True),
        )
    else:
        table.add_row(
            "NRR",
            f"{baseline_nrr:.1f}%" if baseline_nrr is not None else "[dim]N/A[/dim]",
            f"{current_nrr:.1f}%" if current_nrr is not None else "[dim]N/A[/dim]",
            "[dim]—[/dim]",
        )

    baseline_grr = baseline.gross_revenue_retention
    current_grr = current.gross_revenue_retention
    if baseline_grr is not None and current_grr is not None:
        table.add_row(
            "GRR",
            f"{baseline_grr:.1f}%",
            f"{current_grr:.1f}%",
            _format_delta(baseline_grr, current_grr, is_pct=True),
        )
    else:
        table.add_row(
            "GRR",
            f"{baseline_grr:.1f}%" if baseline_grr is not None else "[dim]N/A[/dim]",
            f"{current_grr:.1f}%" if current_grr is not None else "[dim]N/A[/dim]",
            "[dim]—[/dim]",
        )

    # Revenue
    table.add_row(
        "[bold]Revenue[/bold]",
        f"${baseline_revenue:,.2f}",
        f"${current_revenue:,.2f}",
        _format_delta(baseline_revenue, current_revenue),
    )

    console.print()
    console.print(table)
    console.print()


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

        customer = (
            row.get("customer_email") or row.get("shop_domain") or row.get("customer_id") or "-"
        )

        table.add_row(
            str(row["external_id"])[:20] + "..."
            if len(str(row["external_id"])) > 20
            else str(row["external_id"]),
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
