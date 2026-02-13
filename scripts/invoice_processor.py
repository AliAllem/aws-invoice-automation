#!/usr/bin/env python3
"""
Invoice Processor
=================
Main orchestration script for automated AWS invoice processing.

This is the script that replaced 6 hours of monthly spreadsheet work.
It pulls cost data from each payer account, maps everything to business
units, optionally reconciles against budgets, and spits out a clean report.

The audit trail was a requirement from finance — they wanted to know
exactly what data went in, what came out, and a checksum to prove nothing
got modified in between. Fair enough honestly.

Usage:
    python invoice_processor.py
    python invoice_processor.py --month 2025-11 --reconcile --format html
"""

import argparse
import hashlib
import json
import logging
import time
from datetime import datetime
from pathlib import Path

import yaml

from cost_extractor import CostExtractor
from reconciler import BudgetReconciler
from report_generator import ReportGenerator

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


class InvoiceProcessor:
    """
    Orchestrates the end-to-end invoice processing pipeline.

    Pipeline:
        1. Extract cost data from AWS Cost Explorer per payer
        2. Map accounts to business units
        3. Reconcile against budgets (optional)
        4. Generate formatted reports
        5. Write audit trail
    """

    def __init__(self, config_dir: str = "config"):
        self.config_dir = Path(config_dir)
        self.accounts_config = self._load_config("accounts.yaml")
        self.budgets_config = self._load_config("budgets.yaml")
        self.extractor = CostExtractor()
        self.reconciler = BudgetReconciler(self.budgets_config)
        self.report_generator = ReportGenerator()
        self.audit_log = []

    def _load_config(self, filename: str) -> dict:
        """Load a YAML configuration file."""
        filepath = self.config_dir / filename
        if not filepath.exists():
            logger.warning(f"Config file not found: {filepath}")
            return {}

        with open(filepath) as f:
            return yaml.safe_load(f)

    def process(
        self,
        month: str = None,
        reconcile: bool = False,
        output_format: str = "csv",
        output_dir: str = "reports",
    ) -> dict:
        """
        Run the full invoice processing pipeline.

        Args:
            month: Target month in YYYY-MM format. Defaults to current month.
            reconcile: Whether to run budget reconciliation.
            output_format: Output format (csv, html, json).
            output_dir: Directory for output files.

        Returns:
            Dict containing processing results and metadata.
        """
        start_time = time.time()
        run_id = datetime.utcnow().strftime("%Y%m%d_%H%M%S")

        if month is None:
            month = datetime.utcnow().strftime("%Y-%m")

        logger.info(f"Starting invoice processing for {month} (run: {run_id})")
        self._audit("STARTED", f"Processing month: {month}")

        # Step 1: Extract cost data per payer account
        # This is the slowest part — CE API is not fast and rate limited.
        # On a good day it takes a few seconds per payer. On a bad day... coffee time.
        logger.info("Step 1/4: Extracting cost data from AWS Cost Explorer...")
        payer_accounts = self.accounts_config.get("payer_accounts", [])

        all_costs = {}
        for payer in payer_accounts:
            account_id = payer["id"]
            logger.info(f"  Extracting costs for payer: {account_id} ({payer['name']})")

            costs = self.extractor.extract_monthly_costs(
                payer_account_id=account_id,
                month=month,
            )
            all_costs[account_id] = {
                "name": payer["name"],
                "business_unit": payer.get("business_unit", "Unassigned"),
                "costs": costs,
            }

        self._audit("EXTRACTED", f"Processed {len(payer_accounts)} payer account(s)")

        # Step 2: Map and aggregate by business unit
        logger.info("Step 2/4: Mapping costs to business units...")
        aggregated = self._aggregate_by_business_unit(all_costs)
        self._audit("MAPPED", f"Mapped to {len(aggregated)} business unit(s)")

        # Step 3: Reconcile against budgets (optional)
        reconciliation = None
        if reconcile:
            logger.info("Step 3/4: Reconciling against budgets...")
            reconciliation = self.reconciler.reconcile(aggregated, month)
            self._audit("RECONCILED", f"Variances found: {reconciliation['total_variances']}")
        else:
            logger.info("Step 3/4: Skipping reconciliation (not requested)")

        # Step 4: Generate reports
        logger.info(f"Step 4/4: Generating {output_format} report...")
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)

        report_file = self.report_generator.generate(
            costs=all_costs,
            aggregated=aggregated,
            reconciliation=reconciliation,
            month=month,
            format=output_format,
            output_dir=str(output_path),
        )

        elapsed = time.time() - start_time
        self._audit("COMPLETED", f"Elapsed: {elapsed:.2f}s")

        # Generate data checksum for audit
        data_checksum = hashlib.md5(
            json.dumps(all_costs, sort_keys=True, default=str).encode()
        ).hexdigest()

        result = {
            "run_id": run_id,
            "month": month,
            "payer_accounts_processed": len(payer_accounts),
            "business_units": len(aggregated),
            "total_spend": sum(
                a["total"] for a in aggregated.values()
            ),
            "reconciliation": reconciliation,
            "report_file": report_file,
            "elapsed_seconds": round(elapsed, 2),
            "data_checksum": data_checksum,
            "audit_trail": self.audit_log,
        }

        # Write audit log
        audit_file = output_path / f"audit_{run_id}.json"
        with open(audit_file, "w") as f:
            json.dump(result, f, indent=2, default=str)

        logger.info(f"✅ Processing complete in {elapsed:.2f}s")
        logger.info(f"   Report: {report_file}")
        logger.info(f"   Audit:  {audit_file}")

        return result

    def _aggregate_by_business_unit(self, all_costs: dict) -> dict:
        """Aggregate payer-level costs into business unit totals."""
        aggregated = {}

        for account_id, data in all_costs.items():
            bu = data["business_unit"]

            if bu not in aggregated:
                aggregated[bu] = {
                    "total": 0,
                    "accounts": [],
                    "services": {},
                }

            account_total = sum(
                cost["amount"] for cost in data["costs"]
            )
            aggregated[bu]["total"] += account_total
            aggregated[bu]["accounts"].append({
                "id": account_id,
                "name": data["name"],
                "total": account_total,
            })

            for cost in data["costs"]:
                service = cost.get("service", "Other")
                aggregated[bu]["services"][service] = (
                    aggregated[bu]["services"].get(service, 0) + cost["amount"]
                )

        return aggregated

    def _audit(self, event: str, detail: str):
        """Add an entry to the audit trail."""
        entry = {
            "timestamp": datetime.utcnow().isoformat(),
            "event": event,
            "detail": detail,
        }
        self.audit_log.append(entry)
        logger.debug(f"AUDIT: [{event}] {detail}")


