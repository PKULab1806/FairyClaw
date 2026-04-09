"""CLI entrypoint for FairyClaw."""

from __future__ import annotations

import argparse
import atexit
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

from fairyclaw.paths import package_dir

PROXY_ENV_KEYS = (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
)

# Always bypass proxy for loopback so httpx/urllib in child processes never proxy localhost.
_NO_PROXY_LOOPBACK = "127.0.0.1,localhost,::1"

# Child uvicorn processes: cleared on normal shutdown; used by atexit for abnormal exits.
_TRACKED_CHILDREN: list[subprocess.Popen] = []


def _merge_no_proxy(env: dict[str, str]) -> None:
    """Ensure NO_PROXY covers loopback (append to any user value)."""
    for key in ("NO_PROXY", "no_proxy"):
        cur = env.get(key, "").strip()
        if cur:
            if _NO_PROXY_LOOPBACK not in cur:
                env[key] = f"{_NO_PROXY_LOOPBACK},{cur}"
        else:
            env[key] = _NO_PROXY_LOOPBACK


def _http_opener_no_proxy():
    """Opener that does not use HTTP(S)_PROXY (parent CLI may still have proxy env)."""
    import urllib.request

    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _atexit_kill_tracked_children() -> None:
    for proc in list(_TRACKED_CHILDREN):
        if proc.poll() is None:
            try:
                _terminate_process(proc)
            except Exception:
                pass


atexit.register(_atexit_kill_tracked_children)


