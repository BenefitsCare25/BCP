r"""Read-only AWS diagnostic for the Bedrock AccessDenied issue.

Run with the SAME AWS keys that are failing in the app. In PowerShell:

    $env:AWS_ACCESS_KEY_ID="<your access key id>"
    $env:AWS_SECRET_ACCESS_KEY="<your secret access key>"
    $env:AWS_BEDROCK_REGION="ap-southeast-1"
    $env:AWS_BEDROCK_MODEL="apac.anthropic.claude-3-5-sonnet-20241022-v2:0"
    cd C:\Users\huien\inspro\backend
    uv run python scripts/aws_diag.py

It (1) shows which identity the key maps to, (2) reproduces the exact
InvokeModel call and prints the FULL denial reason, and (3) best-effort lists
the identity's policies + permission boundary. It CHANGES NOTHING and prints
NO secret — paste the whole output back.
"""
from __future__ import annotations

import json
import os
import sys

import boto3
from botocore.exceptions import ClientError

REGION = os.environ.get("AWS_BEDROCK_REGION", "ap-southeast-1")
MODEL = os.environ.get(
    "AWS_BEDROCK_MODEL", "apac.anthropic.claude-3-5-sonnet-20241022-v2:0"
)


def main() -> int:
    if not (os.environ.get("AWS_ACCESS_KEY_ID") and os.environ.get("AWS_SECRET_ACCESS_KEY")):
        print("ERROR: set AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY first.")
        return 2

    print("=== 1. Which identity does this key map to? (sts:GetCallerIdentity) ===")
    try:
        ident = boto3.client("sts", region_name=REGION).get_caller_identity()
    except Exception as exc:  # surface raw
        print("FAILED:", type(exc).__name__, exc)
        return 1
    arn = ident["Arn"]
    print("Account:", ident["Account"])
    print("ARN    :", arn)

    print()
    print(f"=== 2. Reproduce InvokeModel — model={MODEL} region={REGION} ===")
    brt = boto3.client("bedrock-runtime", region_name=REGION)
    body = json.dumps(
        {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 8,
            "messages": [{"role": "user", "content": "ping"}],
        }
    )
    try:
        brt.invoke_model(modelId=MODEL, body=body)
        print("SUCCESS — the model responded. The app should work now.")
    except ClientError as exc:
        err = exc.response.get("Error", {})
        md = exc.response.get("ResponseMetadata", {})
        print("ERROR code   :", err.get("Code"))
        print("HTTP status  :", md.get("HTTPStatusCode"))
        print("FULL message :")
        print("   ", err.get("Message"))
    except Exception as exc:
        print("ERROR:", type(exc).__name__, exc)

    print()
    print("=== 3. Identity policies + boundary (best-effort; may be denied) ===")
    try:
        iam = boto3.client("iam", region_name=REGION)
        if ":user/" in arn:
            uname = arn.split("/")[-1]
            user = iam.get_user(UserName=uname)["User"]
            pb = user.get("PermissionsBoundary")
            print("Permissions boundary:", pb.get("PermissionsBoundaryArn") if pb else "(none)")
            attached = iam.list_attached_user_policies(UserName=uname)["AttachedPolicies"]
            print("Attached managed policies:", [p["PolicyName"] for p in attached])
            print("Inline policies:", iam.list_user_policies(UserName=uname)["PolicyNames"])
        else:
            print("(key is not a plain IAM user — it's:", arn, ")")
    except Exception as exc:
        print("(IAM read unavailable — expected if key is bedrock-only):", type(exc).__name__)

    return 0


if __name__ == "__main__":
    sys.exit(main())
