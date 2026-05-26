"""
MCP tool implementations — 10 tools total.

- 5 read tools: lookup_customer, get_recent_orders, get_order_details,
  check_refund_eligibility, search_policies
- 2 action tools: issue_wallet_credit, schedule_return_pickup
- 3 escalation tools: create_ticket, escalate_to_human, route_to_team
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from fastmcp import Context
from contextlib import nullcontext as _noop

import db
import telemetry
from config import settings
from vector import hybrid_search

_tracer = telemetry.tracer


# ═══════════════════════════════════════════════════════════════════
# READ TOOLS
# ═══════════════════════════════════════════════════════════════════

async def lookup_customer(
    email: str | None = None,
    phone: str | None = None,
    *,
    ctx: Context,
) -> dict[str, Any] | None:
    pool = ctx.lifespan_context["pool"]
    with (_tracer.start_as_current_span("postgres lookup_customer") if _tracer else _noop()) as span:
        if span:
            span.set_attributes({"db.operation": "SELECT", "db.table": "users"})
        if email:
            result = await db.get_user_by_email(pool, email)
        elif phone:
            result = await db.get_user_by_phone(pool, phone)
        else:
            result = None
        if span:
            span.set_attribute("db.result", "found" if result else "not_found")
    return result


async def get_recent_orders(
    user_id: str,
    *,
    ctx: Context,
) -> list[dict[str, Any]]:
    pool = ctx.lifespan_context["pool"]
    with (_tracer.start_as_current_span("postgres get_recent_orders") if _tracer else _noop()) as span:
        if span:
            span.set_attributes({"db.operation": "SELECT", "db.table": "orders"})
        result = await db.get_recent_orders(pool, user_id)
        if span:
            span.set_attributes({"results.count": len(result)})
    return result


async def get_order_details(
    order_id: str,
    *,
    ctx: Context,
) -> dict[str, Any] | None:
    pool = ctx.lifespan_context["pool"]
    with (_tracer.start_as_current_span("postgres get_order_details") if _tracer else _noop()) as span:
        if span:
            span.set_attributes({"db.operation": "SELECT", "db.table": "orders JOIN products"})
        result = await db.get_order_with_product(pool, order_id)
        if span:
            span.set_attribute("db.result", "found" if result else "not_found")
    return result


async def check_refund_eligibility(
    order_id: str,
    *,
    ctx: Context,
) -> dict[str, Any]:
    pool = ctx.lifespan_context["pool"]
    with (_tracer.start_as_current_span("postgres check_refund_eligibility") if _tracer else _noop()) as span:
        if span:
            span.set_attributes({"db.operation": "SELECT", "db.table": "orders+billing"})
        order = await db.get_order_with_product(pool, order_id)
        if not order:
            return {"eligible": False, "reason": "order_not_found"}

        billing_rows = await db.get_billing_by_order(pool, order_id)
        for br in billing_rows:
            if br["transaction_type"] == "refund" and br["status"] == "completed":
                return {"eligible": False, "reason": "already_refunded", "refund_id": br["gateway_transaction_id"]}

        delivery_date = order.get("delivery_date")
        return_window = order.get("return_window_days", 10)
        if delivery_date and return_window:
            days_since = (datetime.now(timezone.utc) - delivery_date).days
            if days_since > return_window:
                return {"eligible": False, "reason": f"return_window_expired ({return_window} days)"}

        payment = next((br for br in billing_rows if br["transaction_type"] == "payment"), None)
        if not payment:
            return {"eligible": False, "reason": "no_payment_found"}

        result = {
            "eligible": True,
            "reason": "within_return_window",
            "amount": payment["amount"],
            "method": order.get("payment_method", "upi"),
        }
        if span:
            span.set_attribute("refund.eligible", True)
    return result


async def search_policies(
    query: str,
    top_k: int = 5,
    *,
    ctx: Context,
) -> list[dict[str, Any]]:
    with (_tracer.start_as_current_span("qdrant search_policies") if _tracer else _noop()) as span:
        if span:
            span.set_attributes({
                "db.operation": "vector_search",
                "db.collection": settings.qdrant_collection,
                "query.length": len(query),
                "top_k": top_k,
            })
        results = await hybrid_search(query, top_k)
        if span:
            span.set_attributes({"results.count": len(results)})
    return results


# ═══════════════════════════════════════════════════════════════════
# ACTION TOOLS (Write)
# ═══════════════════════════════════════════════════════════════════

async def issue_wallet_credit(
    user_id: str,
    amount: float,
    reason: str,
    *,
    ctx: Context,
) -> dict[str, Any]:
    if amount > 500:
        return {"status": "rejected", "reason": "Amount exceeds maximum of Rs.500"}
    pool = ctx.lifespan_context["pool"]
    with (_tracer.start_as_current_span("postgres issue_wallet_credit") if _tracer else _noop()) as span:
        if span:
            span.set_attributes({"db.operation": "INSERT", "db.table": "billing", "amount": amount})
        ref_id = await db.issue_wallet_credit(pool, user_id, Decimal(str(amount)), reason)
        if span:
            span.set_attribute("transaction.id", ref_id)
    return {"status": "issued", "transaction_id": ref_id, "amount": amount}


async def schedule_return_pickup(
    order_id: str,
    pickup_date: str,
    *,
    ctx: Context,
) -> dict[str, Any]:
    pool = ctx.lifespan_context["pool"]
    # Verify eligibility first
    eligibility = await check_refund_eligibility(order_id=order_id, ctx=ctx)
    if not eligibility.get("eligible"):
        return {"status": "failed", "reason": eligibility.get("reason", "not_eligible")}
    with (_tracer.start_as_current_span("postgres schedule_return_pickup") if _tracer else _noop()) as span:
        if span:
            span.set_attributes({"db.operation": "UPDATE", "db.table": "orders"})
        await db.schedule_return_pickup(pool, order_id, pickup_date)
    return {"status": "scheduled", "order_id": order_id, "pickup_date": pickup_date}


# ═══════════════════════════════════════════════════════════════════
# ESCALATION TOOLS
# ═══════════════════════════════════════════════════════════════════

async def create_ticket(
    user_id: str,
    query_text: str,
    classification: dict,
    priority: str,
    assigned_team: str = "general_support",
    *,
    ctx: Context,
) -> str:
    pool = ctx.lifespan_context["pool"]
    with (_tracer.start_as_current_span("postgres create_ticket") if _tracer else _noop()) as span:
        if span:
            span.set_attributes({
                "db.operation": "INSERT",
                "db.table": "tickets",
                "ticket.priority": priority,
                "ticket.assigned_team": assigned_team,
            })
        ticket_id = await db.insert_ticket(pool, user_id, query_text, classification, priority, assigned_team)
        if span:
            span.set_attribute("ticket.id", ticket_id)
    return ticket_id


async def escalate_to_human(
    ticket_id: str,
    *,
    ctx: Context,
) -> dict[str, Any]:
    pool = ctx.lifespan_context["pool"]
    with (_tracer.start_as_current_span("postgres escalate_to_human") if _tracer else _noop()) as span:
        if span:
            span.set_attributes({"db.operation": "UPDATE", "db.table": "tickets"})
        await db.update_ticket_status(pool, ticket_id, "pending_human")
        await db.set_ticket_priority(pool, ticket_id, "critical")
    return {"status": "escalated", "ticket_id": ticket_id}


async def route_to_team(
    ticket_id: str,
    team: str,
    *,
    ctx: Context,
) -> dict[str, Any]:
    pool = ctx.lifespan_context["pool"]
    with (_tracer.start_as_current_span("postgres route_to_team") if _tracer else _noop()) as span:
        if span:
            span.set_attributes({"db.operation": "UPDATE", "db.table": "tickets", "team": team})
        await db.assign_ticket_team(pool, ticket_id, team)
    return {"status": "routed", "ticket_id": ticket_id, "team": team}