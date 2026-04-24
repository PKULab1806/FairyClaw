"""CLI entrypoint for FairyClaw."""

from __future__ import annotations

import argparse
import asyncio
import atexit
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, TextIO
from urllib.parse import urlencode

from fairyclaw.capabilities_seed import sync_capabilities, upgrade_capabilities
from fairyclaw.config.env_normalize import normalize_fairyclaw_env_file
from fairyclaw.config.locations import (
    capabilities_dir_from_env_values,
    resolve_config_dir,
    resolve_capabilities_seed_dir,
)
from fairyclaw.core.gateway_protocol.models import new_frame_id
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
_CLI_MAP_FILENAME = "cli_session_map.json"


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


def _bundled_config_template(name: str) -> Path | None:
    """Shipped copies of repo `config/*.example` for wheel installs / unknown cwd."""
    p = package_dir() / "config_templates" / name
    return p if p.exists() else None


def _env_file_missing_content(path: Path) -> bool:
    """True if file is missing, blank, or has no KEY=value lines (comments-only counts as empty)."""
    if not path.exists():
        return True
    text = path.read_text(encoding="utf-8")
    stripped = text.strip()
    if not stripped:
        return True
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _val = line.split("=", 1)
        if key.strip():
            return False
    return True


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


def _ensure_repo_config_from_examples(repo_config_dir: Path) -> None:
    """Cold start: copy *.example -> real files under config/."""
    try:
        env_plain = repo_config_dir / "fairyclaw.env"
        env_ex = repo_config_dir / "fairyclaw.env.example"
        if env_ex.exists() and (not env_plain.exists() or _env_file_missing_content(env_plain)):
            shutil.copy2(env_ex, env_plain)
        llm_plain = repo_config_dir / "llm_endpoints.yaml"
        llm_ex = repo_config_dir / "llm_endpoints.yaml.example"
        if llm_ex.exists() and (not llm_plain.exists() or _llm_yaml_missing_profiles(llm_plain)):
            shutil.copy2(llm_ex, llm_plain)
    except OSError as exc:
        print(f"Warning: could not seed repo config under {repo_config_dir}: {exc}", flush=True)


def _prepare_project_config(no_sync_config: bool) -> tuple[Path, Path, dict[str, str]]:
    """Seed config/*.example -> real files under project config/, normalize paths (G8), sync capabilities."""
    config_dir = resolve_config_dir(mkdir=True)
    project_root = config_dir.parent

    if not no_sync_config:
        _ensure_repo_config_from_examples(config_dir)
        env_f = config_dir / "fairyclaw.env"
        if _env_file_missing_content(env_f):
            _be = _bundled_config_template("fairyclaw.env.example")
            if _be is not None:
                shutil.copy2(_be, env_f)
        llm_f = config_dir / "llm_endpoints.yaml"
        if _llm_yaml_missing_profiles(llm_f):
            _bl = _bundled_config_template("llm_endpoints.yaml.example")
            if _bl is not None:
                shutil.copy2(_bl, llm_f)
            elif not llm_f.exists():
                raise RuntimeError(
                    "Missing LLM endpoints config and bundled template; reinstall fairyclaw or "
                    "add config/llm_endpoints.yaml.example."
                )

        anchor = project_root.resolve()
        normalize_fairyclaw_env_file(env_f, anchor)
        vals = _parse_env_file(env_f)
        cap_dest = capabilities_dir_from_env_values(anchor, vals)
        seed = resolve_capabilities_seed_dir()
        added, skipped = sync_capabilities(seed_root=seed, dest_root=cap_dest)
        if added:
            print(f"Materialized capability groups: {', '.join(added)}", flush=True)
        if skipped:
            print(
                "Skipped capability groups (differ from package seed; use "
                "`fairyclaw capabilities upgrade` to overwrite): "
                + ", ".join(skipped),
                flush=True,
            )

    values = _parse_env_file(config_dir / "fairyclaw.env")
    return project_root, config_dir, values


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
    subprocess.run(
        ["npm", "ci", "--no-progress", "--silent"],
        cwd=web_dir,
        env=build_env,
        check=True,
    )
    subprocess.run(
        ["npm", "run", "build", "--silent", "--", "--logLevel", "warn"],
        cwd=web_dir,
        env=build_env,
        check=True,
    )


