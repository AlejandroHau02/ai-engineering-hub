<#
.SYNOPSIS
  Launch SYNCODE Desktop, wait for the Diagnóstico window, capture it and close it.

.DESCRIPTION
  Evidence helper for local runs and CI. With -Isolated the app is started from
  a hostile working directory with PYTHONPATH/PYTHONHOME pointing at shadowing
  modules and a PATH without any Python, to show the bundled runtime does not
  depend on a global interpreter. With -ExpectText the rendered page is read
  through UI Automation, so a window that opens but shows a transport failure
  instead of a diagnosis fails the check. With -Screen sincronizacion the
  read-only preview is requested through the UI before capturing.
  Writes a PNG and a JSON summary.
#>
param(
  [Parameter(Mandatory)] [string] $Exe,
  [Parameter(Mandatory)] [string] $Out,
  [int] $WaitSeconds = 8,
  [switch] $Isolated,
  # Fails unless a rendered element's accessible name contains this text
  # (read through UI Automation from the WebView2 accessibility tree).
  [string] $ExpectText,
  # Screen to show before capturing. "sincronizacion" opens the section and
  # presses "Generar vista previa" through UI Automation.
  [ValidateSet('diagnostico', 'sincronizacion', 'catalogo')] [string] $Screen = 'diagnostico',
  # Run the app with USERPROFILE pointing at this directory (evidence against
  # a disposable home; the real profile is never involved).
  [string] $HomeOverride,
  # With -Screen sincronizacion: after the preview, press "Aplicar N cambios…"
  # and confirm in the dialog. Refused without -HomeOverride, so the real
  # profile can never be written by this script.
  [switch] $Apply,
  # On Diagnóstico: press "Recuperar operación interrumpida…" and confirm.
  # Refused without -HomeOverride (a disposable home with a staged interruption).
  [switch] $Recover,
  # Keyboard and reflow evidence: Tab through the page recording the focused
  # elements, then narrow the window (about the CSS width of 200% zoom) and
  # check that no rendered element extends past the window horizontally.
  [switch] $A11yCheck
)
$ErrorActionPreference = 'Stop'
if ($Apply -and (-not $HomeOverride -or $Screen -ne 'sincronizacion')) {
  throw '-Apply requires -Screen sincronizacion and -HomeOverride (a disposable home)'
}
if ($Recover -and (-not $HomeOverride -or $Screen -ne 'diagnostico')) {
  throw '-Recover requires -Screen diagnostico and -HomeOverride (a disposable home)'
}

Add-Type -AssemblyName System.Drawing
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName UIAutomationClient, UIAutomationTypes
Add-Type @"
using System;
using System.Runtime.InteropServices;
public static class SyncodeWin {
  [StructLayout(LayoutKind.Sequential)] public struct RECT { public int Left, Top, Right, Bottom; }
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
  [DllImport("user32.dll")] public static extern bool MoveWindow(IntPtr h, int x, int y, int w, int ht, bool repaint);
  [DllImport("user32.dll")] public static extern bool SetProcessDPIAware();
  [DllImport("dwmapi.dll")] public static extern int DwmGetWindowAttribute(IntPtr h, int a, out RECT r, int s);
}
"@
[void][SyncodeWin]::SetProcessDPIAware()

$exePath = (Resolve-Path $Exe).Path
$work = Join-Path ([IO.Path]::GetTempPath()) ("syncode-launch-" + [Guid]::NewGuid().ToString('N').Substring(0, 8))
New-Item -ItemType Directory -Force $work | Out-Null
$marker = Join-Path $work 'poison-imported.txt'
$saved = @{}
function Set-ProcessEnv([string] $Name, [string] $Value) {
  if (-not $saved.ContainsKey($Name)) { $saved[$Name] = [Environment]::GetEnvironmentVariable($Name, 'Process') }
  [Environment]::SetEnvironmentVariable($Name, $Value, 'Process')
}
if ($HomeOverride) {
  $homePath = (New-Item -ItemType Directory -Force $HomeOverride).FullName
  Set-ProcessEnv 'USERPROFILE' $homePath
}
if ($Isolated) {
  foreach ($name in 'sync', 'json', 'cli_contract', 'doctor', 'specialists') {
    "open(r'$marker', 'a').write(__name__)`nraise SystemExit(99)`n" | Set-Content -Encoding ascii (Join-Path $work "$name.py")
  }
  foreach ($kv in @{ PYTHONPATH = $work; PYTHONHOME = $work; PATH = "$env:SystemRoot\System32;$env:SystemRoot" }.GetEnumerator()) {
    Set-ProcessEnv $kv.Key $kv.Value
  }
}
try {
  $proc = Start-Process -FilePath $exePath -WorkingDirectory $work -PassThru
} finally {
  foreach ($key in $saved.Keys) { [Environment]::SetEnvironmentVariable($key, $saved[$key], 'Process') }
}

