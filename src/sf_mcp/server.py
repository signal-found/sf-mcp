from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
import uuid
from datetime import date, timedelta
from html import escape as _he
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP  # type: ignore[import-not-found]
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from .api_client import InsufficientCreditsError, OnboardApiClient, OnboardApiError
from .config import Settings

_mcp_host = os.getenv("MCP_HOST", "0.0.0.0")
try:
    _mcp_port = int(os.getenv("PORT", os.getenv("MCP_PORT", "8080")))
except ValueError:
    _mcp_port = 8080

mcp = FastMCP("signal-found-onboard", host=_mcp_host, port=_mcp_port, stateless_http=True)
_session_client_id: str | None = None

_ALLOWED_CONVERSION_STATES = {
    "UNKNOWN",
    "REPLY",
    "UNINTERESTED",
    "CLOSE",
    "CONFIRMED",
    "UNCATEGORIZED",
    "OUTBOUND_SENT",
    "DISQUALIFIED",
    "CLOSE_FOLLOWUP_SENT",
}

_TERMINAL_CONVERSION_STATES = {
    "CONFIRMED",
    "DISQUALIFIED",
    "UNINTERESTED",
    "UNKNOWN",
    "CLOSE",
}

_NON_TERMINAL_CONVERSION_STATES = _ALLOWED_CONVERSION_STATES - _TERMINAL_CONVERSION_STATES

_STATE_INTENT_ALIASES: dict[str, str] = {
    "NEUTRAL": "REPLY",
    "CONTINUE": "REPLY",
    "CONTINUE_CONVERSATION": "REPLY",
    "FOLLOW_UP": "REPLY",
    "KEEP_OPEN": "REPLY",
}

_LEAD_CATEGORY_BUCKETS: dict[str, set[str]] = {
    "warm": {"CONFIRMED", "CLOSE_FOLLOWUP_SENT", "CLOSE"},
    "bad": {"UNINTERESTED", "DISQUALIFIED"},
    "in_progress": {"OUTBOUND_SENT", "REPLY"},
    "unknown": {"UNKNOWN"},
}


def _client() -> OnboardApiClient:
    return OnboardApiClient(Settings.from_env())


def _get_session_client_id() -> str | None:
    return (_session_client_id or "").strip() or None


def _set_session_client_id(client_id: str | None) -> None:
    global _session_client_id
    _session_client_id = (client_id or "").strip() or None


def _require_client_id(explicit_client_id: str | None, settings: Settings) -> str:
    # Explicit arg > session > env var default
    if explicit_client_id and explicit_client_id.strip():
        return explicit_client_id.strip()
    session_client_id = _get_session_client_id()
    if session_client_id:
        return session_client_id
    if settings.default_client_id:
        return settings.default_client_id
    raise ValueError(
        "No authenticated client context found. New user? Run create_new_account(business_name, email) to sign up. Existing user? Run login_with_client_id(client_id) first."
    )


_CREDIT_SUCCESS_URL = "https://signal-found.com/billing?payment=success"
_CREDIT_CANCEL_URL = "https://signal-found.com/billing"


def _make_credit_purchase_message(client_id: str) -> str:
    """Generate a friendly credit-purchase message with live Stripe checkout URLs."""
    client = _client()
    starter_url: str | None = None
    pro_url: str | None = None
    try:
        starter_resp = client.post(
            "/credits/checkout",
            client_id=client_id,
            json={"plan": "starter", "success_url": _CREDIT_SUCCESS_URL, "cancel_url": _CREDIT_CANCEL_URL},
        )
        starter_url = starter_resp.get("checkout_url")
    except Exception:
        pass
    try:
        pro_resp = client.post(
            "/credits/checkout",
            client_id=client_id,
            json={"plan": "pro", "success_url": _CREDIT_SUCCESS_URL, "cancel_url": _CREDIT_CANCEL_URL},
        )
        pro_url = pro_resp.get("checkout_url")
    except Exception:
        pass
    finally:
        client.close()

    lines = [
        "You're out of credits! Choose a plan to continue:",
        "",
        f"  • Starter — 1,000 credits: {starter_url or '(run billing_and_credits to generate link)'}",
        f"  • Pro     — 7,000 credits: {pro_url or '(run billing_and_credits to generate link)'}",
        "",
        "Want 1000s of DMs/day through our private bot network of hundreds of Reddit accounts?",
        "Contact us at admin@signal-found.com to get set up.",
    ]
    return "\n".join(lines)


def _build_setup_message(balance: int, client_id: str) -> str:
    """Build the welcome/setup message shown after login."""
    lines = [
        "Welcome to Signal Found!",
        "",
        "Signal Found is the only tool that lets you connect an AI agent directly to our",
        "proprietary Reddit outreach network — find prospects, personalize messages, and",
        "send thousands of DMs per day at scale.",
        "",
        "── Getting started ──────────────────────────────────────────────────",
        "",
        "DIY (use your own Reddit account):",
        "  Install the Chrome extension to link your Reddit account:",
        "  https://onboard.signal-found.com/extensions/reddit",
        "",
        "Managed bot network (1000s of DMs/day):",
        "  We operate hundreds of Reddit accounts on your behalf.",
        "  Email admin@signal-found.com to get onboarded.",
        "",
        "─────────────────────────────────────────────────────────────────────",
    ]

    if balance == 0:
        credit_lines = _make_credit_purchase_message(client_id).splitlines()
        lines.append("")
        lines.append("⚠️  Your credit balance is 0. Purchase credits to start outreaching:")
        lines.append("")
        lines.extend(credit_lines)
    else:
        lines.append(f"✓ Credit balance: {balance:,} credits — you're ready to go.")
        lines.append("  Run agent_quickstart to see the recommended onboarding flow.")

    return "\n".join(lines)


def _products_by_slug(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(p.get("product_slug") or ""): p
        for p in payload.get("products", [])
        if p.get("product_slug")
    }


def _resolve_product_by_slug(
    client: OnboardApiClient,
    *,
    client_id: str,
    product_slug: str,
) -> dict[str, Any]:
    products_resp = client.get(f"/clients/{client_id}/products", client_id=client_id)
    product_lookup = _products_by_slug(products_resp)
    product = product_lookup.get(product_slug)
    if not product:
        raise ValueError(f"Unknown product_slug: {product_slug}")
    return product


def _resolve_product_unique(
    client: OnboardApiClient,
    *,
    client_id: str,
    product_slug: str,
) -> str:
    product = _resolve_product_by_slug(client, client_id=client_id, product_slug=product_slug)
    product_unique = str(product.get("product_unique") or "").strip()
    if not product_unique:
        raise ValueError(f"Product has no product_unique yet: {product_slug}")
    return product_unique


def _safe_int(value: Any) -> int:
    try:
        return int(value)
    except Exception:
        return 0


def _lead_bucket_for_state(state: str | None) -> str:
    normalized = (state or "").strip().upper()
    for bucket, states in _LEAD_CATEGORY_BUCKETS.items():
        if normalized in states:
            return bucket
    return "uncategorized"


def _normalize_conversion_state_input(state: str) -> tuple[str, str | None]:
    normalized = (state or "").strip().upper()
    if not normalized:
        raise ValueError("conversion_state is required")

    mapped = _STATE_INTENT_ALIASES.get(normalized)
    if mapped:
        return mapped, normalized
    return normalized, None