def _wait_for_healthz(
    proc: subprocess.Popen,
    health_url: str,
    *,
    timeout_seconds: float,
    status_prefix: str,
    process_log: Path | None = None,
) -> None:
    """Block until GET health_url returns 200 or process exits / timeout. One-line TTY progress."""
    import urllib.error
    import urllib.request

    opener = _http_opener_no_proxy()
    start = time.time()
    deadline = start + timeout_seconds
    interval = 0.35
    tty = sys.stdout.isatty()
    if not tty:
        print(f"{status_prefix} (timeout {timeout_seconds:.0f}s) ...", flush=True)
    while time.time() < deadline:
        rc = proc.poll()
        if rc is not None:
            hint = f" See {process_log} for details." if process_log else ""
            raise RuntimeError(
                f"Process exited before {health_url} became ready (exit code {rc}).{hint} "
                "Check FAIRYCLAW_* env and port conflicts, or run uvicorn manually."
            )
        try:
            req = urllib.request.Request(health_url, method="GET")
            with opener.open(req, timeout=2.0) as res:
                if res.status == 200:
                    if tty:
                        sys.stdout.write("\r\033[K")
                        sys.stdout.flush()
                    return
        except (urllib.error.URLError, OSError, TimeoutError):
            pass
        elapsed = time.time() - start
        if tty:
            sys.stdout.write(f"\r\033[K{status_prefix} · {elapsed:.0f}s")
            sys.stdout.flush()
        time.sleep(interval)
        interval = min(interval * 1.12, 2.0)

    if tty:
        sys.stdout.write("\r\033[K")
        sys.stdout.flush()
    hint = f" See {process_log}." if process_log else ""
    raise RuntimeError(
        f"Timed out after {timeout_seconds:.0f}s waiting for {health_url}.{hint} "
        "Try --health-wait-seconds 300 or run uvicorn manually."
    )


def _linux_primary_ipv4_hint() -> str | None:
    """Best-effort non-loopback IPv4 (Linux `hostname -I`); None if unavailable."""
    try:
        out = subprocess.check_output(["hostname", "-I"], text=True).strip().split()
        if out:
            return out[0]
    except Exception:
        pass
    return None


def _uvicorn_common_args() -> list[str]:
    return ["--log-level", "warning", "--no-access-log", "--no-use-colors"]


def _print_ready_banner(
    *,
    gateway_host: str,
    gateway_port: int,
    business_port: int,
    biz_log: Path,
    gw_log: Path,
    app_log: Path | None,
) -> None:
    print("", flush=True)
    print("FairyClaw is ready.", flush=True)
    print(f"  Web UI (this machine):     http://127.0.0.1:{gateway_port}/app", flush=True)
    lan = _linux_primary_ipv4_hint()
    if lan and lan != "127.0.0.1":
        print(f"  Web UI (LAN example):      http://{lan}:{gateway_port}/app", flush=True)
    print(f"  Gateway bind address:      {gateway_host}:{gateway_port}", flush=True)
    print(f"  Business (internal):       http://127.0.0.1:{business_port}  (bridge / healthz only)", flush=True)
    print("  Uvicorn stdout/stderr:", flush=True)
    print(f"    {biz_log}", flush=True)
    print(f"    {gw_log}", flush=True)
    if app_log is not None:
        print(f"  Application log file:      {app_log}", flush=True)
    print("", flush=True)
    print("Press Ctrl+C to stop both processes.", flush=True)


