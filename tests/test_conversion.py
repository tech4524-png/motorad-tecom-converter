"""
Offline verification: runs the real conversion engine against the two sample
files provided by the customer, using a throwaway config, and prints the
resulting XML (or the error) for manual review. Does not touch the web UI,
service wrapper, or watcher - just engine.process_file end to end.

Run with: python3 tests/test_conversion.py
"""
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import config as config_module
from app import db
from app import engine

WORKDIR = ROOT / "tests" / "_run"


def make_test_config():
    cfg = config_module.load_config()
    cfg["seller"]["name1"] = "Motorad Ltd."
    cfg["seller"]["street1"] = "1 Industry St."
    cfg["seller"]["postal_code"] = "12345"
    cfg["seller"]["city"] = "Beit Shean"
    cfg["seller"]["country_code"] = "IL"
    cfg["seller"]["country_name"] = "Israel"
    cfg["seller"]["tax_registration_number"] = "IL000000000"
    cfg["seller"]["message_function"] = "Original"
    cfg["banking"]["bank_name"] = "Bank Hapoalim"
    cfg["banking"]["bank_account"] = "123456"
    cfg["banking"]["bank_code_number"] = "12"
    cfg["banking"]["iban"] = "IL120001200000000123456"
    cfg["banking"]["swift_bic"] = "POALILIT"
    return cfg


def run_one(sample_filename):
    profile = {
        "id": "tecom",
        "converter": "tecom",
        "input_dir": str(WORKDIR / "input"),
        "output_dir": str(WORKDIR / "output"),
        "archive_dir": str(WORKDIR / "archive"),
        "error_dir": str(WORKDIR / "errors"),
    }
    for d in profile.values():
        if isinstance(d, str) and d.startswith(str(WORKDIR)):
            Path(d).mkdir(parents=True, exist_ok=True)

    src = ROOT / "tests" / "sample_files" / sample_filename
    working_copy = Path(profile["input_dir"]) / sample_filename
    shutil.copy(src, working_copy)

    cfg = make_test_config()
    result = engine.process_file(working_copy, profile, cfg)

    print(f"\n===== {sample_filename} =====")
    print("ok:", result.ok)
    print("invoice_number:", result.invoice_number)
    print("customer_name:", result.customer_name)
    print("message:", result.message)
    if result.ok:
        print("--- XML output ---")
        print(result.output_path.read_text(encoding="utf-8"))
    else:
        err_files = list(Path(profile["error_dir"]).glob("*.error.txt"))
        if err_files:
            print("--- error detail ---")
            print(err_files[-1].read_text(encoding="utf-8"))


if __name__ == "__main__":
    if WORKDIR.exists():
        shutil.rmtree(WORKDIR)
    db.init_db()
    run_one("sample1_INVOICE.XLS")
    run_one("sample2_INVOICE.XLS")
