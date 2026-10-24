"""
smoke_test.py
=============
Standalone smoke test for the LexAI API. Uploads a PDF, asks one question it
should answer and one it should refuse, and checks the response shapes match
what lexai_engine.LexAIEngine.query() / add_document() actually return.

Usage:
  python smoke_test.py --pdf PATH --q-in "question the PDF answers" \\
      --q-out "question the PDF cannot answer" \\
      [--base http://127.0.0.1:8000] [--owner smoke-test-user] [--token ACCESS_TOKEN]

--token sends "Authorization: Bearer <token>" on /upload and /query (needed when
the API has auth enabled; the server then ignores --owner and uses the token's user).
"""

import argparse
import json
import sys
import time

import requests

MAX_BODY_CHARS = 300


def truncate(text, limit=MAX_BODY_CHARS):
    text = str(text)
    return text if len(text) <= limit else text[:limit] + "...<truncated>"


def fail(msg):
    print(f"FAIL: {msg}")


def check_health(base):
    print("\n--- GET /health ---")
    try:
        resp = requests.get(f"{base}/health", timeout=30)
    except requests.exceptions.ConnectionError as e:
        fail(f"could not connect to {base}/health ({e})")
        return False
    if resp.status_code != 200:
        fail(f"/health returned status {resp.status_code}: {truncate(resp.text)}")
        return False
    print(resp.json())
    return True


def auth_headers(token):
    return {"Authorization": f"Bearer {token}"} if token else None


def upload_pdf(base, pdf_path, owner_id, token=None):
    print("\n--- POST /upload ---")
    try:
        with open(pdf_path, "rb") as f:
            files = {"file": (pdf_path.split("/")[-1].split("\\")[-1], f, "application/pdf")}
            data = {"owner_id": owner_id}
            try:
                resp = requests.post(f"{base}/upload", files=files, data=data, timeout=120,
                                     headers=auth_headers(token))
            except requests.exceptions.ConnectionError as e:
                fail(f"could not connect to {base}/upload ({e})")
                return None
    except OSError as e:
        fail(f"could not open PDF at {pdf_path} ({e})")
        return None

    if resp.status_code != 200:
        fail(f"/upload returned status {resp.status_code}: {truncate(resp.text)}")
        return None

    result = resp.json()
    print(result)
    if result.get("chunks", 0) == 0:
        fail(f"/upload produced 0 chunks: {result.get('note', '(no note)')}")
        return None
    return result


def run_query(base, question, owner_id, debug=False, token=None):
    print(f"\n--- POST /query : {question!r} ---")
    body = {"question": question, "owner_id": owner_id}
    if debug:
        body["debug"] = True
    start = time.perf_counter()
    try:
        resp = requests.post(f"{base}/query", json=body, timeout=180, headers=auth_headers(token))
    except requests.exceptions.ConnectionError as e:
        fail(f"could not connect to {base}/query ({e})")
        return None
    latency = time.perf_counter() - start

    if resp.status_code != 200:
        fail(f"/query returned status {resp.status_code}: {truncate(resp.text)}")
        return None

    result = resp.json()
    answer = result.get("answer")
    confidence = result.get("confidence")
    refused = result.get("refused")
    sources = result.get("sources") or []

    print(f"question:   {question}")
    print(f"answer:     {truncate(answer)}")
    print(f"confidence: {confidence}")
    print(f"refused:    {refused}")
    print(f"sources:    {len(sources)}")
    print(f"latency:    {latency:.2f}s")

    if debug and result.get("debug") is not None:
        print("debug:")
        print(json.dumps(result["debug"], indent=2, default=str))

    result["_latency"] = latency
    return result


def main():
    parser = argparse.ArgumentParser(description="LexAI API smoke test")
    parser.add_argument("--pdf", required=True, help="Path to a PDF to upload")
    parser.add_argument("--q-in", required=True, dest="q_in",
                         help="Question the PDF should answer (must not be refused)")
    parser.add_argument("--q-out", required=True, dest="q_out",
                         help="Question the PDF cannot answer (must be refused)")
    parser.add_argument("--base", default="http://127.0.0.1:8000", help="API base URL")
    parser.add_argument("--owner", default="smoke-test-user", help="owner_id to use")
    parser.add_argument("--debug", action="store_true",
                         help="send debug=true on /query and pretty-print the debug block")
    parser.add_argument("--token", default=None,
                         help="Supabase access token; sent as 'Authorization: Bearer <token>'")
    args = parser.parse_args()

    base = args.base.rstrip("/")
    checks = []  # (name, passed: bool)

    if not check_health(base):
        print("\nSUMMARY: FAIL (health check failed)")
        sys.exit(1)

    upload_result = upload_pdf(base, args.pdf, args.owner, token=args.token)
    if upload_result is None:
        print("\nSUMMARY: FAIL (upload failed)")
        sys.exit(1)

    in_result = run_query(base, args.q_in, args.owner, debug=args.debug, token=args.token)
    out_result = run_query(base, args.q_out, args.owner, debug=args.debug, token=args.token)

    if in_result is None:
        checks.append(("q-in request succeeded", False))
    else:
        in_answer = in_result.get("answer") or ""
        checks.append(("q-in not refused", in_result.get("refused") is False))
        checks.append(("q-in answer non-empty", len(in_answer.strip()) > 0))

    if out_result is None:
        checks.append(("q-out request succeeded", False))
    else:
        out_answer = out_result.get("answer") or ""
        checks.append(("q-out refused", out_result.get("refused") is True))
        checks.append(("q-out answer non-empty", len(out_answer.strip()) > 0))

    print("\n--- PASS/FAIL ---")
    all_passed = True
    for name, passed in checks:
        status = "PASS" if passed else "FAIL"
        if not passed:
            all_passed = False
        print(f"{status}: {name}")

    print(f"\nSUMMARY: {'PASS' if all_passed else 'FAIL'}")
    sys.exit(0 if all_passed else 1)


if __name__ == "__main__":
    main()
