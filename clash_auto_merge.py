from __future__ import annotations

import argparse
import copy
import ctypes
import json
import os
import re
import sys
import time
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from pathlib import Path
from typing import Any

import requests
import yaml


VERGE_DIR_NAME = "io.github.clash-verge-rev.clash-verge-rev"
DEFAULT_OUTPUT_NAME = "codex_auto_merge.yaml"
DEFAULT_STATUS_NAME = "codex_auto_merge_status.json"
DEFAULT_LATENCY_URL = "https://auth.openai.com"
DEFAULT_PROXY_PROBE_URL = "https://chatgpt.com"
DEFAULT_DIRECT_PROBE_URL = "https://www.gstatic.com/generate_204"
DEFAULT_TIMEOUT = 15
DEFAULT_INTERVAL = 300
DEFAULT_TOLERANCE = 80
DEFAULT_GLOBAL_GROUP = "AI_AUTO"

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


def detect_verge_dir() -> Path:
    candidates = []
    appdata = os.environ.get("APPDATA")
    localappdata = os.environ.get("LOCALAPPDATA")
    userprofile = os.environ.get("USERPROFILE")
    if appdata:
        candidates.append(Path(appdata) / VERGE_DIR_NAME)
    if localappdata:
        candidates.append(Path(localappdata) / VERGE_DIR_NAME)
    if userprofile:
        candidates.append(Path(userprofile) / ".config" / "clash")

    for candidate in candidates:
        if (candidate / "profiles.yaml").exists() and (candidate / "config.yaml").exists():
            return candidate

    raise ClashAutomationError("Could not find the Clash Verge config directory.")


def load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise ClashAutomationError(f"Missing YAML file: {path}")
    content = path.read_text(encoding="utf-8")
    data = yaml.safe_load(content)
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ClashAutomationError(f"Expected a YAML mapping in {path}")
    return data


def dump_yaml(path: Path, data: dict[str, Any]) -> None:
    rendered = yaml.safe_dump(
        data,
        allow_unicode=True,
        sort_keys=False,
        width=120,
    )
    path.write_text(rendered, encoding="utf-8")


def redact_url(url: str | None) -> str | None:
    if not url:
        return None
    try:
        parsed = urlsplit(url)
        redacted_query = urlencode([(key, "***") for key, _ in parse_qsl(parsed.query, keep_blank_values=True)])
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, redacted_query, parsed.fragment))
    except Exception:  # noqa: BLE001
        return "<redacted>"


def make_session() -> requests.Session:
    session = requests.Session()
    session.trust_env = False
    session.headers.update({"User-Agent": "clash-auto-merge/1.0"})
    return session


def fetch_profile_snapshot(
    item: dict[str, Any],
    local_path: Path,
    session: requests.Session,
    offline: bool,
    timeout: int,
) -> tuple[dict[str, Any], str, str | None]:
    warning = None
    if not offline and item.get("url"):
        try:
            response = session.get(item["url"], timeout=timeout)
            response.raise_for_status()
            data = yaml.safe_load(response.text)
            if isinstance(data, dict) and data.get("proxies"):
                return data, "remote", None
            warning = "remote profile did not contain a valid proxies list, fell back to local cache"
        except Exception as exc:  # noqa: BLE001
            warning = f"remote refresh failed, fell back to local cache: {exc}"

    data = load_yaml(local_path)
    return data, "cache", warning


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


def dedupe_keep_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            output.append(value)
    return output