def main():
    parser = argparse.ArgumentParser(
        description="Automated AWS invoice processing for multi-payer environments"
    )
    parser.add_argument(
        "--month", help="Target month (YYYY-MM). Defaults to current month."
    )
    parser.add_argument(
        "--reconcile", action="store_true", help="Run budget reconciliation"
    )
    parser.add_argument(
        "--format",
        choices=["csv", "html", "json"],
        default="csv",
        help="Output format (default: csv)",
    )
    parser.add_argument(
        "--output", default="reports", help="Output directory (default: reports/)"
    )
    parser.add_argument(
        "--config", default="config", help="Config directory (default: config/)"
    )
    args = parser.parse_args()

    processor = InvoiceProcessor(config_dir=args.config)
    result = processor.process(
        month=args.month,
        reconcile=args.reconcile,
        output_format=args.format,
        output_dir=args.output,
    )

    print(f"\n{'=' * 60}")
    print(f"  Invoice Processing Summary")
    print(f"{'=' * 60}")
    print(f"  Month:              {result['month']}")
    print(f"  Payers processed:   {result['payer_accounts_processed']}")
    print(f"  Business units:     {result['business_units']}")
    print(f"  Total spend:        £{result['total_spend']:,.2f}")
    print(f"  Processing time:    {result['elapsed_seconds']}s")
    print(f"  Data checksum:      {result['data_checksum']}")
    print(f"{'=' * 60}\n")


if __name__ == "__main__":
    main()
