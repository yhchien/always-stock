"""Manual Jev Gateway smoke test for GitHub Actions or local development."""

import json
import sys
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.jev_client import JevClientError, evaluate  # noqa: E402


def main() -> int:
    try:
        result = evaluate(
            state={
                "message": "The support agent confirmed that the customer's payment was refunded.",
                "source": "always-stock Jev Gateway smoke test",
            },
            questions={
                "refunded": {
                    "type": "boolean",
                    "instructions": "Was money returned to the customer?",
                    "criteria": {
                        "true": "The payment was refunded.",
                        "false": "No refund was issued.",
                    },
                }
            },
        )
    except JevClientError as exc:
        print("Jev Gateway smoke test failed: " + str(exc))
        return 1

    print(json.dumps({
        "model": result.get("model"),
        "answers": result.get("answers"),
        "usage": result.get("usage"),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
