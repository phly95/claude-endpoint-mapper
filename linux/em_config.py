"""Config engine for Claude Endpoint Mapper (Linux / Nobara).

Writes the Claude Desktop third-party (gateway) config, the claude-desktop-extra opt-in that lets non-Anthropic model names through, and
Claude Code settings. Ports Write-DesktopConfig / Write-ClaudeCodeConfig /
Get-EndpointModels from EndpointMapper.psm1.

Rules shared by every writer: an existing file that is not valid JSON is never
overwritten (ValueError, nothing written); files are replaced atomically; files
that can hold a key are created with mode 0600.
"""
import json
import os
import pathlib
import re
import tempfile
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass, field

TIERS = ("sonnet", "opus", "haiku", "fable", "mythos")
WEB_SEARCH_CHOICES = ("keep", "none", "tavily", "brave", "exa", "custom")
SYSTEM_ASAR = "/usr/lib/claude-desktop/resources/app.asar"
_SWITCH_MARKER = b"__CDB_ALLOW_NONANTHROPIC__"
_EXTRA_KEY = "allowNonAnthropicModels"
USER_AGENT = "claude-endpoint-mapper/1.0"


# ------------------------------------------------------------------ helpers --

def _strip_jsonc(text: str) -> str:
    # same string-aware comment / trailing-comma stripper the app uses
    text = re.sub(r'("(?:[^"\\]|\\.)*")|//[^\n]*|/\*[\s\S]*?\*/',
                  lambda m: m.group(1) or "", text)
    return re.sub(r",(\s*[}\]])", r"\1", text)


def load_object(path, jsonc: bool = False):
    """Return the JSON object at path, {} when missing/empty, ValueError when bad."""
    p = pathlib.Path(path)
    if not p.exists():
        return {}
    text = p.read_text(encoding="utf-8")
    if jsonc:
        text = _strip_jsonc(text)
    if not text.strip():
        return {}
    try:
        obj = json.loads(text)
    except ValueError as e:
        raise ValueError(f"{p} is not valid JSON ({e}); fix or remove it first, nothing was written") from None
    if not isinstance(obj, dict):
        raise ValueError(f"{p} must contain a JSON object; nothing was written")
    return obj


def _write_json(path, obj, mode: int = 0o600) -> None:
    p = pathlib.Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".em-", dir=str(p.parent))
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(obj, f, indent=2, ensure_ascii=False)
            f.write("\n")
        os.replace(tmp, p)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


@dataclass
class Plan:
    """Validated pending writes. Building a plan never touches the disk."""
    writes: list = field(default_factory=list)   # (path, object, mode)
    result: dict = field(default_factory=dict)


def commit(*plans) -> None:
    for plan in plans:
        for path, obj, mode in plan.writes:
            _write_json(path, obj, mode)


def userdata() -> pathlib.Path:
    """Claude Desktop's data dir for the default profile."""
    base = os.environ.get("XDG_CONFIG_HOME") or str(pathlib.Path.home() / ".config")
    return pathlib.Path(base) / "Claude"


def userdata_3p() -> pathlib.Path:
    """Where the app keeps third-party (gateway) state once it runs in 3p mode."""
    d = userdata()
    return d.with_name(d.name + "-3p")


def has_native_switch(asar_path: str = SYSTEM_ASAR) -> bool:
    """True when the installed app.asar carries the claude-desktop-extra
    allowNonAnthropicModels switch."""
    try:
        keep = len(_SWITCH_MARKER) - 1
        tail = b""
        with open(asar_path, "rb") as f:
            while True:
                chunk = f.read(8 << 20)
                if not chunk:
                    return False
                if _SWITCH_MARKER in tail + chunk:
                    return True
                tail = chunk[-keep:]
    except OSError:
        return False


# ------------------------------------------------------------------- models --

def fetch_models(base_url: str, api_key: str = "", auth_scheme: str = "bearer",
                 timeout: int = 20) -> list:
    base = base_url.rstrip("/")
    if base.endswith("/v1"):
        candidates = [base + "/models", base[:-3] + "/models"]
    else:
        candidates = [base + "/v1/models", base + "/models"]
    # urllib's default "Python-urllib/x" User-Agent is blocked outright by Cloudflare
    # (HTTP 403, error 1010) in front of many gateways, so identify as ourselves.
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    if api_key:
        if auth_scheme == "x-api-key":
            headers["x-api-key"] = api_key
        else:
            headers["Authorization"] = "Bearer " + api_key
    attempts = []
    for url in dict.fromkeys(candidates):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = json.load(resp)
        except (urllib.error.URLError, OSError, ValueError) as e:
            attempts.append(f"{url} -> {e}")
            if isinstance(e, urllib.error.HTTPError):
                e.close()
            continue
        items = []
        if isinstance(body, dict) and isinstance(body.get("data"), list):
            items = body["data"]
        elif isinstance(body, dict) and isinstance(body.get("models"), list):
            items = body["models"]
        elif isinstance(body, list):
            items = body
        ids = []
        for it in items:
            if isinstance(it, dict):
                ident = it.get("id") or it.get("name")
                if ident:
                    ids.append(str(ident))
        if ids:
            return sorted(set(ids))
        attempts.append(f"{url} -> replied, but no model ids were recognized")
    raise RuntimeError("Could not list models from endpoint. Tried: " + "; ".join(attempts))