def _launch_dual_processes(
    env: dict[str, str],
    business_host: str,
    business_port: int,
    gateway_host: str,
    gateway_port: int,
    *,
    logs_dir: Path,
    app_log_path: Path | None,
    health_wait_seconds: float,
    skip_health_check: bool,
    cwd: Path | None,
) -> int:
    preexec = os.setsid if os.name == "posix" else None
    popen_kw: dict[str, object] = {}
    if cwd is not None:
        popen_kw["cwd"] = str(cwd)

    logs_dir.mkdir(parents=True, exist_ok=True)
    biz_log_path = logs_dir / "uvicorn-business.log"
    gw_log_path = logs_dir / "uvicorn-gateway.log"
    uv_extra = _uvicorn_common_args()
    biz_cmd = [
        sys.executable,
        "-m",
        "uvicorn",
        "fairyclaw.main:app",
        *uv_extra,
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
        *uv_extra,
        "--host",
        gateway_host,
        "--port",
        str(gateway_port),
    ]

    print("Starting business server ...", flush=True)
    biz_log_f = open(biz_log_path, "a", encoding="utf-8", buffering=1)
    gw_log_f: TextIO | None = None
    biz: subprocess.Popen | None = None
    gw: subprocess.Popen | None = None
    try:
        try:
            biz = subprocess.Popen(
                biz_cmd,
                env=env,
                preexec_fn=preexec,
                stdout=biz_log_f,
                stderr=subprocess.STDOUT,
                **popen_kw,
            )
        except Exception:
            biz_log_f.close()
            raise
        assert biz is not None
        _TRACKED_CHILDREN.append(biz)

        business_health = f"http://127.0.0.1:{business_port}/healthz"
        gateway_health = f"http://127.0.0.1:{gateway_port}/healthz"
        if skip_health_check:
            print("Skipping business /healthz; 5s grace period ...", flush=True)
            time.sleep(5.0)
            if biz.poll() is not None:
                raise RuntimeError(
                    f"Business process exited during startup grace period (exit code {biz.poll()}). "
                    f"See {biz_log_path}."
                )
        else:
            _wait_for_healthz(
                biz,
                business_health,
                timeout_seconds=health_wait_seconds,
                status_prefix="  Business /healthz",
                process_log=biz_log_path,
            )
            print("  Business /healthz OK", flush=True)

        print("Starting gateway server ...", flush=True)
        gw_log_f = open(gw_log_path, "a", encoding="utf-8", buffering=1)
        try:
            gw = subprocess.Popen(
                gw_cmd,
                env=env,
                preexec_fn=preexec,
                stdout=gw_log_f,
                stderr=subprocess.STDOUT,
                **popen_kw,
            )
        except Exception:
            gw_log_f.close()
            gw_log_f = None
            raise
        _TRACKED_CHILDREN.append(gw)

        if skip_health_check:
            print("Skipping gateway /healthz; 3s grace period ...", flush=True)
            time.sleep(3.0)
            if gw.poll() is not None:
                raise RuntimeError(
                    f"Gateway process exited during startup grace period (exit code {gw.poll()}). "
                    f"See {gw_log_path}."
                )
        else:
            _wait_for_healthz(
                gw,
                gateway_health,
                timeout_seconds=min(health_wait_seconds, 60.0),
                status_prefix="  Gateway /healthz",
                process_log=gw_log_path,
            )
            print("  Gateway /healthz OK", flush=True)
    except Exception:
        if gw is not None and gw.poll() is None:
            _terminate_process(gw)
        if biz is not None and biz.poll() is None:
            _terminate_process(biz)
        for p in (biz, gw):
            if p is None:
                continue
            try:
                _TRACKED_CHILDREN.remove(p)
            except ValueError:
                pass
        biz_log_f.close()
        if gw_log_f is not None:
            gw_log_f.close()
        raise

    assert biz is not None and gw is not None
    biz_log_f.close()
    assert gw_log_f is not None
    gw_log_f.close()

    _print_ready_banner(
        gateway_host=gateway_host,
        gateway_port=gateway_port,
        business_port=business_port,
        biz_log=biz_log_path,
        gw_log=gw_log_path,
        app_log=app_log_path,
    )

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
                print(
                    f"\nBusiness process exited (code {biz_rc}); stopping Gateway.\n"
                    f"  Log: {biz_log_path}",
                    flush=True,
                )
                if gw.poll() is None:
                    _terminate_process(gw)
                return biz_rc
            if gw_rc is not None:
                print(
                    f"\nGateway process exited (code {gw_rc}); stopping Business.\n"
                    f"  Log: {gw_log_path}",
                    flush=True,
                )
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


