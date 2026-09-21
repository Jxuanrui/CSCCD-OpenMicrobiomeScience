"""FastAPI application for the local MRA workbench.

The application is intended for a local loopback bind only. It has no
authentication, so it represents one local user. Every sensitive operation
goes through the backend PEP; the browser only renders views and submits an
approval label.
"""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from mra.knowledge import KnowledgeStore
from mra.knowledge.types import REVIEW_STATUS_VALUES
from mra.pep import Pep, PepError


_WEB_ROOT = Path(__file__).parent
_TEMPLATE_ROOT = _WEB_ROOT / "templates"
_STATIC_ROOT = _WEB_ROOT / "static"


def _policy_data() -> dict[str, Any]:
    """Read the checked-in policy data without making it a second authority."""

    candidates = (
        _WEB_ROOT.parents[2] / "policies" / "opa" / "bundle" / "data.json",
        Path.cwd() / "policies" / "opa" / "bundle" / "data.json",
    )
    for path in candidates:
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, TypeError, json.JSONDecodeError):
            continue
        if isinstance(document, dict):
            mra = document.get("mra")
            return mra if isinstance(mra, dict) else {}
    return {}


def _ledger_rows(pep: Pep, status: str | None = None) -> list[dict[str, Any]]:
    """Read approval rows under the ledger lock; this function never writes."""

    ledger = pep.ledger
    connection = getattr(ledger, "_connection", None)
    lock = getattr(ledger, "_lock", None)
    if connection is None:
        return []
    query = """
        SELECT approval_requests.*,
               (SELECT MIN(ts) FROM audit_events
                WHERE audit_events.request_id = approval_requests.request_id)
               AS created_at
        FROM approval_requests
    """
    values: tuple[str, ...] = ()
    if status is not None:
        query += " WHERE status = ?"
        values = (status,)
    query += " ORDER BY COALESCE(approved_at, created_at, '') DESC, request_id DESC"

    def read() -> list[dict[str, Any]]:
        return [dict(row) for row in connection.execute(query, values).fetchall()]

    if lock is None:
        return read()
    with lock:
        return read()


def _events(pep: Pep, **filters: object) -> list[dict[str, Any]]:
    clean = {key: value for key, value in filters.items() if value not in (None, "")}
    return pep.query_audit(**clean)


def _prefix(value: object, length: int = 12) -> str:
    if value is None or value == "":
        return "-"
    text = str(value)
    return text if len(text) <= length else f"{text[:length]}..."


def _reason_codes(value: object) -> str:
    if isinstance(value, (list, tuple)):
        return ", ".join(str(item) for item in value) or "-"
    return str(value) if value else "-"


def _digest_excerpt(value: object, edge: int = 8) -> str:
    if value is None or value == "":
        return "-"
    text = str(value)
    return text if len(text) <= edge * 2 else f"{text[:edge]}...{text[-edge:]}"


