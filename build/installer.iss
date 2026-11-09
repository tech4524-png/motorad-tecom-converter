; Inno Setup script for the Motorad Invoice Converter.
; Build with Inno Setup (https://jrsoftware.org/isinfo.php) AFTER running
; PyInstaller (see pyinstaller.spec) - this script packages the resulting
; dist\MotoradInvoiceConverter folder into a single installer .exe that:
;   1. Copies the application to Program Files
;   2. Registers + starts the Windows service automatically
;   3. Adds a Start Menu shortcut that opens the admin web UI in a browser
;   4. On uninstall, stops + unregisters the service first
;
; Compile with: iscc build\installer.iss

#define MyAppName "Motorad Invoice Converter"
#define MyAppVersion "1.0.0"
#define MyAppExeName "MotoradInvoiceConverter.exe"
#define MyAppUrl "http://localhost:8080"

[Setup]
AppName={#MyAppName}
AppVersion={#MyAppVersion}
DefaultDirName={autopf}\Motorad\InvoiceConverter
DefaultGroupName=Motorad Invoice Converter
DisableProgramGroupPage=yes
OutputBaseFilename=MotoradInvoiceConverter_Setup_{#MyAppVersion}
Compression=lzma
SolidCompression=yes
PrivilegesRequired=admin
ArchitecturesInstallIn64BitMode=x64

[Files]
Source: "..\dist\MotoradInvoiceConverter\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\פתיחת ממשק הניהול"; Filename: "{#MyAppUrl}"
Name: "{group}\הסרת התקנה"; Filename: "{uninstallexe}"

[Run]
Filename: "{app}\{#MyAppExeName}"; Parameters: "install"; Flags: runhidden waituntilterminated; StatusMsg: "רושם את שירות המערכת..."
Filename: "{app}\{#MyAppExeName}"; Parameters: "start"; Flags: runhidden waituntilterminated; StatusMsg: "מפעיל את השירות..."
Filename: "{#MyAppUrl}"; Description: "פתיחת ממשק הניהול בדפדפן"; Flags: postinstall shellexec skipifsilent

[UninstallRun]
Filename: "{app}\{#MyAppExeName}"; Parameters: "stop"; Flags: runhidden waituntilterminated
Filename: "{app}\{#MyAppExeName}"; Parameters: "remove"; Flags: runhidden waituntilterminated
