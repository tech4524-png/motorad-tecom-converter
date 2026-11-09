# Phase 1 deployment script — run from C:\motorad-tecom-converter
$root = "C:\motorad-tecom-converter"
$src = Split-Path -Parent $MyInvocation.MyCommand.Path

# Create directories
New-Item -ItemType Directory -Force -Path "$root\app\core" | Out-Null
New-Item -ItemType Directory -Force -Path "$root\app\builtin" | Out-Null
New-Item -ItemType Directory -Force -Path "$root\app\sources" | Out-Null
New-Item -ItemType Directory -Force -Path "$root\app\destinations" | Out-Null
New-Item -ItemType Directory -Force -Path "$root\tests" | Out-Null

# New files (core framework)
Copy-Item "$src\integration.py"    "$root\app\core\integration.py" -Force
Copy-Item "$src\transform.py"      "$root\app\core\transform.py" -Force
Copy-Item "$src\source_manager.py" "$root\app\core\source_manager.py" -Force
Copy-Item "$src\dest_manager.py"   "$root\app\core\dest_manager.py" -Force
Copy-Item "$src\core_init.py"      "$root\app\core\__init__.py" -Force

# Builtin wrappers
Copy-Item "$src\builtin_init.py"         "$root\app\builtin\__init__.py" -Force
Copy-Item "$src\tecom_integration.py"    "$root\app\builtin\tecom_integration.py" -Force
Copy-Item "$src\ordrsp_integration.py"   "$root\app\builtin\ordrsp_integration.py" -Force

# Empty packages
Copy-Item "$src\sources_init.py"      "$root\app\sources\__init__.py" -Force
Copy-Item "$src\destinations_init.py" "$root\app\destinations\__init__.py" -Force

# Modified files
Copy-Item "$src\engine.py"  "$root\app\engine.py" -Force
Copy-Item "$src\config.py"  "$root\app\config.py" -Force
Copy-Item "$src\watcher.py" "$root\app\watcher.py" -Force

# Tests + docs
Copy-Item "$src\test_integration_framework.py" "$root\tests\test_integration_framework.py" -Force
Copy-Item "$src\ARCHITECTURE.md"               "$root\ARCHITECTURE.md" -Force

Write-Host "Phase 1 deployed to $root" -ForegroundColor Green
Write-Host "Now run:" -ForegroundColor Yellow
Write-Host "  cd $root"
Write-Host '  git add -A'
Write-Host '  git commit -m "feat: Phase 1 — Integration Engine Foundation"'
Write-Host '  git push origin main'