# ----------------------------------------------------------- desktop config --

def _applied_config_path(lib: pathlib.Path, meta: dict):
    applied = meta.get("appliedId")
    if applied and (lib / f"{applied}.json").exists():
        return lib / f"{applied}.json"
    others = sorted(p for p in lib.glob("*.json") if p.name != "_meta.json") if lib.exists() else []
    return others[0] if others else None


def _web_search_entry(provider, key, custom_url, existing):
    reuse = bool(existing) and str(existing.get("provider")) == provider
    headers = existing.get("headers") if (not key and reuse and existing.get("headers")) else None
    if provider == "custom":
        custom_url = custom_url or (existing.get("customUrl") if reuse else "") or ""
        if not custom_url:
            raise ValueError('Custom web search needs a URL. The built-in server POSTs {"q":"..."} '
                             'to it and expects {"results":[{title,url,snippet}]} back.')
    if headers is None:
        if provider == "tavily":
            headers = {"Authorization": "Bearer " + key} if key else {"X-Tavily-Access-Mode": "keyless"}
        elif provider == "brave":
            if not key:
                raise ValueError("Brave web search needs an API key (sent as X-Subscription-Token).")
            headers = {"X-Subscription-Token": key}
        elif provider == "exa":
            if not key:
                raise ValueError("Exa web search needs an API key (sent as x-api-key).")
            headers = {"x-api-key": key}
        else:
            headers = {"Authorization": "Bearer " + key} if key else {}
    entry = {"name": "Web search", "server": "websearch", "provider": provider,
             "headers": headers, "toolPolicy": {"web_search": "allow"}}
    if provider == "custom":
        entry["customUrl"] = custom_url
    return entry


def write_desktop_config(*args, **kwargs) -> dict:
    plan = plan_desktop_config(*args, **kwargs)
    commit(plan)
    return plan.result


def plan_desktop_config(user_dir, base_url: str, api_key: str, auth_scheme: str, models: list,
                        web_search: str = "keep", web_search_key: str = "",
                        web_search_url: str = "") -> Plan:
    if auth_scheme not in ("bearer", "x-api-key"):
        raise ValueError("auth scheme must be 'bearer' or 'x-api-key'")
    if web_search not in WEB_SEARCH_CHOICES:
        raise ValueError(f"web search must be one of {', '.join(WEB_SEARCH_CHOICES)}")
    lib = pathlib.Path(user_dir) / "configLibrary"
    meta_path = lib / "_meta.json"
    meta = load_object(meta_path)
    cfg_path = _applied_config_path(lib, meta)
    cfg = load_object(cfg_path) if cfg_path else {}

    key_out = api_key or cfg.get("inferenceGatewayApiKey")
    if not key_out:
        raise ValueError("No API key supplied and no existing key in config.")

    entries = []
    for m in models:
        name = str(m.get("name") or "").strip()
        if not name:
            continue
        e = {"name": name}
        if m.get("label"):
            e["labelOverride"] = m["label"]
        tier = str(m.get("tier") or "").lower()
        if tier in TIERS:
            e["anthropicFamilyTier"] = tier
            e["isFamilyDefault"] = True
        entries.append(e)
    if not entries:
        raise ValueError("No models selected.")

    existing_ws = None
    others = []
    for srv in cfg.get("managedMcpServers") or []:
        if isinstance(srv, dict) and srv.get("server") == "websearch":
            existing_ws = existing_ws or srv
        else:
            others.append(srv)
    state = "unchanged"
    servers = None
    if web_search == "none":
        servers, state = others, "disabled"
    elif web_search != "keep":
        entry = _web_search_entry(web_search, (web_search_key or "").strip(),
                                  (web_search_url or "").strip(), existing_ws)
        servers = [entry] + others
        state = web_search + (" (keyless)" if "X-Tavily-Access-Mode" in entry["headers"] else "")

    cfg.update({
        "inferenceProvider": "gateway",
        "inferenceCredentialKind": "static",
        "inferenceGatewayBaseUrl": base_url.rstrip("/"),
        "inferenceGatewayApiKey": key_out,
        "inferenceGatewayAuthScheme": auth_scheme,
        "inferenceModels": entries,
    })
    cfg.setdefault("alwaysStartWithDefaultModel", False)
    cfg.setdefault("developerModeEnabled", True)
    if servers is not None:
        if servers:
            cfg["managedMcpServers"] = servers
        else:
            cfg.pop("managedMcpServers", None)

    writes = []
    if cfg_path is None:
        ident = str(uuid.uuid4())
        cfg_path = lib / f"{ident}.json"
        meta.setdefault("entries", [])
        meta["entries"].append({"id": ident, "name": "Custom endpoint"})
        meta["appliedId"] = ident
        writes.append((meta_path, meta, 0o600))
    writes.append((cfg_path, cfg, 0o600))
    return Plan(writes, {"path": str(cfg_path), "model_count": len(entries), "web_search": state})


