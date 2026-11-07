"""Strictly read-only kubectl runner for remote cluster observation."""

import json
import subprocess

MAX_ARGS = 32
MAX_ARG_CHARS = 2_000
MAX_OUTPUT = 250_000
TIMEOUT_SECONDS = 30

_SIMPLE_COMMANDS = {
    "api-resources",
    "api-versions",
    "cluster-info",
    "describe",
    "events",
    "explain",
    "get",
    "logs",
    "top",
    "version",
}
_BLOCKED_FLAGS = {
    "--as",
    "--as-group",
    "--as-uid",
    "--certificate-authority",
    "--client-certificate",
    "--client-key",
    "--cluster",
    "--context",
    "--follow",
    "--insecure-skip-tls-verify",
    "--kubeconfig",
    "--kuberc",
    "--password",
    "--profile",
    "--profile-output",
    "--raw",
    "--server",
    "--token",
    "--user",
    "--username",
    "--watch",
    "--watch-only",
    "-f",
    "-w",
}
_BLOCKED_OUTPUT_SOURCES = {"custom-columns-file", "go-template-file", "jsonpath-file", "templatefile"}
_SECRET_RESOURCES = {"sec", "secret", "secrets"}
_SAFE_ENV = {
    "HOME": "/home/kdvops",
    "KUBECONFIG": "/home/kdvops/.kube/config",
    "KUBECTL_KUBERC": "false",
    "LANG": "C.UTF-8",
    "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
}


def _flag_name(argument: str) -> str:
    return argument.split("=", 1)[0]


def _contains_secret_resource(arguments: list[str]) -> bool:
    for argument in arguments:
        if argument.startswith("-"):
            continue
        for resource in argument.lower().split(","):
            # Resource identifiers may be qualified as resource.group/name or
            # passed through discovery-style paths such as v1/secrets. Check
            # every path segment and strip both API groups and object names.
            components = resource.split("/")
            candidates = {
                component.split(".", 1)[0]
                for component in components
                if component
            }
            if candidates & _SECRET_RESOURCES:
                return True
    return False


def _validate(args: list[str]) -> None:
    if not isinstance(args, list) or not args:
        raise ValueError("kubectl arguments are required")
    if len(args) > MAX_ARGS:
        raise ValueError("too many kubectl arguments")
    if any(not isinstance(arg, str) or not arg or "\x00" in arg for arg in args):
        raise ValueError("invalid kubectl argument")
    if sum(len(arg) for arg in args) > MAX_ARG_CHARS:
        raise ValueError("kubectl arguments are too large")

    command = args[0]
    allowed = command in _SIMPLE_COMMANDS
    if command == "rollout":
        allowed = len(args) > 1 and args[1] in {"history", "status"}
    elif command == "auth":
        allowed = len(args) > 1 and args[1] in {"can-i", "whoami"}
    elif command == "cluster-info":
        allowed = len(args) == 1 or args[1].startswith("-")
    if not allowed:
        raise ValueError("kubectl command is not allowed in read-only mode")

    for argument in args:
        if _flag_name(argument) in _BLOCKED_FLAGS:
            raise ValueError(f"kubectl option is not allowed in read-only mode: {_flag_name(argument)}")
        lowered = argument.lower()
        if any(source in lowered for source in _BLOCKED_OUTPUT_SOURCES):
            raise ValueError("kubectl output file sources are not allowed")

    if command in {"get", "describe"} and _contains_secret_resource(args[1:]):
        raise ValueError("Kubernetes Secret access is not allowed")


def _cap_output(output: str) -> str:
    if len(output.encode("utf-8")) <= MAX_OUTPUT:
        return output
    encoded = output.encode("utf-8")[:MAX_OUTPUT]
    clipped = encoded.decode("utf-8", errors="ignore")
    return clipped + "\n[output truncated by kdvops-cli]\n"


def _kubeconfig_is_static() -> bool:
    """Reject kubeconfigs that can execute plugins or redirect through a proxy."""
    result = subprocess.run(
        ["kubectl", "config", "view", "--raw", "-o", "json"],
        capture_output=True,
        check=False,
        env=_SAFE_ENV,
        shell=False,
        text=True,
        timeout=5,
    )
    if result.returncode:
        return False
    try:
        config = json.loads(result.stdout)
    except (json.JSONDecodeError, TypeError):
        return False
    users = config.get("users", []) if isinstance(config, dict) else []
    clusters = config.get("clusters", []) if isinstance(config, dict) else []
    return not any(
        isinstance(item, dict)
        and isinstance(item.get("user"), dict)
        and ({"exec", "auth-provider"} & set(item["user"]))
        for item in users
    ) and not any(
        isinstance(item, dict)
        and isinstance(item.get("cluster"), dict)
        and "proxy-url" in item["cluster"]
        for item in clusters
    )


def kubectl_read(args: list[str]) -> str:
    """Run an allowlisted, non-interactive kubectl observation command."""
    _validate(args)
    try:
        if not _kubeconfig_is_static():
            return "ERROR: kubeconfig must be static and cannot use exec, auth-provider, or proxy-url"
        result = subprocess.run(
            ["kubectl", *args],
            capture_output=True,
            check=False,
            env=_SAFE_ENV,
            shell=False,
            text=True,
            timeout=TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        return f"ERROR: kubectl timed out after {TIMEOUT_SECONDS} seconds"
    except OSError as exc:
        return f"ERROR: unable to run kubectl: {exc}"

    output = result.stdout
    if result.returncode:
        detail = result.stderr or result.stdout or "unknown error"
        output = f"ERROR: kubectl exited {result.returncode}: {detail}"
    elif result.stderr:
        output += result.stderr
    return _cap_output(output)
