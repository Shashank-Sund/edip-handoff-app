#!/usr/bin/env python3
"""Set up the optional Zerobus streaming path for the FHIR feed (docs/zerobus.md).

    DATABRICKS_CONFIG_PROFILE=<profile> python scripts/setup_zerobus.py --catalog edip [--name edip-zerobus]
        [--env-file ~/.config/edip/zerobus.env]

Creates (or reuses) a service principal, gives it exactly what Zerobus needs on one table (USE CATALOG, USE SCHEMA,
SELECT and MODIFY on <catalog>.bronze.redox_fhir_events), creates an OAuth secret, and writes everything the
replayer reads to an env file outside the repo with owner only permissions. The secret is shown nowhere else.
Run it after the setup job has created the bronze table. Re-running creates a new secret and rewrites the file.

Then stream the events:
    set -a; . ~/.config/edip/zerobus.env; set +a
    python synthetic/replay/zerobus_push.py --events synthetic/out/world/fhir/events.jsonl \\
        --table <catalog>.bronze.redox_fhir_events
"""
import argparse
import os
import stat
from pathlib import Path

from databricks.sdk import WorkspaceClient
from databricks.sdk.service import catalog as uc


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--catalog", required=True)
    ap.add_argument("--name", help="service principal display name (default edip-zerobus-<catalog>, so each deployment "
                                   "has its own and never rotates another one's secret)")
    ap.add_argument("--env-file", default=str(Path.home() / ".config" / "edip" / "zerobus.env"))
    a = ap.parse_args()
    a.name = a.name or f"edip-zerobus-{a.catalog}".replace("_", "-")
    w = WorkspaceClient()
    table = f"{a.catalog}.bronze.redox_fhir_events"
    w.tables.get(table)          # fails clearly if setup has not created the table yet

    sp = next(iter(w.service_principals.list(filter=f'displayName eq "{a.name}"')), None)
    if sp is None:
        sp = w.service_principals.create(display_name=a.name, active=True)
        print(f"created service principal {a.name} ({sp.application_id})")
    else:
        print(f"using service principal {a.name} ({sp.application_id})")
    who = sp.application_id
    grants = [(uc.SecurableType.CATALOG, a.catalog, [uc.Privilege.USE_CATALOG]),
              (uc.SecurableType.SCHEMA, f"{a.catalog}.bronze", [uc.Privilege.USE_SCHEMA]),
              (uc.SecurableType.TABLE, table, [uc.Privilege.SELECT, uc.Privilege.MODIFY])]
    for kind, name, privs in grants:
        w.grants.update(securable_type=kind.value, full_name=name,
                        changes=[uc.PermissionsChange(principal=who, add=privs)])
        print(f"granted {', '.join(p.value for p in privs)} on {name}")

    workspace_id = w.get_workspace_id()
    region = w.metastores.summary().region
    host = w.config.host.rstrip("/")
    endpoint = zerobus_endpoint(host, workspace_id, region)
    secret = w.service_principal_secrets_proxy.create(service_principal_id=str(sp.id))
    path = Path(a.env_file).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    # Create the file owner only before the secret is written to it.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, stat.S_IRUSR | stat.S_IWUSR)
    os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    with os.fdopen(fd, "w") as f:
        f.write(f"DATABRICKS_CLIENT_ID={who}\nDATABRICKS_CLIENT_SECRET={secret.secret}\nWORKSPACE_URL={host}\n"
                f"WORKSPACE_ID={workspace_id}\nZEROBUS_ENDPOINT={endpoint}\n")
    print(f"wrote {path} (owner read and write only). Zerobus endpoint: {endpoint}")


def zerobus_endpoint(host, workspace_id, region):
    """https://<workspace id>.zerobus.<region>.<cloud domain>, with the domain of the workspace URL."""
    if host.endswith(".azuredatabricks.net"):
        domain = "azuredatabricks.net"
    elif host.endswith(".gcp.databricks.com"):
        domain = "gcp.databricks.com"          # to confirm: Zerobus on Google Cloud
        print("note: check the Zerobus endpoint for Google Cloud in the Databricks docs")
    else:
        domain = "cloud.databricks.com"
    return f"https://{workspace_id}.zerobus.{region}.{domain}"


if __name__ == "__main__":
    main()
