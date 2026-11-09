"""
Builds an "update package" zip (see app/updates.py for the format and the
apply flow) from the current app/ folder, for the customer to upload from
the Updates screen instead of reinstalling.

Usage:
    python3 build/make_update_package.py 1.0.1 "Fixed payment-terms parsing for new customer format"

Produces: dist/update_1.0.1.zip
"""
import json
import shutil
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)

    version, description = sys.argv[1], sys.argv[2]

    dist_dir = ROOT / "dist"
    dist_dir.mkdir(exist_ok=True)
    staging = dist_dir / f"_staging_{version}"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir()

    (staging / "manifest.json").write_text(
        json.dumps({"version": version, "description": description}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    shutil.copytree(
        ROOT / "app",
        staging / "app",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )

    zip_path = dist_dir / f"update_{version}.zip"
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in staging.rglob("*"):
            zf.write(path, path.relative_to(staging))

    shutil.rmtree(staging)
    print(f"Built {zip_path}")


if __name__ == "__main__":
    main()
