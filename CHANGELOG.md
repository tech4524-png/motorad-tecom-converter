# Changelog

> Note: entries for 1.2.1 - 1.3.1 were not recorded here before this file
> was put under version control; see git history from this point on.

## 1.3.3 - OrdRsp: TradeNumber and Brand code (TECOM compatibility)

Per TECOM's OrdRsp-sync compatibility feedback (OrdRsp only; the invoice
format is untouched):

- `AdditionalProductId/Number` where `Qualifier == 'TradeNumber'` now ends
  with `=` (`B` -> `B=`, `C` -> `C=`); never doubled if already present.
- `Brand/Code` is now the fixed value `Motora` (the code registered on the
  TecCom platform), regardless of what Priority sends in `BRAND_CODE`.

## 1.3.2 - OrdRsp: date and boolean formats rejected by TECOM

TECOM's schema validation rejected real OrdRsp files: date-only fields
arrived from Priority as `M/D/YYYY` (not `xs:date`) and
`Backlog`/`CompleteDelivery`/`CompleteShipment` as `TRUE`/`FALSE` (not
`xs:boolean`). Dates are now normalized to `YYYY-MM-DD` and booleans to
`true`/`false` inside `app/ordrsp_xml.py`.

## 1.2.0 - Fixes the update mechanism itself (requires one full reinstall)

**What happened:** after installing the 1.1.0 update package (date format
fix, real logo, language switcher, MotoGateway branding) through the
Updates screen, the site started returning 500 errors on every page.

**Root cause:** the very first packaged build (1.0.0) let PyInstaller
compile our own application code (the `app/` package) into the EXE, the
same way it bundles any third-party library. That's normally fine - except
the whole point of the Updates screen is to replace files under `app/` on
disk and have a service restart pick up the change. A frozen process does
not re-read `.py` files from disk; it runs the code baked into the EXE.
So applying the 1.1.0 update rewrote the on-disk template files (real
files, so that part "worked") while the actual Python logic silently
stayed on old 1.0.0 code - which doesn't know about the new template
features (language switcher, translation function, branding variables)
the new templates now referenced. Every page render failed.

**The fix:** `build/pyinstaller.spec` now explicitly excludes `app` from
being compiled into the bundle and ships it as plain loose files next to
the EXE instead (`service_windows.py` adds that folder to `sys.path`
before importing it). This is a standard pattern for making a frozen app
hot-updatable, and it's exactly what makes the templates-vs-logic
mismatch impossible going forward: both now live in the same real,
overwritable place. `app/updates.py` also gained a guard
(`is_hot_updatable()`) that refuses to apply any future update against a
build old enough to still have the bug, instead of silently leaving the
install half-updated again.

**What this means for you:** this one specific release cannot be delivered
as an update package - the bug being fixed is precisely the thing that
would have made an update package unreliable. It needs one last full
reinstall (see BUILD_INSTRUCTIONS.md). Every release after this one will
install through the Updates screen normally, with no further reinstalls.

**Immediate relief before rebuilding:** if the site needs to work again
right now, re-running the *original* 1.0.0 installer restores the
pristine bundled templates and clears the 500s (you'll be back on 1.0.0,
without the 1.1.0 features, until the 1.2.0 reinstall is done). If that
installer isn't available anymore, restoring the contents of
`instance/backups/app_<timestamp>/` (created automatically right before
the botched update was applied) back over `app/templates`, `app/static`,
and `app/country_codes.json` achieves the same thing.

## 1.1.0

- Removed the `Format="YYYYMMDD"` attribute from every `<Date>` element in
  the generated XML (kept `Qualifier="At"`).
- Added the real Motorad logo; updated the UI color palette to match.
- Added a language switcher (Hebrew/English) across the whole admin UI.
- Renamed the product to "MotoGateway" in the UI.

## 1.0.0

Initial version: TECOM TXML 2.5 conversion engine, folder watcher, admin
web UI (settings, profiles, country mapping, conversion log, updates),
Windows service packaging.
