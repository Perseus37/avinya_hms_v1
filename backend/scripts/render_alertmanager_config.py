import argparse
import os
from pathlib import Path
import re
from urllib.parse import urlparse

from dotenv import dotenv_values

REPO_ROOT = Path(__file__).resolve().parents[2]
TEMPLATE_PATH = REPO_ROOT / "deployments" / "alertmanager.template.yml"
DEFAULT_OUTPUT_PATH = REPO_ROOT / "deployments" / "alertmanager.yml"


def render_alertmanager_config(env_file: Path | None = None, output_file: Path | None = None) -> Path:
    target_output = output_file or DEFAULT_OUTPUT_PATH
    template_content = TEMPLATE_PATH.read_text(encoding="utf-8")

    env_values = {}
    if env_file and env_file.is_file():
        env_values = dotenv_values(env_file, interpolate=False)

    def get_val(key: str, default: str) -> str:
        val = env_values.get(key) or os.getenv(key)
        return val.strip() if val and val.strip() else default

    # 1. Resolve SMTP host and port
    smtp_host = get_val("SMTP_HOST", "smtp.example.com")
    smtp_port = get_val("SMTP_PORT", "587")
    smtp_smarthost = f"{smtp_host}:{smtp_port}"

    # 2. Resolve SMTP sender email (aligning with SMTP_FROM in .env)
    smtp_from = get_val("ALERT_EMAIL_FROM", "") or get_val("SMTP_FROM", "alerts@avinya-hms.local")
    if "@" not in smtp_from:
        smtp_from = "alerts@avinya-hms.local"

    # 3. Resolve webhook URLs
    default_webhook = get_val("ALERT_DEFAULT_WEBHOOK_URL", "http://api:8000/api/v1/alerts/ingress")
    critical_webhook = get_val(
        "ALERT_CRITICAL_WEBHOOK_URL",
        f"{default_webhook}?severity=critical" if "?" not in default_webhook else default_webhook,
    )
    warning_webhook = get_val(
        "ALERT_WARNING_WEBHOOK_URL",
        f"{default_webhook}?severity=warning" if "?" not in default_webhook else default_webhook,
    )

    # Validate URLs
    for url, name in [
        (default_webhook, "ALERT_DEFAULT_WEBHOOK_URL"),
        (critical_webhook, "ALERT_CRITICAL_WEBHOOK_URL"),
        (warning_webhook, "ALERT_WARNING_WEBHOOK_URL"),
    ]:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise ValueError(f"Invalid URL for {name}: '{url}'. Must have http or https scheme and valid host.")

    # Substitute variables
    substitutions = {
        "${ALERT_SMTP_SMARTHOST}": smtp_smarthost,
        "${ALERT_SMTP_FROM}": smtp_from,
        "${ALERT_DEFAULT_WEBHOOK_URL}": default_webhook,
        "${ALERT_CRITICAL_WEBHOOK_URL}": critical_webhook,
        "${ALERT_WARNING_WEBHOOK_URL}": warning_webhook,
    }

    rendered = template_content
    for placeholder, val in substitutions.items():
        rendered = rendered.replace(placeholder, val)

    # Guard: no unexpanded variables must remain
    unexpanded = re.findall(r"\$\{[^}]+\}", rendered)
    if unexpanded:
        raise ValueError(f"Unexpanded placeholders remain in Alertmanager config: {unexpanded}")

    target_output.write_text(rendered, encoding="utf-8")
    return target_output


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Render production Alertmanager configuration from template and env.")
    parser.add_argument("--env-file", type=Path, default=Path(".env.production"), help="Environment file.")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_PATH, help="Output alertmanager.yml path.")
    args = parser.parse_args(argv)

    out = render_alertmanager_config(args.env_file, args.output)
    print(f"Rendered valid Alertmanager configuration to: {out}")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())

