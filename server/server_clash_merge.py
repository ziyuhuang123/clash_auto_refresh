from __future__ import annotations

import argparse
import copy
import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

import requests
import yaml

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_BASE_CONFIG = Path("/cephfs/zyhuang/clash/config.yaml")
DEFAULT_SOURCES_CONFIG = SCRIPT_DIR / "sources.yaml"
DEFAULT_SERVICE_TARGETS = SCRIPT_DIR / "service_targets.yaml"
DEFAULT_OUTPUT = SCRIPT_DIR / "generated" / "merged.yaml"
DEFAULT_PROBE_OUTPUT = SCRIPT_DIR / "generated" / "probe.yaml"
DEFAULT_STATUS = SCRIPT_DIR / "generated" / "status.json"
DEFAULT_TIMEOUT = 15
DEFAULT_INTERVAL = 300
DEFAULT_TOLERANCE = 80
DEFAULT_GLOBAL_GROUP = "AI_AUTO"
DEFAULT_DIRECT_PROBE_URL = "https://www.gstatic.com/generate_204"
DEFAULT_PROXY_PROBE_URL = "https://chatgpt.com"
DEFAULT_SELECTION_PROBE_URL = "https://auth.openai.com"
DEFAULT_TARGET_PROBE_TIMEOUT_MS = 10000
DEFAULT_TARGET_PROBE_WORKERS = 8
DEFAULT_PROBE_TARGETS = [
    ("api_openai", "https://api.openai.com"),
    ("auth_openai", "https://auth.openai.com"),
    ("chatgpt", "https://chatgpt.com"),
    ("platform_openai", "https://platform.openai.com"),
]
DEFAULT_SERVICE_RULES = [
    "DOMAIN-SUFFIX,openai.com,AI_AUTO",
    "DOMAIN-SUFFIX,chatgpt.com,AI_AUTO",
    "DOMAIN-SUFFIX,oaistatic.com,AI_AUTO",
    "DOMAIN-SUFFIX,oaiusercontent.com,AI_AUTO",
]

BASE_CONFIG_KEYS = [
    "mixed-port",
    "socks-port",
    "port",
    "redir-port",
    "tproxy-port",
    "allow-lan",
    "bind-address",
    "authentication",
    "skip-auth-prefixes",
    "mode",
    "log-level",
    "ipv6",
    "unified-delay",
    "tcp-concurrent",
    "find-process-mode",
    "global-client-fingerprint",
    "geodata-mode",
    "geodata-loader",
    "geo-auto-update",
    "geo-update-interval",
    "geox-url",
    "interface-name",
    "routing-mark",
    "experimental",
    "hosts",
    "dns",
    "sniffer",
    "tun",
    "ntp",
    "profile",
    "external-controller",
    "external-controller-cors",
    "external-ui",
    "secret",
]

INFO_PATTERNS = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in [
        r"剩余流量",
        r"套餐到期",
        r"订阅到期",
        r"到期时间",
        r"过期时间",
        r"官网",
        r"公告",
        r"提示[:：]",
        r"更新地址",
        r"使用说明",
        r"客服",
        r"群组",
        r"TG群",
        r"有问题",
        r"联系我们",
    ]
]

BLOCKED_REGION_PATTERNS = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in [
        r"香港",
        r"hong\s*kong",
        r"hongkong",
        r"(?<![a-z])hk(?![a-z])",
        r"俄罗斯",
        r"俄羅斯",
        r"俄国",
        r"俄國",
        r"russia",
        r"moscow",
        r"(?<![a-z])ru(?![a-z])",
    ]
]


class ClashAutomationError(RuntimeError):
    pass


def load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise ClashAutomationError(f"Missing YAML file: {path}")
    content = path.read_text(encoding="utf-8")
    data = yaml.safe_load(content)
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ClashAutomationError(f"Expected YAML mapping in {path}")
    return data


