import subprocess

import pytest

from kdvops.kubectl_tool import kubectl_read


def completed(stdout="ok\n", stderr="", returncode=0):
    return subprocess.CompletedProcess(["kubectl"], returncode, stdout, stderr)


STATIC_CONFIG = '{"users":[{"user":{"token":"masked"}}],"clusters":[{"cluster":{}}]}'


@pytest.mark.parametrize(
    "args",
    [
        ["get", "pods", "--all-namespaces", "-o", "wide"],
        ["describe", "deployment", "api", "-n", "prod"],
        ["logs", "api-123", "--previous", "--tail=200", "-n", "prod"],
        ["top", "nodes"],
        ["events", "--all-namespaces"],
        ["api-resources"],
        ["cluster-info"],
        ["rollout", "status", "deployment/api", "-n", "prod"],
        ["auth", "can-i", "list", "pods", "--all-namespaces"],
    ],
)
def test_allows_observation_commands(args, monkeypatch):
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        if command[1:3] == ["config", "view"]:
            return completed(stdout=STATIC_CONFIG)
        return completed()

    monkeypatch.setattr("kdvops.kubectl_tool.subprocess.run", fake_run)

    assert kubectl_read(args) == "ok\n"
    command, kwargs = calls[-1]
    assert command == ["kubectl", *args]
    assert kwargs["shell"] is False
    assert kwargs["timeout"] == 30
    assert kwargs["env"] == {
        "HOME": "/home/kdvops",
        "KUBECONFIG": "/home/kdvops/.kube/config",
        "KUBECTL_KUBERC": "false",
        "LANG": "C.UTF-8",
        "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
    }


@pytest.mark.parametrize(
    "args",
    [
        ["delete", "pod", "api"],
        ["apply", "-f", "deployment.yaml"],
        ["exec", "api", "--", "sh"],
        ["port-forward", "pod/api", "8080:80"],
        ["get", "secrets", "-A", "-o", "yaml"],
        ["get", "sec", "-A"],
        ["get", "secret/db-password"],
        ["get", "v1/secrets"],
        ["get", "secrets.v1/db-password"],
        ["get", "pods,secrets"],
        ["logs", "api", "--follow"],
        ["logs", "api", "-f"],
        ["get", "--raw=/api/v1/namespaces/default/secrets"],
        ["get", "pods", "--server=https://attacker.example"],
        ["get", "pods", "--kubeconfig=/workspace/other-config"],
        ["get", "pods", "--context=other-cluster"],
        ["get", "pods", "--context", "other-cluster"],
        ["get", "pods", "--kuberc=/workspace/other.kuberc"],
        ["get", "pods", "--kuberc", "/workspace/other.kuberc"],
        ["rollout", "restart", "deployment/api"],
        ["auth", "reconcile", "rbac.yaml"],
        ["get", "pods", "--watch"],
        ["get", "pods", "-w"],
        ["get", "pods", "--output", "go-template-file=/workspace/template"],
        ["get", "pods", "-o", "custom-columns-file=/workspace/template"],
        ["get", "pods", "-o", "jsonpath-file=/home/kdvops/.kube/config"],
        ["cluster-info", "dump"],
    ],
)
def test_blocks_mutation_secret_access_and_escape_hatches(args, monkeypatch):
    monkeypatch.setattr(
        "kdvops.kubectl_tool.subprocess.run",
        lambda *a, **kw: (_ for _ in ()).throw(AssertionError("kubectl must not run")),
    )

    with pytest.raises(ValueError, match="not allowed"):
        kubectl_read(args)


@pytest.mark.parametrize("args", [[], ["get", "pods", "bad\x00arg"], ["get"] + ["pods"] * 40])
def test_rejects_malformed_or_excessive_arguments(args):
    with pytest.raises(ValueError):
        kubectl_read(args)


def test_reports_kubectl_failure(monkeypatch):
    def fake_run(command, **kwargs):
        if command[1:3] == ["config", "view"]:
            return completed(stdout=STATIC_CONFIG)
        return completed(stderr="forbidden\n", returncode=1)

    monkeypatch.setattr("kdvops.kubectl_tool.subprocess.run", fake_run)

    assert kubectl_read(["get", "pods"]) == "ERROR: kubectl exited 1: forbidden\n"


def test_caps_output(monkeypatch):
    def fake_run(command, **kwargs):
        if command[1:3] == ["config", "view"]:
            return completed(stdout=STATIC_CONFIG)
        return completed(stdout="x" * 300_000)

    monkeypatch.setattr("kdvops.kubectl_tool.subprocess.run", fake_run)

    result = kubectl_read(["get", "pods"])
    assert len(result) < 300_000
    assert result.endswith("\n[output truncated by kdvops-cli]\n")


def test_reports_timeout(monkeypatch):
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], kwargs["timeout"])

    monkeypatch.setattr("kdvops.kubectl_tool.subprocess.run", timeout)

    assert "timed out" in kubectl_read(["get", "pods"])


@pytest.mark.parametrize(
    "config",
    [
        '{"users":[{"user":{"exec":{"command":"plugin"}}}],"clusters":[]}',
        '{"users":[{"user":{"auth-provider":{"name":"legacy"}}}],"clusters":[]}',
        '{"users":[],"clusters":[{"cluster":{"proxy-url":"https://proxy"}}]}',
    ],
)
def test_rejects_dynamic_or_proxied_kubeconfig(config, monkeypatch):
    monkeypatch.setattr(
        "kdvops.kubectl_tool.subprocess.run", lambda *a, **kw: completed(stdout=config)
    )
    assert "kubeconfig must be static" in kubectl_read(["get", "pods"])
