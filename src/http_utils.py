"""HTTP fetch with the project's headers, timeout and retry settings."""

import hashlib
import os
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


def pdf_check(status, content_type, head):
    """Return "" if a response looks like a PDF, otherwise the reasons it does not.

    HTTP 200 alone is not enough: the site returns an HTML page with a 2xx status
    for some bad paths. A response passes only if the status is 200/206, the
    Content-Type mentions pdf, and the body starts with b"%PDF".
    """
    problems = []
    if status not in (200, 206):
        problems.append(f"HTTP {status}")
    if "pdf" not in (content_type or "").lower():
        problems.append(f"Content-Type {content_type!r}")
    if not head.startswith(b"%PDF"):
        problems.append(f"body starts {head[:8]!r}, not %PDF")
    return "; ".join(problems)


def probe_pdf(url, n_bytes=1024, session=None):
    """Check that `url` serves a PDF, reading at most `n_bytes` of the body."""
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
    result["error"] = pdf_check(resp.status_code, result["content_type"], head)
    result["ok"] = not result["error"]
    return result


BODY_ATTEMPTS = 3  # attempts at reading the response body (timeouts, dropped connections)


def download_pdf(url, dest, session=None):
    """Download `url` to `dest` (a Path), keeping it only if it passes pdf_check.

    Streams to `<dest>.part` and renames it to `dest` only after validation, so a
    failed download never leaves a file at `dest`. A timeout or dropped
    connection while reading the body is retried (BODY_ATTEMPTS in total, short
    back-off); the partial file is deleted after every failed attempt. Returns a
    dict with http_status, content_type, bytes, sha256, ok, error and attempts.
    """
    result = {"http_status": None, "content_type": "", "bytes": 0, "sha256": "", "ok": False, "error": "",
              "attempts": 0}
    tmp = dest.with_name(dest.name + ".part")
    for attempt in range(1, BODY_ATTEMPTS + 1):
        result["attempts"] = attempt
        if attempt > 1:
            time.sleep(2 * (attempt - 1))
        try:
            resp = fetch(url, session=session, stream=True)
        except requests.RequestException as e:
            resp_err = getattr(e, "response", None)
            result["http_status"] = resp_err.status_code if resp_err is not None else None
            result["error"] = f"request failed: {e}"
            return result

        digest, n_bytes, head = hashlib.sha256(), 0, b""
        with resp:
            result["http_status"] = resp.status_code
            result["content_type"] = resp.headers.get("Content-Type", "")
            try:
                with open(tmp, "wb") as fh:
                    for chunk in resp.iter_content(64 * 1024):
                        if len(head) < 8:
                            head += chunk[: 8 - len(head)]
                        fh.write(chunk)
                        digest.update(chunk)
                        n_bytes += len(chunk)
            except (requests.ConnectionError, requests.Timeout, requests.exceptions.ChunkedEncodingError) as e:
                tmp.unlink(missing_ok=True)
                result["error"] = f"download interrupted (attempt {attempt}/{BODY_ATTEMPTS}): {e}"
                print(f"  body attempt {attempt} failed for {url}: {e.__class__.__name__}")
                continue
            except OSError as e:
                tmp.unlink(missing_ok=True)
                result["error"] = f"could not write file: {e}"
                return result
            expected = resp.headers.get("Content-Length")
            compressed = bool(resp.headers.get("Content-Encoding"))
        break
    else:
        return result  # every attempt was interrupted; error holds the last one

    problems = [pdf_check(resp.status_code, result["content_type"], head)]
    if expected and not compressed and int(expected) != n_bytes:
        problems.append(f"received {n_bytes} bytes, Content-Length says {expected}")
    error = "; ".join(p for p in problems if p)
    if error:
        tmp.unlink(missing_ok=True)
        result.update(bytes=n_bytes, error=error)
        return result

    os.replace(tmp, dest)
    note = f"succeeded on attempt {result['attempts']}" if result["attempts"] > 1 else ""
    result.update(bytes=n_bytes, sha256=digest.hexdigest(), ok=True, error=note)
    return result
