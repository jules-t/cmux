from __future__ import annotations

import argparse


class PreflightError(RuntimeError):
    pass


def evaluate_preflight(
    *,
    observe_result: str,
    ready: str,
    prepare_result: str,
    candidate_status: str,
    resolve_result: str,
    review_result: str,
    approved: str,
) -> str:
    if observe_result != "success":
        raise PreflightError(f"upstream observation failed ({observe_result or 'missing'})")
    if ready == "false":
        return "preflight verified: personal/stable is already current"
    if ready != "true":
        raise PreflightError(f"upstream readiness output is invalid ({ready or 'missing'})")
    if prepare_result != "success":
        raise PreflightError(
            f"candidate preparation failed ({prepare_result or 'missing'})"
        )
    if candidate_status == "clean":
        return "preflight verified: clean candidate passed deterministic verification"
    if candidate_status != "conflict":
        raise PreflightError(
            f"candidate status is invalid ({candidate_status or 'missing'})"
        )
    if (
        resolve_result != "success"
        or review_result != "success"
        or approved != "true"
    ):
        raise PreflightError(
            "conflict candidate did not pass the resolver and review gates "
            f"(resolve={resolve_result or 'missing'}, "
            f"review={review_result or 'missing'}, approved={approved or 'missing'})"
        )
    return "preflight verified: resolved candidate passed deterministic and independent review"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Fail closed unless a manual main-canary preflight completed safely."
    )
    parser.add_argument("--observe-result", required=True)
    parser.add_argument("--ready", required=True)
    parser.add_argument("--prepare-result", required=True)
    parser.add_argument("--candidate-status", required=True)
    parser.add_argument("--resolve-result", required=True)
    parser.add_argument("--review-result", required=True)
    parser.add_argument("--approved", required=True)
    args = parser.parse_args()
    print(
        evaluate_preflight(
            observe_result=args.observe_result,
            ready=args.ready,
            prepare_result=args.prepare_result,
            candidate_status=args.candidate_status,
            resolve_result=args.resolve_result,
            review_result=args.review_result,
            approved=args.approved,
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except PreflightError as exc:
        raise SystemExit(f"main canary preflight blocked: {exc}") from exc