def _run_groups(rows: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        request_id = row.get("request_id")
        if isinstance(request_id, str) and request_id:
            grouped[request_id].append(dict(row))
    result: list[dict[str, Any]] = []
    for request_id, chain in grouped.items():
        chain.sort(key=lambda row: (str(row.get("ts", "")), str(row.get("id", ""))))
        latest = chain[-1]
        result.append(
            {
                "request_id": request_id,
                "events": chain,
                "latest_ts": latest.get("ts", ""),
                "decision": latest.get("decision", ""),
                "reason_codes": _reason_codes(latest.get("reason_codes")),
                "input_digest": _digest_excerpt(latest.get("input_digest")),
                "output_digest": _digest_excerpt(latest.get("output_digest")),
            }
        )
    result.sort(key=lambda run: (str(run["latest_ts"]), run["request_id"]), reverse=True)
    return result


def create_app(
    pep: Pep,
    title: str = "MRA Workbench",
    knowledge: KnowledgeStore | None = None,
) -> FastAPI:
    """Create the local-only workbench around one trusted :class:`Pep`.

    Bind the returned app to a loopback address only. There is no
    authentication because this is a single-user local application. All
    sensitive operations, including approval, are enforced by the backend
    PEP.
    """

    app = FastAPI(title=title, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.pep = pep
    app.state.knowledge = knowledge
    app.state.title = title
    templates = Jinja2Templates(directory=str(_TEMPLATE_ROOT))
    templates.env.filters["prefix"] = _prefix
    templates.env.filters["digest"] = _digest_excerpt
    templates.env.filters["reason_codes"] = _reason_codes
    app.mount("/static", StaticFiles(directory=str(_STATIC_ROOT)), name="static")

    def render(
        request: Request,
        template: str,
        *,
        status_code: int = 200,
        **values: Any,
    ) -> HTMLResponse:
        context = {"request": request, "title": title, **values}
        return templates.TemplateResponse(
            request=request,
            name=template,
            context=context,
            status_code=status_code,
        )

    @app.get("/", response_class=HTMLResponse)
    async def dashboard(request: Request) -> HTMLResponse:
        events = _events(pep)
        return render(
            request,
            "dashboard.html",
            pending_count=len(_ledger_rows(pep, "pending")),
            event_count=len(events),
            recent_events=list(reversed(events[-10:])),
        )

    def approvals_fragment(
        request: Request,
        *,
        error: str | None = None,
        status_code: int = 200,
    ) -> HTMLResponse:
        return render(
            request,
            "_approvals_fragment.html",
            status_code=status_code,
            pending=_ledger_rows(pep, "pending"),
            approved=_ledger_rows(pep, "approved")[:10],
            error=error,
        )

    @app.get("/approvals", response_class=HTMLResponse)
    async def approvals(request: Request) -> HTMLResponse:
        return render(
            request,
            "approvals.html",
            pending=_ledger_rows(pep, "pending"),
            approved=_ledger_rows(pep, "approved")[:10],
        )

    @app.post("/approvals/{request_id}/approve", response_class=HTMLResponse)
    async def approve(
        request: Request,
        request_id: str,
        label: str = Form(""),
    ) -> HTMLResponse:
        label = label.strip()
        if not label:
            return approvals_fragment(
                request,
                error="Approval label is required.",
                status_code=400,
            )
        try:
            pep.approve(request_id, label)
        except PepError as exc:
            return approvals_fragment(request, error=str(exc), status_code=400)
        return approvals_fragment(request)

    @app.get("/audit", response_class=HTMLResponse)
    async def audit(
        request: Request,
        action: str | None = Query(default=None),
        decision: str | None = Query(default=None),
    ) -> HTMLResponse:
        rows = list(reversed(_events(pep, action=action, decision=decision)[-200:]))
        return render(
            request,
            "audit.html",
            events=rows,
            selected_action=action or "",
            selected_decision=decision or "",
        )

    @app.get("/runs", response_class=HTMLResponse)
    async def runs(request: Request) -> HTMLResponse:
        return render(request, "runs.html", runs=_run_groups(_events(pep)))

    @app.get("/runs/{request_id}", response_class=HTMLResponse)
    async def run_detail(request: Request, request_id: str) -> HTMLResponse:
        chain = _events(pep, request_id=request_id)
        if not chain:
            raise HTTPException(status_code=404, detail="Run not found")
        chain.sort(key=lambda row: (str(row.get("ts", "")), str(row.get("id", ""))))
        return render(request, "run_detail.html", request_id=request_id, events=chain)

    @app.get("/permissions", response_class=HTMLResponse)
    async def permissions(request: Request) -> HTMLResponse:
        return render(request, "permissions.html", policy=_policy_data())

    @app.get("/knowledge", response_class=HTMLResponse)
    async def knowledge_overview(request: Request) -> HTMLResponse:
        if knowledge is None:
            return render(request, "knowledge.html", knowledge_enabled=False)
        entries = knowledge.list()
        grouped: dict[str, list[Any]] = defaultdict(list)
        for entry in entries:
            grouped[entry.package].append(entry)
        return render(
            request,
            "knowledge.html",
            knowledge_enabled=True,
            package_groups=dict(sorted(grouped.items())),
            query="",
        )

    @app.get("/knowledge/search", response_class=HTMLResponse)
    async def knowledge_search(
        request: Request,
        q: str = Query(default=""),
    ) -> HTMLResponse:
        query = q.strip()
        if knowledge is None:
            return render(
                request,
                "knowledge.html",
                knowledge_enabled=False,
                query=query,
                search_hits=[],
            )
        if not query:
            return render(
                request,
                "knowledge.html",
                knowledge_enabled=True,
                query="",
                search_hits=[],
                search_message="请输入搜索词。",
            )
        return render(
            request,
            "knowledge.html",
            knowledge_enabled=True,
            query=query,
            search_hits=knowledge.search(query),
            package_groups={},
        )

    @app.get("/sources", response_class=HTMLResponse)
    async def sources(
        request: Request,
        review_status: str | None = Query(default=None),
    ) -> HTMLResponse:
        if knowledge is None:
            return render(request, "sources.html", knowledge_enabled=False)
        selected = (review_status or "").strip() or None
        if selected is not None and selected not in REVIEW_STATUS_VALUES:
            raise HTTPException(status_code=400, detail="Unknown review status")
        return render(
            request,
            "sources.html",
            knowledge_enabled=True,
            sources=knowledge.list_sources(status=None),
            candidates=knowledge.list_candidate_evidence(review_status=selected),
            selected_review_status=selected or "",
        )

    @app.post("/sources/review", response_class=HTMLResponse)
    async def sources_review(
        request: Request,
        evidence_id: str = Form(""),
        status: str = Form(""),
    ) -> HTMLResponse:
        if knowledge is None:
            raise HTTPException(status_code=404, detail="Knowledge store not enabled")
        evidence_id = evidence_id.strip()
        status = status.strip()
        if not evidence_id:
            raise HTTPException(status_code=400, detail="evidence_id is required")
        if status not in REVIEW_STATUS_VALUES:
            raise HTTPException(status_code=400, detail="Unknown review status")
        try:
            knowledge.set_candidate_review_status(evidence_id, status)
        except KeyError:
            raise HTTPException(status_code=404, detail="Candidate evidence not found")
        from fastapi.responses import RedirectResponse

        query = f"?review_status={status}" if status != "pending" else ""
        return RedirectResponse(url=f"/sources{query}", status_code=303)

    @app.exception_handler(404)
    async def not_found(request: Request, exc: HTTPException) -> HTMLResponse:
        del request, exc
        return HTMLResponse("<h1>404</h1><p>Page not found.</p>", status_code=404)

    return app


__all__ = ["create_app"]
