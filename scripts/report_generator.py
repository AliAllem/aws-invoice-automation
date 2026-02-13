"""
Report Generator - Produces formatted invoice reports in multiple formats.

Supports CSV, HTML, and JSON output with consistent formatting
suitable for finance stakeholder consumption.
"""

import csv
import json
from datetime import datetime
from pathlib import Path
import logging

logger = logging.getLogger(__name__)


class ReportGenerator:
    """Generates formatted invoice reports."""

    def generate(
        self,
        costs: dict,
        aggregated: dict,
        reconciliation: dict = None,
        month: str = None,
        format: str = "csv",
        output_dir: str = "reports",
    ) -> str:
        """
        Generate a report in the specified format.

        Args:
            costs: Raw cost data per payer account.
            aggregated: Aggregated costs by business unit.
            reconciliation: Optional budget reconciliation results.
            month: The reporting month.
            format: Output format (csv, html, json).
            output_dir: Output directory path.

        Returns:
            Path to the generated report file.
        """
        timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        output_path = Path(output_dir)

        if format == "csv":
            return self._generate_csv(costs, aggregated, month, timestamp, output_path)
        elif format == "html":
            return self._generate_html(
                costs, aggregated, reconciliation, month, timestamp, output_path
            )
        elif format == "json":
            return self._generate_json(
                costs, aggregated, reconciliation, month, timestamp, output_path
            )

    def _generate_csv(
        self, costs, aggregated, month, timestamp, output_path
    ) -> str:
        """Generate CSV report with cost breakdown."""
        filepath = output_path / f"invoice_{month}_{timestamp}.csv"

        with open(filepath, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow([
                "Month", "Payer Account", "Payer Name", "Business Unit",
                "Date", "Service", "Amount (Unblended)", "Currency",
            ])

            for account_id, data in costs.items():
                for cost in data["costs"]:
                    writer.writerow([
                        month,
                        account_id,
                        data["name"],
                        data["business_unit"],
                        cost.get("date", ""),
                        cost.get("service", ""),
                        cost.get("amount", 0),
                        cost.get("currency", "USD"),
                    ])

        # Summary sheet
        summary_path = output_path / f"invoice_summary_{month}_{timestamp}.csv"
        with open(summary_path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["Business Unit", "Total Spend", "Account Count"])

            for bu_name, bu_data in aggregated.items():
                writer.writerow([
                    bu_name,
                    f"{bu_data['total']:.2f}",
                    len(bu_data["accounts"]),
                ])

        logger.info(f"CSV report: {filepath}")
        logger.info(f"CSV summary: {summary_path}")
        return str(filepath)

    def _generate_html(
        self, costs, aggregated, reconciliation, month, timestamp, output_path
    ) -> str:
        """Generate HTML report for stakeholder presentation."""
        filepath = output_path / f"invoice_{month}_{timestamp}.html"

        total_spend = sum(a["total"] for a in aggregated.values())

        html = f"""<!DOCTYPE html>
<html>
<head>
    <title>AWS Invoice Report — {month}</title>
    <style>
        body {{ font-family: Arial, sans-serif; margin: 40px; color: #333; }}
        h1 {{ color: #232f3e; border-bottom: 3px solid #ff9900; padding-bottom: 10px; }}
        h2 {{ color: #232f3e; margin-top: 30px; }}
        table {{ border-collapse: collapse; width: 100%; margin: 20px 0; }}
        th {{ background: #232f3e; color: white; padding: 12px; text-align: left; }}
        td {{ border: 1px solid #ddd; padding: 10px; }}
        tr:nth-child(even) {{ background: #f9f9f9; }}
        .overrun {{ color: #d13212; font-weight: bold; }}
        .on-track {{ color: #1d8102; }}
        .summary-box {{ background: #f0f0f0; padding: 20px; border-radius: 8px;
                        margin: 20px 0; display: inline-block; }}
        .metadata {{ color: #666; font-size: 0.9em; }}
    </style>
</head>
<body>
    <h1>AWS Invoice Report — {month}</h1>
    <p class="metadata">Generated: {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}</p>

    <div class="summary-box">
        <strong>Total Spend: £{total_spend:,.2f}</strong><br>
        Payer Accounts: {len(costs)}<br>
        Business Units: {len(aggregated)}
    </div>

    <h2>Spend by Business Unit</h2>
    <table>
        <tr><th>Business Unit</th><th>Total Spend</th><th>Accounts</th><th>Top Service</th></tr>
"""
        for bu_name, bu_data in sorted(
            aggregated.items(), key=lambda x: x[1]["total"], reverse=True
        ):
            top_service = max(
                bu_data.get("services", {"N/A": 0}).items(),
                key=lambda x: x[1],
            )
            html += f"""        <tr>
            <td>{bu_name}</td>
            <td>£{bu_data['total']:,.2f}</td>
            <td>{len(bu_data['accounts'])}</td>
            <td>{top_service[0]} (£{top_service[1]:,.2f})</td>
        </tr>\n"""

        html += "    </table>\n"

        if reconciliation:
            html += """
    <h2>Budget Reconciliation</h2>
    <table>
        <tr><th>Business Unit</th><th>Actual</th><th>Budget</th>
        <th>Variance</th><th>Status</th></tr>
"""
            for bu_name, data in reconciliation.get("units", {}).items():
                status_class = "overrun" if data["status"] == "OVERRUN" else "on-track"
                budget_str = f"£{data['budget']:,.2f}" if data["budget"] else "N/A"
                variance_str = (
                    f"£{data.get('variance', 0):,.2f} ({data.get('variance_pct', 0):+.1f}%)"
                    if data.get("variance") is not None
                    else "N/A"
                )
                html += f"""        <tr>
            <td>{bu_name}</td>
            <td>£{data['actual']:,.2f}</td>
            <td>{budget_str}</td>
            <td class="{status_class}">{variance_str}</td>
            <td class="{status_class}">{data['status']}</td>
        </tr>\n"""

            html += "    </table>\n"

        html += """
</body>
</html>"""

        with open(filepath, "w") as f:
            f.write(html)

        logger.info(f"HTML report: {filepath}")
        return str(filepath)

    def _generate_json(
        self, costs, aggregated, reconciliation, month, timestamp, output_path
    ) -> str:
        """Generate JSON report for programmatic consumption."""
        filepath = output_path / f"invoice_{month}_{timestamp}.json"

        report = {
            "metadata": {
                "month": month,
                "generated_at": datetime.utcnow().isoformat(),
                "payer_accounts": len(costs),
            },
            "summary": {
                bu: {"total": data["total"], "accounts": len(data["accounts"])}
                for bu, data in aggregated.items()
            },
            "reconciliation": reconciliation,
        }

        with open(filepath, "w") as f:
            json.dump(report, f, indent=2, default=str)

        logger.info(f"JSON report: {filepath}")
        return str(filepath)
