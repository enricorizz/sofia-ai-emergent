"""
Gateway configuration utilities for writing dynamic environment variables.

This module handles writing secrets (tokens, API keys) to an environment file
that gets loaded by the supervised gateway wrapper script.

Security note
-------------
Credentials are written to a root-owned file with 0o600 permissions
(readable by the process owner only). This is intentional: the gateway
subprocess launched by supervisord needs the credentials available as
environment variables. No alternative mechanism (e.g. stdin pipe) is
supported by the gateway binary. The file is deleted when the gateway is
stopped via clear_gateway_env().
"""

import os
import stat

# Path to the gateway environment file
GATEWAY_ENV_FILE = "/root/.openclaw/gateway.env"
GATEWAY_ENV_DIR = "/root/.openclaw"


def write_gateway_env(token: str, api_key: str = None, provider: str = "emergent") -> None:
    """Write secrets to an owner-only (0o600) env file for the gateway subprocess.

    The file is consumed by the supervisor-managed gateway wrapper and deleted
    by clear_gateway_env() when the gateway is stopped.

    Args:
        token: The gateway authentication token.
        api_key: Optional provider API key (Anthropic / OpenAI).
        provider: The provider name – "emergent", "anthropic", or "openai".
    """
    # Ensure directory exists with restrictive permissions
    os.makedirs(GATEWAY_ENV_DIR, mode=0o700, exist_ok=True)

    lines = [f'export OPENCLAW_GATEWAY_TOKEN="{token}"']

    if api_key:
        if provider == "anthropic":
            lines.append(f'export ANTHROPIC_API_KEY="{api_key}"')
        elif provider == "openai":
            lines.append(f'export OPENAI_API_KEY="{api_key}"')
        # emergent provider: API key lives in the JSON config, not an env var

    content = "\n".join(lines) + "\n"

    # Write then immediately restrict to owner-read/write only (0o600).
    # nosec B105 – this is the intended behaviour; see module docstring.
    with open(GATEWAY_ENV_FILE, "w") as f:  # noqa: S506
        f.write(content)

    os.chmod(GATEWAY_ENV_FILE, stat.S_IRUSR | stat.S_IWUSR)  # 0o600


def clear_gateway_env() -> None:
    """Delete the gateway environment file.

    Called when stopping the gateway to purge credentials from disk.
    """
    if os.path.exists(GATEWAY_ENV_FILE):
        os.remove(GATEWAY_ENV_FILE)
