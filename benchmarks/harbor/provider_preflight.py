"""Minimal paid provider contract probes with redacted, auditable output."""

from __future__ import annotations

import argparse
import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


def post(url: str, api_key: str, body: dict[str, Any]) -> tuple[int, dict[str, Any], dict[str, str]]:
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            payload = json.loads(response.read())
            return response.status, payload, dict(response.headers.items())
    except urllib.error.HTTPError as error:
        try:
            payload = json.loads(error.read())
        except (ValueError, UnicodeDecodeError):
            payload = {"error": {"type": "non_json_error"}}
        return error.code, payload, dict(error.headers.items())


def usage_summary(payload: dict[str, Any]) -> dict[str, int]:
    usage = payload.get("usage") or {}
    input_tokens = int(usage.get("input_tokens", usage.get("prompt_tokens", 0)))
    output_tokens = int(usage.get("output_tokens", usage.get("completion_tokens", 0)))
    cached = int(
        (usage.get("input_tokens_details") or {}).get(
            "cached_tokens",
            (usage.get("prompt_tokens_details") or {}).get("cached_tokens", 0),
        )
    )
    reasoning = int((usage.get("output_tokens_details") or {}).get("reasoning_tokens", 0))
    return {
        "input_tokens": input_tokens,
        "cached_input_tokens": cached,
        "output_tokens": output_tokens,
        "reasoning_tokens": reasoning,
    }


def point_evidence(payload: dict[str, Any], headers: dict[str, str]) -> dict[str, Any] | None:
    candidates: dict[str, Any] = {}
    for name, value in headers.items():
        if "point" in name.lower() or "credit" in name.lower():
            candidates[f"header:{name.lower()}"] = value
    for name, value in (payload.get("usage") or {}).items():
        if "point" in name.lower() or "credit" in name.lower():
            candidates[f"usage:{name}"] = value
    return candidates or None


def safe_error(payload: dict[str, Any]) -> dict[str, Any] | None:
    error = payload.get("error")
    if not isinstance(error, dict):
        return None
    return {name: error.get(name) for name in ("type", "code") if error.get(name) is not None}