def read_desktop_state(user_dir):
    """Summary of the applied gateway config (never the key), or None when absent."""
    lib = pathlib.Path(user_dir) / "configLibrary"
    path = _applied_config_path(lib, load_object(lib / "_meta.json"))
    if path is None:
        return None
    cfg = load_object(path)
    return {
        "path": str(path),
        "base_url": cfg.get("inferenceGatewayBaseUrl"),
        "model_count": len(cfg.get("inferenceModels") or []),
        "has_key": bool(cfg.get("inferenceGatewayApiKey")),
        "web_search": any(isinstance(s, dict) and s.get("server") == "websearch"
                          for s in cfg.get("managedMcpServers") or []),
    }


def read_prefill(user_dir):
    """What the GUI shows from the applied config (never the key), or None."""
    lib = pathlib.Path(user_dir) / "configLibrary"
    path = _applied_config_path(lib, load_object(lib / "_meta.json"))
    if path is None:
        return None
    cfg = load_object(path)
    models = [{"name": str(m.get("name") or ""), "tier": str(m.get("anthropicFamilyTier") or ""),
               "label": str(m.get("labelOverride") or "")}
              for m in cfg.get("inferenceModels") or [] if isinstance(m, dict) and m.get("name")]
    web = next((s for s in cfg.get("managedMcpServers") or []
                if isinstance(s, dict) and s.get("server") == "websearch"), None)
    return {
        "base_url": cfg.get("inferenceGatewayBaseUrl") or "",
        "auth_scheme": cfg.get("inferenceGatewayAuthScheme") or "bearer",
        "has_key": bool(cfg.get("inferenceGatewayApiKey")),
        "models": models,
        "web_search": ({"provider": str(web.get("provider") or ""),
                        "custom_url": str(web.get("customUrl") or "")} if web else None),
    }


def read_extra_pref(user_dir) -> str:
    """'on', 'off', or 'off (overridden by claude-desktop-extra.jsonc)'."""
    d = pathlib.Path(user_dir)
    try:
        jsonc = load_object(d / "claude-desktop-extra.jsonc", jsonc=True).get(_EXTRA_KEY)
        plain = load_object(d / "claude-desktop-extra.json", jsonc=True).get(_EXTRA_KEY)
    except ValueError:
        return "unreadable"
    if jsonc is True or (jsonc is None and plain is True):
        return "on"
    if jsonc is False and plain is True:
        return "off (overridden by claude-desktop-extra.jsonc)"
    return "off"


# -------------------------------------------------------------- claude code --

def write_claude_code_config(*args, **kwargs):
    plan = plan_claude_code_config(*args, **kwargs)
    commit(plan)
    return plan.result["path"]


def plan_claude_code_config(settings_path, base_url: str, api_key: str, models: list) -> Plan:
    path = pathlib.Path(settings_path)
    settings = load_object(path)
    env = settings.get("env")
    if env is None:
        env = settings["env"] = {}
    elif not isinstance(env, dict):
        raise ValueError(f"{path}: 'env' must be an object; nothing was written")
    env["ANTHROPIC_BASE_URL"] = base_url.rstrip("/")
    if api_key:
        env["ANTHROPIC_AUTH_TOKEN"] = api_key
    for m in models:
        tier = str(m.get("tier") or "").lower()
        if tier in TIERS:
            var = f"ANTHROPIC_DEFAULT_{tier.upper()}_MODEL"
            if m.get("name"):
                env[var] = m["name"]
            else:
                env.pop(var, None)
    return Plan([(path, settings, 0o600)], {"path": path})


# --------------------------------------------------------------- extra pref --

def write_extra_pref(user_dir) -> dict:
    plan = plan_extra_pref(user_dir)
    commit(plan)
    return plan.result


def plan_extra_pref(user_dir) -> Plan:
    d = pathlib.Path(user_dir)
    path = d / "claude-desktop-extra.json"
    cfg = load_object(path, jsonc=True)
    cfg[_EXTRA_KEY] = True
    warning = None
    try:
        jsonc = load_object(d / "claude-desktop-extra.jsonc", jsonc=True)
    except ValueError:
        jsonc = {}
    if jsonc.get(_EXTRA_KEY) is False:
        warning = (f"{d / 'claude-desktop-extra.jsonc'} sets {_EXTRA_KEY} to false and wins over "
                   f"the value written here; edit that file for non-Anthropic models to work.")
    return Plan([(path, cfg, 0o644)], {"path": str(path), "warning": warning})
