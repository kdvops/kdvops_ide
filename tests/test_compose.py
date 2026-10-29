from pathlib import Path


def test_tools_profile_has_safe_idle_command_and_no_image_chat_fallback():
    compose = Path("compose.yaml").read_text()
    tools = compose.split("  kdvops-tools:", 1)[1].split("  kdvops-web:", 1)[0]

    assert "entrypoint: []" in tools
    assert 'command: ["sleep", "infinity"]' in tools


def test_web_profile_invokes_kdvops_web_subcommand():
    compose = Path("compose.yaml").read_text()
    web = compose.split("  kdvops-web:", 1)[1].split("\nvolumes:", 1)[0]

    assert "command: [web]" in web
    assert "entrypoint: []" not in web
    assert "KDVOPS_MAX_TOOL_ITERATIONS" not in web
    assert "KDVOPS_RUN_TIMEOUT: ${KDVOPS_RUN_TIMEOUT:-1800}" in web