def dump_yaml(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rendered = yaml.safe_dump(data, allow_unicode=True, sort_keys=False, width=120)
    path.write_text(rendered, encoding="utf-8")


def write_status(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def make_session(trust_env: bool = False) -> requests.Session:
    session = requests.Session()
    session.trust_env = trust_env
    session.headers.update({"User-Agent": "server-clash-merge/1.0"})
    return session


def slugify(value: str) -> str:
    text = re.sub(r"[^A-Za-z0-9._-]+", "_", value.strip())
    return text.strip("._-") or "source"


def redact_url(url: str | None) -> str | None:
    if not url:
        return None
    try:
        parsed = urlsplit(url)
        redacted_query = urlencode([(key, "***") for key, _ in parse_qsl(parsed.query, keep_blank_values=True)])
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, redacted_query, parsed.fragment))
    except Exception:
        return "<redacted>"


def default_service_target_config() -> dict[str, Any]:
    return {
        "selection_probe_url": DEFAULT_SELECTION_PROBE_URL,
        "probe_timeout_ms": DEFAULT_TARGET_PROBE_TIMEOUT_MS,
        "probe_targets": [{"name": name, "url": url} for name, url in DEFAULT_PROBE_TARGETS],
        "route_rules": list(DEFAULT_SERVICE_RULES),
    }


def load_service_target_config(path: Path) -> dict[str, Any]:
    raw = default_service_target_config()
    if path.exists():
        data = load_yaml(path)
        if "selection_probe_url" in data:
            raw["selection_probe_url"] = data["selection_probe_url"]
        if "probe_timeout_ms" in data:
            raw["probe_timeout_ms"] = data["probe_timeout_ms"]
        if "probe_targets" in data:
            raw["probe_targets"] = data["probe_targets"]
        if "route_rules" in data:
            raw["route_rules"] = data["route_rules"]

    selection_probe_url = str(raw.get("selection_probe_url") or DEFAULT_SELECTION_PROBE_URL).strip()
    if not selection_probe_url:
        selection_probe_url = DEFAULT_SELECTION_PROBE_URL

    probe_timeout_ms_raw = raw.get("probe_timeout_ms")
    try:
        probe_timeout_ms = int(probe_timeout_ms_raw)
    except Exception:
        probe_timeout_ms = DEFAULT_TARGET_PROBE_TIMEOUT_MS
    if probe_timeout_ms <= 0:
        probe_timeout_ms = DEFAULT_TARGET_PROBE_TIMEOUT_MS

    probe_targets: list[tuple[str, str]] = []
    for item in raw.get("probe_targets") or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        url = str(item.get("url") or "").strip()
        if name and url:
            probe_targets.append((name, url))
    if not probe_targets:
        probe_targets = list(DEFAULT_PROBE_TARGETS)

    route_rules: list[str] = []
    for item in raw.get("route_rules") or []:
        rule = str(item or "").strip()
        if rule:
            route_rules.append(rule)
    if not route_rules:
        route_rules = list(DEFAULT_SERVICE_RULES)

    return {
        "selection_probe_url": selection_probe_url,
        "probe_timeout_ms": probe_timeout_ms,
        "probe_targets": probe_targets,
        "route_rules": route_rules,
    }


