"""
Validators - Data validation and integrity checks for invoice processing.

I added this after a painful incident where a Cost Explorer API response
came back with a slightly different structure than expected (thanks AWS)
and the whole report was silently wrong. Now every stage of the pipeline
validates its inputs before proceeding.

The checksum generation might seem like overkill for an internal tool,
but when finance asks "is this the same data you showed us last week?"
you want a definitive answer, not a shrug.
"""

import hashlib
import json
import logging
from datetime import datetime

logger = logging.getLogger(__name__)


class DataValidator:
    """Validates invoice data at each processing stage."""

    def __init__(self):
        self.validation_log = []

    def validate_cost_data(self, costs: list, payer_id: str) -> dict:
        """
        Validate extracted cost data for completeness and consistency.

        Args:
            costs: List of cost records from extraction.
            payer_id: The payer account ID for context.

        Returns:
            Validation result dict with status and any issues found.
        """
        issues = []

        if not costs:
            issues.append(f"No cost data returned for payer {payer_id}")
            return self._result("FAIL", issues)

        for i, record in enumerate(costs):
            if "amount" not in record:
                issues.append(f"Record {i}: missing 'amount' field")
            elif not isinstance(record["amount"], (int, float)):
                issues.append(f"Record {i}: 'amount' is not numeric")
            elif record["amount"] < 0:
                issues.append(f"Record {i}: negative amount ({record['amount']})")

            if "date" not in record:
                issues.append(f"Record {i}: missing 'date' field")
            else:
                try:
                    datetime.strptime(record["date"], "%Y-%m-%d")
                except ValueError:
                    issues.append(f"Record {i}: invalid date format ({record['date']})")

            if "service" not in record:
                issues.append(f"Record {i}: missing 'service' field")

        # Check for duplicates
        seen = set()
        for record in costs:
            key = f"{record.get('date')}|{record.get('service')}|{record.get('amount')}"
            if key in seen:
                issues.append(f"Potential duplicate: {key}")
            seen.add(key)

        status = "PASS" if not issues else "WARN" if len(issues) < 3 else "FAIL"
        result = self._result(status, issues)
        result["record_count"] = len(costs)
        result["total_amount"] = sum(r.get("amount", 0) for r in costs)
        result["checksum"] = self._checksum(costs)

        self._log_validation("cost_data", payer_id, result)
        return result

    def validate_aggregation(self, aggregated: dict) -> dict:
        """
        Validate that aggregated business unit totals are consistent.

        Args:
            aggregated: Dict of business unit cost aggregations.

        Returns:
            Validation result dict.
        """
        issues = []

        for bu_name, bu_data in aggregated.items():
            if bu_data["total"] <= 0:
                issues.append(f"{bu_name}: zero or negative total")

            account_sum = sum(a["total"] for a in bu_data.get("accounts", []))
            if abs(account_sum - bu_data["total"]) > 0.01:
                issues.append(
                    f"{bu_name}: account sum ({account_sum:.2f}) != "
                    f"total ({bu_data['total']:.2f})"
                )

            service_sum = sum(bu_data.get("services", {}).values())
            if abs(service_sum - bu_data["total"]) > 0.01:
                issues.append(
                    f"{bu_name}: service sum ({service_sum:.2f}) != "
                    f"total ({bu_data['total']:.2f})"
                )

        status = "PASS" if not issues else "FAIL"
        result = self._result(status, issues)
        result["business_units"] = len(aggregated)

        self._log_validation("aggregation", "all", result)
        return result

    def validate_reconciliation(self, reconciliation: dict) -> dict:
        """
        Validate reconciliation results for logical consistency.

        Args:
            reconciliation: Budget reconciliation results.

        Returns:
            Validation result dict.
        """
        issues = []

        for bu_name, data in reconciliation.get("units", {}).items():
            if data.get("budget") and data.get("actual") is not None:
                expected_variance = data["actual"] - data["budget"]
                if abs(expected_variance - data.get("variance", 0)) > 0.01:
                    issues.append(f"{bu_name}: variance calculation mismatch")

                if data.get("variance_pct") is not None:
                    expected_pct = (expected_variance / data["budget"]) * 100
                    if abs(expected_pct - data["variance_pct"]) > 0.1:
                        issues.append(f"{bu_name}: variance percentage mismatch")

        status = "PASS" if not issues else "FAIL"
        return self._result(status, issues)

    def _checksum(self, data) -> str:
        """Generate MD5 checksum for data integrity verification."""
        serialised = json.dumps(data, sort_keys=True, default=str)
        return hashlib.md5(serialised.encode()).hexdigest()

    def _result(self, status: str, issues: list) -> dict:
        """Create a standardised validation result."""
        return {
            "status": status,
            "issues": issues,
            "issue_count": len(issues),
            "validated_at": datetime.utcnow().isoformat(),
        }

    def _log_validation(self, stage: str, context: str, result: dict):
        """Log validation result for audit trail."""
        entry = {
            "stage": stage,
            "context": context,
            "status": result["status"],
            "issue_count": result["issue_count"],
            "timestamp": result["validated_at"],
        }
        self.validation_log.append(entry)

        if result["status"] == "FAIL":
            logger.error(f"Validation FAILED at {stage} ({context}): {result['issues']}")
        elif result["status"] == "WARN":
            logger.warning(f"Validation warnings at {stage} ({context}): {result['issues']}")
        else:
            logger.info(f"Validation passed at {stage} ({context})")

    def get_validation_summary(self) -> dict:
        """Get summary of all validation checks performed."""
        return {
            "total_checks": len(self.validation_log),
            "passed": sum(1 for v in self.validation_log if v["status"] == "PASS"),
            "warnings": sum(1 for v in self.validation_log if v["status"] == "WARN"),
            "failed": sum(1 for v in self.validation_log if v["status"] == "FAIL"),
            "checks": self.validation_log,
        }
