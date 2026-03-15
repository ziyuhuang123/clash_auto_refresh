CLASH_PROXY_URL_DEFAULT="${CLASH_PROXY_URL_DEFAULT:-http://127.0.0.1:7890}"
CLASH_NO_PROXY_DEFAULT="${CLASH_NO_PROXY_DEFAULT:-localhost,127.0.0.1,::1}"
CLASH_BIN_DEFAULT="${CLASH_BIN_DEFAULT:-/cephfs/zyhuang/clash/clash}"
CLASH_DIR_DEFAULT="${CLASH_DIR_DEFAULT:-/cephfs/zyhuang/clash}"
CLASH_AUTO_ROOT="${CLASH_AUTO_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}"
CLASH_AUTO_REFRESH_SCRIPT="${CLASH_AUTO_REFRESH_SCRIPT:-$CLASH_AUTO_ROOT/run_refresh.sh}"
CLASH_AUTO_REFRESH_ON_ENABLE="${CLASH_AUTO_REFRESH_ON_ENABLE:-1}"

_vpn_enable_env() {
    local proxy="${1:-$CLASH_PROXY_URL_DEFAULT}"
    export http_proxy="$proxy"
    export https_proxy="$proxy"
    export HTTP_PROXY="$proxy"
    export HTTPS_PROXY="$proxy"
    export no_proxy="$CLASH_NO_PROXY_DEFAULT"
    export NO_PROXY="$CLASH_NO_PROXY_DEFAULT"
}

vpnoff() {
    unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY
    unset all_proxy ALL_PROXY
    export no_proxy="$CLASH_NO_PROXY_DEFAULT"
    export NO_PROXY="$CLASH_NO_PROXY_DEFAULT"
}

vpnrefresh() {
    if [ ! -x "$CLASH_AUTO_REFRESH_SCRIPT" ]; then
        printf 'refresh script not found: %s\n' "$CLASH_AUTO_REFRESH_SCRIPT" >&2
        return 1
    fi
    "$CLASH_AUTO_REFRESH_SCRIPT" "$@"
}

vpnstatus() {
    printf 'http_proxy=%s\n' "${http_proxy:-<unset>}"
    printf 'https_proxy=%s\n' "${https_proxy:-<unset>}"
    printf 'HTTP_PROXY=%s\n' "${HTTP_PROXY:-<unset>}"
    printf 'HTTPS_PROXY=%s\n' "${HTTPS_PROXY:-<unset>}"
    printf 'no_proxy=%s\n' "${no_proxy:-<unset>}"
    printf 'NO_PROXY=%s\n' "${NO_PROXY:-<unset>}"
}

novpn() {
    if [ "$#" -eq 0 ]; then
        printf 'usage: novpn <command> [args...]\n' >&2
        return 2
    fi
    env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY "$@"
}

withvpn() {
    if [ "$#" -eq 0 ]; then
        printf 'usage: withvpn <command> [args...]\n' >&2
        return 2
    fi
    env \
        http_proxy="$CLASH_PROXY_URL_DEFAULT" \
        https_proxy="$CLASH_PROXY_URL_DEFAULT" \
        HTTP_PROXY="$CLASH_PROXY_URL_DEFAULT" \
        HTTPS_PROXY="$CLASH_PROXY_URL_DEFAULT" \
        no_proxy="$CLASH_NO_PROXY_DEFAULT" \
        NO_PROXY="$CLASH_NO_PROXY_DEFAULT" \
        "$@"
}

clashup() {
    if pgrep -f -- "$CLASH_BIN_DEFAULT -d $CLASH_DIR_DEFAULT" >/dev/null 2>&1; then
        printf 'clash is already running\n'
        return 0
    fi
    nohup "$CLASH_BIN_DEFAULT" -d "$CLASH_DIR_DEFAULT" >/dev/null 2>&1 &
    sleep 1
    if pgrep -f -- "$CLASH_BIN_DEFAULT -d $CLASH_DIR_DEFAULT" >/dev/null 2>&1; then
        printf 'clash started\n'
        return 0
    fi
    printf 'failed to start clash\n' >&2
    return 1
}

vpnon() {
    clashup || return $?
    _vpn_enable_env "$1"

    if [ "$CLASH_AUTO_REFRESH_ON_ENABLE" = "1" ] && [ -x "$CLASH_AUTO_REFRESH_SCRIPT" ]; then
        nohup "$CLASH_AUTO_REFRESH_SCRIPT" >/dev/null 2>&1 &
        printf 'vpn enabled; refresh started in background\n'
    else
        printf 'vpn enabled\n'
    fi
    vpnstatus
}

vpnon_wait() {
    clashup || return $?
    _vpn_enable_env "$1"
    vpnrefresh
    vpnstatus
}

vpnboot() {
    vpnon "$@"
}