def load_sources_config(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise ClashAutomationError(f"Missing sources config: {path}")

    data = load_yaml(path)
    raw_items = data.get("sources")
    if not isinstance(raw_items, list):
        raise ClashAutomationError(f"Expected sources list in {path}")

    sources: list[dict[str, Any]] = []
    for index, item in enumerate(raw_items, start=1):
        if not isinstance(item, dict):
            continue
        if item.get("enabled", True) is False:
            continue

        source_type = str(item.get("type") or "file").strip().lower()
        if source_type not in {"file", "url"}:
            raise ClashAutomationError(f"Unsupported source type {source_type!r} in {path}")

        raw_name = str(item.get("name") or item.get("id") or "").strip()
        if source_type == "file":
            raw_path = str(item.get("path") or "").strip()
            if not raw_path:
                raise ClashAutomationError(f"Missing file path for source #{index} in {path}")
            source_path = Path(raw_path).expanduser()
            if not source_path.is_absolute():
                source_path = (path.parent / source_path).resolve()
            name = raw_name or source_path.stem
            sources.append(
                {
                    "name": name,
                    "type": "file",
                    "path": source_path,
                    "cache_path": None,
                }
            )
            continue

        raw_url = str(item.get("url") or "").strip()
        if not raw_url:
            raise ClashAutomationError(f"Missing url for source #{index} in {path}")
        name = raw_name or f"remote_{index}"

        raw_cache_path = str(item.get("cache_path") or "").strip()
        if raw_cache_path:
            cache_path = Path(raw_cache_path).expanduser()
            if not cache_path.is_absolute():
                cache_path = (path.parent / cache_path).resolve()
        else:
            cache_path = (path.parent / "cache" / f"{slugify(name)}.yaml").resolve()

            sources.append(
                {
                    "name": name,
                    "type": "url",
                    "url": raw_url,
                    "path": None,
                    "cache_path": cache_path,
                    "use_proxy": bool(item.get("use_proxy", False)),
                }
            )

    if not sources:
        raise ClashAutomationError(f"No enabled sources in {path}")
    return sources


def fetch_source_snapshot(
    source: dict[str, Any],
    timeout: int,
) -> tuple[dict[str, Any], str, str | None]:
    if source["type"] == "file":
        data = load_yaml(source["path"])
        return data, "file", None

    warning = None
    cache_path: Path = source["cache_path"]
    session = make_session(trust_env=bool(source.get("use_proxy")))
    try:
        response = session.get(source["url"], timeout=timeout)
        response.raise_for_status()
        data = yaml.safe_load(response.text)
        if isinstance(data, dict) and isinstance(data.get("proxies"), list):
            dump_yaml(cache_path, data)
            return data, "remote", None
        warning = "remote source did not contain a valid proxies list, fell back to cache"
    except Exception as exc:
        warning = f"remote refresh failed, fell back to cache: {exc}"

    if cache_path.exists():
        return load_yaml(cache_path), "cache", warning
    raise ClashAutomationError(f"Remote source {source['name']!r} failed and no cache exists.")


def is_real_proxy(proxy: dict[str, Any]) -> bool:
    if not isinstance(proxy, dict):
        return False
    if not proxy.get("name") or not proxy.get("type"):
        return False
    return any(key in proxy for key in ("server", "servername", "peer", "ip", "interface-name"))


def is_informational_proxy(name: str) -> bool:
    return any(pattern.search(name) for pattern in INFO_PATTERNS)


def is_blocked_region(text: str) -> bool:
    return any(pattern.search(text) for pattern in BLOCKED_REGION_PATTERNS)


def proxy_search_blob(proxy: dict[str, Any]) -> str:
    values: list[str] = [str(proxy.get("name") or ""), str(proxy.get("server") or "")]
    for key in ("servername", "peer", "sni"):
        if proxy.get(key):
            values.append(str(proxy[key]))

    plugin_opts = proxy.get("plugin-opts")
    if isinstance(plugin_opts, dict) and plugin_opts.get("host"):
        values.append(str(plugin_opts["host"]))

    ws_opts = proxy.get("ws-opts")
    if isinstance(ws_opts, dict):
        headers = ws_opts.get("headers")
        if isinstance(headers, dict) and headers.get("Host"):
            values.append(str(headers["Host"]))

    return " ".join(values)


def is_blocked_region_proxy(proxy: dict[str, Any]) -> bool:
    return is_blocked_region(proxy_search_blob(proxy))


def proxy_signature(proxy: dict[str, Any]) -> str:
    normalized = {key: value for key, value in proxy.items() if key != "name"}
    return json.dumps(normalized, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def dedupe_keep_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            output.append(value)
    return output


def collect_sources(
    sources: list[dict[str, Any]],
    timeout: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    all_proxies: list[dict[str, Any]] = []
    source_summaries: list[dict[str, Any]] = []
    signatures: set[str] = set()

    for source in sources:
        snapshot, origin, warning = fetch_source_snapshot(source, timeout)
        proxies = snapshot.get("proxies", [])
        if not isinstance(proxies, list):
            proxies = []

        source_name = str(source["name"])
        kept = 0
        dropped_info = 0
        dropped_invalid = 0
        duplicated = 0

        for proxy in proxies:
            if not is_real_proxy(proxy):
                dropped_invalid += 1
                continue

            original_name = str(proxy["name"])
            if is_informational_proxy(original_name):
                dropped_info += 1
                continue

            prepared = copy.deepcopy(proxy)
            prepared["name"] = f"[{source_name}] {original_name}"
            signature = proxy_signature(prepared)
            if signature in signatures:
                duplicated += 1
                continue

            signatures.add(signature)
            all_proxies.append(prepared)
            kept += 1

        summary: dict[str, Any] = {
            "name": source_name,
            "origin": origin,
            "warning": warning,
            "kept": kept,
            "dropped_informational": dropped_info,
            "dropped_invalid": dropped_invalid,
            "dropped_duplicate": duplicated,
        }
        if source["type"] == "file":
            summary["path"] = str(source["path"])
        else:
            summary["url_redacted"] = redact_url(source.get("url"))
            summary["cache_path"] = str(source["cache_path"])
        source_summaries.append(summary)

    if not all_proxies:
        raise ClashAutomationError("No valid proxies were collected from the configured sources.")

    return all_proxies, source_summaries


def split_allowed_and_blocked(proxies: list[dict[str, Any]]) -> tuple[list[str], list[str]]:
    allowed: list[str] = []
    blocked: list[str] = []
    for proxy in proxies:
        name = str(proxy["name"])
        if is_blocked_region_proxy(proxy):
            blocked.append(name)
        else:
            allowed.append(name)
    return allowed, blocked


def build_config(
    base_config: dict[str, Any],
    proxies: list[dict[str, Any]],
    allowed_names: list[str],
    blocked_names: list[str],
    auto_names: list[str] | None = None,
    selection_probe_url: str = DEFAULT_SELECTION_PROBE_URL,
    route_rules: list[str] | None = None,
) -> dict[str, Any]:
    if not allowed_names:
        raise ClashAutomationError("All merged nodes were filtered out. Adjust the blocked region rules first.")

    selected_auto_names = dedupe_keep_order(auto_names or allowed_names)
    if not selected_auto_names:
        raise ClashAutomationError("No candidate nodes are available for AI_AUTO.")

    config: dict[str, Any] = {}
    for key in BASE_CONFIG_KEYS:
        if key in base_config:
            config[key] = copy.deepcopy(base_config[key])

    config["mode"] = "rule"
    profile = dict(config.get("profile") or {})
    profile["store-selected"] = True
    config["profile"] = profile
    config["proxies"] = proxies

    proxy_groups: list[dict[str, Any]] = [
        {
            "name": "AI_AUTO",
            "type": "url-test",
            "url": selection_probe_url,
            "interval": DEFAULT_INTERVAL,
            "tolerance": DEFAULT_TOLERANCE,
            "proxies": selected_auto_names,
        },
        {
            "name": "AI_STABLE",
            "type": "fallback",
            "url": selection_probe_url,
            "interval": DEFAULT_INTERVAL,
            "proxies": selected_auto_names,
        },
        {
            "name": "AI_ALLOWED",
            "type": "select",
            "proxies": dedupe_keep_order(["AI_AUTO", "AI_STABLE", *selected_auto_names, *allowed_names, "DIRECT"]),
        },
        {
            "name": "ALL_NODES",
            "type": "select",
            "proxies": dedupe_keep_order([*allowed_names, *blocked_names, "DIRECT"]),
        },
    ]

    if blocked_names:
        proxy_groups.append(
            {
                "name": "BLOCKED_REGIONS",
                "type": "select",
                "proxies": dedupe_keep_order(blocked_names),
            }
        )

    proxy_groups.append(
        {
            "name": "GLOBAL",
            "type": "select",
            "proxies": dedupe_keep_order(
                [
                    "AI_AUTO",
                    "AI_STABLE",
                    "AI_ALLOWED",
                    *(["BLOCKED_REGIONS"] if blocked_names else []),
                    "ALL_NODES",
                    "DIRECT",
                ]
            ),
        }
    )

    config["proxy-groups"] = proxy_groups
    config["rules"] = [*(route_rules or DEFAULT_SERVICE_RULES), "MATCH,GLOBAL"]
    return config


class ControllerClient:
    def __init__(self, base_url: str, secret: str | None):
        self.base_url = base_url.rstrip("/")
        self.secret = secret
        self.session = make_session()
        if secret:
            self.session.headers["Authorization"] = f"Bearer {secret}"

    def get_json(self, path: str) -> dict[str, Any]:
        response = self.session.get(f"{self.base_url}{path}", timeout=DEFAULT_TIMEOUT)
        response.raise_for_status()
        return response.json()

    def put_json(self, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any] | None:
        response = self.session.put(f"{self.base_url}{path}", json=payload, timeout=DEFAULT_TIMEOUT)
        response.raise_for_status()
        if not response.content:
            return None
        return response.json()

    def probe_delay(self, proxy_name: str, url: str, timeout_ms: int) -> int | None:
        session = make_session()
        if self.secret:
            session.headers["Authorization"] = f"Bearer {self.secret}"
        response = session.get(
            f"{self.base_url}/proxies/{quote(proxy_name, safe='')}/delay",
            params={"url": url, "timeout": timeout_ms},
            timeout=max(DEFAULT_TIMEOUT, timeout_ms / 1000 + 5),
        )
        if response.status_code != 200:
            return None
        try:
            payload = response.json()
        except ValueError:
            return None
        delay = payload.get("delay")
        return int(delay) if isinstance(delay, (int, float)) and int(delay) > 0 else None


def build_controller(base_config: dict[str, Any], controller: str | None, secret: str | None) -> ControllerClient:
    controller_value = controller or base_config.get("external-controller")
    if not controller_value:
        raise ClashAutomationError("No external-controller configured. Provide --controller or add it to base config.")

    secret_value = secret if secret is not None else base_config.get("secret")
    if str(controller_value).startswith("http://") or str(controller_value).startswith("https://"):
        base_url = str(controller_value)
    else:
        base_url = f"http://{controller_value}"
    return ControllerClient(base_url, str(secret_value) if secret_value else None)


def wait_for_group(client: ControllerClient, group_name: str, timeout_seconds: int = 10) -> dict[str, Any]:
    deadline = time.time() + timeout_seconds
    last_snapshot: dict[str, Any] | None = None
    while time.time() < deadline:
        snapshot = client.get_json("/proxies")
        last_snapshot = snapshot
        proxies = snapshot.get("proxies", {})
        if group_name in proxies:
            return snapshot
        time.sleep(0.5)
    raise ClashAutomationError(f"Timed out waiting for group {group_name!r} to appear in Clash: {last_snapshot}")


def apply_generated_config(client: ControllerClient, config_path: Path, global_group: str) -> dict[str, Any]:
    client.put_json("/configs?force=true", {"path": str(config_path)})
    snapshot = wait_for_group(client, "GLOBAL")
    client.put_json("/proxies/GLOBAL", {"name": global_group})
    return snapshot


def probe_proxy_targets(
    client: ControllerClient,
    proxy_name: str,
    timeout_ms: int,
    probe_targets: list[tuple[str, str]],
) -> dict[str, Any]:
    targets: dict[str, dict[str, Any]] = {}
    ok = True
    for label, url in probe_targets:
        delay = client.probe_delay(proxy_name, url, timeout_ms)
        target_result: dict[str, Any] = {"url": url, "ok": delay is not None}
        if delay is not None:
            target_result["delay_ms"] = delay
        else:
            ok = False
        targets[label] = target_result
        if not ok:
            break
    return {"name": proxy_name, "ok": ok, "targets": targets}


def qualify_proxy_candidates(
    client: ControllerClient,
    candidate_names: list[str],
    probe_targets: list[tuple[str, str]],
    timeout_ms: int = DEFAULT_TARGET_PROBE_TIMEOUT_MS,
    max_workers: int = DEFAULT_TARGET_PROBE_WORKERS,
) -> tuple[list[str], list[dict[str, Any]]]:
    if not candidate_names:
        return [], []

    workers = max(1, min(max_workers, len(candidate_names)))
    results_by_name: dict[str, dict[str, Any]] = {}

    with ThreadPoolExecutor(max_workers=workers) as executor:
        future_map = {
            executor.submit(probe_proxy_targets, client, name, timeout_ms, probe_targets): name for name in candidate_names
        }
        for future in as_completed(future_map):
            name = future_map[future]
            try:
                results_by_name[name] = future.result()
            except Exception as exc:
                results_by_name[name] = {
                    "name": name,
                    "ok": False,
                    "error": str(exc),
                    "targets": {},
                }

    ordered_results = [results_by_name[name] for name in candidate_names]
    qualified_names = [item["name"] for item in ordered_results if item.get("ok")]
    return qualified_names, ordered_results


def direct_connectivity_ok(timeout: int) -> bool:
    session = make_session()
    try:
        response = session.get(DEFAULT_DIRECT_PROBE_URL, timeout=timeout)
        return response.status_code in (200, 204)
    except Exception:
        return False


def proxy_connectivity_ok(mixed_port: int, timeout: int) -> bool:
    session = make_session()
    session.proxies = {
        "http": f"http://127.0.0.1:{mixed_port}",
        "https": f"http://127.0.0.1:{mixed_port}",
    }
    try:
        response = session.get(DEFAULT_PROXY_PROBE_URL, timeout=timeout)
        return response.ok
    except Exception:
        return False


def current_global_now(snapshot: dict[str, Any]) -> str | None:
    proxies = snapshot.get("proxies", {})
    global_group = proxies.get("GLOBAL", {})
    now = global_group.get("now")
    return str(now) if now else None


def group_health(snapshot: dict[str, Any], group_name: str) -> dict[str, Any]:
    proxies = snapshot.get("proxies", {})
    group = proxies.get(group_name, {})
    members = group.get("all", [])
    alive_members = 0
    tested_members = 0
    alive_names: list[str] = []

    for member in members:
        member_info = proxies.get(member, {})
        history = member_info.get("history") or []
        if history:
            tested_members += 1
        if member_info.get("alive"):
            alive_members += 1
            alive_names.append(member)

    return {
        "group": group_name,
        "now": group.get("now"),
        "members": len(members),
        "tested_members": tested_members,
        "alive_members": alive_members,
        "alive_names": alive_names[:10],
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Merge server-side Clash sources and build AI-friendly auto groups.")
    parser.add_argument("--base-config", default=str(DEFAULT_BASE_CONFIG), help="Base Clash config used for ports, DNS and controller.")
    parser.add_argument("--sources-config", default=str(DEFAULT_SOURCES_CONFIG), help="YAML index of local or remote sources.")
    parser.add_argument("--service-targets", default=str(DEFAULT_SERVICE_TARGETS), help="YAML file that defines required probe targets.")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT), help="Final merged config path.")
    parser.add_argument("--probe-output", default=str(DEFAULT_PROBE_OUTPUT), help="Temporary probe config path.")
    parser.add_argument("--status", default=str(DEFAULT_STATUS), help="Status JSON path.")
    parser.add_argument("--controller", default=None, help="Override external-controller, for example 127.0.0.1:9090.")
    parser.add_argument("--secret", default=None, help="Controller secret if required.")
    parser.add_argument("--generate-only", action="store_true", help="Generate the merged YAML but do not hot-reload it.")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT, help="HTTP timeout in seconds.")
    parser.add_argument("--probe-workers", type=int, default=DEFAULT_TARGET_PROBE_WORKERS, help="Max concurrent node probes.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    base_config_path = Path(args.base_config).expanduser().resolve()
    sources_config_path = Path(args.sources_config).expanduser().resolve()
    service_targets_path = Path(args.service_targets).expanduser().resolve()
    output_path = Path(args.output).expanduser().resolve()
    probe_output_path = Path(args.probe_output).expanduser().resolve()
    status_path = Path(args.status).expanduser().resolve()

    base_config = load_yaml(base_config_path)
    sources = load_sources_config(sources_config_path)
    service_config = load_service_target_config(service_targets_path)

    merged_proxies, source_summaries = collect_sources(sources, args.timeout)
    allowed_names, blocked_names = split_allowed_and_blocked(merged_proxies)
    selection_probe_url = str(service_config["selection_probe_url"])
    probe_timeout_ms = int(service_config["probe_timeout_ms"])
    probe_targets = list(service_config["probe_targets"])
    route_rules = list(service_config["route_rules"])

    probe_config = build_config(
        base_config,
        merged_proxies,
        allowed_names,
        blocked_names,
        auto_names=allowed_names,
        selection_probe_url=selection_probe_url,
        route_rules=route_rules,
    )

    status: dict[str, Any] = {
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "base_config": str(base_config_path),
        "sources_config": str(sources_config_path),
        "service_targets": str(service_targets_path),
        "output_config": str(output_path),
        "probe_output_config": str(probe_output_path),
        "generate_only": args.generate_only,
        "total_proxies": len(merged_proxies),
        "allowed_proxies": len(allowed_names),
        "blocked_proxies": len(blocked_names),
        "blocked_region_patterns": [pattern.pattern for pattern in BLOCKED_REGION_PATTERNS],
        "selection_probe_url": selection_probe_url,
        "required_probe_targets": [url for _, url in probe_targets],
        "route_rules": route_rules,
        "sources": source_summaries,
        "controller_applied": False,
        "global_group": DEFAULT_GLOBAL_GROUP,
    }

    if args.generate_only:
        dump_yaml(output_path, probe_config)
        status["qualification_skipped"] = True
        write_status(status_path, status)
        print(f"Generated {output_path}")
        print(f"Total proxies: {len(merged_proxies)}")
        print(f"Allowed proxies: {len(allowed_names)}")
        print(f"Blocked proxies: {len(blocked_names)}")
        return 0

    client = build_controller(base_config, args.controller, args.secret)
    dump_yaml(probe_output_path, probe_config)
    apply_generated_config(client, probe_output_path, DEFAULT_GLOBAL_GROUP)
    qualified_names, qualification_results = qualify_proxy_candidates(
        client,
        allowed_names,
        probe_targets=probe_targets,
        timeout_ms=probe_timeout_ms,
        max_workers=args.probe_workers,
    )
    status["qualified_proxies"] = len(qualified_names)
    status["unqualified_proxies"] = len(allowed_names) - len(qualified_names)
    status["qualification_results"] = qualification_results

    if not qualified_names:
        status["direct_connectivity"] = direct_connectivity_ok(args.timeout)
        write_status(status_path, status)
        print("Merged candidates were loaded, but no node passed all required probes.", file=sys.stderr)
        return 4

    merged_config = build_config(
        base_config,
        merged_proxies,
        allowed_names,
        blocked_names,
        auto_names=qualified_names,
        selection_probe_url=selection_probe_url,
        route_rules=route_rules,
    )
    dump_yaml(output_path, merged_config)
    apply_generated_config(client, output_path, DEFAULT_GLOBAL_GROUP)
    status["controller_applied"] = True

    mixed_port = int(merged_config.get("mixed-port") or merged_config.get("port") or 7890)
    time.sleep(2)
    latest_snapshot = client.get_json("/proxies")
    auto_health = group_health(latest_snapshot, "AI_AUTO")
    stable_health = group_health(latest_snapshot, "AI_STABLE")
    proxy_ok = bool(auto_health["alive_members"] or stable_health["alive_members"])

    status["proxy_health"] = {
        "AI_AUTO": auto_health,
        "AI_STABLE": stable_health,
    }
    status["proxy_connectivity"] = proxy_ok
    status["global_now"] = current_global_now(latest_snapshot)
    status["auto_now"] = auto_health["now"]

    direct_ok = None
    if not proxy_ok:
        direct_ok = direct_connectivity_ok(args.timeout)
        if not direct_ok:
            proxy_ok = proxy_connectivity_ok(mixed_port, args.timeout)
    status["direct_connectivity"] = direct_ok
    write_status(status_path, status)

    print(f"Generated {output_path}")
    print(f"Total proxies: {len(merged_proxies)}")
    print(f"Allowed proxies: {len(allowed_names)}")
    print(f"Blocked proxies: {len(blocked_names)}")
    print(f"Qualified proxies: {len(qualified_names)}")
    print(f"GLOBAL now: {status.get('global_now')}")
    print(f"AI_AUTO now: {status.get('auto_now')}")
    print(f"Proxy check: {'OK' if proxy_ok else 'FAILED'}")
    return 0 if proxy_ok else 3


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ClashAutomationError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(2)
