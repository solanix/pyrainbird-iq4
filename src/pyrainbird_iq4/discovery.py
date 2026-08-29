"""Read-only Rain Bird IQ4 discovery utility."""

from __future__ import annotations

import argparse
import asyncio
import json

from .client import RainBirdIQ4Client
from .exceptions import RainBirdIQ4Error


async def _probe(host: str, port: int, verify_ssl: bool, path: str) -> int:
    client = RainBirdIQ4Client(
        host,
        port=port,
        verify_ssl=verify_ssl,
    )

    try:
        result = await client.probe(path)
    except RainBirdIQ4Error as exc:
        print(f"ERROR: {exc}")
        return 1

    print()
    print("Rain Bird IQ4 HTTP Probe")
    print("=" * 60)
    print(f"URL:          {result.url}")
    print(f"Status:       {result.status} {result.reason}")
    print(f"Content-Type: {result.content_type}")
    print()

    print("Response headers")
    print("-" * 60)
    print(json.dumps(result.headers, indent=2, sort_keys=True))

    print()
    print("Response body")
    print("-" * 60)

    body = result.body

    if len(body) > 10000:
        body = body[:10000]
        body += "\n\n[response truncated after 10,000 characters]"

    print(body)

    return 0


def main() -> None:
    """Run the discovery utility."""
    parser = argparse.ArgumentParser(
        description="Perform a read-only HTTP probe of a Rain Bird IQ4 controller."
    )

    parser.add_argument(
        "host",
        help="IQ4 controller hostname or IP address",
    )

    parser.add_argument(
        "--port",
        type=int,
        default=443,
        help="HTTPS port (default: 443)",
    )

    parser.add_argument(
        "--path",
        default="/",
        help="HTTP path to probe (default: /)",
    )

    parser.add_argument(
        "--insecure",
        action="store_true",
        help="Disable TLS certificate verification for local discovery",
    )

    args = parser.parse_args()

    raise SystemExit(
        asyncio.run(
            _probe(
                args.host,
                args.port,
                not args.insecure,
                args.path,
            )
        )
    )


if __name__ == "__main__":
    main()