def decoded_arguments(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        return value
    if not isinstance(value, str):
        return None
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError:
        return None
    return decoded if isinstance(decoded, dict) else None


def valid_completion_arguments(value: Any) -> bool:
    arguments = decoded_arguments(value)
    return arguments is not None and arguments.get("summary") == "preflight-ok"


def glm_probe(api_key: str) -> dict[str, Any]:
    url = "https://open.bigmodel.cn/api/coding/paas/v4/chat/completions"
    tool = {
        "type": "function",
        "function": {
            "name": "runtime_complete",
            "description": "Complete the preflight with the fixed summary",
            "parameters": {
                "type": "object",
                "properties": {"summary": {"type": "string", "minLength": 1}},
                "required": ["summary"],
                "additionalProperties": False,
            },
        },
    }
    first_body = {
        "model": "glm-5.3-flash",
        "messages": [
            {
                "role": "system",
                "content": (
                    "Structure terminal control: validation succeeded. "
                    "Call runtime_complete exactly once with summary preflight-ok. "
                    "Emit no text and call no other tool."
                ),
            },
            {"role": "user", "content": "Validation is complete; finish now."},
        ],
        "tools": [tool],
        "tool_choice": "auto",
        "thinking": {"type": "enabled", "clear_thinking": False},
        "reasoning_effort": "max",
        "max_tokens": 128,
        "temperature": 1,
        "top_p": 0.95,
    }
    status, first, headers = post(url, api_key, first_body)
    choice = ((first.get("choices") or [{}])[0]).get("message") or {}
    calls = choice.get("tool_calls") or []
    follow_status = 0
    follow: dict[str, Any] = {}
    if status == 200 and calls:
        messages = first_body["messages"] + [choice]
        messages.append(
            {
                "role": "tool",
                "tool_call_id": calls[0].get("id"),
                "content": "nonce-ok",
            }
        )
        follow_status, follow, _ = post(
            url,
            api_key,
            {
                **first_body,
                "messages": messages,
                "tool_choice": "none",
                "max_tokens": 64,
            },
        )
    error_status, _, _ = post(
        url,
        api_key,
        {"model": "invalid-preflight-model", "messages": [{"role": "user", "content": "x"}]},
    )
    evidence = point_evidence(first, headers)
    sole_call = calls[0] if len(calls) == 1 else {}
    function = sole_call.get("function") or {}
    structured_completion_call_passed = (
        len(calls) == 1
        and function.get("name") == "runtime_complete"
        and valid_completion_arguments(function.get("arguments"))
    )
    assistant_text_present = bool(str(choice.get("content") or "").strip())
    strict_no_mixed_text_passed = (
        structured_completion_call_passed and not assistant_text_present
    )
    return {
        "provider": "glm_coding_plan",
        "endpoint": url,
        "model": "glm-5.3-flash",
        "authentication_passed": status == 200,
        "http_status": status,
        "safe_error": safe_error(first),
        "thinking_passed": bool(choice.get("reasoning_content")),
        "tool_call_passed": structured_completion_call_passed,
        "auto_completion_contract_passed": structured_completion_call_passed,
        "strict_no_mixed_text_passed": strict_no_mixed_text_passed,
        "assistant_text_present": assistant_text_present,
        "auto_v2_normalization_required": (
            structured_completion_call_passed and assistant_text_present
        ),
        "reasoning_tool_state_roundtrip_passed": follow_status == 200,
        "error_contract_passed": error_status >= 400,
        "usage": usage_summary(first),
        "follow_up_usage": usage_summary(follow),
        "auditable_coding_plan_point_evidence": evidence,
        "price_weighting_auditable": evidence is not None,
    }


def deepseek_probe(api_key: str) -> dict[str, Any]:
    url = "https://api.deepseek.com/responses"
    tool = {
        "type": "function",
        "name": "runtime_complete",
        "description": "Complete the preflight with the fixed summary",
        "parameters": {
            "type": "object",
            "properties": {"summary": {"type": "string", "minLength": 1}},
            "required": ["summary"],
            "additionalProperties": False,
        },
    }
    first_body = {
        "model": "deepseek-v4-flash",
        "input": (
            "Call runtime_complete exactly once with summary preflight-ok. "
            "Emit no text and call no other tool."
        ),
        "tools": [tool],
        # DeepSeek thinking mode rejects forced tool choices, so the probe uses
        # the supported automatic mode and validates the emitted call below.
        "tool_choice": "auto",
        "reasoning": {"effort": "high"},
        "max_output_tokens": 1024,
    }
    status, first, _ = post(url, api_key, first_body)
    output = first.get("output") or []
    calls = [item for item in output if item.get("type") == "function_call"]
    reasoning = [item for item in output if item.get("type") == "reasoning"]
    follow_status = 0
    follow: dict[str, Any] = {}
    if status == 200 and calls:
        follow_status, follow, _ = post(
            url,
            api_key,
            {
                "model": "deepseek-v4-flash",
                "input": [
                    {
                        "role": "user",
                        "content": (
                            "Call runtime_complete exactly once with summary preflight-ok. "
                            "Emit no text and call no other tool."
                        ),
                    },
                    *output,
                    {
                        "type": "function_call_output",
                        "call_id": calls[0].get("call_id"),
                        "output": "nonce-ok",
                    },
                ],
                "reasoning": {"effort": "high"},
                "max_output_tokens": 1024,
            },
        )
    error_status, _, _ = post(
        url,
        api_key,
        {"model": "deepseek-v4-flash", "input": [], "tools": [{"type": "function"}]},
    )
    message_text = "".join(
        str(content.get("text") or "")
        for item in output
        if item.get("type") == "message"
        for content in item.get("content") or []
        if isinstance(content, dict)
    )
    sole_call = calls[0] if len(calls) == 1 else {}
    structured_completion_call_passed = (
        len(calls) == 1
        and sole_call.get("name") == "runtime_complete"
        and valid_completion_arguments(sole_call.get("arguments"))
    )
    assistant_text_present = bool(message_text.strip())
    strict_no_mixed_text_passed = (
        structured_completion_call_passed and not assistant_text_present
    )
    return {
        "provider": "deepseek_responses",
        "endpoint": url,
        "model": "deepseek-v4-flash",
        "authentication_passed": status == 200,
        "http_status": status,
        "safe_error": safe_error(first),
        "thinking_passed": bool(reasoning),
        "tool_call_passed": structured_completion_call_passed,
        "auto_completion_contract_passed": structured_completion_call_passed,
        "strict_no_mixed_text_passed": strict_no_mixed_text_passed,
        "assistant_text_present": assistant_text_present,
        "auto_v2_normalization_required": (
            structured_completion_call_passed and assistant_text_present
        ),
        "reasoning_tool_state_roundtrip_passed": follow_status == 200,
        "error_contract_passed": error_status >= 400,
        "usage": usage_summary(first),
        "follow_up_usage": usage_summary(follow),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--execute-paid-preflight", action="store_true")
    parser.add_argument("--provider", choices=("both", "glm", "deepseek"), default="both")
    args = parser.parse_args()
    if not args.execute_paid_preflight:
        raise ValueError("paid provider calls require --execute-paid-preflight")
    glm_key = os.environ.get("GLM_APIKEY")
    deepseek_key = os.environ.get("DEEPSEEK_APIKEY")
    if args.provider in ("both", "glm") and not glm_key:
        raise ValueError("GLM_APIKEY must be present for the selected provider")
    if args.provider in ("both", "deepseek") and not deepseek_key:
        raise ValueError("DEEPSEEK_APIKEY must be present for the selected provider")
    report = {"schema_version": "structure.provider-preflight/2026-09-v4"}
    if args.provider in ("both", "glm"):
        report["glm"] = glm_probe(str(glm_key))
    if args.provider in ("both", "deepseek"):
        report["deepseek"] = deepseek_probe(str(deepseek_key))
    if args.output.exists():
        raise FileExistsError(args.output)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
