"""Workspace resolution and deterministic cascading configuration."""

from __future__ import annotations

import json
import os
import sys
import ipaddress
import tempfile
from urllib.parse import urlsplit
from pathlib import Path
from typing import Any, Mapping


def resolve_workspace(
    cli_workspace: str | os.PathLike[str] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    cwd: Path | None = None,
    installation_path: Path | None = None,
    allow_missing_installation: bool = False,
) -> Path:
    """Resolve CLI, environment, installation, then current-directory workspace."""
    env = os.environ if environ is None else environ
    # TEXTSTRATA_WORKSPACE is the public name. The older names remain accepted
    # for existing workspaces and integrations.
    selected = cli_workspace or env.get("TEXTSTRATA_WORKSPACE") or env.get("MARKBASE_WORKSPACE") or env.get("FABRIC_ROOT")
    base = cwd or Path.cwd()
    if not selected:
        installed = load_installation_config(path=installation_path, environ=env, allow_missing=allow_missing_installation)
        selected = installed.get("workspace")
    return Path(selected).expanduser().resolve() if selected else (base / ".workspace").resolve()


def global_config_path(
    *, environ: Mapping[str, str] | None = None, home: Path | None = None
) -> Path:
    env = os.environ if environ is None else environ
    user_home = home or Path.home()
    if sys.platform == "win32":
        base = Path(env.get("APPDATA", user_home / "AppData" / "Roaming"))
        return base / "TextStrata" / "config.toml"
    if sys.platform == "darwin":
        return user_home / "Library" / "Application Support" / "TextStrata" / "config.toml"
    base = Path(env.get("XDG_CONFIG_HOME", user_home / ".config"))
    return base / "textstrata" / "config.toml"


NETWORK_MODES = ("local", "https", "proxy")


def installation_config_path(*, environ: Mapping[str, str] | None = None, home: Path | None = None) -> Path:
    env = os.environ if environ is None else environ
    selected = env.get("TEXTSTRATA_CONFIG")
    if selected:
        return Path(selected).expanduser().resolve()
    return global_config_path(environ=env, home=home).with_name("installation.json")