function Get-WindowSize([IntPtr] $h) {
  $r = New-Object SyncodeWin+RECT
  if (-not [SyncodeWin]::GetWindowRect($h, [ref]$r)) { return 0 }
  [Math]::Min($r.Right - $r.Left, $r.Bottom - $r.Top)
}
# On a first launch (fresh WebView2 profile) the process can briefly expose a
# small auxiliary window as its main window; wait for the titled app window.
$deadline = (Get-Date).AddSeconds(45)
while (-not $proc.HasExited -and (Get-Date) -lt $deadline -and -not (
    $proc.MainWindowHandle -ne 0 -and $proc.MainWindowTitle -like 'SYNCODE*' -and
    (Get-WindowSize $proc.MainWindowHandle) -ge 300)) {
  Start-Sleep -Milliseconds 250
  $proc.Refresh()
}
if ($proc.HasExited -or $proc.MainWindowHandle -eq 0 -or $proc.MainWindowTitle -notlike 'SYNCODE*') {
  throw "Window did not open (exited: $($proc.HasExited), title: '$($proc.MainWindowTitle)')"
}
$title = $proc.MainWindowTitle
Start-Sleep -Seconds $WaitSeconds
$handle = $proc.MainWindowHandle
[void][SyncodeWin]::SetForegroundWindow($handle)
$root = [System.Windows.Automation.AutomationElement]::FromHandle($handle)

function Find-Named([string] $Text, [int] $Seconds = 15, [switch] $Exact) {
  $until = (Get-Date).AddSeconds($Seconds)
  while ((Get-Date) -lt $until) {
    $all = $root.FindAll([System.Windows.Automation.TreeScope]::Descendants,
      [System.Windows.Automation.Condition]::TrueCondition)
    foreach ($node in $all) {
      $name = $node.Current.Name
      if ($name -and (($Exact -and $name -eq $Text) -or (-not $Exact -and $name.Contains($Text)))) { return $node }
    }
    Start-Sleep -Milliseconds 400
  }
  return $null
}
function Invoke-Named([string] $Text) {
  $node = Find-Named $Text -Exact
  if (-not $node) { throw "UI element '$Text' not found" }
  $node.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern).Invoke()
}
function Invoke-Matching([string] $Pattern, [int] $Seconds = 40) {
  $until = (Get-Date).AddSeconds($Seconds)
  while ((Get-Date) -lt $until) {
    $all = $root.FindAll([System.Windows.Automation.TreeScope]::Descendants,
      [System.Windows.Automation.Condition]::TrueCondition)
    foreach ($node in $all) {
      $name = $node.Current.Name
      if ($name -and $name -match $Pattern -and
          $node.Current.ControlType -eq [System.Windows.Automation.ControlType]::Button) {
        $node.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern).Invoke()
        return $name
      }
    }
    Start-Sleep -Milliseconds 400
  }
  throw "No button matching '$Pattern'"
}