def _pin_cli_project_env(project_root: Path, config_dir: Path, config_values: dict[str, str]) -> None:
    """Apply project config data paths to the process env before loading DB (``fairyclaw agent``)."""
    data_resolved = _resolve_data_dir(project_root, config_values).resolve()
    os.environ["FAIRYCLAW_DATA_DIR"] = str(data_resolved)
    os.environ["FAIRYCLAW_LLM_ENDPOINTS_CONFIG_PATH"] = str((config_dir / "llm_endpoints.yaml").resolve())
    if "FAIRYCLAW_DATABASE_URL" not in os.environ:
        os.environ["FAIRYCLAW_DATABASE_URL"] = f"sqlite+aiosqlite:///{data_resolved / 'fairyclaw.db'}"
    if "FAIRYCLAW_LOG_FILE_PATH" not in os.environ:
        os.environ["FAIRYCLAW_LOG_FILE_PATH"] = str(data_resolved / "logs" / "fairyclaw.log")
    cap_dest = capabilities_dir_from_env_values(project_root.resolve(), config_values)
    os.environ["FAIRYCLAW_CAPABILITIES_DIR"] = str(cap_dest)


def _resolve_data_dir(project_root: Path, config_values: dict[str, str]) -> Path:
    raw = (
        os.getenv("FAIRYCLAW_DATA_DIR")
        or config_values.get("FAIRYCLAW_DATA_DIR")
        or str(project_root / "data")
    )
    p = Path(raw).expanduser()
    if not p.is_absolute():
        p = project_root / p
    return p.resolve()


def _cli_session_map_path(data_dir: Path) -> Path:
    return data_dir / _CLI_MAP_FILENAME


def _load_cli_session_map(map_path: Path) -> dict[str, str]:
    try:
        raw = map_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    except OSError:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    if not isinstance(data, dict):
        return {}
    out: dict[str, str] = {}
    for k, v in data.items():
        if isinstance(k, str) and isinstance(v, str) and k.strip() and v.strip():
            out[k.strip()] = v.strip()
    return out