def proxy_signature(proxy: dict[str, Any]) -> str:
    normalized = {key: value for key, value in proxy.items() if key != "name"}
    return json.dumps(normalized, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def collect_remote_profiles(
    verge_dir: Path,
    session: requests.Session,
    offline: bool,
    timeout: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    profiles_index = load_yaml(verge_dir / "profiles.yaml")
    remote_items = [item for item in profiles_index.get("items", []) if item.get("type") == "remote"]
    if not remote_items:
        raise ClashAutomationError("No remote subscriptions were found in profiles.yaml.")

    all_proxies: list[dict[str, Any]] = []
    source_summaries: list[dict[str, Any]] = []
    signatures: set[str] = set()

    for item in remote_items:
        local_path = verge_dir / "profiles" / item["file"]
        snapshot, origin, warning = fetch_profile_snapshot(item, local_path, session, offline, timeout)
        proxies = snapshot.get("proxies", [])
        if not isinstance(proxies, list):
            proxies = []

        source_name = item.get("name") or item.get("uid") or item.get("file")
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

        source_summaries.append(
            {
                "name": source_name,
                "origin": origin,
                "warning": warning,
                "kept": kept,
                "dropped_informational": dropped_info,
                "dropped_invalid": dropped_invalid,
                "dropped_duplicate": duplicated,
                "url_redacted": redact_url(item.get("url")),
                "local_file_name": local_path.name,
            }
        )

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
) -> dict[str, Any]:
    if not allowed_names:
        raise ClashAutomationError("All merged nodes were filtered out. Adjust the blocked region rules first.")

    config: dict[str, Any] = {}
    for key in BASE_CONFIG_KEYS:
        if key in base_config:
            config[key] = copy.deepcopy(base_config[key])

    config["mode"] = "global"
    profile = dict(config.get("profile") or {})
    profile["store-selected"] = True
    config["profile"] = profile
    config["proxies"] = proxies

    proxy_groups: list[dict[str, Any]] = [
        {
            "name": "AI_AUTO",
            "type": "url-test",
            "url": DEFAULT_LATENCY_URL,
            "interval": DEFAULT_INTERVAL,
            "tolerance": DEFAULT_TOLERANCE,
            "proxies": allowed_names,
        },
        {
            "name": "AI_STABLE",
            "type": "fallback",
            "url": DEFAULT_LATENCY_URL,
            "interval": DEFAULT_INTERVAL,
            "proxies": allowed_names,
        },
        {
            "name": "AI_ALLOWED",
            "type": "select",
            "proxies": dedupe_keep_order(["AI_AUTO", "AI_STABLE", *allowed_names, "DIRECT"]),
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
            "name": "ALL_NODES",
            "type": "select",
            "proxies": dedupe_keep_order([*allowed_names, *blocked_names, "DIRECT"]),
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
    config["rules"] = ["MATCH,GLOBAL"]
    return config


class ControllerClient:
    def __init__(self, base_url: str, secret: str | None):
        self.base_url = base_url.rstrip("/")
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


def build_controller(base_config: dict[str, Any]) -> ControllerClient:
    controller = base_config.get("external-controller")
    if not controller:
        raise ClashAutomationError("The generated base config does not define external-controller.")
    secret = base_config.get("secret")
    return ControllerClient(f"http://{controller}", secret)


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
    raise ClashAutomationError(f"Timed out waiting for group {group_name!r} to appear in Clash.")


def apply_generated_config(client: ControllerClient, config_path: Path, global_group: str) -> dict[str, Any]:
    client.put_json("/configs?force=true", {"path": str(config_path)})
    snapshot = wait_for_group(client, "GLOBAL")
    client.put_json("/proxies/GLOBAL", {"name": global_group})
    return snapshot


def direct_connectivity_ok(timeout: int) -> bool:
    session = make_session()
    try:
        response = session.get(DEFAULT_DIRECT_PROBE_URL, timeout=timeout)
        return response.status_code in (200, 204)
    except Exception:  # noqa: BLE001
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
    except Exception:  # noqa: BLE001
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


def show_popup(message: str, title: str) -> None:
    if os.name != "nt":
        return
    try:
        ctypes.windll.user32.MessageBoxW(0, message, title, 0x30)
    except Exception:  # noqa: BLE001
        pass


def write_status(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Merge all Clash Verge subscriptions into one auto-selected AI-friendly config.")
    parser.add_argument("--offline", action="store_true", help="Use local subscription cache only.")
    parser.add_argument("--generate-only", action="store_true", help="Generate the merged YAML but do not hot-reload it.")
    parser.add_argument("--no-popup", action="store_true", help="Do not show a Windows popup when checks fail.")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT, help="HTTP timeout in seconds.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    script_dir = Path(__file__).resolve().parent
    verge_dir = detect_verge_dir()
    output_path = verge_dir / "profiles" / DEFAULT_OUTPUT_NAME
    status_path = script_dir / DEFAULT_STATUS_NAME

    session = make_session()
    base_config = load_yaml(verge_dir / "clash-verge.yaml")
    merged_proxies, source_summaries = collect_remote_profiles(verge_dir, session, args.offline, args.timeout)
    allowed_names, blocked_names = split_allowed_and_blocked(merged_proxies)
    merged_config = build_config(base_config, merged_proxies, allowed_names, blocked_names)
    dump_yaml(output_path, merged_config)

    status: dict[str, Any] = {
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "verge_dir": str(verge_dir),
        "output_config": str(output_path),
        "generate_only": args.generate_only,
        "offline": args.offline,
        "total_proxies": len(merged_proxies),
        "allowed_proxies": len(allowed_names),
        "blocked_proxies": len(blocked_names),
        "sources": source_summaries,
        "controller_applied": False,
        "global_group": DEFAULT_GLOBAL_GROUP,
    }

    if args.generate_only:
        write_status(status_path, status)
        print(f"Generated {output_path}")
        print(f"Allowed proxies: {len(allowed_names)} | Blocked proxies: {len(blocked_names)}")
        return 0

    client = build_controller(base_config)
    snapshot = apply_generated_config(client, output_path, DEFAULT_GLOBAL_GROUP)
    status["controller_applied"] = True

    mixed_port = int(merged_config.get("mixed-port") or merged_config.get("port") or 7897)
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
    print(f"Allowed proxies: {len(allowed_names)} | Blocked proxies: {len(blocked_names)}")
    print(f"GLOBAL now: {status.get('global_now')}")
    print(f"AI_AUTO now: {status.get('auto_now')}")

    if proxy_ok:
        print("Proxy check: OK")
        return 0

    if direct_ok is False:
        message = (
            "Merged config was applied, but the direct connectivity probe failed.\n"
            "This usually means the machine is offline or the local network is down."
        )
        print(message, file=sys.stderr)
        if not args.no_popup:
            show_popup(message, "Clash Auto Merge")
        return 3

    message = (
        "Merged config was applied, but the proxy probe still failed.\n"
        "This usually means the allowed nodes are currently unavailable or blocked."
    )
    print(message, file=sys.stderr)
    if not args.no_popup:
        show_popup(message, "Clash Auto Merge")
    return 4


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ClashAutomationError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1)
