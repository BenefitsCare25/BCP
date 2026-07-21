"""Standalone AWS Bedrock access check — run BEFORE wiring the app.

Confirms that your AWS credentials, the Singapore/APAC region, and the chosen
Claude inference profile actually work, in isolation from the rest of Inspro.

Set these env vars, then run `uv run python scripts/verify_bedrock.py`:

    AWS_BEDROCK_REGION       (default ap-southeast-1)
    AWS_BEDROCK_MODEL        the apac.* inference-profile id from the console
    AWS_ACCESS_KEY_ID        the inspro-bedrock-dev access key
    AWS_SECRET_ACCESS_KEY    its secret

A "bedrock ok" reply means access works and you can flip INSPRO_AI_PROVIDER=bedrock.
"""
from __future__ import annotations

import os
import sys


def main() -> int:
    region = os.environ.get("AWS_BEDROCK_REGION", "ap-southeast-1").strip()
    model = os.environ.get("AWS_BEDROCK_MODEL", "").strip()
    if not model:
        print("ERROR: set AWS_BEDROCK_MODEL to your apac.* inference-profile id.")
        return 2
    if model.lower().startswith("global."):
        print(
            "REFUSED: AWS_BEDROCK_MODEL is a 'global.*' profile — it can leave "
            "Singapore. Use a single-region or 'apac.*' profile."
        )
        return 2
    if not (os.environ.get("AWS_ACCESS_KEY_ID") and os.environ.get("AWS_SECRET_ACCESS_KEY")):
        print("ERROR: set AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY.")
        return 2

    try:
        from anthropic import AnthropicBedrock
    except ImportError:
        print("ERROR: run inside the backend venv (uv run ...) so anthropic[bedrock] is present.")
        return 2

    client = AnthropicBedrock(aws_region=region)
    print(f"Region : {region}")
    print(f"Model  : {model}")
    try:
        resp = client.messages.create(
            model=model,
            max_tokens=32,
            messages=[{"role": "user", "content": "Reply with exactly: bedrock ok"}],
        )
    except Exception as exc:  # surface the raw AWS/Anthropic error verbatim
        print(f"\nFAILED: {type(exc).__name__}: {exc}")
        print(
            "\nCommon causes: model access not enabled in ap-southeast-1, the "
            "profile id is wrong, or the IAM policy doesn't allow "
            "bedrock:InvokeModel on the profile + foundation-model ARNs."
        )
        return 1

    text = "".join(getattr(b, "text", "") for b in resp.content)
    print(f"\nRESPONSE: {text!r}")
    print(f"USAGE   : in={resp.usage.input_tokens} out={resp.usage.output_tokens}")
    print("\nOK — Bedrock access works. You can set INSPRO_AI_PROVIDER=bedrock.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
