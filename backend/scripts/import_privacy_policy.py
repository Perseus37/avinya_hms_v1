"""Validate a structured questionnaire export and submit it as a policy draft."""

import argparse
import json
import os
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from backend.domains.privacy.policy_schema import (
    questionnaire_answers_to_policy_document,
    validate_policy_document,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "policy_file",
        help="JSON envelope with policy_type and answers or policy_document",
    )
    parser.add_argument(
        "--api-url",
        required=True,
        help="Tenant API base URL, for example https://hospital.example",
    )
    args = parser.parse_args()
    api_url = urlsplit(args.api_url)
    loopback_hosts = {"localhost", "127.0.0.1", "::1"}
    if (
        api_url.scheme != "https"
        and not (api_url.scheme == "http" and api_url.hostname in loopback_hosts)
    ):
        parser.error("Use HTTPS for tenant APIs; HTTP is allowed only for loopback.")
    if api_url.username or api_url.password or not api_url.hostname:
        parser.error("API URL must not include credentials and must include a hostname.")
    token = os.environ.get("AVINYA_PRIVACY_API_TOKEN")
    if not token:
        parser.error("Set AVINYA_PRIVACY_API_TOKEN to an authorized tenant API token.")

    try:
        with open(args.policy_file, encoding="utf-8") as policy_file:
            payload = json.load(policy_file)
        policy_type = payload["policy_type"]
        if "answers" in payload:
            policy_document = questionnaire_answers_to_policy_document(
                policy_type, payload["answers"]
            )
            payload = {
                "policy_type": policy_type,
                "policy_document": policy_document,
            }
        else:
            policy_document = payload["policy_document"]
            validate_policy_document(policy_type, policy_document)
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        print(f"Policy file is invalid: {exc}", file=sys.stderr)
        return 2

    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    request = Request(
        f"{args.api_url.rstrip('/')}/v1/api/privacy/policies",
        data=body,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=30) as response:  # nosec B310
            result = json.loads(response.read())
    except HTTPError as exc:
        print(f"Policy import failed with HTTP {exc.code}.", file=sys.stderr)
        return 1
    except URLError as exc:
        print(f"Policy import failed: {exc.reason}", file=sys.stderr)
        return 1

    print(
        f"Created policy draft {result['id']} "
        f"(type={result['policy_type']}, status={result['status']})."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
