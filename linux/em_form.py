"""GUI-independent logic for the Tk front end: tier guessing and turning the
form state into a CLI request. Keys are never placed in argv; they travel on
the stdin text returned alongside it."""

_FAMILIES = ("haiku", "sonnet", "opus", "fable", "mythos")


def guess_tier(model_id: str) -> str:
    lowered = model_id.lower()
    for family in _FAMILIES:
        if family in lowered:
            return family
    return ""


def build_apply_request(url: str, auth: str, key: str, rows: list, code: bool,
                        ws_enabled: bool, ws_provider: str, ws_key: str, ws_url: str):
    """Return (argv for `endpoint_mapper.py`, stdin text carrying the keys)."""
    url = (url or "").strip()
    if not url:
        raise ValueError("Enter the endpoint base URL.")
    argv = ["apply", "--url", url, "--auth", auth or "bearer"]
    count = 0
    for r in rows:
        name = str(r.get("name") or "").strip()
        if not r.get("use") or not name:
            continue
        spec = name
        tier = str(r.get("tier") or "").strip().lower()
        label = str(r.get("label") or "").strip()
        if tier or label:
            spec += f"={tier}"
        if label:
            spec += f"={label}"
        argv += ["--model", spec]
        count += 1
    if not count:
        raise ValueError("Select at least one model with a name.")

    stdin = ""
    key, ws_key = (key or "").strip(), (ws_key or "").strip()
    if key:
        argv.append("--key-stdin")
        stdin += key + "\n"
    if ws_enabled:
        provider = "custom" if ws_provider == "other" else ws_provider
        argv += ["--web-search", provider]
        if provider == "custom" and (ws_url or "").strip():
            argv += ["--web-search-url", ws_url.strip()]
        if ws_key:
            argv.append("--web-search-key-stdin")
            stdin += ws_key + "\n"
    else:
        argv += ["--web-search", "none"]
    if not code:
        argv.append("--no-code")
    argv.append("--restart")
    return argv, stdin
