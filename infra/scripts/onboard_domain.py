#!/usr/bin/env python3
"""Print the steps that put a broker hostname live behind Azure Front Door.

Given a firm slug, a hostname and the surface it serves, this prints:

  1. the DNS records the broker creates (CNAME to the Front Door endpoint and
     the `_dnsauth` TXT record that validates the managed certificate);
  2. the infrastructure change: the `frontDoorCustomDomains` entry for
     infra/bicep/parameters.<env>.json (the source of truth), and the `az`
     commands an operator can run to apply it before the next infra deploy;
  3. the application step: register the hostname for the firm in the platform
     console (a `pending` tenant_domains row) and activate it once the
     certificate is deployed, plus the Entra redirect URI for staff hosts.

Nothing is executed and no network call is made. The `az` commands are printed
for an operator to review and run. The only side effect is optional: with
`--write-params` the new entry is added to the local parameters file.

Runbook: docs/FRONT_DOOR_RUNBOOK.md.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
SURFACES = ("staff", "client", "all")
# Front Door Premium allows 500 custom domains per profile.
DOMAIN_LIMIT = 500
# Microsoft.Cdn/profiles/customDomains names: letters, digits, single or
# repeated hyphens between them, at most 260 characters.
RESOURCE_NAME = re.compile(r"[a-zA-Z0-9]+(-*[a-zA-Z0-9])*")
RESOURCE_NAME_MAX = 260
DNS_LABEL = re.compile(r"(?!-)[a-z0-9-]{1,63}(?<!-)")
# Two-label public suffixes common in this market. A hostname with exactly one
# label in front of one of these, or two labels in total, is probably an apex.
TWO_LABEL_SUFFIXES = frozenset({
    "com.sg", "net.sg", "org.sg", "edu.sg", "gov.sg", "per.sg",
    "com.my", "com.hk", "com.au", "co.uk", "co.id", "co.nz", "co.jp", "com.cn",
})


class OnboardError(ValueError):
    """An input that would produce a broken onboarding."""


def normalize_hostname(raw: str) -> str:
    """Lowercase, validated hostname (same rules as the platform API)."""
    host = raw.strip().lower().rstrip(".")
    labels = host.split(".")
    if host.startswith("*."):
        raise OnboardError("Wildcard hostnames are not onboarded per firm.")
    if not host or len(host) > 253 or len(labels) < 2 or not all(
        DNS_LABEL.fullmatch(label) for label in labels
    ):
        raise OnboardError(f"{raw!r} is not a hostname such as benefits.example.com.")
    if labels[-1].isdigit():
        raise OnboardError(f"{raw!r} is an IP address, not a hostname.")
    if host.endswith(".localhost") or host.endswith(".azurefd.net"):
        raise OnboardError(f"{raw!r} cannot be a customer domain.")
    return host


def normalize_slug(raw: str) -> str:
    slug = raw.strip().lower()
    if not DNS_LABEL.fullmatch(slug):
        raise OnboardError(f"{raw!r} is not a firm slug (one DNS label: a-z, 0-9, hyphen).")
    return slug


def looks_like_apex(host: str) -> bool:
    labels = host.split(".")
    if len(labels) == 2:
        return True
    return len(labels) == 3 and ".".join(labels[-2:]) in TWO_LABEL_SUFFIXES


def resource_name(slug: str, host: str) -> str:
    """The customDomains resource name; same formula as modules/frontdoor.bicep."""
    name = f"{slug}--{host.replace('.', '-')}"
    if len(name) > RESOURCE_NAME_MAX or not RESOURCE_NAME.fullmatch(name):
        raise OnboardError(f"Derived resource name {name!r} is not a valid Front Door name.")
    return name


def load_domains(params_path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """The parameters document and its current frontDoorCustomDomains list."""
    try:
        document = json.loads(params_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise OnboardError(f"Parameters file not found: {params_path}") from None
    except json.JSONDecodeError as exc:
        raise OnboardError(f"{params_path} is not valid JSON: {exc}") from None
    entry = document.get("parameters", {}).get("frontDoorCustomDomains", {})
    domains = entry.get("value", []) if isinstance(entry, dict) else []
    if not isinstance(domains, list) or not all(
        isinstance(d, dict) and {"hostname", "firmSlug"} <= d.keys() for d in domains
    ):
        raise OnboardError(
            f"{params_path}: frontDoorCustomDomains must be a list of "
            '{"hostname": ..., "firmSlug": ...} objects.'
        )
    return document, domains


def _compact(value: object) -> str:
    return json.dumps(value, ensure_ascii=False)


def dump_params(document: dict[str, Any]) -> str:
    """The parameters file in its house style: one line per parameter."""
    lines = ["{"]
    keys = list(document)
    for index, key in enumerate(keys):
        comma = "," if index < len(keys) - 1 else ""
        if key == "parameters" and isinstance(document[key], dict):
            params = document[key]
            lines.append(f'  "{key}": {{')
            names = list(params)
            for p_index, name in enumerate(names):
                p_comma = "," if p_index < len(names) - 1 else ""
                entry = params[name]
                body = _compact(entry)
                if isinstance(entry, dict):
                    pairs = ", ".join(f"{_compact(k)}: {_compact(v)}" for k, v in entry.items())
                    body = "{ " + pairs + " }"
                lines.append(f"    {_compact(name)}: {body}{p_comma}")
            lines.append(f"  }}{comma}")
        else:
            lines.append(f"  {_compact(key)}: {_compact(document[key])}{comma}")
    lines.append("}")
    return "\n".join(lines) + "\n"


def write_domains(
    params_path: Path, document: dict[str, Any], domains: list[dict[str, Any]]
) -> None:
    """Set frontDoorCustomDomains, keeping every other line of the file as it was."""
    original = params_path.read_bytes().decode("utf-8")
    if dump_params(document) != original.replace("\r\n", "\n"):
        raise OnboardError(
            f"{params_path} is not in the expected one-parameter-per-line layout; "
            "add the entry by hand instead of --write-params."
        )
    document.setdefault("parameters", {})["frontDoorCustomDomains"] = {"value": domains}
    newline = "\r\n" if "\r\n" in original else "\n"
    with params_path.open("w", encoding="utf-8", newline=newline) as handle:
        handle.write(dump_params(document))


def section(title: str) -> None:
    print(f"\n{'=' * 78}\n{title}\n{'=' * 78}")


def print_dns(host: str, endpoint_host: str, apex: bool) -> None:
    section("1. DNS records the broker creates (at their DNS provider)")
    print(f"  TXT    _dnsauth.{host}    <validation token: see step 2c>")
    if apex:
        print(f"  ALIAS/ANAME/flattened CNAME  {host}  ->  {endpoint_host}")
        print("\n  WARNING: this looks like an apex domain. An apex cannot hold a CNAME;")
        print("  it needs an Azure DNS alias record (or the provider's CNAME flattening),")
        print("  and Front Door-managed certificates on an apex need the domain")
        print("  revalidated before each renewal. Ask the broker for a subdomain instead")
        print("  (e.g. benefits.<their domain>).")
    else:
        print(f"  CNAME  {host}    {endpoint_host}")
    print("\n  The TXT record comes first, as soon as step 2c gives its value. The CNAME")
    print("  comes last, after step 3 activates the hostname: once it resolves, the")
    print("  broker's traffic reaches Front Door.")
    print("  If the broker's domain publishes CAA records, they must allow DigiCert, the")
    print("  issuer of Front Door-managed certificates (`0 issue digicert.com`), or")
    print("  issuance fails. Keep the CNAME pointing straight at the endpoint (no chain,")
    print("  no A record): only then does Front Door renew the certificate by itself.")


def display_path(path: Path) -> str:
    resolved = path.resolve()
    return str(resolved.relative_to(REPO_ROOT)) if resolved.is_relative_to(REPO_ROOT) else str(path)


def print_infra(args: argparse.Namespace, host: str, name: str, names: list[str],
                new_entry: dict[str, str], wrote: bool) -> None:
    prefix = f"inspro-{args.env}"
    section("2. Front Door: custom domain, certificate, routes and WAF")
    print("  a) Source of truth: add this entry to frontDoorCustomDomains in")
    print(f"     {display_path(args.params_file)}:")
    print("\n       " + json.dumps(new_entry))
    print("\n     " + ("Written (--write-params)." if wrote else
                       "Not written (dry run). Re-run with --write-params to add it."))
    print("     The next infrastructure deploy then creates the domain and binds it to")
    print("     both routes and the WAF. Routes and the WAF policy are reconciled from")
    print("     this list on every deploy, so a domain added only with the commands")
    print("     below is DETACHED again by the next deploy unless the entry is committed.")

    domain_ids = " ".join(f'"$PROFILE_ID/customDomains/{n}"' for n in names)
    print("\n  b) Apply now without a full infrastructure deploy (operator, reviewed):\n")
    print(f"     RG={args.resource_group}")
    print(f"     PROFILE={prefix}-afd")
    print(f"     ENDPOINT={prefix}")
    print('     PROFILE_ID=$(az afd profile show -g "$RG" --profile-name "$PROFILE" \\')
    print("       --query id -o tsv)")
    print()
    print('     az afd custom-domain create -g "$RG" --profile-name "$PROFILE" \\')
    print(f"       --custom-domain-name {name} --host-name {host} \\")
    print("       --certificate-type ManagedCertificate --minimum-tls-version TLS12")
    print()
    print("     # Both routes take the FULL list: --custom-domains replaces it.")
    for route in ("default", "assets"):
        print('     az afd route update -g "$RG" --profile-name "$PROFILE" \\')
        print(f'       --endpoint-name "$ENDPOINT" --route-name {route} \\')
        print(f"       --custom-domains {' '.join(names)}")
    print()
    print("     # The WAF covers only the domains on its security policy (FULL list).")
    print('     az afd security-policy update -g "$RG" --profile-name "$PROFILE" \\')
    print("       --security-policy-name waf \\")
    print(f'       --domains "$PROFILE_ID/afdEndpoints/$ENDPOINT" {domain_ids}')

    print("\n  c) Read the TXT value for step 1, then poll until the certificate is live:\n")
    print('     az afd custom-domain show -g "$RG" --profile-name "$PROFILE" \\')
    print(f"       --custom-domain-name {name} \\")
    print('       --query "{validation:domainValidationState,'
          ' token:validationProperties.validationToken,'
          ' expires:validationProperties.expirationDate,'
          ' deployment:deploymentStatus}" -o json')
    print("\n     Wait for validation=Approved and deployment=Succeeded. An unused token")
    print("     times out after 7 days; regenerate it with")
    print("     `az afd custom-domain regenerate-validation-token`.")


def print_app(args: argparse.Namespace, host: str) -> None:
    section("3. Application: register and activate the hostname")
    print("  Master admin, signed in on a platform host, in the platform console:")
    print(f"    Platform -> Firms -> {args.firm_slug} -> Domains -> Add")
    print(f"      hostname={host}  surface={args.surface}  primary={str(args.primary).lower()}")
    print("    The row is created `pending`: nothing routes to it yet.")
    print("    After step 2c shows Approved + Succeeded, open the domain and Activate it,")
    print("    then have the broker switch the CNAME. (Activated before the CNAME, nothing")
    print("    routes to it yet; a CNAME before activation answers 404 unknown_site.)")
    print("    Broker hosts resolve only once frontDoorRequireEdge is on: before that the")
    print("    app sees the origin's own hostname and serves the platform owner's firm.")
    print("\n  The same through the platform API (master-admin session on a platform host):")
    print(f"    GET   /api/v1/platform/firms                     (id of firm {args.firm_slug!r})")
    body = {"hostname": host, "surface": args.surface, "is_primary": args.primary}
    print(f"    POST  /api/v1/platform/firms/<firm_id>/domains   {json.dumps(body)}")
    print('    PATCH /api/v1/platform/domains/<domain_id>        {"status": "active"}')
    print("  (A firm admin may already have requested it from the firm console; then")
    print("  only the activation is needed.) There is no CLI script for this step: the")
    print("  console and API write the platform audit log, which a direct database edit")
    print("  would bypass.")
    if args.surface in ("staff", "all"):
        print("\n  Staff surface: add the Entra redirect URI before staff sign in")
        print(f"    https://{host}/auth/callback   (SPA platform; see docs/ENTRA_SETUP.md)")
    print("\n  Verify:")
    print(f"    curl -sI https://{host}/health    # 200 through Front Door")
    print(f"    https://{host}/                   # shows the firm's brand, not Inspro's")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Print the DNS, Front Door and application steps that put a broker "
            "hostname live. Executes nothing; makes no network calls."
        ),
    )
    parser.add_argument("--firm-slug", required=True,
                        help="broker_firms.slug of the owning firm")
    parser.add_argument("--hostname", required=True, help="e.g. benefits.acme.com")
    parser.add_argument("--surface", required=True, choices=SURFACES,
                        help="staff app, client (HR + employee) portals, or all")
    parser.add_argument("--primary", action="store_true",
                        help="the firm's primary hostname for this surface (emailed links)")
    parser.add_argument("--env", default="prod", choices=("prod", "staging"))
    parser.add_argument("--resource-group", default="rg-inspro-prod")
    parser.add_argument("--params-file", type=Path, default=None,
                        help="parameters file (default: infra/bicep/parameters.<env>.json)")
    parser.add_argument("--endpoint-host", default=None,
                        help="Front Door endpoint hostname: the frontDoorEndpointHostName "
                             "deployment output (<name>-<hash>.<zone>.azurefd.net)")
    parser.add_argument("--write-params", action="store_true",
                        help="add the entry to the local parameters file (default: dry run)")
    return parser


def plan(args: argparse.Namespace) -> tuple[str, str, list[str], dict[str, str], bool, bool]:
    """Validate the request against the parameters file; write it if asked."""
    args.firm_slug = normalize_slug(args.firm_slug)
    host = normalize_hostname(args.hostname)
    name = resource_name(args.firm_slug, host)
    document, domains = load_domains(args.params_file)
    existing = {str(d["hostname"]).strip().lower().rstrip("."): d for d in domains}
    owner = str(existing[host]["firmSlug"]).strip().lower() if host in existing else None
    if owner is not None and owner != args.firm_slug:
        raise OnboardError(f"{host} is already assigned to firm {owner!r} in the parameters file.")
    new_entry = {"hostname": host, "firmSlug": args.firm_slug}
    updated = domains if owner else [*domains, new_entry]
    if len(updated) > DOMAIN_LIMIT:
        raise OnboardError(
            f"{len(updated)} domains exceeds the Front Door Premium limit of "
            f"{DOMAIN_LIMIT} per profile; plan a second profile."
        )
    names = [
        resource_name(normalize_slug(str(d["firmSlug"])), normalize_hostname(str(d["hostname"])))
        for d in updated
    ]
    wrote = bool(args.write_params and not owner)
    if wrote:
        write_domains(args.params_file, document, updated)
    return host, name, names, new_entry, wrote, owner is not None


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.params_file is None:
        args.params_file = REPO_ROOT / "infra" / "bicep" / f"parameters.{args.env}.json"
    try:
        host, name, names, new_entry, wrote, already = plan(args)
    except OnboardError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    endpoint_host = args.endpoint_host or "<frontDoorEndpointHostName output>"
    state = "parameters updated" if wrote else "dry run, nothing changed"
    print(f"Onboarding {host} for firm {args.firm_slug!r} (surface: {args.surface}): {state}.")
    if already:
        print("Note: the hostname is already in the parameters file; printed for reference.")
    print(f"Domains on the profile after this change: {len(names)} of {DOMAIN_LIMIT}.")
    print_dns(host, endpoint_host, looks_like_apex(host))
    print_infra(args, host, name, names, new_entry, wrote)
    print_app(args, host)
    print("\nOrder: custom domain created -> TXT record -> validation Approved -> certificate")
    print("deployed -> hostname activated in the platform console -> CNAME switched.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