$applied = $null
if ($Screen -eq 'sincronizacion') {
  Invoke-Named 'Sincronización'
  Start-Sleep -Milliseconds 800
  Invoke-Named 'Generar vista previa'
  if ($Apply) {
    $applied = @()
    $applied += Invoke-Matching '^Aplicar \d+ cambios?…$'
    Start-Sleep -Milliseconds 800
    $applied += Invoke-Matching '^Aplicar \d+ cambios?$'
  }
}
if ($Screen -eq 'catalogo') {
  Invoke-Named 'Catálogo'
}
if ($Recover) {
  $applied = @()
  $applied += Invoke-Matching '^Recuperar operación interrumpida…$'
  Start-Sleep -Milliseconds 800
  $applied += Invoke-Matching '^Recuperar$'
}
$a11y = $null
if ($A11yCheck) {
  [void][SyncodeWin]::SetForegroundWindow($handle)
  Start-Sleep -Milliseconds 400
  $focused = @()
  for ($i = 0; $i -lt 14; $i++) {
    [System.Windows.Forms.SendKeys]::SendWait('{TAB}')
    Start-Sleep -Milliseconds 250
    $f = [System.Windows.Automation.AutomationElement]::FocusedElement
    if ($f) { $focused += $f.Current.Name }
  }
  $required = 'Diagnóstico', 'Sincronización', 'Catálogo'
  $missing = @($required | Where-Object { $focused -notcontains $_ })
  # Reflow: about 1000 CSS px at 200% zoom is a ~500 px wide viewport.
  [void][SyncodeWin]::MoveWindow($handle, 40, 40, 560, 760, $true)
  Start-Sleep -Milliseconds 1200
  $win = New-Object SyncodeWin+RECT
  [void][SyncodeWin]::GetWindowRect($handle, [ref]$win)
  $overflow = @()
  $all = $root.FindAll([System.Windows.Automation.TreeScope]::Descendants,
    [System.Windows.Automation.Condition]::TrueCondition)
  foreach ($node in $all) {
    $c = $node.Current
    if ($c.IsOffscreen -or -not $c.Name) { continue }
    $r = $c.BoundingRectangle
    if ($r.IsEmpty -or $r.Width -le 0) { continue }
    if ($r.Right -gt $win.Right + 2) { $overflow += "$($c.Name) ($([int]$r.Right) > $($win.Right))" }
  }
  $a11y = [ordered]@{
    focusOrder = $focused
    missingFromTabOrder = $missing
    narrowWindow = "$($win.Right - $win.Left)x$($win.Bottom - $win.Top)"
    horizontalOverflow = @($overflow | Select-Object -First 10)
  }
}
$found = $null
if ($ExpectText) {
  $node = Find-Named $ExpectText -Seconds 40
  if ($node) { $found = $node.Current.Name }
}
Start-Sleep -Milliseconds 600
[void][SyncodeWin]::SetForegroundWindow($handle)
Start-Sleep -Milliseconds 400
$rect = New-Object SyncodeWin+RECT
# DWMWA_EXTENDED_FRAME_BOUNDS (9) excludes the invisible resize border.
if ([SyncodeWin]::DwmGetWindowAttribute($handle, 9, [ref]$rect, 16) -ne 0) { [void][SyncodeWin]::GetWindowRect($handle, [ref]$rect) }
$width = $rect.Right - $rect.Left; $height = $rect.Bottom - $rect.Top
$bitmap = New-Object System.Drawing.Bitmap $width, $height
$graphics = [System.Drawing.Graphics]::FromImage($bitmap)
$graphics.CopyFromScreen($rect.Left, $rect.Top, 0, 0, $bitmap.Size)
$outPath = [IO.Path]::GetFullPath($Out)
New-Item -ItemType Directory -Force (Split-Path $outPath) | Out-Null
$bitmap.Save($outPath, [System.Drawing.Imaging.ImageFormat]::Png)
$graphics.Dispose(); $bitmap.Dispose()

$children = @(Get-CimInstance Win32_Process -Filter "ParentProcessId=$($proc.Id)" | Select-Object -ExpandProperty ExecutablePath)
[void]$proc.CloseMainWindow()
if (-not $proc.WaitForExit(5000)) { Stop-Process -Id $proc.Id -Force }
$poisoned = Test-Path $marker
Remove-Item -Recurse -Force $work -ErrorAction SilentlyContinue

$expectOk = (-not $ExpectText) -or [bool]$found
[ordered]@{
  ok = (-not $poisoned) -and $expectOk
  exe = $exePath
  windowTitle = $title
  isolated = [bool]$Isolated
  screen = $Screen
  homeOverride = [bool]$HomeOverride
  applyButtons = $applied
  a11y = $a11y
  poisonImported = $poisoned
  expectText = $ExpectText
  foundText = $found
  screenshot = $outPath
  size = "${width}x${height}"
  childProcessesAtCapture = $children
} | ConvertTo-Json
# Throw (not `exit 1`): a later native call in the same CI step would reset
# $LASTEXITCODE and hide the failure.
if ($poisoned) { throw "poisoned module was imported" }
if (-not $expectOk) { throw "expected text not rendered: $ExpectText" }
if ($a11y -and ($a11y.missingFromTabOrder.Count -gt 0 -or $a11y.horizontalOverflow.Count -gt 0)) {
  throw "accessibility check failed: missing=$($a11y.missingFromTabOrder -join ',') overflow=$($a11y.horizontalOverflow -join '; ')"
}
