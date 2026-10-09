"""HTTP fetch with the project's headers, timeout and retry settings."""

import time

import requests

import config

# Status codes worth retrying; anything else is returned to the caller as an error.
RETRY_STATUS = {429, 500, 502, 503, 504}


def fetch(url, session=None, headers=None, stream=False):
    """GET `url` and return the Response, retrying transient failures.

    Makes 1 + config.HTTP_RETRIES attempts with a short linear backoff.
    `headers` are added to config.HTTP_HEADERS. Raises the last error if every
    attempt fails, or HTTPError straight away for a non-retryable error status.
    """
    getter = session or requests
    all_headers = {**config.HTTP_HEADERS, **(headers or {})}
    last_error = None
    for attempt in range(1 + config.HTTP_RETRIES):
        if attempt:
            time.sleep(2 * attempt)
        try:
            resp = getter.get(url, headers=all_headers, timeout=config.HTTP_TIMEOUT, stream=stream)
        except (requests.ConnectionError, requests.Timeout) as e:
            last_error = e
            print(f"  attempt {attempt + 1} failed for {url}: {e.__class__.__name__}")
            continue
        if resp.status_code in RETRY_STATUS:
            last_error = requests.HTTPError(f"HTTP {resp.status_code}", response=resp)
            print(f"  attempt {attempt + 1} failed for {url}: HTTP {resp.status_code}")
            continue
        resp.raise_for_status()
        return resp
    raise last_error


def probe_pdf(url, n_bytes=1024, session=None):
    """Check that `url` serves a PDF, reading at most `n_bytes` of the body.

    HTTP 200 alone is not enough: the site returns an HTML page with a 2xx status
    for some bad paths. A URL passes only if the status is 200/206, the
    Content-Type mentions pdf, and the body starts with b"%PDF".
    """
    result = {"url": url, "status": None, "content_type": None, "starts_pdf": False,
              "redirected": False, "ok": False, "error": ""}
    try:
        resp = fetch(url, session=session, headers={"Range": f"bytes=0-{n_bytes - 1}"}, stream=True)
    except requests.RequestException as e:
        resp_err = getattr(e, "response", None)
        result["status"] = resp_err.status_code if resp_err is not None else None
        result["error"] = str(e)
        return result
    with resp:
        head = next(resp.iter_content(n_bytes), b"")
    result.update(
        status=resp.status_code,
        content_type=resp.headers.get("Content-Type", ""),
        starts_pdf=head.startswith(b"%PDF"),
        redirected=bool(resp.history),
    )
    result["ok"] = (
        resp.status_code in (200, 206)
        and "pdf" in result["content_type"].lower()
        and result["starts_pdf"]
    )
    return result