def _normalize_iso(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _dedupe_customers(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    dedupe_map: dict[str, dict[str, Any]] = {}

    for row in rows:
        customer_name = str(row.get("customer_name") or "").strip().lower()
        conversation_id = str(row.get("conversation_id") or "").strip()
        key = customer_name or conversation_id
        if not key:
            continue

        current = dedupe_map.get(key)
        if current is None:
            dedupe_map[key] = row
            continue

        current_ts = _normalize_iso(current.get("last_message_timestamp") or current.get("created_at"))
        candidate_ts = _normalize_iso(row.get("last_message_timestamp") or row.get("created_at"))
        if candidate_ts >= current_ts:
            dedupe_map[key] = row

    deduped = list(dedupe_map.values())
    deduped.sort(
        key=lambda item: _normalize_iso(item.get("last_message_timestamp") or item.get("created_at")),
        reverse=True,
    )

    diagnostics = {
        "dedupe_input_count": len(rows),
        "dedupe_output_count": len(deduped),
        "dedupe_removed_count": max(0, len(rows) - len(deduped)),
        "dedupe_key_policy": "customer_name (fallback conversation_id), keep newest by last_message_timestamp",
    }
    return deduped, diagnostics


def _conversation_snippet(conversation: list[dict[str, Any]], max_messages: int = 3) -> str:
    if not conversation:
        return ""
    tail = conversation[-max_messages:]
    lines: list[str] = []
    for msg in tail:
        direction = str(msg.get("type") or "?").upper()
        sender = str(msg.get("from") or "unknown")
        body = str(msg.get("message_text") or "").strip().replace("\n", " ")
        lines.append(f"[{direction}] {sender}: {body[:220]}")
    return "\n".join(lines)


def _read_context_folder(folder_path: str, max_files: int = 16, max_chars: int = 24000) -> dict[str, Any]:
    path = Path(folder_path).expanduser().resolve()
    if not path.exists() or not path.is_dir():
        raise ValueError(f"context_folder_path is not a valid directory: {folder_path}")

    allowed_suffixes = {".md", ".txt", ".json", ".yml", ".yaml", ".csv"}
    files = [p for p in path.rglob("*") if p.is_file() and p.suffix.lower() in allowed_suffixes]
    files = sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)[:max_files]

    gathered: list[str] = []
    total_chars = 0
    selected: list[str] = []

    for file in files:
        try:
            text = file.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue

        if not text.strip():
            continue

        relative = str(file.relative_to(path)).replace("\\", "/")
        block = f"\n--- {relative} ---\n{text[:4000]}"
        if total_chars + len(block) > max_chars:
            break

        gathered.append(block)
        selected.append(relative)
        total_chars += len(block)

    return {
        "path": str(path),
        "files_used": selected,
        "context": "\n".join(gathered).strip(),
    }


def _new_trace_id() -> str:
    return str(uuid.uuid4())


def _elapsed_ms(start: float) -> int:
    return int((time.perf_counter() - start) * 1000)


def _required_next_action(session: dict[str, Any]) -> str:
    phase = str(session.get("phase") or "")
    phase_to_action = {
        "session_started": "create_product",
        "product_created": "ack_prompt_pack",
        "prompt_pack_acknowledged": "submit_clarification_questions",
        "clarifications_questions_submitted": "set_clarification_mode",
        "clarifications_mode_set": "submit_clarification_answers",
        "clarifications_answers_submitted": "set_conversation_mode",
        "conversation_mode_set": "submit_conversation_transcript",
        "conversation_transcript_submitted": "extract_conversation_artifacts",
        "conversation_extracted": "submit_artifacts",
        "artifacts_completed": "submit_targeting",
        "targeting_keywords_submitted": "submit_subreddits",
        "targeting_subreddits_submitted": "approve_targeting",
        "targeting_approved": "check_readiness_or_start_campaign",
    }
    return phase_to_action.get(phase, "unknown")


@mcp.tool()
def sf_health() -> dict[str, Any]:
    """
    Preflight check for MCP -> onboard_api connectivity and auth context.

    Use this first in a new session to confirm:
    - backend is reachable
    - current MCP session auth state
    - whether a default client id is configured
    """
    settings = Settings.from_env()
    client = _client()
    try:
        onboard_ok = False
        onboard_health: dict[str, Any] | None = None
        onboard_error: str | None = None
        try:
            onboard_health = client.get("/")
            onboard_ok = True
        except Exception as exc:
            onboard_error = str(exc)

        authenticated = bool(_get_session_client_id())
        has_default = bool(settings.default_client_id)
        next_steps: list[str] = []
        if not authenticated and not has_default:
            next_steps = [
                "No client ID configured. New user? Run create_new_account(business_name, email) to create an account and get your client_id.",
                "Existing user? Run login_with_client_id(client_id) to authenticate this session.",
            ]
        elif not authenticated:
            next_steps = [
                "Run login_with_client_id(client_id) to activate the default client ID for this session.",
            ]

        return {
            "server": "signal-found-onboard",
            "base_url": settings.onboard_api_base_url,
            "session_client_id": _get_session_client_id(),
            "authenticated": authenticated,
            "default_client_id_configured": has_default,
            "onboard_api_ok": onboard_ok,
            "onboard_api": onboard_health,
            "onboard_api_error": onboard_error,
            **({"next_steps": next_steps} if next_steps else {}),
        }
    finally:
        client.close()


@mcp.tool()
def login_with_client_id(client_id: str) -> dict[str, Any]:
    """
    Authenticate MCP session to a Signal Found client account.

    Run this at the start of each session before business tools.
    Most tools require authenticated context and will use this session client id unless
    you pass an explicit `client_id` argument.
    """
    normalized_client_id = (client_id or "").strip()
    if not normalized_client_id:
        raise ValueError("client_id is required")

    client = _client()
    try:
        login_result = client.post(
            "/login",
            client_id=normalized_client_id,
            json={"client_id": normalized_client_id},
        )
        _set_session_client_id(normalized_client_id)

        balance = 0
        try:
            credits_result = client.get("/credits", client_id=normalized_client_id)
            balance = int(credits_result.get("credits_balance", 0) or 0)
        except Exception:
            pass

        return {
            "authenticated": True,
            "client_id": normalized_client_id,
            "credits_balance": balance,
            "setup_info": _build_setup_message(balance, normalized_client_id),
            "login": login_result,
        }
    finally:
        client.close()


@mcp.tool()
def current_client_context() -> dict[str, Any]:
    """Return the currently authenticated client context for this MCP server session."""
    settings = Settings.from_env()
    return {
        "session_client_id": _get_session_client_id(),
        "authenticated": bool(_get_session_client_id()),
        "default_client_id_configured": bool(settings.default_client_id),
    }


@mcp.tool()
def agent_quickstart() -> dict[str, Any]:
    """
    Zero-context onboarding playbook for agents using this MCP server.

    Returns the recommended call sequence, common guardrails, and recovery hints.
    """
    return {
        "goal": "Safely run onboarding and campaign launch with minimal context.",
        "new_user_path": [
            "1. create_new_account(business_name, email) — creates account + auto-logs in",
            "2. create_new_product(product_name, website_url) — initialize onboarding",
            "3. run_full_agentic_onboarding(...) — execute the full staged flow",
        ],
        "order": [
            {
                "step": 1,
                "tool": "sf_health",
                "why": "Verify backend connectivity and session auth state. If not authenticated and no client_id, see new_user_path.",
            },
            {
                "step": "1b (new users only)",
                "tool": "create_new_account",
                "why": "Create a new Signal Found account. Returns client_id and auto-authenticates this session. Skip if you already have a client_id.",
            },
            {
                "step": 2,
                "tool": "login_with_client_id",
                "why": "Set authenticated client context required by business tools. Skip if create_new_account was just called (it auto-logs in).",
            },
            {
                "step": 3,
                "tool": "create_new_product",
                "why": "Create product and obtain onboarding session context.",
            },
            {
                "step": 4,
                "tool": "get_onboarding_prompt_pack",
                "why": "Load schema/contract guidance before generating payloads.",
                "optional": True,
            },
            {
                "step": 5,
                "tool": "run_full_agentic_onboarding",
                "why": "Execute the full staged flow using validated payloads.",
            },
            {
                "step": 6,
                "tool": "get_onboarding_status",
                "why": "Inspect phase and next required action for recovery/partial runs.",
            },
            {
                "step": 7,
                "tool": "onboarding_campaign_decision",
                "why": "Check readiness and optionally start campaign.",
            },
        ],
        "guardrails": [
            "Always authenticate first with login_with_client_id unless every call passes client_id.",
            "run_full_agentic_onboarding requires exactly 3 clarifying_questions.",
            "If onboarding is partial, call get_onboarding_status and resume from next_required_action.",
            "If readiness includes credits_insufficient, use onboarding_campaign_decision checkout output.",
        ],
        "deprecated_removed": [
            "onboard_product",
            "onboard_api_request",
        ],
    }


@mcp.tool()
def logout_client_context() -> dict[str, Any]:
    """Clear the active authenticated client context for this MCP server session."""
    previous = _get_session_client_id()
    _set_session_client_id(None)
    return {
        "logged_out": True,
        "previous_client_id": previous,
    }


@mcp.tool()
def list_products(client_id: str | None = None) -> dict[str, Any]:
    """List all products for a client (slug, display_name, product_unique, folder_id)."""
    settings = Settings.from_env()
    resolved_client_id = _require_client_id(client_id, settings)

    client = _client()
    try:
        return client.get(f"/clients/{resolved_client_id}/products", client_id=resolved_client_id)
    finally:
        client.close()


@mcp.tool()
def get_product_tree(client_id: str | None = None) -> dict[str, Any]:
    """Get nested folders and products for a client, equivalent to frontend product tree."""
    settings = Settings.from_env()
    resolved_client_id = _require_client_id(client_id, settings)

    client = _client()
    try:
        return client.get(f"/clients/{resolved_client_id}/product-tree", client_id=resolved_client_id)
    finally:
        client.close()


@mcp.tool()
def create_new_account(
    business_name: str,
    email: str,
    source: str | None = None,
    auto_login: bool = True,
) -> dict[str, Any]:
    """
    Create a brand-new Signal Found client account for onboarding.

    Requires:
    - `business_name`
    - `email`

    Returns created `client_id` and (by default) logs this MCP session into it.

    Next step after success:
    - call `create_new_product`
    """
    payload: dict[str, Any] = {
        "business_name": business_name,
        "email": email,
    }
    if source:
        payload["source"] = source

    client = _client()
    try:
        created = client.post("/agent-onboarding/signup/agent", json=payload)
        client_id = str(created.get("client_id") or "").strip()
        login_result = None
        balance = 0
        if auto_login and client_id:
            login_result = client.post(
                "/login",
                client_id=client_id,
                json={"client_id": client_id},
            )
            _set_session_client_id(client_id)
            try:
                credits_result = client.get("/credits", client_id=client_id)
                balance = int(credits_result.get("credits_balance", 0) or 0)
            except Exception:
                pass

        return {
            "account": created,
            "authenticated_context_set": bool(auto_login and client_id),
            "credits_balance": balance,
            "setup_info": _build_setup_message(balance, client_id) if client_id else None,
            "login": login_result,
        }
    finally:
        client.close()


@mcp.tool()
def create_new_product(
    product_name: str,
    website_url: str,
    client_id: str | None = None,
    session_id: str | None = None,
    folder_id: str | None = None,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    """
    Create a product and initialize the agent onboarding session context.

    The response includes:
    - product creation result
    - context packet (existing artifacts + screenshot uri)
    - prompt pack + version metadata

    Prerequisite:
    - authenticated session via `login_with_client_id` (or provide `client_id` explicitly)

    Next step after success:
    - call `run_full_agentic_onboarding` (or run staged tools manually)
    """
    settings = Settings.from_env()
    resolved_client_id = _require_client_id(client_id, settings)

    client = _client()
    try:
        active_session_id = session_id
        if not active_session_id:
            started = client.post(
                f"/agent-onboarding/clients/{resolved_client_id}/agent-session/start",
                client_id=resolved_client_id,
                json={"actor": "mcp", "metadata": {"source": "create_new_product"}},
            )
            active_session_id = str(started.get("session_id") or "").strip()

        create_payload: dict[str, Any] = {
            "product_name": product_name,
            "website_url": website_url,
            "session_id": active_session_id,
        }
        if folder_id is not None:
            create_payload["folder_id"] = folder_id
        if idempotency_key is not None:
            create_payload["idempotency_key"] = idempotency_key

        created = client.post(
            f"/agent-onboarding/clients/{resolved_client_id}/agent-products",
            client_id=resolved_client_id,
            json=create_payload,
        )
        product_slug = str(created.get("product_slug") or "").strip()
        if not product_slug:
            raise OnboardApiError("Product created but product_slug missing in response")

        context = client.get(
            f"/agent-onboarding/clients/{resolved_client_id}/agent-products/{product_slug}/context",
            client_id=resolved_client_id,
        )
        prompt_pack = client.get(
            f"/agent-onboarding/clients/{resolved_client_id}/agent-products/{product_slug}/prompt-pack",
            client_id=resolved_client_id,
        )
        prompt_pack_version = client.get(
            f"/agent-onboarding/clients/{resolved_client_id}/agent-products/{product_slug}/prompt-pack/version",
            client_id=resolved_client_id,
        )

        return {
            "client_id": resolved_client_id,
            "session_id": active_session_id,
            "created": created,
            "context": context,
            "prompt_pack": prompt_pack,
            "prompt_pack_version": prompt_pack_version,
        }
    finally:
        client.close()


@mcp.tool()
def get_onboarding_prompt_pack(
    product_slug: str,
    client_id: str | None = None,
    artifact: str | None = None,
) -> dict[str, Any]:
    """
    Fetch server-curated prompt contracts that define required onboarding outputs.

    Use this when an agent needs exact formatting/expectations before generating:
    - clarifications
    - conversation transcript
    - market positioning
    - keywords/subreddits

    Use `artifact` for focused contracts:
    - clarifications
    - market_position
    - conversation
    - keywords
    - subreddits
    """
    settings = Settings.from_env()
    resolved_client_id = _require_client_id(client_id, settings)

    client = _client()
    try:
        if artifact:
            pack = client.get(
                f"/agent-onboarding/clients/{resolved_client_id}/agent-products/{product_slug}/prompt-pack/{artifact}",
                client_id=resolved_client_id,
            )
        else:
            pack = client.get(
                f"/agent-onboarding/clients/{resolved_client_id}/agent-products/{product_slug}/prompt-pack",
                client_id=resolved_client_id,
            )

        version = client.get(
            f"/agent-onboarding/clients/{resolved_client_id}/agent-products/{product_slug}/prompt-pack/version",
            client_id=resolved_client_id,
        )
        return {
            "client_id": resolved_client_id,
            "product_slug": product_slug,
            "artifact": artifact,
            "prompt_pack": pack,
            "version": version,
        }
    finally:
        client.close()


@mcp.tool()
def submit_onboarding_artifacts(
    product_slug: str,
    session_id: str,
    client_id: str | None = None,
    market_position: dict[str, Any] | None = None,
    conversion_notes: dict[str, Any] | None = None,
    funnels: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """
    Validate and persist core onboarding artifacts for a product/session.

    Prerequisites:
    - prompt pack must be acknowledged for `session_id`
    - payloads should match the artifact schemas below

    Common use:
    - called by `run_full_agentic_onboarding`
    - can also be used for staged/recovery runs

    Expected formats:
    - market_position: patch object with market position keys
    - conversion_notes: {'Product Name','Payment Terms/Plans','General Notes'}
    - funnels: [{'url','description','primary_use_case', optional 'qualification'}]
    """
    settings = Settings.from_env()
    resolved_client_id = _require_client_id(client_id, settings)
    trace_id = _new_trace_id()
    timings_ms: dict[str, int] = {}

    client = _client()
    try:
        status_started = time.perf_counter()
        session = client.get(
            f"/agent-onboarding/clients/{resolved_client_id}/agent-session/{session_id}",
            client_id=resolved_client_id,
        )
        timings_ms["get_session"] = _elapsed_ms(status_started)

        if not session.get("prompt_pack_ack"):
            return {
                "saved": False,
                "workflow_trace_id": trace_id,
                "timings_ms": timings_ms,
                "error_type": "precondition_failed",
                "retry_hint": "Acknowledge prompt pack first.",
                "message": "prompt_pack_ack missing; artifacts not saved.",
            }

        validation = client.post(
            f"/agent-onboarding/clients/{resolved_client_id}/agent-products/{product_slug}/artifacts/validate",
            client_id=resolved_client_id,
            params={"session_id": session_id},
            json={
                "market_position": market_position,
                "conversion_notes": conversion_notes,
                "funnels": funnels,
            },
        )
        timings_ms["validate_artifacts"] = _elapsed_ms(status_started)
        if not bool(validation.get("valid")):
            return {
                "saved": False,
                "workflow_trace_id": trace_id,
                "timings_ms": timings_ms,
                "validation": validation,
                "message": "Validation failed; artifacts not saved.",
            }

        writes: dict[str, Any] = {}
        if market_position is not None:
            write_started = time.perf_counter()
            writes["market_position"] = client.put(
                f"/agent-onboarding/clients/{resolved_client_id}/agent-products/{product_slug}/artifacts/market-position",
                client_id=resolved_client_id,
                params={"session_id": session_id},
                json=market_position,
            )
            timings_ms["write_market_position"] = _elapsed_ms(write_started)
        if conversion_notes is not None:
            write_started = time.perf_counter()
            writes["conversion_notes"] = client.put(
                f"/agent-onboarding/clients/{resolved_client_id}/agent-products/{product_slug}/artifacts/conversion-notes",
                client_id=resolved_client_id,
                params={"session_id": session_id},
                json=conversion_notes,
            )
            timings_ms["write_conversion_notes"] = _elapsed_ms(write_started)
        if funnels is not None:
            write_started = time.perf_counter()
            writes["funnels"] = client.put(
                f"/agent-onboarding/clients/{resolved_client_id}/agent-products/{product_slug}/artifacts/funnels",
                client_id=resolved_client_id,
                params={"session_id": session_id},
                json={"funnels": funnels},
            )
            timings_ms["write_funnels"] = _elapsed_ms(write_started)

        return {
            "saved": True,
            "client_id": resolved_client_id,
            "product_slug": product_slug,
            "session_id": session_id,
            "workflow_trace_id": trace_id,
            "timings_ms": timings_ms,
            "validation": validation,
            "writes": writes,
        }
    finally:
        client.close()


@mcp.tool()
def submit_agent_targeting(
    product_slug: str,
    session_id: str,
    client_id: str | None = None,
    keywords: list[str] | None = None,
    subreddit_groups: list[dict[str, Any]] | None = None,
    keyword_search_params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Persist targeting artifacts (keywords/subreddits) and return policy/preview.

    Prerequisites:
    - prompt pack must be acknowledged for `session_id`
    - artifacts should already be saved for best results

    Typical next step:
    - approve targeting (done automatically by `run_full_agentic_onboarding` when enabled)

    Formats:
    - keywords: list[str]
    - subreddit_groups: [{'subreddits': ['name1','name2']}]
    """
    settings = Settings.from_env()
    resolved_client_id = _require_client_id(client_id, settings)
    trace_id = _new_trace_id()
    timings_ms: dict[str, int] = {}

    client = _client()
    try:
        session_started = time.perf_counter()
        session = client.get(
            f"/agent-onboarding/clients/{resolved_client_id}/agent-session/{session_id}",
            client_id=resolved_client_id,
        )
        timings_ms["get_session"] = _elapsed_ms(session_started)
        if not session.get("prompt_pack_ack"):
            return {
                "client_id": resolved_client_id,
                "product_slug": product_slug,
                "session_id": session_id,
                "workflow_trace_id": trace_id,
                "timings_ms": timings_ms,
                "error_type": "precondition_failed",
                "retry_hint": "Acknowledge prompt pack and submit artifacts first.",
                "message": "prompt_pack_ack missing; targeting not submitted.",
            }

        actions: dict[str, Any] = {}

        if keywords is not None:
            keyword_started = time.perf_counter()
            payload: dict[str, Any] = {
                "session_id": session_id,
                "keywords": keywords,
            }
            if keyword_search_params is not None:
                payload["search_params"] = keyword_search_params
            actions["keywords"] = client.post(
                f"/agent-onboarding/clients/{resolved_client_id}/agent-products/{product_slug}/targeting/keywords",
                client_id=resolved_client_id,
                json=payload,
            )
            timings_ms["submit_keywords"] = _elapsed_ms(keyword_started)

        if subreddit_groups is not None:
            subreddit_started = time.perf_counter()
            actions["subreddits"] = client.post(
                f"/agent-onboarding/clients/{resolved_client_id}/agent-products/{product_slug}/targeting/subreddits",
                client_id=resolved_client_id,
                json={"session_id": session_id, "groups": subreddit_groups},
            )
            timings_ms["submit_subreddits"] = _elapsed_ms(subreddit_started)

        preview_started = time.perf_counter()
        preview = client.get(
            f"/agent-onboarding/clients/{resolved_client_id}/agent-products/{product_slug}/targeting/preview",
            client_id=resolved_client_id,
        )
        timings_ms["targeting_preview"] = _elapsed_ms(preview_started)
        policy_started = time.perf_counter()
        policy = client.get(
            f"/agent-onboarding/clients/{resolved_client_id}/agent-products/{product_slug}/targeting/policy",
            client_id=resolved_client_id,
        )
        timings_ms["targeting_policy"] = _elapsed_ms(policy_started)

        return {
            "client_id": resolved_client_id,
            "product_slug": product_slug,
            "session_id": session_id,
            "workflow_trace_id": trace_id,
            "timings_ms": timings_ms,
            "actions": actions,
            "preview": preview,
            "policy": policy,
        }
    finally:
        client.close()


@mcp.tool()
def onboarding_campaign_decision(
    product_slug: str,
    client_id: str | None = None,
    start_now: bool = False,
    outbound_per_day: int = 20,
    inbound_per_day: int = 0,
    checkout_plan: str = "starter",
    success_url: str = "https://signal-found.com/dashboard?payment=success",
    cancel_url: str = "https://signal-found.com/dashboard?payment=cancel",
) -> dict[str, Any]:
    """
    Evaluate readiness and optionally start campaign immediately.

    If not ready and blocked on credits, also returns a checkout link.

    Use this after onboarding reaches targeting approval.
    Set `start_now=true` to attempt campaign launch.
    """
    settings = Settings.from_env()
    resolved_client_id = _require_client_id(client_id, settings)
    trace_id = _new_trace_id()
    timings_ms: dict[str, int] = {}

    client = _client()
    try:
        readiness_started = time.perf_counter()
        readiness = client.get(
            f"/agent-onboarding/clients/{resolved_client_id}/agent-products/{product_slug}/readiness",
            client_id=resolved_client_id,
        )
        timings_ms["readiness"] = _elapsed_ms(readiness_started)

        start_result: dict[str, Any] | None = None
        checkout: dict[str, Any] | None = None

        if start_now:
            start_started = time.perf_counter()
            start_result = client.post(
                f"/agent-onboarding/clients/{resolved_client_id}/agent-products/{product_slug}/campaign/start-now",
                client_id=resolved_client_id,
                json={
                    "outbound_per_day": outbound_per_day,
                    "inbound_per_day": inbound_per_day,
                },
            )
            timings_ms["campaign_start"] = _elapsed_ms(start_started)

        blockers = set(str(b) for b in readiness.get("blockers", []))
        if "credits_insufficient" in blockers:
            checkout_started = time.perf_counter()
            checkout = client.post(
                f"/agent-onboarding/clients/{resolved_client_id}/agent-products/{product_slug}/checkout-link",
                client_id=resolved_client_id,
                json={
                    "plan": checkout_plan,
                    "success_url": success_url,
                    "cancel_url": cancel_url,
                },
            )
            timings_ms["checkout_link"] = _elapsed_ms(checkout_started)

        return {
            "client_id": resolved_client_id,
            "product_slug": product_slug,
            "workflow_trace_id": trace_id,
            "timings_ms": timings_ms,
            "readiness": readiness,
            "campaign_start": start_result,
            "checkout": checkout,
        }
    finally:
        client.close()


@mcp.tool()
def configure_product_strategy(
    product_slug: str,
    client_id: str | None = None,
    market_position_patch: dict[str, str] | None = None,
    conversion_notes: dict[str, str] | None = None,
    funnels: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """
    Read/update product strategy assets: market position, conversion notes, and funnels.
    """
    settings = Settings.from_env()
    resolved_client_id = _require_client_id(client_id, settings)

    client = _client()
    try:
        baseline = {
            "market_position": client.get(
                f"/clients/{resolved_client_id}/{product_slug}/market-position",
                client_id=resolved_client_id,
            ),
            "conversion_notes": client.get(
                f"/clients/{resolved_client_id}/{product_slug}/conversion-notes",
                client_id=resolved_client_id,
            ),
            "funnels": client.get(
                f"/clients/{resolved_client_id}/{product_slug}/funnels",
                client_id=resolved_client_id,
            ),
        }

        updates: dict[str, Any] = {}

        if market_position_patch:
            updates["market_position"] = client.put(
                f"/clients/{resolved_client_id}/{product_slug}/market-position",
                client_id=resolved_client_id,
                json=market_position_patch,
            )

        if conversion_notes:
            updates["conversion_notes"] = client.put(
                f"/clients/{resolved_client_id}/{product_slug}/conversion-notes",
                client_id=resolved_client_id,
                json=conversion_notes,
            )

        if funnels is not None:
            updates["funnels"] = client.put(
                f"/clients/{resolved_client_id}/{product_slug}/funnels",
                client_id=resolved_client_id,
                json={"funnels": funnels},
            )

        current = baseline if not updates else {
            "market_position": updates.get("market_position", baseline["market_position"]),
            "conversion_notes": updates.get("conversion_notes", baseline["conversion_notes"]),
            "funnels": updates.get("funnels", baseline["funnels"]),
        }

        return {
            "client_id": resolved_client_id,
            "product_slug": product_slug,
            "updated_sections": sorted(list(updates.keys())),
            "current": current,
        }
    finally:
        client.close()


@mcp.tool()
def configure_targeting(
    product_slug: str,
    client_id: str | None = None,
    setup_subreddits: bool = False,
    subreddit_groups: list[dict[str, Any]] | None = None,
    check_subreddits: bool = False,
    setup_keywords: bool = False,
    keywords: list[str] | None = None,
    keyword_search_params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Read/update subreddit targeting and keywords for a product.
    """
    settings = Settings.from_env()
    resolved_client_id = _require_client_id(client_id, settings)

    client = _client()
    try:
        actions: dict[str, Any] = {}

        if setup_subreddits:
            actions["subreddits_setup"] = client.post(
                f"/clients/{resolved_client_id}/{product_slug}/setup-targeting",
                client_id=resolved_client_id,
            )

        if subreddit_groups is not None:
            actions["subreddits_update"] = client.put(
                f"/clients/{resolved_client_id}/{product_slug}/targeting",
                client_id=resolved_client_id,
                json={"groups": subreddit_groups},
            )

        if check_subreddits:
            actions["subreddits_check"] = client.get(
                f"/clients/{resolved_client_id}/{product_slug}/targeting/check",
                client_id=resolved_client_id,
            )

        if setup_keywords:
            actions["keywords_setup"] = client.post(
                f"/clients/{resolved_client_id}/{product_slug}/setup-keywords",
                client_id=resolved_client_id,
            )

        if keywords is not None:
            payload: dict[str, Any] = {"keywords": keywords}
            if keyword_search_params is not None:
                payload["search_params"] = keyword_search_params
            actions["keywords_update"] = client.put(
                f"/clients/{resolved_client_id}/{product_slug}/keywords",
                client_id=resolved_client_id,
                json=payload,
            )

        current = {
            "targeting": client.get(
                f"/clients/{resolved_client_id}/{product_slug}/targeting",
                client_id=resolved_client_id,
            ),
            "keywords": client.get(
                f"/clients/{resolved_client_id}/{product_slug}/keywords",
                client_id=resolved_client_id,
            ),
        }

        return {
            "client_id": resolved_client_id,
            "product_slug": product_slug,
            "actions_applied": sorted(list(actions.keys())),
            "action_results": actions,
            "current": current,
        }
    finally:
        client.close()


@mcp.tool()
def modify_market_positioning(
    product_slug: str,
    client_id: str | None = None,
    market_position_patch: dict[str, str] | None = None,
) -> dict[str, Any]:
    """
    Read or update market positioning with explicit format guidance.

    How to use:
    - Call without `market_position_patch` to inspect current data before editing.
    - Send only keys you want to change (patch semantics).

    Expected patch keys (string values):
    - `one_line_pitch`
    - `icp`
    - `competitive_alternatives`
    - `uniqueness`
    - `value_proof`
    - `market_category`
    - `trends`
    - `additional_info`
    """
    settings = Settings.from_env()
    resolved_client_id = _require_client_id(client_id, settings)

    allowed_keys = {
        "one_line_pitch",
        "icp",
        "competitive_alternatives",
        "uniqueness",
        "value_proof",
        "market_category",
        "trends",
        "additional_info",
    }

    client = _client()
    try:
        current = client.get(
            f"/clients/{resolved_client_id}/{product_slug}/market-position",
            client_id=resolved_client_id,
        )

        if market_position_patch is None:
            return {
                "client_id": resolved_client_id,
                "product_slug": product_slug,
                "updated": False,
                "format_guide": {
                    "mode": "patch",
                    "allowed_keys": sorted(list(allowed_keys)),
                    "value_type": "string",
                },
                "current": current,
            }

        unknown_keys = sorted([k for k in market_position_patch.keys() if k not in allowed_keys])
        if unknown_keys:
            raise ValueError(f"Unknown market_position_patch keys: {unknown_keys}")

        normalized_patch: dict[str, str] = {}
        for key, value in market_position_patch.items():
            if value is None:
                continue
            normalized_patch[key] = str(value)

        updated = client.put(
            f"/clients/{resolved_client_id}/{product_slug}/market-position",
            client_id=resolved_client_id,
            json=normalized_patch,
        )

        return {
            "client_id": resolved_client_id,
            "product_slug": product_slug,
            "updated": True,
            "updated_keys": sorted(list(normalized_patch.keys())),
            "format_guide": {
                "mode": "patch",
                "allowed_keys": sorted(list(allowed_keys)),
                "value_type": "string",
            },
            "current": updated,
        }
    finally:
        client.close()


@mcp.tool()
def modify_conversion_notes(
    product_slug: str,
    client_id: str | None = None,
    conversion_notes: dict[str, str] | None = None,
) -> dict[str, Any]:
    """
    Read or update conversion notes with explicit required key names.

    How to use:
    - Call without `conversion_notes` to inspect current notes.
    - For updates, provide all three canonical keys for consistency.

    Expected keys (string values):
    - `Product Name`
    - `Payment Terms/Plans`
    - `General Notes`
    """
    settings = Settings.from_env()
    resolved_client_id = _require_client_id(client_id, settings)

    required_keys = {"Product Name", "Payment Terms/Plans", "General Notes"}

    client = _client()
    try:
        current = client.get(
            f"/clients/{resolved_client_id}/{product_slug}/conversion-notes",
            client_id=resolved_client_id,
        )

        if conversion_notes is None:
            return {
                "client_id": resolved_client_id,
                "product_slug": product_slug,
                "updated": False,
                "format_guide": {
                    "required_keys": sorted(list(required_keys)),
                    "value_type": "string",
                },
                "current": current,
            }

        missing = sorted([k for k in required_keys if k not in conversion_notes])
        if missing:
            raise ValueError(f"Missing conversion note keys: {missing}")

        normalized_notes = {key: str(value or "") for key, value in conversion_notes.items()}
        updated = client.put(
            f"/clients/{resolved_client_id}/{product_slug}/conversion-notes",
            client_id=resolved_client_id,
            json=normalized_notes,
        )

        return {
            "client_id": resolved_client_id,
            "product_slug": product_slug,
            "updated": True,
            "format_guide": {
                "required_keys": sorted(list(required_keys)),
                "value_type": "string",
            },
            "current": updated,
        }
    finally:
        client.close()


@mcp.tool()
def modify_funnels(
    product_slug: str,
    client_id: str | None = None,
    funnels: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """
    Read or replace funnels with explicit shape validation.

    How to use:
    - Call without `funnels` to inspect current funnel list.
    - Update is replace-style: provide the full intended funnels list.

    Expected item shape:
    - `url` (string)
    - `description` (string)
    - `primary_use_case` (string)
    - `qualification` (optional string)
    """
    settings = Settings.from_env()
    resolved_client_id = _require_client_id(client_id, settings)

    client = _client()
    try:
        current = client.get(
            f"/clients/{resolved_client_id}/{product_slug}/funnels",
            client_id=resolved_client_id,
        )

        if funnels is None:
            return {
                "client_id": resolved_client_id,
                "product_slug": product_slug,
                "updated": False,
                "format_guide": {
                    "update_mode": "replace_all",
                    "item_required_fields": ["url", "description", "primary_use_case"],
                    "item_optional_fields": ["qualification"],
                },
                "current": current,
            }

        normalized_funnels: list[dict[str, Any]] = []
        for index, item in enumerate(funnels):
            if not isinstance(item, dict):
                raise ValueError(f"funnels[{index}] must be an object")

            missing_fields = [
                field
                for field in ["url", "description", "primary_use_case"]
                if not str(item.get(field) or "").strip()
            ]
            if missing_fields:
                raise ValueError(f"funnels[{index}] missing required fields: {missing_fields}")

            normalized: dict[str, Any] = {
                "url": str(item.get("url") or "").strip(),
                "description": str(item.get("description") or "").strip(),
                "primary_use_case": str(item.get("primary_use_case") or "").strip(),
            }
            if item.get("qualification") is not None:
                normalized["qualification"] = str(item.get("qualification") or "").strip()
            normalized_funnels.append(normalized)

        updated = client.put(
            f"/clients/{resolved_client_id}/{product_slug}/funnels",
            client_id=resolved_client_id,
            json={"funnels": normalized_funnels},
        )

        return {
            "client_id": resolved_client_id,
            "product_slug": product_slug,
            "updated": True,
            "funnels_count": len(normalized_funnels),
            "format_guide": {
                "update_mode": "replace_all",
                "item_required_fields": ["url", "description", "primary_use_case"],
                "item_optional_fields": ["qualification"],
            },
            "current": updated,
        }
    finally:
        client.close()


@mcp.tool()
def modify_subreddits(
    product_slug: str,
    client_id: str | None = None,
    subreddit_groups: list[dict[str, Any]] | None = None,
    run_quality_check: bool = False,
) -> dict[str, Any]:
    """
    Read or update subreddit targeting groups.

    How to use:
    - Call without `subreddit_groups` to inspect current targeting.
    - Update expects `groups` format used by onboard API targeting endpoint.

    Expected `subreddit_groups` item shape:
    - `subreddits`: list of subreddit names (without `r/` prefix preferred).
    """
    settings = Settings.from_env()
    resolved_client_id = _require_client_id(client_id, settings)

    client = _client()
    try:
        actions: dict[str, Any] = {}

        if subreddit_groups is not None:
            for index, group in enumerate(subreddit_groups):
                if not isinstance(group, dict):
                    raise ValueError(f"subreddit_groups[{index}] must be an object")
                subs = group.get("subreddits")
                if not isinstance(subs, list):
                    raise ValueError(f"subreddit_groups[{index}].subreddits must be a list")

            actions["subreddits_update"] = client.put(
                f"/clients/{resolved_client_id}/{product_slug}/targeting",
                client_id=resolved_client_id,
                json={"groups": subreddit_groups},
            )

        if run_quality_check:
            actions["subreddits_check"] = client.get(
                f"/clients/{resolved_client_id}/{product_slug}/targeting/check",
                client_id=resolved_client_id,
            )

        current = client.get(
            f"/clients/{resolved_client_id}/{product_slug}/targeting",
            client_id=resolved_client_id,
        )

        return {
            "client_id": resolved_client_id,
            "product_slug": product_slug,
            "updated": subreddit_groups is not None,
            "actions_applied": sorted(list(actions.keys())),
            "format_guide": {
                "group_shape": {"subreddits": ["subreddit_one", "subreddit_two"]},
                "note": "Prefer subreddit names without r/ prefix",
            },
            "action_results": actions,
            "current": current,
        }
    finally:
        client.close()


@mcp.tool()
def modify_keywords(
    product_slug: str,
    client_id: str | None = None,
    keywords: list[str] | None = None,
    search_params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Read or update keyword targeting with optional search parameters.

    How to use:
    - Call without `keywords` to inspect current keywords config.
    - Update writes explicit keyword list and optional search params.

    Expected payload shape:
    - `keywords`: list[str]
    - `search_params` (optional):
      - `sort`: one of `relevance|hot|top|new|comments`
      - `time_filter`: one of `hour|day|week|month|year|all`
      - `per_keyword_limit`: int
    """
    settings = Settings.from_env()
    resolved_client_id = _require_client_id(client_id, settings)

    client = _client()
    try:
        actions: dict[str, Any] = {}

        if keywords is not None:
            normalized_keywords = [str(k).strip() for k in keywords if str(k).strip()]
            if not normalized_keywords:
                raise ValueError("keywords must include at least one non-empty string")

            payload: dict[str, Any] = {"keywords": normalized_keywords}
            if search_params is not None:
                payload["search_params"] = search_params

            actions["keywords_update"] = client.put(
                f"/clients/{resolved_client_id}/{product_slug}/keywords",
                client_id=resolved_client_id,
                json=payload,
            )

        current = client.get(
            f"/clients/{resolved_client_id}/{product_slug}/keywords",
            client_id=resolved_client_id,
        )

        return {
            "client_id": resolved_client_id,
            "product_slug": product_slug,
            "updated": keywords is not None,
            "actions_applied": sorted(list(actions.keys())),
            "format_guide": {
                "keywords_type": "list[str]",
                "search_params": {
                    "sort": "relevance|hot|top|new|comments",
                    "time_filter": "hour|day|week|month|year|all",
                    "per_keyword_limit": "int",
                },
            },
            "action_results": actions,
            "current": current,
        }
    finally:
        client.close()


@mcp.tool()
def list_campaigns(client_id: str | None = None) -> dict[str, Any]:
    """List campaigns for a client (active/inactive, product mapping, budget, and warning signals)."""
    settings = Settings.from_env()
    resolved_client_id = _require_client_id(client_id, settings)

    client = _client()
    try:
        return client.get("/campaigns", client_id=resolved_client_id)
    finally:
        client.close()


@mcp.tool()
def rebalance_resources_to_product(
    target_product_slug: str,
    transfer_outbound_per_day: int = 20,
    client_id: str | None = None,
    lookback_days: int = 14,
    minimum_worst_outbound: int = 10,
    target_campaign_nickname: str | None = None,
    target_start_date: str | None = None,
    target_end_date: str | None = None,
) -> dict[str, Any]:
    """
    Shift outbound/day budget from the worst active campaign to a target product campaign.
    """
    settings = Settings.from_env()
    resolved_client_id = _require_client_id(client_id, settings)

    if transfer_outbound_per_day <= 0:
        raise ValueError("transfer_outbound_per_day must be > 0")

    client = _client()
    try:
        products_resp = client.get(f"/clients/{resolved_client_id}/products", client_id=resolved_client_id)
        product_lookup = _products_by_slug(products_resp)
        target_product = product_lookup.get(target_product_slug)
        if not target_product:
            raise ValueError(f"Target product not found: {target_product_slug}")

        target_product_unique = str(target_product.get("product_unique") or "").strip()
        if not target_product_unique:
            raise ValueError(f"Target product has no product_unique yet: {target_product_slug}")

        campaigns_resp = client.get("/campaigns", client_id=resolved_client_id)
        campaigns = campaigns_resp.get("campaigns", [])
        active_campaigns = [c for c in campaigns if bool(c.get("active"))]
        if len(active_campaigns) < 1:
            raise ValueError("No active campaigns found for this client.")

        scored: list[tuple[float, dict[str, Any], dict[str, Any]]] = []
        for campaign in active_campaigns:
            product_unique = str(campaign.get("product_unique") or "").strip()
            if not product_unique or product_unique == target_product_unique:
                continue

            stats = client.get(
                f"/campaigns/{campaign['campaign_id']}/daily_stats",
                client_id=resolved_client_id,
                params={"limit": max(lookback_days, 1)},
            )
            rows = stats.get("stats", [])
            total_outbounds = sum(_safe_int(row.get("outbounds")) for row in rows)
            total_replies = sum(_safe_int(row.get("replies")) for row in rows)
            total_closes = sum(_safe_int(row.get("closes")) for row in rows)

            if total_outbounds <= 0:
                score = 0.0
            else:
                reply_rate = total_replies / total_outbounds
                close_rate = total_closes / total_outbounds
                score = (close_rate * 0.7) + (reply_rate * 0.3)

            scored.append((score, campaign, {"outbounds": total_outbounds, "replies": total_replies, "closes": total_closes}))

        if not scored:
            raise ValueError("No eligible source campaign found (non-target active campaign with stats).")

        scored.sort(key=lambda item: item[0])
        worst_score, worst_campaign, worst_metrics = scored[0]

        worst_outbound = _safe_int(worst_campaign.get("outbound_per_day"))
        max_reducible = max(0, worst_outbound - max(minimum_worst_outbound, 0))
        reduction = min(transfer_outbound_per_day, max_reducible)
        if reduction <= 0:
            raise ValueError(
                "Worst campaign has no transferable outbound capacity with current minimum_worst_outbound constraint."
            )

        worst_updated = client.put(
            f"/campaigns/{worst_campaign['campaign_id']}",
            client_id=resolved_client_id,
            json={"outbound_per_day": worst_outbound - reduction},
        )

        target_campaign = next(
            (c for c in active_campaigns if str(c.get("product_unique")) == target_product_unique),
            None,
        )

        if target_campaign is None:
            today = date.today()
            start = target_start_date or today.isoformat()
            end = target_end_date or (today + timedelta(days=30)).isoformat()
            created_target = client.post(
                "/campaigns",
                client_id=resolved_client_id,
                json={
                    "product_unique": target_product_unique,
                    "outbound_per_day": reduction,
                    "inbound_per_day": 0,
                    "outbound_model": str(worst_campaign.get("outbound_model") or "gpt-5-mini"),
                    "inbound_model": str(worst_campaign.get("inbound_model") or "gpt-5-mini"),
                    "start_date": start,
                    "end_date": end,
                    "nickname": target_campaign_nickname or f"Auto allocation for {target_product_slug}",
                },
            )
            target_updated = created_target
            action = "created"
        else:
            target_outbound = _safe_int(target_campaign.get("outbound_per_day"))
            target_updated = client.put(
                f"/campaigns/{target_campaign['campaign_id']}",
                client_id=resolved_client_id,
                json={"outbound_per_day": target_outbound + reduction},
            )
            action = "updated"

        return {
            "transfer_outbound_per_day": reduction,
            "worst_campaign_score": worst_score,
            "worst_campaign_metrics": worst_metrics,
            "worst_campaign_before": worst_campaign,
            "worst_campaign_after": worst_updated,
            "target_campaign_action": action,
            "target_campaign_after": target_updated,
        }
    finally:
        client.close()


@mcp.tool()
def portfolio_close_rate(client_id: str | None = None) -> dict[str, Any]:
    """Compute close rate across all products based on CRM conversion states."""
    settings = Settings.from_env()
    resolved_client_id = _require_client_id(client_id, settings)

    positive_states = {"CLOSE", "CLOSE_FOLLOWUP_SENT", "CONFIRMED"}
    negative_states = {"UNINTERESTED", "DISQUALIFIED"}

    client = _client()
    try:
        products_resp = client.get(f"/clients/{resolved_client_id}/products", client_id=resolved_client_id)
        products = products_resp.get("products", [])

        per_product: list[dict[str, Any]] = []
        total_positive = 0
        total_negative = 0

        for product in products:
            product_slug = str(product.get("product_slug") or "")
            product_unique = str(product.get("product_unique") or "")
            if not product_unique:
                continue

            rows = client.get(
                "/crm/customers",
                params=[
                    ("client-id", resolved_client_id),
                    ("product_unique", product_unique),
                    ("conversion_state", "CLOSE"),
                    ("conversion_state", "CONFIRMED"),
                    ("conversion_state", "CLOSE_FOLLOWUP_SENT"),
                    ("conversion_state", "UNINTERESTED"),
                    ("conversion_state", "DISQUALIFIED"),
                ],
            )

            positive = 0
            negative = 0
            for row in rows:
                state = str(row.get("conversion_state") or "").upper()
                if state in positive_states:
                    positive += 1
                elif state in negative_states:
                    negative += 1

            denom = positive + negative
            close_rate = (positive / denom) if denom > 0 else None

            per_product.append(
                {
                    "product_slug": product_slug,
                    "product_unique": product_unique,
                    "won_count": positive,
                    "lost_count": negative,
                    "decided_total": denom,
                    "close_rate": close_rate,
                }
            )

            total_positive += positive
            total_negative += negative

        portfolio_denom = total_positive + total_negative
        portfolio_rate = (total_positive / portfolio_denom) if portfolio_denom > 0 else None

        return {
            "client_id": resolved_client_id,
            "portfolio": {
                "won_count": total_positive,
                "lost_count": total_negative,
                "decided_total": portfolio_denom,
                "close_rate": portfolio_rate,
            },
            "products": per_product,
        }
    finally:
        client.close()


@mcp.tool()
def compare_confirmed_vs_uninterested(
    product_slug: str,
    client_id: str | None = None,
    confirmed_count: int = 5,
    uninterested_count: int = 5,
) -> dict[str, Any]:
    """
    Pull sample confirmed and uninterested conversations and return side-by-side message snippets.
    """
    settings = Settings.from_env()
    resolved_client_id = _require_client_id(client_id, settings)

    confirmed_count = max(1, min(confirmed_count, 20))
    uninterested_count = max(1, min(uninterested_count, 20))

    client = _client()
    try:
        products_resp = client.get(f"/clients/{resolved_client_id}/products", client_id=resolved_client_id)
        product_lookup = _products_by_slug(products_resp)
        product = product_lookup.get(product_slug)
        if not product:
            raise ValueError(f"Unknown product_slug: {product_slug}")

        product_unique = str(product.get("product_unique") or "")
        if not product_unique:
            raise ValueError(f"Product has no product_unique: {product_slug}")

        confirmed_rows = client.get(
            "/crm/customers",
            params=[
                ("client-id", resolved_client_id),
                ("product_unique", product_unique),
                ("limit", max(confirmed_count * 4, 30)),
                ("conversion_state", "CONFIRMED"),
                ("conversion_state", "CLOSE"),
                ("conversion_state", "CLOSE_FOLLOWUP_SENT"),
            ],
        )
        uninterested_rows = client.get(
            "/crm/customers",
            params=[
                ("client-id", resolved_client_id),
                ("product_unique", product_unique),
                ("limit", max(uninterested_count * 4, 30)),
                ("conversion_state", "UNINTERESTED"),
                ("conversion_state", "DISQUALIFIED"),
            ],
        )

        def build_samples(rows: list[dict[str, Any]], max_items: int) -> list[dict[str, Any]]:
            results: list[dict[str, Any]] = []
            for row in rows:
                conversation_id = str(row.get("conversation_id") or "").strip()
                if not conversation_id:
                    continue

                conversation = client.get(
                    f"/crm/conversation/{conversation_id}",
                    params={"product_unique": product_unique},
                )
                results.append(
                    {
                        "customer_name": row.get("customer_name"),
                        "conversion_state": row.get("conversion_state"),
                        "conversation_id": conversation_id,
                        "snippet": _conversation_snippet(conversation, max_messages=3),
                    }
                )
                if len(results) >= max_items:
                    break
            return results

        confirmed_samples = build_samples(confirmed_rows, confirmed_count)
        uninterested_samples = build_samples(uninterested_rows, uninterested_count)

        return {
            "client_id": resolved_client_id,
            "product_slug": product_slug,
            "product_unique": product_unique,
            "confirmed_samples": confirmed_samples,
            "uninterested_samples": uninterested_samples,
        }
    finally:
        client.close()


@mcp.tool()
def crm_workbench(
    product_slug: str,
    action: str,
    client_id: str | None = None,
    username: str | None = None,
    customer_name: str | None = None,
    conversation_id: str | None = None,
    conversion_state: str | None = None,
    blacklist_state: bool | None = None,
    notes: str | None = None,
    limit: int = 100,
    awaiting_response: bool | None = None,
    blacklisted: bool | None = None,
    conversion_states: list[str] | None = None,
) -> dict[str, Any]:
    """
    CRM read/write operations (no reply generation and no DM sending).

    Use `action` to select operation; each action has its own required fields.
    This tool intentionally excludes outbound messaging behaviors.

    Supported actions:
    - list_customers
    - prospect_stats
    - get_conversation_by_id
    - update_conversion_state
    - update_blacklist
    - get_notes
    - update_notes
    """
    settings = Settings.from_env()
    resolved_client_id = _require_client_id(client_id, settings)
    normalized_action = action.strip().lower()

    client = _client()
    try:
        product_unique = _resolve_product_unique(
            client,
            client_id=resolved_client_id,
            product_slug=product_slug,
        )

        if normalized_action == "list_customers":
            params: list[tuple[str, Any]] = [
                ("client-id", resolved_client_id),
                ("product_unique", product_unique),
                ("limit", max(0, limit)),
            ]
            if username:
                params.append(("username", username))
            if awaiting_response is not None:
                params.append(("awaiting_response", awaiting_response))
            if blacklisted is not None:
                params.append(("blacklisted", blacklisted))
            for state in conversion_states or []:
                params.append(("conversion_state", state))

            customers = client.get("/crm/customers", params=params)
            return {
                "action": normalized_action,
                "client_id": resolved_client_id,
                "product_slug": product_slug,
                "product_unique": product_unique,
                "count": len(customers),
                "customers": customers,
            }

        if normalized_action == "prospect_stats":
            params = {
                "client-id": resolved_client_id,
                "product_unique": product_unique,
                "username": username or "",
            }
            stats = client.get("/crm/prospect_stats", params=params)
            return {
                "action": normalized_action,
                "client_id": resolved_client_id,
                "product_slug": product_slug,
                "product_unique": product_unique,
                "stats": stats,
            }

        if normalized_action == "get_conversation_by_id":
            if not conversation_id:
                raise ValueError("conversation_id is required for get_conversation_by_id")
            conversation = client.get(
                f"/crm/conversation/{conversation_id}",
                params={"product_unique": product_unique},
            )
            return {
                "action": normalized_action,
                "conversation_id": conversation_id,
                "messages": conversation,
            }

        if normalized_action == "update_conversion_state":
            if not customer_name or not conversion_state:
                raise ValueError("customer_name and conversion_state are required for update_conversion_state")
            result = client.post(
                "/crm/categorize",
                json={
                    "client_id": resolved_client_id,
                    "customer_name": customer_name,
                    "conversion_state": conversion_state,
                    "product_unique": product_unique,
                },
            )
            return {
                "action": normalized_action,
                "result": result,
            }

        if normalized_action == "update_blacklist":
            if not customer_name or not username or blacklist_state is None:
                raise ValueError("customer_name, username, and blacklist_state are required for update_blacklist")
            result = client.post(
                "/crm/blacklist_customer",
                json={
                    "client_id": resolved_client_id,
                    "customer_name": customer_name,
                    "username": username,
                    "blacklist_state": blacklist_state,
                    "product_unique": product_unique,
                },
            )
            return {
                "action": normalized_action,
                "result": result,
            }

        if normalized_action == "get_notes":
            if not conversation_id:
                raise ValueError("conversation_id is required for get_notes")
            result = client.get(
                f"/crm/notes/{conversation_id}",
                params={"product_unique": product_unique},
            )
            return {
                "action": normalized_action,
                "result": result,
            }

        if normalized_action == "update_notes":
            if not conversation_id:
                raise ValueError("conversation_id is required for update_notes")
            result = client.put(
                "/crm/notes",
                json={
                    "conversation_id": conversation_id,
                    "product_unique": product_unique,
                    "notes": notes,
                },
            )
            return {
                "action": normalized_action,
                "result": result,
            }

        raise ValueError(
            "Unsupported action. Use one of: list_customers, prospect_stats, get_conversation_by_id, "
            "update_conversion_state, update_blacklist, get_notes, update_notes"
        )
    finally:
        client.close()


@mcp.tool()
def list_conversion_states(client_id: str | None = None) -> dict[str, Any]:
    """Return canonical CRM conversion states available for recategorization."""
    settings = Settings.from_env()
    _require_client_id(client_id, settings)

    return {
        "states": sorted(list(_ALLOWED_CONVERSION_STATES)),
        "terminal_states": sorted(list(_TERMINAL_CONVERSION_STATES)),
        "non_terminal_states": sorted(list(_NON_TERMINAL_CONVERSION_STATES)),
        "state_intent_aliases": dict(sorted(_STATE_INTENT_ALIASES.items(), key=lambda kv: kv[0])),
        "lead_category_buckets": {
            bucket: sorted(list(states))
            for bucket, states in _LEAD_CATEGORY_BUCKETS.items()
        },
        "note": (
            "Terminal states are CONFIRMED/DISQUALIFIED/UNINTERESTED/UNKNOWN/CLOSE. "
            "For neutral or continue-conversation intent, use REPLY (or alias NEUTRAL/CONTINUE). "
            "CLOSE_FOLLOWUP_SENT is an internal state that may be presented as CLOSE in some views."
        ),
    }


@mcp.tool()
def crm_customers_by_state(
    product_slug: str,
    states: list[str] | None = None,
    client_id: str | None = None,
    limit: int = 500,
    include_conversations: bool = False,
) -> dict[str, Any]:
    """List CRM customer data filtered by conversion states with explicit dedupe diagnostics."""
    settings = Settings.from_env()
    resolved_client_id = _require_client_id(client_id, settings)
    bounded_limit = max(1, min(limit, 2000))

    client = _client()
    try:
        product_unique = _resolve_product_unique(
            client,
            client_id=resolved_client_id,
            product_slug=product_slug,
        )

        params: list[tuple[str, Any]] = [
            ("client-id", resolved_client_id),
            ("product_unique", product_unique),
            ("limit", bounded_limit),
        ]

        normalized_states: list[str] = []
        for state in states or []:
            normalized = (state or "").strip().upper()
            if not normalized:
                continue
            if normalized not in _ALLOWED_CONVERSION_STATES:
                raise ValueError(f"Unknown conversion state: {normalized}")
            normalized_states.append(normalized)

        for state in normalized_states:
            params.append(("conversion_state", state))

        rows = client.get("/crm/customers", params=params)
        deduped, diagnostics = _dedupe_customers(rows if isinstance(rows, list) else [])

        conversation_map: dict[str, list[dict[str, Any]]] = {}
        if include_conversations:
            unique_conversation_ids = {
                str(row.get("conversation_id") or "").strip()
                for row in deduped
                if str(row.get("conversation_id") or "").strip()
            }
            for conversation_id in sorted(list(unique_conversation_ids)):
                try:
                    messages = client.get(
                        f"/crm/conversation/{conversation_id}",
                        params={"product_unique": product_unique},
                    )
                    conversation_map[conversation_id] = messages if isinstance(messages, list) else []
                except Exception:
                    conversation_map[conversation_id] = []

        return {
            "client_id": resolved_client_id,
            "product_slug": product_slug,
            "product_unique": product_unique,
            "states_filter": normalized_states,
            "count": len(deduped),
            "customers": [
                {
                    **row,
                    "lead_category": _lead_bucket_for_state(str(row.get("conversion_state") or "")),
                }
                for row in deduped
            ],
            "conversations": conversation_map if include_conversations else None,
            "dedupe": diagnostics,
        }
    finally:
        client.close()


@mcp.tool()
def crm_state_stats(
    product_slug: str,
    client_id: str | None = None,
    limit: int = 2000,
) -> dict[str, Any]:
    """Return CRM state statistics (unique deduped customer counts by conversion_state)."""
    dataset = crm_customers_by_state(
        product_slug=product_slug,
        states=None,
        client_id=client_id,
        limit=limit,
        include_conversations=False,
    )

    counts: dict[str, int] = {}
    bucket_counts: dict[str, int] = {
        "warm": 0,
        "bad": 0,
        "in_progress": 0,
        "unknown": 0,
        "uncategorized": 0,
    }
    for row in dataset.get("customers", []):
        state = str(row.get("conversion_state") or "UNCATEGORIZED").strip().upper() or "UNCATEGORIZED"
        counts[state] = counts.get(state, 0) + 1
        bucket = _lead_bucket_for_state(state)
        bucket_counts[bucket] = bucket_counts.get(bucket, 0) + 1

    return {
        "client_id": dataset.get("client_id"),
        "product_slug": dataset.get("product_slug"),
        "product_unique": dataset.get("product_unique"),
        "total_customers": dataset.get("count", 0),
        "state_counts": dict(sorted(counts.items(), key=lambda kv: kv[0])),
        "lead_category_counts": bucket_counts,
        "lead_category_buckets": {
            bucket: sorted(list(states))
            for bucket, states in _LEAD_CATEGORY_BUCKETS.items()
        },
        "dedupe": dataset.get("dedupe"),
    }


@mcp.tool()
def get_deduped_crm_by_category(
    product_slug: str,
    category: str,
    client_id: str | None = None,
    limit: int = 500,
    include_conversations: bool = True,
) -> dict[str, Any]:
    """Retrieve deduped CRM records by single state or lead-category bucket."""
    normalized_category = (category or "").strip().upper()
    if not normalized_category:
        raise ValueError("category is required")

    bucket_key = normalized_category.lower()
    if bucket_key in _LEAD_CATEGORY_BUCKETS:
        target_states = sorted(list(_LEAD_CATEGORY_BUCKETS[bucket_key]))
        category_type = bucket_key
    else:
        if normalized_category not in _ALLOWED_CONVERSION_STATES:
            raise ValueError(
                "category must be one of conversion states "
                f"{sorted(list(_ALLOWED_CONVERSION_STATES))} or bucket names {sorted(list(_LEAD_CATEGORY_BUCKETS.keys()))}"
            )
        target_states = [normalized_category]
        category_type = _lead_bucket_for_state(normalized_category)

    result = crm_customers_by_state(
        product_slug=product_slug,
        states=target_states,
        client_id=client_id,
        limit=limit,
        include_conversations=include_conversations,
    )
    result["category_input"] = category
    result["category_type"] = category_type
    result["resolved_states"] = target_states
    return result


@mcp.tool()
def get_conversation_by_id(
    product_slug: str,
    conversation_id: str,
    client_id: str | None = None,
) -> dict[str, Any]:
    """Get conversation messages for a conversation ID inside a product scope."""
    result = crm_workbench(
        product_slug=product_slug,
        action="get_conversation_by_id",
        client_id=client_id,
        conversation_id=conversation_id,
    )
    messages = result.get("messages")
    return {
        "client_id": result.get("client_id"),
        "product_slug": result.get("product_slug"),
        "product_unique": result.get("product_unique"),
        "conversation_id": result.get("conversation_id"),
        "messages_count": len(messages) if isinstance(messages, list) else 0,
        "messages": messages,
    }


@mcp.tool()
def get_conversation_notes(
    product_slug: str,
    conversation_id: str,
    client_id: str | None = None,
) -> dict[str, Any]:
    """Get notes for a CRM conversation in a product scope."""
    result = crm_workbench(
        product_slug=product_slug,
        action="get_notes",
        client_id=client_id,
        conversation_id=conversation_id,
    )
    return {
        "client_id": result.get("client_id"),
        "product_slug": result.get("product_slug"),
        "product_unique": result.get("product_unique"),
        "conversation_id": conversation_id,
        "result": result.get("result"),
    }


@mcp.tool()
def update_conversation_notes(
    product_slug: str,
    conversation_id: str,
    notes: str | None,
    client_id: str | None = None,
) -> dict[str, Any]:
    """Update notes for a CRM conversation in a product scope."""
    result = crm_workbench(
        product_slug=product_slug,
        action="update_notes",
        client_id=client_id,
        conversation_id=conversation_id,
        notes=notes,
    )
    return {
        "client_id": result.get("client_id"),
        "product_slug": result.get("product_slug"),
        "product_unique": result.get("product_unique"),
        "conversation_id": conversation_id,
        "result": result.get("result"),
    }


@mcp.tool()
def change_crm_state(
    product_slug: str,
    customer_name: str,
    category: str,
    client_id: str | None = None,
) -> dict[str, Any]:
    """Change a customer's CRM conversion state (category) for a product."""
    normalized_state, alias_source = _normalize_conversion_state_input(category)
    if normalized_state not in _ALLOWED_CONVERSION_STATES:
        raise ValueError(f"category must be one of: {sorted(list(_ALLOWED_CONVERSION_STATES))}")

    result = crm_workbench(
        product_slug=product_slug,
        action="update_conversion_state",
        client_id=client_id,
        customer_name=customer_name,
        conversion_state=normalized_state,
    )
    return {
        "client_id": result.get("client_id"),
        "product_slug": result.get("product_slug"),
        "product_unique": result.get("product_unique"),
        "input_conversion_state": (category or "").strip(),
        "resolved_conversion_state": normalized_state,
        "resolved_from_alias": alias_source,
        "is_terminal_state": normalized_state in _TERMINAL_CONVERSION_STATES,
        "result": result.get("result"),
    }


@mcp.tool()
def upsert_conversation_note(
    product_slug: str,
    conversation_id: str,
    note: str | None,
    client_id: str | None = None,
) -> dict[str, Any]:
    """Create/update a conversation note for a product scope conversation."""
    return update_conversation_notes(
        product_slug=product_slug,
        conversation_id=conversation_id,
        notes=note,
        client_id=client_id,
    )


@mcp.tool()
def voice_of_customer_report(
    product_slug: str,
    client_id: str | None = None,
    lookback_days: int = 30,
    sample_size: int = 5,
    include_ai_insights: bool = False,
) -> dict[str, Any]:
    """
    Produce a voice-of-customer report combining outcome metrics and confirmed/uninterested conversation samples.
    """
    settings = Settings.from_env()
    resolved_client_id = _require_client_id(client_id, settings)
    bounded_days = max(1, min(lookback_days, 365))
    bounded_sample = max(1, min(sample_size, 20))

    client = _client()
    try:
        product_unique = _resolve_product_unique(
            client,
            client_id=resolved_client_id,
            product_slug=product_slug,
        )

        outcomes = client.get(
            f"/crm/subreddit-outcomes/{product_unique}",
            params={"client-id": resolved_client_id, "days": bounded_days},
        )

        comparison = compare_confirmed_vs_uninterested(
            product_slug=product_slug,
            client_id=resolved_client_id,
            confirmed_count=bounded_sample,
            uninterested_count=bounded_sample,
        )

        prospect_stats = client.get(
            "/crm/prospect_stats",
            params={
                "client-id": resolved_client_id,
                "product_unique": product_unique,
                "username": "",
            },
        )

        ai_insights: dict[str, Any] | None = None
        ai_insights_error: str | None = None
        if include_ai_insights:
            try:
                ai_insights = client.post(
                    "/crm/ai-insights",
                    json={
                        "client_id": resolved_client_id,
                        "product_unique": product_unique,
                    },
                )
            except Exception as exc:
                ai_insights_error = str(exc)

        return {
            "client_id": resolved_client_id,
            "product_slug": product_slug,
            "product_unique": product_unique,
            "lookback_days": bounded_days,
            "outcomes": outcomes,
            "prospect_stats": prospect_stats,
            "confirmed_vs_uninterested": comparison,
            "ai_insights": ai_insights,
            "ai_insights_error": ai_insights_error,
        }
    finally:
        client.close()


@mcp.tool()
def billing_and_credits(
    client_id: str | None = None,
    history_limit: int = 50,
    include_checkout_preview: bool = False,
    checkout_plan: str = "starter",
    success_url: str | None = None,
    cancel_url: str | None = None,
) -> dict[str, Any]:
    """
    Get billing + credits overview, and optionally create a checkout session preview/action.
    """
    settings = Settings.from_env()
    resolved_client_id = _require_client_id(client_id, settings)
    bounded_history_limit = max(1, min(history_limit, 100))

    client = _client()
    try:
        credits = client.get("/credits", client_id=resolved_client_id)
        credits_history = client.get(
            "/credits/history",
            client_id=resolved_client_id,
            params={"limit": bounded_history_limit},
        )
        subscriptions = client.get(
            f"/clients/{resolved_client_id}/subscriptions",
            client_id=resolved_client_id,
        )

        checkout: dict[str, Any] | None = None
        checkout_error: str | None = None
        if include_checkout_preview:
            if not success_url or not cancel_url:
                checkout_error = "success_url and cancel_url are required when include_checkout_preview=true"
            else:
                try:
                    checkout = client.post(
                        "/credits/checkout",
                        client_id=resolved_client_id,
                        json={
                            "plan": checkout_plan,
                            "success_url": success_url,
                            "cancel_url": cancel_url,
                        },
                    )
                except Exception as exc:
                    checkout_error = str(exc)

        return {
            "client_id": resolved_client_id,
            "credits": credits,
            "history": credits_history,
            "subscriptions": subscriptions,
            "checkout": checkout,
            "checkout_error": checkout_error,
        }
    finally:
        client.close()


@mcp.tool()
def sales_control_tower(client_id: str | None = None) -> dict[str, Any]:
    """
    Command-center summary for a client: products, campaign health, close-rate, and recommendations.

    Best first operational tool after login when the user asks for "what should we do next?"
    """
    settings = Settings.from_env()
    resolved_client_id = _require_client_id(client_id, settings)

    client = _client()
    try:
        products_resp = client.get(f"/clients/{resolved_client_id}/products", client_id=resolved_client_id)
        campaigns_resp = client.get("/campaigns", client_id=resolved_client_id)
        close_rate_resp = portfolio_close_rate(client_id=resolved_client_id)

        products = products_resp.get("products", [])
        campaigns = campaigns_resp.get("campaigns", [])
        active_campaigns = [c for c in campaigns if c.get("active")]
        product_uniques_with_active_campaign = {str(c.get("product_unique") or "") for c in active_campaigns}

        products_without_active_campaign = [
            p for p in products
            if str(p.get("product_unique") or "") and str(p.get("product_unique") or "") not in product_uniques_with_active_campaign
        ]

        dm_warnings = [
            {
                "campaign_id": c.get("campaign_id"),
                "product_unique": c.get("product_unique"),
                "warning_message": c.get("warning_message"),
                "problem_sockets": c.get("problem_sockets", []),
            }
            for c in active_campaigns
            if c.get("dm_limitation_warning")
        ]

        recommendations: list[str] = []
        if products_without_active_campaign:
            recommendations.append("Some products have no active campaign; consider launching or reallocating capacity.")
        if dm_warnings:
            recommendations.append("At least one active campaign has DM limitation warnings; rebalance away from affected sockets.")

        portfolio_close_rate_value = close_rate_resp.get("portfolio", {}).get("close_rate")
        if portfolio_close_rate_value is not None and portfolio_close_rate_value < 0.1:
            recommendations.append("Portfolio close rate is low; compare CONFIRMED vs UNINTERESTED conversations to refine targeting.")

        if not recommendations:
            recommendations.append("System looks healthy. Next step: increase outbound on best-performing product by 10-20%.")

        return {
            "client_id": resolved_client_id,
            "products_total": len(products),
            "campaigns_total": len(campaigns),
            "active_campaigns_total": len(active_campaigns),
            "products_without_active_campaign": products_without_active_campaign,
            "dm_warnings": dm_warnings,
            "close_rate": close_rate_resp,
            "recommendations": recommendations,
        }
    finally:
        client.close()


@mcp.tool()
def get_onboarding_status(
    product_slug: str,
    session_id: str,
    client_id: str | None = None,
) -> dict[str, Any]:
    """
    Canonical onboarding progress view for a product/session.

    Returns:
    - current phase
    - next required action
    - prompt-pack ack state
    - targeting approval state
    - missing artifacts + readiness blockers

    Use this for recovery if a previous onboarding run was partial or failed.
    """
    settings = Settings.from_env()
    resolved_client_id = _require_client_id(client_id, settings)
    trace_id = _new_trace_id()
    timings_ms: dict[str, int] = {}

    client = _client()
    try:
        session_started = time.perf_counter()
        session = client.get(
            f"/agent-onboarding/clients/{resolved_client_id}/agent-session/{session_id}",
            client_id=resolved_client_id,
        )
        timings_ms["get_session"] = _elapsed_ms(session_started)

        readiness_started = time.perf_counter()
        readiness = client.get(
            f"/agent-onboarding/clients/{resolved_client_id}/agent-products/{product_slug}/readiness",
            client_id=resolved_client_id,
        )
        timings_ms["readiness"] = _elapsed_ms(readiness_started)

        artifacts = readiness.get("artifacts", {}) if isinstance(readiness, dict) else {}
        missing_artifacts = [
            name
            for name, exists in artifacts.items()
            if not bool(exists)
        ]

        return {
            "client_id": resolved_client_id,
            "product_slug": product_slug,
            "session_id": session_id,
            "workflow_trace_id": trace_id,
            "timings_ms": timings_ms,
            "status": {
                "phase": session.get("phase"),
                "next_required_action": _required_next_action(session),
                "prompt_pack_ack_state": bool(session.get("prompt_pack_ack")),
                "targeting_approved": bool(session.get("targeting_approved")),
                "missing_artifacts": missing_artifacts,
                "readiness_blockers": readiness.get("blockers", []),
            },
            "session": session,
            "readiness": readiness,
        }
    finally:
        client.close()


@mcp.tool()
def run_full_agentic_onboarding(
    product_slug: str,
    session_id: str,
    clarifying_questions: list[dict[str, str]],
    clarifying_answers: list[dict[str, str]],
    conversation_transcript: list[dict[str, Any]],
    market_position: dict[str, Any],
    conversion_notes: dict[str, Any],
    funnels: list[dict[str, Any]],
    keywords: list[str],
    subreddit_groups: list[dict[str, Any]],
    client_id: str | None = None,
    clarification_mode: str = "agent_answers_questions",
    conversation_mode: str = "agent_answers_conversation",
    approve_targeting: bool = True,
    agent_id: str = "sf-mcp-orchestrator",
    keyword_search_params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Execute the full staged onboarding flow end-to-end in one call.

    Intended for zero-context agents that want a safe default path.
    This tool orchestrates:
    - prompt-pack version + ack
    - clarifications (questions/mode/answers)
    - conversation (mode/transcript/extract)
    - artifact validation + writes
    - targeting submit (+ optional approve)
    - readiness snapshot

    Requirements:
    - `clarifying_questions` must have exactly 3 items
    - pass valid schema payloads for artifacts/targeting

    On partial failure, returns `failed_stage`, `retry_hint`, and completed stages.
    """
    settings = Settings.from_env()
    resolved_client_id = _require_client_id(client_id, settings)
    trace_id = _new_trace_id()
    timings_ms: dict[str, int] = {}
    completed_stages: list[str] = []

    if len(clarifying_questions) != 3:
        raise ValueError("clarifying_questions must contain exactly 3 items")

    client = _client()
    try:
        try:
            stage_started = time.perf_counter()
            prompt_version = client.get(
                f"/agent-onboarding/clients/{resolved_client_id}/agent-products/{product_slug}/prompt-pack/version",
                client_id=resolved_client_id,
            )
            timings_ms["prompt_pack_version"] = _elapsed_ms(stage_started)
            completed_stages.append("prompt_pack_version")

            stage_started = time.perf_counter()
            client.post(
                f"/agent-onboarding/clients/{resolved_client_id}/agent-products/{product_slug}/prompt-pack/ack",
                client_id=resolved_client_id,
                json={
                    "session_id": session_id,
                    "prompt_pack_version": prompt_version.get("prompt_pack_version"),
                    "prompt_pack_hash": prompt_version.get("prompt_pack_hash"),
                    "agent_id": agent_id,
                },
            )
            timings_ms["prompt_pack_ack"] = _elapsed_ms(stage_started)
            completed_stages.append("prompt_pack_ack")

            stage_started = time.perf_counter()
            client.post(
                f"/agent-onboarding/clients/{resolved_client_id}/agent-products/{product_slug}/clarifications/questions",
                client_id=resolved_client_id,
                json={"session_id": session_id, "questions": clarifying_questions},
            )
            timings_ms["clarifications_questions"] = _elapsed_ms(stage_started)
            completed_stages.append("clarifications_questions")

            stage_started = time.perf_counter()
            client.post(
                f"/agent-onboarding/clients/{resolved_client_id}/agent-products/{product_slug}/clarifications/mode",
                client_id=resolved_client_id,
                json={"session_id": session_id, "mode": clarification_mode},
            )
            timings_ms["clarifications_mode"] = _elapsed_ms(stage_started)
            completed_stages.append("clarifications_mode")

            stage_started = time.perf_counter()
            client.post(
                f"/agent-onboarding/clients/{resolved_client_id}/agent-products/{product_slug}/clarifications/answers",
                client_id=resolved_client_id,
                json={"session_id": session_id, "answers": clarifying_answers},
            )
            timings_ms["clarifications_answers"] = _elapsed_ms(stage_started)
            completed_stages.append("clarifications_answers")

            stage_started = time.perf_counter()
            client.post(
                f"/agent-onboarding/clients/{resolved_client_id}/agent-products/{product_slug}/conversation/mode",
                client_id=resolved_client_id,
                json={"session_id": session_id, "mode": conversation_mode},
            )
            timings_ms["conversation_mode"] = _elapsed_ms(stage_started)
            completed_stages.append("conversation_mode")

            stage_started = time.perf_counter()
            client.post(
                f"/agent-onboarding/clients/{resolved_client_id}/agent-products/{product_slug}/conversation/transcript",
                client_id=resolved_client_id,
                json={"session_id": session_id, "transcript": conversation_transcript},
            )
            timings_ms["conversation_transcript"] = _elapsed_ms(stage_started)
            completed_stages.append("conversation_transcript")

            stage_started = time.perf_counter()
            extract = client.post(
                f"/agent-onboarding/clients/{resolved_client_id}/agent-products/{product_slug}/conversation/extract",
                client_id=resolved_client_id,
                json={"session_id": session_id},
            )
            timings_ms["conversation_extract"] = _elapsed_ms(stage_started)
            completed_stages.append("conversation_extract")

            stage_started = time.perf_counter()
            artifacts = submit_onboarding_artifacts(
                product_slug=product_slug,
                session_id=session_id,
                client_id=resolved_client_id,
                market_position=market_position,
                conversion_notes=conversion_notes,
                funnels=funnels,
            )
            timings_ms["submit_artifacts"] = _elapsed_ms(stage_started)
            completed_stages.append("submit_artifacts")
            if not bool(artifacts.get("saved")):
                return {
                    "client_id": resolved_client_id,
                    "product_slug": product_slug,
                    "session_id": session_id,
                    "workflow_trace_id": trace_id,
                    "timings_ms": timings_ms,
                    "completed_stages": completed_stages,
                    "failed_stage": "submit_artifacts",
                    "error_type": str(artifacts.get("error_type") or "validation_failed"),
                    "retry_hint": str(artifacts.get("retry_hint") or "Fix artifact payloads and rerun."),
                    "partial_success": True,
                    "artifacts": artifacts,
                    "conversation_extract": extract,
                }

            stage_started = time.perf_counter()
            targeting = submit_agent_targeting(
                product_slug=product_slug,
                session_id=session_id,
                client_id=resolved_client_id,
                keywords=keywords,
                subreddit_groups=subreddit_groups,
                keyword_search_params=keyword_search_params,
            )
            timings_ms["submit_targeting"] = _elapsed_ms(stage_started)
            completed_stages.append("submit_targeting")
            if targeting.get("error_type"):
                return {
                    "client_id": resolved_client_id,
                    "product_slug": product_slug,
                    "session_id": session_id,
                    "workflow_trace_id": trace_id,
                    "timings_ms": timings_ms,
                    "completed_stages": completed_stages,
                    "failed_stage": "submit_targeting",
                    "error_type": str(targeting.get("error_type")),
                    "retry_hint": str(targeting.get("retry_hint") or "Fix targeting payloads and rerun."),
                    "partial_success": True,
                    "artifacts": artifacts,
                    "targeting": targeting,
                }

            approval: dict[str, Any] | None = None
            if approve_targeting:
                stage_started = time.perf_counter()
                approval = client.put(
                    f"/agent-onboarding/clients/{resolved_client_id}/agent-products/{product_slug}/targeting/approve",
                    client_id=resolved_client_id,
                    json={"session_id": session_id, "approved": True},
                )
                timings_ms["approve_targeting"] = _elapsed_ms(stage_started)
                completed_stages.append("approve_targeting")

            stage_started = time.perf_counter()
            readiness = client.get(
                f"/agent-onboarding/clients/{resolved_client_id}/agent-products/{product_slug}/readiness",
                client_id=resolved_client_id,
            )
            timings_ms["readiness"] = _elapsed_ms(stage_started)
            completed_stages.append("readiness")

            return {
                "client_id": resolved_client_id,
                "product_slug": product_slug,
                "session_id": session_id,
                "workflow_trace_id": trace_id,
                "timings_ms": timings_ms,
                "completed_stages": completed_stages,
                "failed_stage": None,
                "error_type": None,
                "retry_hint": None,
                "partial_success": False,
                "conversation_extract": extract,
                "artifacts": artifacts,
                "targeting": targeting,
                "targeting_approval": approval,
                "readiness": readiness,
            }
        except OnboardApiError as exc:
            return {
                "client_id": resolved_client_id,
                "product_slug": product_slug,
                "session_id": session_id,
                "workflow_trace_id": trace_id,
                "timings_ms": timings_ms,
                "completed_stages": completed_stages,
                "failed_stage": "unknown",
                "error_type": "api_error",
                "retry_hint": "Inspect failed stage and retry from get_onboarding_status.",
                "partial_success": bool(completed_stages),
                "error": str(exc),
            }
    finally:
        client.close()


# ---------------------------------------------------------------------------
# Reddit messaging
# ---------------------------------------------------------------------------


@mcp.tool()
def send_reddit_message(
    product_slug: str,
    client_id: str | None = None,
    target_username: str | None = None,
    crm_reply_to: str | None = None,
    batch_from_working_leads: bool = False,
    batch_id: str | None = None,
    limit: int | None = None,
    message: str | None = None,
    generate_message: bool = True,
) -> dict[str, Any]:
    """
    Send Reddit DMs to scraped leads via the Signal Found Chrome extension.

    Three modes (exactly one must be set):
    - target_username: cold DM to a specific Reddit user
    - crm_reply_to: reply to an existing CRM conversation
    - batch_from_working_leads=True: send to all uncontacted working leads for this product

    Message content: provide a manual message, or set generate_message=True (default) to
    auto-generate using the product's market positioning and conversion notes via Phase 2.
    In batch mode, pre-computed suggested_response values are reused when available.

    Requires a Chrome extension connected and active on Reddit. Fails immediately if none
    is online.
    """
    settings = Settings.from_env()
    resolved_client_id = _require_client_id(client_id, settings)

    modes_set = sum([bool(target_username), bool(crm_reply_to), batch_from_working_leads])
    if modes_set == 0:
        raise ValueError(
            "Specify exactly one send mode: target_username (cold DM), "
            "crm_reply_to (reply to existing conversation), or "
            "batch_from_working_leads=True (send to working leads batch)."
        )
    if modes_set > 1:
        raise ValueError(
            "Only one send mode can be active at a time: target_username, "
            "crm_reply_to, or batch_from_working_leads."
        )
    if not generate_message and not message:
        raise ValueError("Provide a message or set generate_message=True to auto-generate one.")

    client = _client()
    try:
        # Validate product exists
        _resolve_product_by_slug(client, client_id=resolved_client_id, product_slug=product_slug)

        # Detect operator Reddit username from connected Chrome extension
        connection = client.get(
            f"/socket/clients/{resolved_client_id}/connection",
            client_id=resolved_client_id,
        )
        sessions = connection.get("sessions") or []
        if not sessions:
            raise ValueError(
                "No Chrome extension is currently connected. "
                "Open reddit.com in Chrome with the Signal Found extension active and try again."
            )
        operator_username: str = str(sessions[0].get("reddit_username") or "").strip()
        if not operator_username:
            raise ValueError(
                "Connected extension has no Reddit username. "
                "Make sure you are logged into Reddit in the extension."
            )

        def _generate(target: str) -> str:
            resp = client.post(
                "/automation/generate-cold-outreach",
                client_id=resolved_client_id,
                json={"product_slug": product_slug, "target_username": target, "model": "gpt-5-mini"},
            )
            generated = str(resp.get("message") or "").strip()
            if not generated:
                raise ValueError(f"Message generation returned empty response for user: {target}")
            return generated

        # ---- Mode: Cold DM ----
        if target_username:
            try:
                msg = message or (_generate(target_username) if generate_message else "")
                if not msg:
                    raise ValueError("No message content available.")
                result = client.post(
                    "/crm/send_socket_dm",
                    client_id=resolved_client_id,
                    json={
                        "client_id": resolved_client_id,
                        "username": operator_username,
                        "target_username": target_username,
                        "message": msg,
                    },
                )
                return {
                    "mode": "cold_dm",
                    "target_username": target_username,
                    "operator_username": operator_username,
                    "request_id": result.get("request_id"),
                    "message_was_generated": not bool(message) and generate_message,
                    "message_preview": msg[:200],
                }
            except InsufficientCreditsError:
                return {
                    "error": "insufficient_credits",
                    "message": _make_credit_purchase_message(resolved_client_id),
                }

        # ---- Mode: CRM Reply ----
        if crm_reply_to:
            try:
                msg = message or (_generate(crm_reply_to) if generate_message else "")
                if not msg:
                    raise ValueError("No message content available.")
                result = client.post(
                    "/crm/send_socket_reply",
                    client_id=resolved_client_id,
                    json={
                        "client_id": resolved_client_id,
                        "body": msg,
                        "target_username": crm_reply_to,
                    },
                )
            except InsufficientCreditsError:
                return {
                    "error": "insufficient_credits",
                    "message": _make_credit_purchase_message(resolved_client_id),
                }
            return {
                "mode": "crm_reply",
                "target_username": crm_reply_to,
                "operator_username": operator_username,
                "request_id": result.get("request_id"),
                "message_was_generated": not bool(message) and generate_message,
                "message_preview": msg[:200],
            }

        # ---- Mode: Batch ----
        resolved_batch_id = batch_id
        if not resolved_batch_id:
            batches_resp = client.get(
                f"/automation/batches/{product_slug}",
                client_id=resolved_client_id,
            )
            batches = batches_resp if isinstance(batches_resp, list) else (batches_resp.get("batches") or [])
            if not batches:
                raise ValueError(f"No lead batches found for product: {product_slug}")
            resolved_batch_id = str(batches[0].get("batch_id") or "").strip()
            if not resolved_batch_id:
                raise ValueError("Could not determine batch_id from latest batch.")

        leads_resp = client.get(
            f"/automation/leads/{resolved_batch_id}",
            client_id=resolved_client_id,
        )
        leads = leads_resp if isinstance(leads_resp, list) else (leads_resp.get("leads") or [])
        uncontacted = [lead for lead in leads if not lead.get("contacted")]
        if limit is not None:
            uncontacted = uncontacted[:limit]

        if not uncontacted:
            return {
                "mode": "batch",
                "batch_id": resolved_batch_id,
                "total_attempted": 0,
                "sent": 0,
                "errors": 0,
                "results": [],
                "note": "No uncontacted leads found in this batch.",
            }

        results: list[dict[str, Any]] = []
        for lead in uncontacted:
            username = str(lead.get("username") or "").strip()
            if not username:
                results.append({"username": None, "status": "skipped", "detail": "Lead has no username"})
                continue

            if message:
                msg = message
                was_generated = False
            elif generate_message:
                suggested = str(lead.get("suggested_response") or "").strip()
                if suggested:
                    msg = suggested
                    was_generated = False
                else:
                    try:
                        msg = _generate(username)
                        was_generated = True
                    except Exception as exc:
                        results.append({"username": username, "status": "error", "detail": f"Generation failed: {exc}"})
                        continue
            else:
                results.append({"username": username, "status": "skipped", "detail": "No message and generate_message=False"})
                continue

            try:
                send_result = client.post(
                    "/crm/send_socket_dm",
                    client_id=resolved_client_id,
                    json={
                        "client_id": resolved_client_id,
                        "username": operator_username,
                        "target_username": username,
                        "message": msg,
                    },
                )
                results.append({
                    "username": username,
                    "status": "sent",
                    "request_id": send_result.get("request_id"),
                    "message_was_generated": was_generated,
                    "message_preview": msg[:100],
                })
            except InsufficientCreditsError:
                sent_count = sum(1 for r in results if r.get("status") == "sent")
                error_count = sum(1 for r in results if r.get("status") == "error")
                return {
                    "mode": "batch",
                    "batch_id": resolved_batch_id,
                    "total_attempted": len(results),
                    "sent": sent_count,
                    "errors": error_count,
                    "results": results,
                    "stopped_early": True,
                    "stopped_reason": _make_credit_purchase_message(resolved_client_id),
                }
            except OnboardApiError as exc:
                results.append({"username": username, "status": "error", "detail": str(exc)})

        sent_count = sum(1 for r in results if r.get("status") == "sent")
        error_count = sum(1 for r in results if r.get("status") == "error")
        return {
            "mode": "batch",
            "batch_id": resolved_batch_id,
            "total_attempted": len(results),
            "sent": sent_count,
            "errors": error_count,
            "results": results,
        }
    finally:
        client.close()


# ---------------------------------------------------------------------------
# OAuth 2.0 / MCP Authorization support
# ---------------------------------------------------------------------------

_OAUTH_SECRET: bytes = os.getenv("OAUTH_SECRET", secrets.token_hex(32)).encode()
_AUTH_CODE_TTL = 300  # seconds


def _b64url_enc(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _b64url_dec(s: str) -> bytes:
    pad = 4 - len(s) % 4
    return base64.urlsafe_b64decode(s + "=" * (pad % 4))


def _make_auth_code(client_id: str, code_challenge: str) -> str:
    payload = _b64url_enc(json.dumps({"c": client_id, "ch": code_challenge, "t": int(time.time())}).encode())
    sig = hmac.new(_OAUTH_SECRET, payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{sig}"


def _verify_auth_code(code: str) -> dict | None:
    parts = code.split(".", 1)
    if len(parts) != 2:
        return None
    payload_b64, sig = parts
    expected = hmac.new(_OAUTH_SECRET, payload_b64.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, sig):
        return None
    try:
        data = json.loads(_b64url_dec(payload_b64))
    except Exception:
        return None
    if int(time.time()) - data.get("t", 0) > _AUTH_CODE_TTL:
        return None
    return data


_SUPPORTS_CUSTOM_ROUTE = hasattr(mcp, "custom_route")


if _SUPPORTS_CUSTOM_ROUTE:

    @mcp.custom_route("/.well-known/oauth-authorization-server", methods=["GET"])
    async def _oauth_server_metadata(request: Request) -> Response:
        scheme = request.headers.get("x-forwarded-proto", request.url.scheme)
        base = f"{scheme}://{request.url.netloc}"
        return JSONResponse({
            "issuer": base,
            "authorization_endpoint": f"{base}/oauth/authorize",
            "token_endpoint": f"{base}/oauth/token",
            "registration_endpoint": f"{base}/oauth/register",
            "scopes_supported": ["mcp"],
            "response_types_supported": ["code"],
            "grant_types_supported": ["authorization_code"],
            "token_endpoint_auth_methods_supported": ["none"],
            "code_challenge_methods_supported": ["S256"],
        })


    @mcp.custom_route("/oauth/register", methods=["POST"])
    async def _oauth_register(request: Request) -> Response:
        try:
            body = await request.json()
        except Exception:
            body = {}
        return JSONResponse({
            "client_id": "claude-code",
            "client_id_issued_at": int(time.time()),
            "grant_types": body.get("grant_types", ["authorization_code"]),
            "response_types": body.get("response_types", ["code"]),
            "token_endpoint_auth_method": "none",
            "redirect_uris": body.get("redirect_uris", []),
        }, status_code=201)


_AUTHORIZE_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Signal Found — Connect</title>
  <style>
    body {{ font-family: system-ui, sans-serif; max-width: 480px; margin: 80px auto; padding: 0 20px; }}
    h1 {{ font-size: 1.4rem; margin-bottom: 8px; }}
    p {{ color: #555; font-size: 0.9rem; margin-bottom: 24px; }}
    label {{ display: block; font-size: 0.85rem; font-weight: 600; margin-bottom: 6px; }}
    input[type=text] {{ width: 100%; padding: 10px; border: 1px solid #ccc; border-radius: 6px; font-size: 1rem; box-sizing: border-box; }}
    button {{ margin-top: 16px; width: 100%; padding: 12px; background: #1a1a1a; color: #fff; border: none; border-radius: 6px; font-size: 1rem; cursor: pointer; }}
    button:hover {{ background: #333; }}
  </style>
</head>
<body>
  <h1>Connect to Signal Found</h1>
  <p>Enter your Signal Found client ID to authorize Claude Code.</p>
  <form method="POST" action="/oauth/authorize">
    <input type="hidden" name="redirect_uri" value="{redirect_uri}">
    <input type="hidden" name="state" value="{state}">
    <input type="hidden" name="code_challenge" value="{code_challenge}">
    <label for="sf_client_id">Client ID</label>
    <input type="text" id="sf_client_id" name="sf_client_id" placeholder="signal-found_xxxxxxxx" required autofocus>
    <button type="submit">Connect</button>
  </form>
</body>
</html>"""


if _SUPPORTS_CUSTOM_ROUTE:

    @mcp.custom_route("/oauth/authorize", methods=["GET", "POST"])
    async def _oauth_authorize(request: Request) -> Response:
        if request.method == "GET":
            p = request.query_params
            html = _AUTHORIZE_HTML.format(
                redirect_uri=_he(p.get("redirect_uri", "")),
                state=_he(p.get("state", "")),
                code_challenge=_he(p.get("code_challenge", "")),
            )
            return HTMLResponse(html)

        form = await request.form()
        sf_client_id = (form.get("sf_client_id") or "").strip()
        redirect_uri = (form.get("redirect_uri") or "").strip()
        state = (form.get("state") or "").strip()
        code_challenge = (form.get("code_challenge") or "").strip()

        if not sf_client_id or not redirect_uri:
            return HTMLResponse("<p>Missing required fields.</p>", status_code=400)

        code = _make_auth_code(sf_client_id, code_challenge)
        sep = "&" if "?" in redirect_uri else "?"
        return RedirectResponse(f"{redirect_uri}{sep}code={code}&state={state}", status_code=302)


    @mcp.custom_route("/oauth/token", methods=["POST"])
    async def _oauth_token(request: Request) -> Response:
        ct = request.headers.get("content-type", "")
        if "application/json" in ct:
            body = await request.json()
        else:
            form = await request.form()
            body = dict(form)

        grant_type = (body.get("grant_type") or "").strip()
        code = (body.get("code") or "").strip()
        code_verifier = (body.get("code_verifier") or "").strip()

        if grant_type != "authorization_code":
            return JSONResponse({"error": "unsupported_grant_type"}, status_code=400)

        data = _verify_auth_code(code)
        if data is None:
            return JSONResponse({"error": "invalid_grant"}, status_code=400)

        stored_challenge = data.get("ch", "")
        if stored_challenge:
            computed = _b64url_enc(hashlib.sha256(code_verifier.encode()).digest())
            if not hmac.compare_digest(computed, stored_challenge):
                return JSONResponse({"error": "invalid_grant"}, status_code=400)

        return JSONResponse({
            "access_token": data["c"],
            "token_type": "bearer",
            "scope": "mcp",
        })


def main() -> None:
    transport = os.getenv("MCP_TRANSPORT", "stdio").strip().lower()

    if transport in {"streamable-http", "http"}:
        try:
            mcp.run(transport="streamable-http")
        except ValueError:
            mcp.run(transport="http")
        return

    if transport == "sse":
        mcp.run(transport="sse")
        return

    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
