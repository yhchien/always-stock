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
                "company": "台積電",
                "news": "公司公告新增先進製程訂單，並提供來源日期與 URL。",
                "source": "always-stock Jev Gateway smoke test",
            },
            questions={
                "business_relevance": {
                    "type": "choice",
                    "instructions": "How directly does this news concern the named company's own business?",
                    "criteria": {
                        "DIRECT": "Directly concerns the company's own business.",
                        "INDIRECT": "Concerns a related industry or supply-chain relationship.",
                        "UNRELATED": "Does not materially concern the company.",
                        "UNKNOWN": "The supplied evidence is insufficient.",
                    },
                }
            },
        )
    except JevClientError as exc:
        details = ["Jev Gateway smoke test failed: " + str(exc)]
        if exc.status_code is not None:
            details.append("status=" + str(exc.status_code))
        if exc.response_body:
            # Do not print gateway response bodies: they are not needed for
            # the smoke-test result and could contain sensitive request data.
            details.append("response_body_present=true")
        print(" ".join(details))
        return 1

    print(json.dumps({
        "model": result.get("model"),
        "answers": result.get("answers"),
        "usage": result.get("usage"),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
