#!/usr/bin/env python3
"""Patch a Container App YAML export to mount an Azure Files volume.

Azure CLI has no flag for adding volume mounts to an existing container app,
so the supported path is: export YAML -> edit -> re-apply. Hand-editing that
YAML is fiddly (indentation-sensitive, easy to put keys at the wrong level),
so this does it programmatically and idempotently.

Usage:
    python3 patch_volume.py app.yaml

Writes the patched YAML back in place. Safe to run twice.
"""

import sys
import shutil

try:
    import yaml
except ImportError:
    sys.exit(
        "PyYAML not installed. Run:  python3 -m pip install pyyaml\n"
        "(If you're on the anaconda python it's very likely already there.)"
    )

VOLUME_NAME = "vinyl-data"      # arbitrary label used inside the app spec
STORAGE_NAME = "vinyldata"      # must match `az containerapp env storage set --storage-name`
MOUNT_PATH = "/home/data"       # matches VINYL_DATA_DIR baked into the Dockerfile


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit("usage: python3 patch_volume.py <app.yaml>")
    path = sys.argv[1]

    shutil.copy(path, path + ".bak")

    with open(path) as f:
        doc = yaml.safe_load(f)

    props = doc.setdefault("properties", {})
    template = props.setdefault("template", {})

    # --- declare the volume ------------------------------------------------
    volumes = template.setdefault("volumes", []) or []
    volumes = [v for v in volumes if v.get("name") != VOLUME_NAME]
    volumes.append(
        {
            "name": VOLUME_NAME,
            "storageName": STORAGE_NAME,
            "storageType": "AzureFile",
        }
    )
    template["volumes"] = volumes

    # --- mount it into every container -------------------------------------
    containers = template.get("containers") or []
    if not containers:
        sys.exit("No containers found in the YAML — is this the right file?")

    for c in containers:
        mounts = c.get("volumeMounts") or []
        mounts = [m for m in mounts if m.get("volumeName") != VOLUME_NAME]
        mounts.append({"volumeName": VOLUME_NAME, "mountPath": MOUNT_PATH})
        c["volumeMounts"] = mounts

    with open(path, "w") as f:
        yaml.safe_dump(doc, f, default_flow_style=False, sort_keys=False)

    print(f"Patched {path}")
    print(f"  volume   : {VOLUME_NAME} -> storage '{STORAGE_NAME}' (AzureFile)")
    print(f"  mounted  : {MOUNT_PATH} in {len(containers)} container(s)")
    print(f"  backup   : {path}.bak")


if __name__ == "__main__":
    main()
