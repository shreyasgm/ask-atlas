"""Reverse proxy for PostHog analytics.

Routes analytics requests through our own domain so they aren't blocked
by ad blockers.  Two upstream hosts are needed: one for static assets
and one for everything else (event ingestion, decide, etc.).
"""

import httpx
from fastapi import APIRouter, Request
from starlette.background import BackgroundTask
from starlette.responses import StreamingResponse

POSTHOG_API_HOST = "us.i.posthog.com"
POSTHOG_ASSET_HOST = "us-assets.i.posthog.com"

_client = httpx.AsyncClient(timeout=30.0)

router = APIRouter()


@router.api_route(
    "/ph/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    include_in_schema=False,
)
async def posthog_proxy(request: Request, path: str) -> StreamingResponse:
    """Forward analytics requests to PostHog."""
    upstream_host = (
        POSTHOG_ASSET_HOST if path.startswith("static/") else POSTHOG_API_HOST
    )

    upstream_url = f"https://{upstream_host}/{path}"
    if request.url.query:
        upstream_url += f"?{request.url.query}"

    headers = dict(request.headers)
    headers["host"] = upstream_host
    # Don't forward app cookies or encoding hints to PostHog.
    for key in ("accept-encoding", "connection", "cookie"):
        headers.pop(key, None)

    # Preserve client IP for PostHog GeoIP resolution.
    if request.client:
        headers["x-forwarded-for"] = request.client.host

    body = await request.body()

    rp_req = _client.build_request(
        method=request.method,
        url=upstream_url,
        headers=headers,
        content=body,
    )
    rp_resp = await _client.send(rp_req, stream=True)

    response_headers = dict(rp_resp.headers)
    # Strip encoding/length — httpx decompresses for us, so these would be wrong.
    for key in ("content-encoding", "content-length", "transfer-encoding"):
        response_headers.pop(key, None)

    return StreamingResponse(
        rp_resp.aiter_bytes(),
        status_code=rp_resp.status_code,
        headers=response_headers,
        background=BackgroundTask(rp_resp.aclose),
    )