def is_loopback_host(host: str) -> bool:
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _absolute_path(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value or not Path(value).is_absolute():
        raise ValueError(f"{label} must be an absolute path")
    return value


def validate_network(network: Mapping[str, Any]) -> dict[str, Any]:
    mode = network.get("mode")
    host = network.get("host")
    port = network.get("port")
    if mode not in NETWORK_MODES:
        raise ValueError(f"network mode must be one of {', '.join(NETWORK_MODES)}")
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        raise ValueError("installation port must be between 1 and 65535")
    if not isinstance(host, str) or not host:
        raise ValueError("installation host must be a string")
    allowed = {"mode", "host", "port", "auth"}
    result: dict[str, Any] = {"mode": mode, "host": host, "port": port}
    auth = network.get("auth", mode != "local")
    if not isinstance(auth, bool):
        raise ValueError("network auth must be true or false")
    if mode == "local":
        if not is_loopback_host(host):
            raise ValueError("local mode requires a loopback host; use https or proxy mode for LAN access")
    else:
        if not auth:
            raise ValueError(f"{mode} mode always requires authentication")
        if host.lower() != "localhost":
            try:
                ipaddress.ip_address(host)
            except ValueError as exc:
                raise ValueError("network host must be an IP address or localhost") from exc
    result["auth"] = auth
    if mode == "https":
        allowed |= {"tls", "public_url"}
        tls = network.get("tls")
        if not isinstance(tls, dict) or set(tls) != {"cert_file", "key_file"}:
            raise ValueError("https mode requires tls.cert_file and tls.key_file")
        result["tls"] = {
            "cert_file": _absolute_path(tls["cert_file"], "tls.cert_file"),
            "key_file": _absolute_path(tls["key_file"], "tls.key_file"),
        }
    if mode == "proxy":
        allowed |= {"public_url", "trusted_proxies"}
        proxies = network.get("trusted_proxies")
        if not isinstance(proxies, list) or not proxies:
            raise ValueError("proxy mode requires a non-empty trusted_proxies list")
        try:
            result["trusted_proxies"] = [str(ipaddress.ip_network(str(item), strict=False)) for item in proxies]
        except ValueError as exc:
            raise ValueError("trusted_proxies entries must be IP addresses or CIDR ranges") from exc
        if "public_url" not in network:
            raise ValueError("proxy mode requires public_url")
    if "public_url" in network:
        public = network["public_url"]
        parsed = urlsplit(public) if isinstance(public, str) else None
        if (parsed is None or parsed.scheme != "https" or not parsed.hostname
                or parsed.path not in {"", "/"} or parsed.query or parsed.fragment or parsed.username):
            raise ValueError("public_url must be an https origin such as https://notes.example.internal")
        result["public_url"] = f"https://{parsed.netloc}"
    if set(network) - allowed:
        raise ValueError("installation network contains unknown fields")
    return result


def validate_installation_config(value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate the versioned installation document."""
    if value.get("schema_version") != 1:
        raise ValueError("installation config requires schema_version = 1")
    workspace = _absolute_path(value.get("workspace"), "installation workspace")
    network = value.get("network")
    if not isinstance(network, dict):
        raise ValueError("installation network must be an object")
    if set(value) - {"schema_version", "workspace", "network", "state_dir"}:
        raise ValueError("installation config contains unknown fields")
    result: dict[str, Any] = {"schema_version": 1, "workspace": workspace, "network": validate_network(network)}
    if "state_dir" in value:
        result["state_dir"] = _absolute_path(value["state_dir"], "state_dir")
    return result


def installation_state_dir(installation: Mapping[str, Any], *, environ: Mapping[str, str] | None = None) -> Path:
    """Directory for installation-wide application metadata such as identities."""
    configured = installation.get("state_dir")
    if configured:
        return Path(configured)
    selected = installation_config_path(environ=environ)
    return selected.with_name(selected.stem + ".state")


def load_installation_config(
    *, path: Path | None = None, environ: Mapping[str, str] | None = None, allow_missing: bool = False
) -> dict[str, Any]:
    env = os.environ if environ is None else environ
    selected = path or installation_config_path(environ=env)
    if not selected.exists():
        if not allow_missing and (path is not None or env.get("TEXTSTRATA_CONFIG")):
            raise ValueError(f"installation config does not exist: {selected}")
        return {}
    try:
        value = json.loads(selected.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid installation config: {selected}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"installation config must be an object: {selected}")
    return validate_installation_config(value)


def save_installation_config(value: Mapping[str, Any], *, path: Path | None = None) -> Path:
    selected = path or installation_config_path()
    validated = validate_installation_config(value)
    selected.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{selected.name}.", dir=selected.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(validated, handle, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp_name, 0o600)
        os.replace(temp_name, selected)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)
    return selected


def effective_network(
    workspace_root: Path,
    *,
    environ: Mapping[str, str] | None = None,
    installation: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Resolve legacy TOML, installation config, then explicit environment."""
    env = os.environ if environ is None else environ
    legacy = load_cascading_config(workspace_root).get("network", {})
    if not isinstance(legacy, dict):
        legacy = {}
    installed = installation if installation is not None else load_installation_config(environ=env)
    configured = installed.get("network", {})
    if not isinstance(configured, dict):
        configured = {}
    env_host = env.get("TEXTSTRATA_HOST") or env.get("FABRIC_HOST")
    host = env_host or configured.get("host") or legacy.get("host") or "127.0.0.1"
    raw_port = env.get("TEXTSTRATA_PORT") or env.get("FABRIC_PORT") or configured.get("port") or legacy.get("port") or 8700
    try:
        port = int(raw_port)
    except (TypeError, ValueError) as exc:
        raise ValueError("network port must be an integer") from exc
    if not 1 <= port <= 65535:
        raise ValueError("network port must be between 1 and 65535")
    candidate = {**configured, "mode": configured.get("mode", "local"), "host": host, "port": port}
    try:
        return validate_network(candidate)
    except ValueError as exc:
        if candidate["mode"] == "local" and not is_loopback_host(str(host)):
            raise ValueError("local mode only serves loopback HTTP; configure https or proxy mode for LAN access") from exc
        raise


def _read_toml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        import tomllib
    except ImportError:  # Python 3.10 compatibility
        data = _read_basic_toml(path)
    else:
        with path.open("rb") as handle:
            data = tomllib.load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"configuration root must be a table: {path}")
    return data


def _read_basic_toml(path: Path) -> dict[str, Any]:
    """Parse the small TOML subset used by TextStrata on Python 3.10."""
    result: dict[str, Any] = {}
    table = result
    for line_number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        if line.startswith("[") and line.endswith("]"):
            table = result
            for part in line[1:-1].strip().split("."):
                if not part:
                    raise ValueError(f"invalid TOML table at {path}:{line_number}")
                value = table.setdefault(part, {})
                if not isinstance(value, dict):
                    raise ValueError(f"TOML table conflicts with value at {path}:{line_number}")
                table = value
            continue
        if "=" not in line:
            raise ValueError(f"invalid TOML assignment at {path}:{line_number}")
        key, raw_value = (part.strip() for part in line.split("=", 1))
        if not key:
            raise ValueError(f"empty TOML key at {path}:{line_number}")
        if raw_value.startswith('"') and raw_value.endswith('"'):
            value: Any = json.loads(raw_value)
        elif raw_value in {"true", "false"}:
            value = raw_value == "true"
        else:
            try:
                value = int(raw_value)
            except ValueError as exc:
                raise ValueError(
                    f"unsupported TOML value at {path}:{line_number}; Python 3.10 fallback supports strings, integers, and booleans"
                ) from exc
        table[key] = value
    return result


def _merge(base: dict[str, Any], override: Mapping[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, Mapping) and isinstance(merged.get(key), Mapping):
            merged[key] = _merge(dict(merged[key]), value)
        else:
            merged[key] = value
    return merged


def load_cascading_config(
    workspace_root: str | Path, *, global_path: Path | None = None
) -> dict[str, Any]:
    """Deep-merge global TOML, workspace TOML, and workspace vocabulary."""
    metadata = Path(workspace_root).expanduser().resolve() / ".fabric"
    config = _merge(
        _read_toml(global_path or global_config_path()),
        _read_toml(metadata / "config.toml"),
    )
    synonyms_path = metadata / "synonyms.json"
    if synonyms_path.exists():
        try:
            synonyms = json.loads(synonyms_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid workspace synonyms JSON: {synonyms_path}") from exc
        if not isinstance(synonyms, dict) or not all(
            isinstance(key, str) and isinstance(value, str)
            for key, value in synonyms.items()
        ):
            raise ValueError("workspace synonyms must be a string-to-string object")
        vocabulary = config.get("vocabulary", {})
        if not isinstance(vocabulary, Mapping):
            vocabulary = {}
        config = _merge(config, {"vocabulary": {"synonyms": synonyms}})
    return config


def apply_config_environment(config: Mapping[str, Any]) -> None:
    """Expose merged engine settings to existing runtime seams without override."""
    mappings = (
        ("network", "host", "FABRIC_HOST"),
        ("network", "port", "FABRIC_PORT"),
        ("llm", "endpoint", "OLLAMA_HOST"),
        ("llm", "model", "FABRIC_LLM_MODEL"),
    )
    for section, key, environment_key in mappings:
        values = config.get(section, {})
        if isinstance(values, Mapping) and key in values:
            os.environ.setdefault(environment_key, str(values[key]))
    network = config.get("network", {})
    if isinstance(network, Mapping):
        if "host" in network:
            os.environ.setdefault("TEXTSTRATA_HOST", str(network["host"]))
        if "port" in network:
            os.environ.setdefault("TEXTSTRATA_PORT", str(network["port"]))
    llm = config.get("llm", {})
    if isinstance(llm, Mapping) and "model" in llm:
        os.environ.setdefault("TEXTSTRATA_LLM_MODEL", str(llm["model"]))
