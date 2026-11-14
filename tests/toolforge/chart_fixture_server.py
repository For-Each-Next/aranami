"""Render fixture charts through the production saved-data API.

Run once with the rendered fixture directory. Each stdin JSON line
supplies an id and query string. Stdout returns its id, status and body.
The worker reads retained synthetic data and never opens source clients.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from urllib.parse import parse_qs

from webservice.quality_chart_requests import (
    StaleQualityChartError,
    filtered_charts,
)


def response(runtime: Path, request: dict) -> dict:
    """Render one request with the production selection validation.

    Returns:
        A response envelope for the local fixture HTTP server.
    """
    identity = request.get("id")
    query = request.get("query")
    if not isinstance(query, str):
        return {
            "id": identity,
            "status": 400,
            "body": {"error": "The fixture chart query must be a string."},
        }
    try:
        parameters = parse_qs(
            query.removeprefix("?"),
            keep_blank_values=True,
            errors="strict",
            max_num_fields=7,
        )
        body = filtered_charts(runtime, parameters)
    except StaleQualityChartError as error:
        return {"id": identity, "status": 409, "body": {"error": str(error)}}
    except LookupError as error:
        return {"id": identity, "status": 503, "body": {"error": str(error)}}
    except (ValueError, TypeError, KeyError) as error:
        return {"id": identity, "status": 400, "body": {"error": str(error)}}
    return {"id": identity, "status": 200, "body": body}


def main() -> None:
    """Serve JSON lines until the browser checker closes its pipe."""
    runtime = Path(sys.argv[1]).resolve() / ".quality-chart-runtime"
    for line in sys.stdin:
        result = response(runtime, json.loads(line))
        sys.stdout.write(json.dumps(result, ensure_ascii=False) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