def _tcp_port_has_listener(port: int, host: str = "127.0.0.1") -> bool:
    """Return True if something accepts TCP connections on host:port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.4)
        try:
            return s.connect_ex((host, port)) == 0
        except OSError:
            return False


def _pids_listening_on_tcp_port(port: int) -> list[int]:
    """Best-effort PIDs listening on TCP port (lsof / ss)."""
    pids: set[int] = set()

    lsof_variants: list[list[str]] = [
        ["lsof", "-t", f"-iTCP:{port}", "-sTCP:LISTEN"],
        ["lsof", "-t", f"-i:{port}"],
        ["lsof", "-t", f"-iTCP:{port}"],
    ]
    for cmd in lsof_variants:
        try:
            r = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=3.0,
                check=False,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue
        if r.returncode != 0 or not (r.stdout or "").strip():
            continue
        for line in r.stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                pids.add(int(line))
            except ValueError:
                continue
        if pids:
            break

    if not pids:
        try:
            r = subprocess.run(
                ["ss", "-lptn", f"sport = :{port}"],
                capture_output=True,
                text=True,
                timeout=3.0,
                check=False,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            r = None
        if r is not None and r.stdout:
            for m in re.finditer(r"pid=(\d+)", r.stdout):
                try:
                    pids.add(int(m.group(1)))
                except ValueError:
                    continue

    return sorted(pids)


def _kill_pids_gracefully(pids: list[int]) -> None:
    for pid in pids:
        if pid <= 0:
            continue
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            continue
        except PermissionError:
            print(f"Warning: no permission to stop PID {pid}", flush=True)
    deadline = time.time() + 5.0
    while time.time() < deadline:
        alive = [p for p in pids if _pid_exists(p)]
        if not alive:
            return
        time.sleep(0.2)
    for pid in pids:
        if _pid_exists(pid):
            try:
                os.kill(pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass


def _pid_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _ensure_ports_free_or_kill(
    business_port: int,
    gateway_port: int,
    *,
    kill_stale: bool,
) -> None:
    busy: list[tuple[int, list[int]]] = []
    for port in (business_port, gateway_port):
        if not _tcp_port_has_listener(port):
            continue
        pids = _pids_listening_on_tcp_port(port)
        busy.append((port, pids))

    if not busy:
        return

    lines = [f"port {port}" + (f" (PIDs: {pids})" if pids else "") for port, pids in busy]
    if kill_stale:
        print("Stopping stale listeners on: " + ", ".join(lines), flush=True)
        seen: set[int] = set()
        for _port, pids in busy:
            for pid in pids:
                seen.add(pid)
        if seen:
            _kill_pids_gracefully(sorted(seen))
        for port, _ in busy:
            if _tcp_port_has_listener(port):
                for fuser_cmd in (
                    ["fuser", "-k", f"{port}/tcp"],
                    ["fuser", "-k", "-n", "tcp", str(port)],
                ):
                    try:
                        subprocess.run(
                            fuser_cmd,
                            capture_output=True,
                            timeout=8.0,
                            check=False,
                        )
                    except (FileNotFoundError, subprocess.TimeoutExpired):
                        pass
        time.sleep(0.6)
        still: list[str] = []
        for port, _ in busy:
            if _tcp_port_has_listener(port):
                still.append(str(port))
        if still:
            raise RuntimeError(
                f"Port(s) still in use after --kill-stale: {', '.join(still)}. "
                "Stop the process manually or pick another port."
            )
        return

    raise RuntimeError(
        "Port(s) already in use (likely a leftover uvicorn from a previous run): "
        + ", ".join(lines)
        + ". Stop them manually or run `fairyclaw start --kill-stale` (default is on; use `--no-kill-stale` to disable)."
    )


def _parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def _candidate_repo_config_dir() -> Path | None:
    cwd_config = Path.cwd() / "config"
    if cwd_config.exists():
        return cwd_config
    parent_config = package_dir().parent / "config"
    if parent_config.exists():
        return parent_config
    return None


def _pick_source(repo_config_dir: Path | None, name: str, example_name: str) -> Path | None:
    if repo_config_dir is None:
        return None
    plain = repo_config_dir / name
    if plain.exists():
        return plain
    example = repo_config_dir / example_name
    if example.exists():
        return example
    return None


def _bundled_config_template(name: str) -> Path | None:
    """Shipped copies of repo `config/*.example` for wheel installs / unknown cwd."""
    p = package_dir() / "config_templates" / name
    return p if p.exists() else None


def _llm_yaml_missing_profiles(path: Path) -> bool:
    """True if file is missing usable profile entries (e.g. old buggy cold start wrote profiles: {})."""
    try:
        import yaml

        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception:
        return True
    if not isinstance(data, dict):
        return True
    profiles = data.get("profiles")
    if not isinstance(profiles, dict) or not profiles:
        return True
    return False


def _sync_runtime_config(runtime_home: Path, no_sync_config: bool) -> tuple[Path, dict[str, str]]:
    runtime_config_dir = runtime_home / "config"
    runtime_config_dir.mkdir(parents=True, exist_ok=True)
    runtime_data_dir = runtime_home / "data"
    runtime_data_dir.mkdir(parents=True, exist_ok=True)
    (runtime_data_dir / "logs").mkdir(parents=True, exist_ok=True)
    (runtime_data_dir / "files").mkdir(parents=True, exist_ok=True)

    repo_config_dir = _candidate_repo_config_dir()
    src_env = _pick_source(repo_config_dir, "fairyclaw.env", "fairyclaw.env.example")
    src_llm = _pick_source(repo_config_dir, "llm_endpoints.yaml", "llm_endpoints.yaml.example")

    env_target = runtime_config_dir / "fairyclaw.env"
    llm_target = runtime_config_dir / "llm_endpoints.yaml"

    if not no_sync_config:
        if src_env is not None:
            shutil.copy2(src_env, env_target)
        else:
            bundled_env = _bundled_config_template("fairyclaw.env.example")
            if bundled_env is not None and not env_target.exists():
                shutil.copy2(bundled_env, env_target)
            elif not env_target.exists():
                env_target.write_text("", encoding="utf-8")
        if src_llm is not None:
            shutil.copy2(src_llm, llm_target)
        else:
            bundled_llm = _bundled_config_template("llm_endpoints.yaml.example")
            need_bundled_llm = not llm_target.exists() or (
                bundled_llm is not None and _llm_yaml_missing_profiles(llm_target)
            )
            if bundled_llm is not None and need_bundled_llm:
                shutil.copy2(bundled_llm, llm_target)
            elif not llm_target.exists():
                raise RuntimeError(
                    "Missing LLM endpoints config and bundled template; reinstall fairyclaw or "
                    "provide config/llm_endpoints.yaml (or llm_endpoints.yaml.example)."
                )

    values = _parse_env_file(env_target)
    return runtime_config_dir, values


def _frontend_root() -> Path | None:
    cwd_web = Path.cwd() / "web"
    if (cwd_web / "package.json").exists():
        return cwd_web
    pkg_web = package_dir().parent / "web"
    if (pkg_web / "package.json").exists():
        return pkg_web
    return None


def _build_frontend(web_dir: Path, token: str, vite_gateway_base_url: str | None) -> None:
    if shutil.which("npm") is None:
        raise RuntimeError("npm not found; install Node.js/npm or run with --skip-build")
    build_env = os.environ.copy()
    build_env["VITE_API_TOKEN"] = token
    if vite_gateway_base_url is None:
        build_env["VITE_GATEWAY_BASE_URL"] = ""
    else:
        build_env["VITE_GATEWAY_BASE_URL"] = vite_gateway_base_url
    subprocess.run(["npm", "ci"], cwd=web_dir, env=build_env, check=True)
    subprocess.run(["npm", "run", "build"], cwd=web_dir, env=build_env, check=True)


def _wait_for_business_ready(
    proc: subprocess.Popen,
    health_url: str,
    *,
    timeout_seconds: float,
    log_interval_seconds: float = 5.0,
) -> None:
    """Block until /healthz responds or the process exits / timeout."""
    import urllib.error
    import urllib.request

    opener = _http_opener_no_proxy()
    deadline = time.time() + timeout_seconds
    next_log = time.time()
    interval = 0.35
    while time.time() < deadline:
        rc = proc.poll()
        if rc is not None:
            raise RuntimeError(
                f"Business process exited before /healthz became ready (exit code {rc}). "
                "Check FAIRYCLAW_* env, port conflicts, and try: "
                f"python -m uvicorn fairyclaw.main:app --host 0.0.0.0 --port <port>"
            )
        try:
            req = urllib.request.Request(health_url, method="GET")
            with opener.open(req, timeout=2.0) as res:
                if res.status == 200:
                    return
        except (urllib.error.URLError, OSError, TimeoutError):
            pass
        if time.time() >= next_log:
            remaining = max(0.0, deadline - time.time())
            print(
                f"Waiting for business API {health_url} ({remaining:.0f}s left) ...",
                flush=True,
            )
            next_log = time.time() + log_interval_seconds
        time.sleep(interval)
        interval = min(interval * 1.12, 2.0)

    raise RuntimeError(
        f"Timed out after {timeout_seconds:.0f}s waiting for {health_url}. "
        "The backend may still be importing or the port may be in use; "
        "try --health-wait-seconds 300 or run uvicorn manually to see errors."
    )


def _wsl_ip() -> str | None:
    try:
        out = subprocess.check_output(["hostname", "-I"], text=True).strip().split()
        if out:
            return out[0]
    except Exception:
        pass
    return None


def _launch_dual_processes(
    env: dict[str, str],
    business_host: str,
    business_port: int,
    gateway_host: str,
    gateway_port: int,
    *,
    health_wait_seconds: float,
    skip_health_check: bool,
) -> int:
    preexec = os.setsid if os.name == "posix" else None
    biz_cmd = [
        sys.executable,
        "-m",
        "uvicorn",
        "fairyclaw.main:app",
        "--host",
        business_host,
        "--port",
        str(business_port),
    ]
    gw_cmd = [
        sys.executable,
        "-m",
        "uvicorn",
        "fairyclaw.gateway.main:app",
        "--host",
        gateway_host,
        "--port",
        str(gateway_port),
    ]

    biz = subprocess.Popen(biz_cmd, env=env, preexec_fn=preexec)
    _TRACKED_CHILDREN.append(biz)
    try:
        health_url = f"http://127.0.0.1:{business_port}/healthz"
        if skip_health_check:
            print("Skipping /healthz wait; giving business process a few seconds to bind ...", flush=True)
            time.sleep(5.0)
            if biz.poll() is not None:
                raise RuntimeError(
                    f"Business process exited during startup grace period (exit code {biz.poll()})."
                )
        else:
            _wait_for_business_ready(biz, health_url, timeout_seconds=health_wait_seconds)
        gw = subprocess.Popen(gw_cmd, env=env, preexec_fn=preexec)
        _TRACKED_CHILDREN.append(gw)
    except Exception:
        if biz.poll() is None:
            _terminate_process(biz)
        try:
            _TRACKED_CHILDREN.remove(biz)
        except ValueError:
            pass
        raise

    print(f"Gateway UI: http://127.0.0.1:{gateway_port}/app")
    ip = _wsl_ip()
    if ip:
        print(f"WSL host access: http://{ip}:{gateway_port}/app")
    print("Press Ctrl+C to stop both processes.")

    children = [biz, gw]

    def _handle(_sig: int, _frame: object) -> None:
        for proc in children:
            if proc.poll() is None:
                _terminate_process(proc)
        raise SystemExit(0)

    signal.signal(signal.SIGINT, _handle)
    signal.signal(signal.SIGTERM, _handle)

    try:
        while True:
            biz_rc = biz.poll()
            gw_rc = gw.poll()
            if biz_rc is not None:
                if gw.poll() is None:
                    _terminate_process(gw)
                return biz_rc
            if gw_rc is not None:
                if biz.poll() is None:
                    _terminate_process(biz)
                return gw_rc
            time.sleep(0.4)
    finally:
        for p in (biz, gw):
            try:
                _TRACKED_CHILDREN.remove(p)
            except ValueError:
                pass
            if p.poll() is None:
                _terminate_process(p)


def _terminate_process(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    if os.name == "posix":
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        except ProcessLookupError:
            return
    else:
        proc.terminate()


def _read_token(config_values: dict[str, str]) -> str:
    return os.getenv("FAIRYCLAW_API_TOKEN") or config_values.get("FAIRYCLAW_API_TOKEN", "sk-fairyclaw-dev-token")


def _start(args: argparse.Namespace) -> int:
    runtime_home = Path(os.getenv("FAIRYCLAW_RUNTIME_HOME", "~/.fairyclaw")).expanduser()
    runtime_config_dir, config_values = _sync_runtime_config(runtime_home, no_sync_config=args.no_sync_config)
    token = _read_token(config_values)

    web_dir = _frontend_root()
    if not args.skip_build and (web_dir is not None or args.force_build):
        if web_dir is None:
            raise RuntimeError("Cannot force web build: missing web/package.json")
        _build_frontend(web_dir, token=token, vite_gateway_base_url=args.vite_gateway_base_url)

    env = os.environ.copy()
    for key in PROXY_ENV_KEYS:
        env.pop(key, None)
    _merge_no_proxy(env)
    if args.no_proxy:
        env["FAIRYCLAW_WEB_PROXY"] = ""
        env["FAIRYCLAW_CAP_WEB_TOOLS__WEB_PROXY"] = ""
        env["FAIRYCLAW_CAP_SOURCED_RESEARCH__WEB_PROXY"] = ""

    for key, value in config_values.items():
        if key and value and key not in env:
            env[key] = value

    # fairyclaw.env often contains relative paths like ./config/llm_endpoints.yaml. Those merge
    # above and would otherwise win over setdefault(), making the runtime load the repo copy (or
    # nothing) instead of ~/.fairyclaw — pin authoritative paths for this command.
    runtime_data_dir = (runtime_home / "data").resolve()
    runtime_llm_path = (runtime_config_dir / "llm_endpoints.yaml").resolve()
    env["FAIRYCLAW_DATA_DIR"] = str(runtime_data_dir)
    env["FAIRYCLAW_LLM_ENDPOINTS_CONFIG_PATH"] = str(runtime_llm_path)
    if "FAIRYCLAW_DATABASE_URL" not in os.environ:
        env["FAIRYCLAW_DATABASE_URL"] = f"sqlite+aiosqlite:///{runtime_data_dir / 'fairyclaw.db'}"
    if "FAIRYCLAW_LOG_FILE_PATH" not in os.environ:
        env["FAIRYCLAW_LOG_FILE_PATH"] = str(runtime_data_dir / "logs" / "fairyclaw.log")

    business_port = args.business_port
    env["FAIRYCLAW_HOST"] = "0.0.0.0"
    env["FAIRYCLAW_PORT"] = str(business_port)
    env["FAIRYCLAW_GATEWAY_HOST"] = "0.0.0.0"
    env["FAIRYCLAW_GATEWAY_PORT"] = env.get("FAIRYCLAW_GATEWAY_PORT", "8081")
    env["FAIRYCLAW_GATEWAY_BRIDGE_URL"] = f"ws://127.0.0.1:{business_port}/internal/gateway/ws"
    env["FAIRYCLAW_API_TOKEN"] = token

    gateway_port = int(env["FAIRYCLAW_GATEWAY_PORT"])
    _ensure_ports_free_or_kill(
        business_port,
        gateway_port,
        kill_stale=bool(args.kill_stale),
    )
    return _launch_dual_processes(
        env=env,
        business_host=env["FAIRYCLAW_HOST"],
        business_port=business_port,
        gateway_host=env["FAIRYCLAW_GATEWAY_HOST"],
        gateway_port=gateway_port,
        health_wait_seconds=float(args.health_wait_seconds),
        skip_health_check=bool(args.skip_health_check),
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="fairyclaw")
    sub = parser.add_subparsers(dest="command", required=True)
    start = sub.add_parser("start", help="Build frontend and run business+gateway")
    start.add_argument("--skip-build", action="store_true", help="Skip npm build")
    start.add_argument("--force-build", action="store_true", help="Force npm build when web/ is present")
    start.add_argument("--no-proxy", action="store_true", help="Disable proxy env for child processes")
    start.add_argument("--no-sync-config", action="store_true", help="Do not copy config from repo into runtime home")
    start.add_argument("--vite-gateway-base-url", default=None, help="Override VITE_GATEWAY_BASE_URL for build")
    start.add_argument("--business-port", type=int, default=16000, help="Business process port (default: 16000)")
    start.add_argument(
        "--health-wait-seconds",
        type=float,
        default=120.0,
        help="Max seconds to wait for business /healthz before failing (default: 120)",
    )
    start.add_argument(
        "--skip-health-check",
        action="store_true",
        help="Do not wait for /healthz; use a short grace delay (risky if business fails to bind)",
    )
    _ks = start.add_mutually_exclusive_group()
    _ks.add_argument(
        "--kill-stale",
        action="store_true",
        dest="kill_stale",
        help="Stop processes listening on business/gateway ports before start (default)",
    )
    _ks.add_argument(
        "--no-kill-stale",
        action="store_false",
        dest="kill_stale",
        help="Do not stop listeners; fail if ports are busy",
    )
    start.set_defaults(kill_stale=True)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if args.command == "start":
        return _start(args)
    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
