"""
Budget Reconciler - Compares actual spend against budgets per business unit.

The most useful output here is the top cost drivers breakdown. When finance
asks "why did engineering go 15% over budget?", you don't want to say
"I'll look into it" — you want to say "EC2 was up £8k because of the load
testing campaign, and Data Transfer was up £3k because of the new
cross-region replication".

The alert_threshold_pct is configurable per business unit because not all
teams need the same sensitivity. A 10% swing on a £200k budget is £20k and
worth investigating. A 10% swing on a £5k sandbox budget is £500 and
probably not worth anyone's time.
"""

import logging

logger = logging.getLogger(__name__)


class BudgetReconciler:
    """Reconciles actual costs against configured budgets."""

    def __init__(self, budgets_config: dict):
        self.budgets = budgets_config.get("budgets", {})

    def reconcile(self, aggregated_costs: dict, month: str) -> dict:
        """
        Compare actual spend to budgets for each business unit.

        Args:
            aggregated_costs: Dict of business unit cost aggregations.
            month: The month being reconciled (YYYY-MM).

        Returns:
            Dict containing variance analysis and flags.
        """
        results = {
            "month": month,
            "units": {},
            "total_variances": 0,
            "total_overrun": 0,
            "total_underrun": 0,
        }

        for bu_name, bu_costs in aggregated_costs.items():
            actual = bu_costs["total"]
            budget_config = self.budgets.get(bu_name, {})
            budget_target = budget_config.get("monthly_target", 0)
            alert_threshold = budget_config.get("alert_threshold_pct", 10)

            if budget_target == 0:
                logger.warning(f"No budget configured for: {bu_name}")
                results["units"][bu_name] = {
                    "actual": actual,
                    "budget": None,
                    "status": "NO_BUDGET",
                    "message": "No budget configured for this business unit",
                }
                continue

            variance = actual - budget_target
            variance_pct = (variance / budget_target) * 100

            if variance_pct > alert_threshold:
                status = "OVERRUN"
                results["total_variances"] += 1
                results["total_overrun"] += variance
            elif variance_pct < -alert_threshold:
                status = "UNDERRUN"
                results["total_underrun"] += abs(variance)
            else:
                status = "ON_TRACK"

            # Identify top cost drivers
            top_services = sorted(
                bu_costs.get("services", {}).items(),
                key=lambda x: x[1],
                reverse=True,
            )[:5]

            results["units"][bu_name] = {
                "actual": round(actual, 2),
                "budget": budget_target,
                "variance": round(variance, 2),
                "variance_pct": round(variance_pct, 1),
                "status": status,
                "alert_threshold_pct": alert_threshold,
                "top_cost_drivers": [
                    {"service": s, "amount": round(a, 2)} for s, a in top_services
                ],
                "accounts": bu_costs.get("accounts", []),
            }

            if status == "OVERRUN":
                logger.warning(
                    f"⚠️  {bu_name}: £{actual:,.2f} vs budget £{budget_target:,.2f} "
                    f"(+{variance_pct:.1f}%)"
                )

        return results
