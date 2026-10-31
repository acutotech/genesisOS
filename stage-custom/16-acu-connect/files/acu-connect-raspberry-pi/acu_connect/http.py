import json
import urllib.error
import urllib.request

from . import VERSION

USER_AGENT = f"acu-connect/{VERSION}"


def request(url, method="GET", body=None, headers=None, timeout=30):
    """Sends a JSON request and returns (status, data). Blocking: use a thread inside the event loop."""
    data = None if body is None else json.dumps(body).encode()
    all_headers = {"Content-Type": "application/json", "User-Agent": USER_AGENT, **(headers or {})}
    req = urllib.request.Request(url, data=data, headers=all_headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            return res.status, _parse(res.read())
    except urllib.error.HTTPError as e:
        return e.code, _parse(e.read())


def _parse(raw):
    try:
        return json.loads(raw)
    except ValueError:
        return {}
