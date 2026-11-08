"""Fast, local secret scanning with the pinned Gitleaks binary."""

import json
import os
import re
import subprocess
import tempfile
from pathlib import Path


SECRET_PATH_PATTERN = re.compile(r"(^|/|\\)(?:\.env(?:\..*)?|.*\.(?:pem|key|p12|pfx)|kubeconfig|credentials?)$", re.I)


def _safe_finding(finding: dict) -> dict:
    return {
        "rule_id": finding.get("RuleID"),
        "description": finding.get("Description"),
        "file": finding.get("File"),
        "line": finding.get("StartLine"),
        "severity": "HIGH",
        "match": "[REDACTED]",
    }


def scan_content(content: str, filename: str = "input.yaml", timeout: float = 8.0) -> dict:
    if not content:
        raise ValueError("content must not be empty")
    suffix = Path(filename).suffix or ".txt"
    with tempfile.TemporaryDirectory(prefix="kdvops-secret-scan-") as directory:
        path = Path(directory) / f"input{suffix}"
        path.write_text(content, encoding="utf-8")
        return _run_scan(path, timeout)


def _run_scan(path: Path, timeout: float) -> dict:
    with tempfile.TemporaryDirectory(prefix="kdvops-gitleaks-") as directory:
        report = Path(directory) / "report.json"
        command = [
            "gitleaks", "dir", str(path), "--no-banner", "--redact",
            "--config", "/app/gitleaks.toml",
            "--report-format", "json", "--report-path", str(report),
            "--exit-code", "1",
        ]
        try:
            completed = subprocess.run(
                command, capture_output=True, text=True, timeout=timeout,
                check=False, env={"PATH": os.getenv("PATH", "")},
            )
        except FileNotFoundError as exc:
            raise RuntimeError("Gitleaks is not installed in the service image") from exc
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("Secret scan exceeded its time limit") from exc
        if completed.returncode not in {0, 1}:
            raise RuntimeError("Gitleaks scan failed")
        findings = []
        if report.exists():
            try:
                raw = json.loads(report.read_text(encoding="utf-8"))
                findings = [_safe_finding(item) for item in raw if isinstance(item, dict)]
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                raise RuntimeError("Gitleaks returned an invalid report") from exc
        return {"clean": not findings, "findings": findings, "scanner": "gitleaks", "version": "8.30.1"}


def is_sensitive_path(path: str) -> bool:
    return bool(SECRET_PATH_PATTERN.search(path.replace("\\", "/")))