def _save_cli_session_map(map_path: Path, mapping: dict[str, str]) -> None:
    map_path.parent.mkdir(parents=True, exist_ok=True)
    map_path.write_text(
        json.dumps(dict(sorted(mapping.items())), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _default_gateway_ws_url(config_values: dict[str, str]) -> str:
    host = os.getenv("FAIRYCLAW_GATEWAY_HOST") or config_values.get("FAIRYCLAW_GATEWAY_HOST") or "127.0.0.1"
    port_raw = os.getenv("FAIRYCLAW_GATEWAY_PORT") or config_values.get("FAIRYCLAW_GATEWAY_PORT") or "8081"
    try:
        port = int(port_raw)
    except ValueError:
        port = 8081
    if host == "0.0.0.0":
        host = "127.0.0.1"
    return f"ws://{host}:{port}/v1/ws"


async def _ws_request_async(ws_url: str, token: str, op: str, body: dict[str, Any]) -> dict[str, Any]:
    import importlib

    req_id = new_frame_id("cli")
    full_url = f"{ws_url}?{urlencode({'token': token})}"
    ws_mod = importlib.import_module("websockets")
    try:
        async with ws_mod.connect(full_url) as ws:
            await ws.send(json.dumps({"op": op, "id": req_id, "body": body}, ensure_ascii=False))
            while True:
                raw = await ws.recv()
                msg = json.loads(raw)
                if not isinstance(msg, dict):
                    continue
                if msg.get("id") != req_id:
                    continue
                if msg.get("op") == "ack":
                    payload = msg.get("body")
                    return payload if isinstance(payload, dict) else {}
                if msg.get("op") == "error":
                    raise RuntimeError(str(msg.get("message") or "gateway op failed"))
    except RuntimeError:
        raise
    except Exception as exc:
        raise RuntimeError("Gateway not reachable. Run `fairyclaw start` first.") from exc


def _ws_request(config_values: dict[str, str], op: str, body: dict[str, Any]) -> dict[str, Any]:
    token = _read_token(config_values)
    ws_url = _default_gateway_ws_url(config_values)
    return asyncio.run(_ws_request_async(ws_url, token, op, body))


def _resolve_cli_session_id(target: str, mapping: dict[str, str]) -> str | None:
    t = target.strip()
    if not t:
        return None
    if t.startswith("sess_"):
        return t
    return mapping.get(t)


def _format_history_rows(events: list[dict[str, Any]]) -> list[str]:
    out: list[str] = []
    for ev in events:
        kind = str(ev.get("kind") or "")
        ts = ev.get("ts_ms")
        prefix = f"[{ts}] " if isinstance(ts, int) else ""
        if kind == "session_event":
            role = str(ev.get("role") or "assistant")
            text = str(ev.get("text") or "")
            out.append(f"{prefix}{role}: {text}")
            continue
        if kind == "operation_event":
            tool = str(ev.get("tool_name") or "tool")
            preview = str(ev.get("result_preview") or "")
            out.append(f"{prefix}operation:{tool}: {preview}")
            continue
        out.append(f"{prefix}{json.dumps(ev, ensure_ascii=False)}")
    return out


def _cmd_help(_args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    print("FairyClaw benchmark CLI commands:")
    print("  fairyclaw help")
    print("  fairyclaw send <text> [--session <name>] [--workspace <path>]")
    print("  fairyclaw agent --session <name> --message '...'   # one process, no `start` (Moltis-style)")
    print("  fairyclaw get <session_name_or_id>")
    print("  fairyclaw session list")
    print("  fairyclaw session rm <session_name_or_id>")
    print("")
    print("Notes:")
    print("  - Run `fairyclaw start` first for `send` and `get`; use `fairyclaw agent` to run without a daemon.")
    print("  - Same --session name reuses the session; omit --session to create an anonymous session.")
    print("  - --workspace only applies when creating a new session; it is ignored for reused sessions.")
    print("  - Session-name mappings are stored in <FAIRYCLAW_DATA_DIR>/cli_session_map.json.")
    print("")
    parser.print_help()
    return 0


def _cmd_send(args: argparse.Namespace) -> int:
    project_root, _config_dir, config_values = _prepare_project_config(no_sync_config=True)
    data_dir = _resolve_data_dir(project_root, config_values)
    map_path = _cli_session_map_path(data_dir)
    mapping = _load_cli_session_map(map_path)

    workspace_raw = (getattr(args, "workspace", None) or "").strip()
    workspace_path: str | None = None
    if workspace_raw:
        workspace_path = str(Path(workspace_raw).expanduser().resolve())

    def _send_meta() -> dict[str, Any]:
        meta: dict[str, Any] = {"source": "cli_benchmark"}
        if workspace_path:
            meta["workspace_root"] = workspace_path
        return meta

    target_sid: str
    session_name = (args.session or "").strip()
    if session_name:
        existing = mapping.get(session_name)
        if existing:
            target_sid = existing
        else:
            created = _ws_request(
                config_values,
                "session.create",
                {
                    "platform": "web",
                    "title": session_name,
                    "meta": _send_meta(),
                },
            )
            target_sid = str(created.get("session_id") or "").strip()
            if not target_sid:
                raise RuntimeError("session.create succeeded but no session_id returned")
            mapping[session_name] = target_sid
            _save_cli_session_map(map_path, mapping)
    else:
        created = _ws_request(
            config_values,
            "session.create",
            {
                "platform": "web",
                "title": None,
                "meta": _send_meta(),
            },
        )
        target_sid = str(created.get("session_id") or "").strip()
        if not target_sid:
            raise RuntimeError("session.create succeeded but no session_id returned")

    text = " ".join(args.text).strip()
    if not text:
        raise RuntimeError("text is required")
    ack = _ws_request(
        config_values,
        "chat.send",
        {"session_id": target_sid, "segments": [{"type": "text", "content": text}]},
    )
    print(json.dumps({"session_id": target_sid, "status": ack.get("status"), "message": ack.get("message")}, ensure_ascii=False))
    return 0


def _cmd_agent(args: argparse.Namespace) -> int:
    """Moltis-style: one OS process, full Business runtime, no ``fairyclaw start``."""
    project_root, config_dir, config_values = _prepare_project_config(no_sync_config=True)
    _pin_cli_project_env(project_root, config_dir, config_values)
    if bool(getattr(args, "json_only", False)):
        from fairyclaw.config.settings import settings as _fc_settings

        # Machine-readable one-liner: avoid INFO lines on stderr/stdout from the runtime.
        if (_fc_settings.log_level or "INFO").upper() in {"DEBUG", "INFO"}:
            _fc_settings.log_level = "WARNING"
    from fairyclaw.headless.one_shot import run_in_process_agent

    map_path = _cli_session_map_path(_resolve_data_dir(project_root, config_values))
    msg = (args.message or "").strip() or " ".join(args.text or []).strip()
    if not msg:
        raise RuntimeError("message is required: use --message or positional text")
    session_name = (args.session or "").strip()
    if not session_name:
        raise RuntimeError("--session is required")

    result = asyncio.run(
        run_in_process_agent(
            map_path=map_path,
            session_name=session_name,
            text=msg,
            wait_idle=not bool(args.no_wait),
            timeout_sec=float(args.timeout),
            poll_sec=float(args.poll_interval),
            min_wait_sec=float(args.min_wait_after_send),
        )
    )
    out: dict[str, Any] = {"cli": "agent", **result}
    if not bool(getattr(args, "json_only", False)):
        reply = result.get("reply")
        if isinstance(reply, str) and reply.strip():
            print("Assistant:\n" + reply.strip() + "\n", flush=True)
    print(json.dumps(out, ensure_ascii=False), flush=True)
    return 0 if result.get("ok") else 1


def _cmd_get(args: argparse.Namespace) -> int:
    project_root, _config_dir, config_values = _prepare_project_config(no_sync_config=True)
    map_path = _cli_session_map_path(_resolve_data_dir(project_root, config_values))
    mapping = _load_cli_session_map(map_path)
    sid = _resolve_cli_session_id(args.target, mapping)
    if not sid:
        raise RuntimeError(f"Unknown session name or id: {args.target}")
    body = _ws_request(config_values, "sessions.history", {"session_id": sid, "limit": 500})
    events = body.get("events")
    rows = _format_history_rows(events if isinstance(events, list) else [])
    for row in rows:
        print(row)
    return 0


def _cmd_session_list(_args: argparse.Namespace) -> int:
    project_root, _config_dir, config_values = _prepare_project_config(no_sync_config=True)
    map_path = _cli_session_map_path(_resolve_data_dir(project_root, config_values))
    mapping = _load_cli_session_map(map_path)
    if not mapping:
        print("(empty)")
        return 0
    for name, sid in sorted(mapping.items()):
        print(f"{name}\t{sid}")
    return 0


def _cmd_session_rm(args: argparse.Namespace) -> int:
    project_root, _config_dir, config_values = _prepare_project_config(no_sync_config=True)
    data_dir = _resolve_data_dir(project_root, config_values)
    map_path = _cli_session_map_path(data_dir)
    mapping = _load_cli_session_map(map_path)
    target = args.target.strip()
    removed: list[str] = []
    if target in mapping:
        removed.append(target)
        mapping.pop(target, None)
    else:
        for k, v in list(mapping.items()):
            if v == target:
                removed.append(k)
                mapping.pop(k, None)
    _save_cli_session_map(map_path, mapping)
    if not removed:
        print(json.dumps({"removed": 0, "target": target}, ensure_ascii=False))
    else:
        print(json.dumps({"removed": len(removed), "names": removed}, ensure_ascii=False))
    return 0


def _start(args: argparse.Namespace) -> int:
    project_root, config_dir, config_values = _prepare_project_config(no_sync_config=args.no_sync_config)
    data_dir = project_root / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "logs").mkdir(parents=True, exist_ok=True)
    (data_dir / "files").mkdir(parents=True, exist_ok=True)
    token = _read_token(config_values)

    web_dir = _frontend_root()
    if not args.skip_build and (web_dir is not None or args.force_build):
        if web_dir is None:
            raise RuntimeError("Cannot force web build: missing web/package.json")
        print("Building web UI ...", flush=True)
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

    # Ensure Gateway serves the latest local build when source tree has web/dist.
    # Otherwise resolve_web_dist_dir() may pick packaged fairyclaw/web_dist first.
    if web_dir is not None:
        local_web_dist = (web_dir / "dist").resolve()
        if (local_web_dist / "index.html").is_file():
            env["FAIRYCLAW_WEB_DIST_DIR"] = str(local_web_dist)

    # Pin paths to project config/ and data/ so merges from fairyclaw.env cannot point elsewhere.
    data_resolved = data_dir.resolve()
    llm_resolved = (config_dir / "llm_endpoints.yaml").resolve()
    env["FAIRYCLAW_DATA_DIR"] = str(data_resolved)
    env["FAIRYCLAW_LLM_ENDPOINTS_CONFIG_PATH"] = str(llm_resolved)
    if "FAIRYCLAW_DATABASE_URL" not in os.environ:
        env["FAIRYCLAW_DATABASE_URL"] = f"sqlite+aiosqlite:///{data_resolved / 'fairyclaw.db'}"
    if "FAIRYCLAW_LOG_FILE_PATH" not in os.environ:
        env["FAIRYCLAW_LOG_FILE_PATH"] = str(data_resolved / "logs" / "fairyclaw.log")

    cap_dest = capabilities_dir_from_env_values(project_root.resolve(), config_values)
    env["FAIRYCLAW_CAPABILITIES_DIR"] = str(cap_dest)

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
        logs_dir=data_resolved / "logs",
        app_log_path=Path(env["FAIRYCLAW_LOG_FILE_PATH"]),
        health_wait_seconds=float(args.health_wait_seconds),
        skip_health_check=bool(args.skip_health_check),
        cwd=project_root,
    )


def _cmd_capabilities_sync(args: argparse.Namespace) -> int:
    if args.no_seed_config:
        config_dir = resolve_config_dir(mkdir=False)
        env_f = config_dir / "fairyclaw.env"
        if not env_f.is_file():
            print(
                "No fairyclaw.env found; run `fairyclaw start` once or omit --no-seed-config.",
                flush=True,
            )
            return 1
        anchor = config_dir.parent.resolve()
        normalize_fairyclaw_env_file(env_f, anchor)
        vals = _parse_env_file(env_f)
        cap_dest = capabilities_dir_from_env_values(anchor, vals)
        added, skipped = sync_capabilities(seed_root=resolve_capabilities_seed_dir(), dest_root=cap_dest)
        if added:
            print(f"Materialized capability groups: {', '.join(added)}", flush=True)
        if skipped:
            print(
                "Skipped (differ from seed): " + ", ".join(skipped),
                flush=True,
            )
        return 0
    _prepare_project_config(no_sync_config=False)
    return 0


def _cmd_capabilities_upgrade(args: argparse.Namespace) -> int:
    config_dir = resolve_config_dir(mkdir=False)
    env_f = config_dir / "fairyclaw.env"
    if not env_f.is_file():
        print("No fairyclaw.env found; run `fairyclaw start` once.", flush=True)
        return 1
    anchor = config_dir.parent.resolve()
    normalize_fairyclaw_env_file(env_f, anchor)
    vals = _parse_env_file(env_f)
    cap_dest = capabilities_dir_from_env_values(anchor, vals)
    names = upgrade_capabilities(
        seed_root=resolve_capabilities_seed_dir(),
        dest_root=cap_dest,
        group=args.group,
        backup=not args.no_backup,
        dry_run=args.dry_run,
    )
    if args.dry_run:
        print("Would upgrade: " + (", ".join(names) if names else "(none)"), flush=True)
    else:
        print("Upgraded: " + (", ".join(names) if names else "(none)"), flush=True)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="fairyclaw")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("help", help="Show benchmark CLI commands")

    start = sub.add_parser("start", help="Build frontend and run Business + Gateway")
    start.add_argument("--skip-build", action="store_true", help="Skip npm build")
    start.add_argument("--force-build", action="store_true", help="Force npm build when web/ is present")
    start.add_argument("--no-proxy", action="store_true", help="Disable proxy env for child processes")
    start.add_argument(
        "--no-sync-config",
        action="store_true",
        help="Do not seed config/ from *.example or bundled templates",
    )
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

    cap = sub.add_parser("capabilities", help="Materialize or upgrade capability groups from package seed")
    cap_sub = cap.add_subparsers(dest="cap_command", required=True)
    cap_sync = cap_sub.add_parser("sync", help="Copy missing groups from seed; skip groups that differ from seed")
    cap_sync.add_argument(
        "--no-seed-config",
        action="store_true",
        help="Do not seed config/ from examples (requires existing fairyclaw.env)",
    )
    cap_up = cap_sub.add_parser(
        "upgrade",
        help="Overwrite capability group(s) from package seed (default: backup replaced tree)",
    )
    cap_up.add_argument("--group", default=None, help="Upgrade only this group directory name")
    cap_up.add_argument("--dry-run", action="store_true", help="Print groups that would be upgraded")
    cap_up.add_argument(
        "--no-backup",
        action="store_true",
        help="Do not keep .bak.<timestamp> backup of replaced groups",
    )

    send = sub.add_parser("send", help="Send one text message for benchmark")
    send.add_argument("text", nargs="+", help="Text content")
    send.add_argument("--session", default=None, help="Named session to reuse/create")
    send.add_argument(
        "--workspace",
        default=None,
        help="Only when creating a new session: workspace root path (ignored if --session reuses an existing name)",
    )

    agent = sub.add_parser("agent", help="One-shot in-process run: no `start` (Moltis-style; loads planner+DB in this process)")
    agent.add_argument("--message", default=None, help="User message text")
    agent.add_argument("text", nargs="*", help="Message if --message is omitted (joined with spaces)")
    agent.add_argument(
        "--session",
        required=True,
        help="Session key (e.g. ClawBench session_id)",
    )
    agent.add_argument("--timeout", type=float, default=2400.0, help="Max wall-clock seconds (default: 2400)")
    agent.add_argument(
        "--idle-seconds",
        type=float,
        default=3.0,
        dest="idle_seconds",
        help="Unchanged history seconds for success (default: 3)",
    )
    agent.add_argument(
        "--poll-interval",
        type=float,
        default=0.5,
        dest="poll_interval",
        help="History poll interval (default: 0.5)",
    )
    agent.add_argument(
        "--min-wait-after-send",
        type=float,
        default=2.0,
        dest="min_wait_after_send",
        help="Min seconds after send before idle can count (default: 2)",
    )
    agent.add_argument(
        "--no-wait",
        action="store_true",
        help="Only enqueue; do not wait for idle history (no ClawBench completion signal)",
    )
    agent.add_argument(
        "--json-only",
        action="store_true",
        dest="json_only",
        help="Do not print the Assistant: block; only print one JSON line (reply is still in the JSON)",
    )

    get = sub.add_parser("get", help="Fetch full history by session name or session_id")
    get.add_argument("target", help="session name or session_id")

    sess = sub.add_parser("session", help="Session mapping management for benchmark CLI")
    sess_sub = sess.add_subparsers(dest="session_command", required=True)
    sess_sub.add_parser("list", help="List local session name mappings")
    sess_rm = sess_sub.add_parser("rm", help="Remove local mapping by name or session_id")
    sess_rm.add_argument("target", help="session name or session_id")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        if args.command == "help":
            return _cmd_help(args, parser)
        if args.command == "start":
            return _start(args)
        if args.command == "send":
            return _cmd_send(args)
        if args.command == "agent":
            return _cmd_agent(args)
        if args.command == "get":
            return _cmd_get(args)
        if args.command == "session":
            if args.session_command == "list":
                return _cmd_session_list(args)
            if args.session_command == "rm":
                return _cmd_session_rm(args)
        if args.command == "capabilities":
            if args.cap_command == "sync":
                return _cmd_capabilities_sync(args)
            if args.cap_command == "upgrade":
                return _cmd_capabilities_upgrade(args)
        parser.print_help()
        return 2
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
