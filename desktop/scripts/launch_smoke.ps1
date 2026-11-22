<#
.SYNOPSIS
  Launch SYNCODE Desktop, wait for the Diagnóstico window, capture it and close it.

.DESCRIPTION
  Evidence helper for local runs and CI. With -Isolated the app is started from
  a hostile working directory with PYTHONPATH/PYTHONHOME pointing at shadowing
  modules and a PATH without any Python, to show the bundled runtime does not
  depend on a global interpreter. Writes a PNG and prints a JSON summary.
#>
param(
  [Parameter(Mandatory)] [string] $Exe,
  [Parameter(Mandatory)] [string] $Out,
  [int] $WaitSeconds = 8,
  [switch] $Isolated
)
$ErrorActionPreference = 'Stop'

Add-Type -AssemblyName System.Drawing
Add-Type @"
using System;
using System.Runtime.InteropServices;
public static class SyncodeWin {
  [StructLayout(LayoutKind.Sequential)] public struct RECT { public int Left, Top, Right, Bottom; }
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
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
if ($Isolated) {
  foreach ($name in 'sync', 'json', 'cli_contract', 'doctor', 'specialists') {
    "open(r'$marker', 'a').write(__name__)`nraise SystemExit(99)`n" | Set-Content -Encoding ascii (Join-Path $work "$name.py")
  }
  foreach ($kv in @{ PYTHONPATH = $work; PYTHONHOME = $work; PATH = "$env:SystemRoot\System32;$env:SystemRoot" }.GetEnumerator()) {
    $saved[$kv.Key] = [Environment]::GetEnvironmentVariable($kv.Key, 'Process')
    [Environment]::SetEnvironmentVariable($kv.Key, $kv.Value, 'Process')
  }
}
try {
  $proc = Start-Process -FilePath $exePath -WorkingDirectory $work -PassThru
} finally {
  foreach ($key in $saved.Keys) { [Environment]::SetEnvironmentVariable($key, $saved[$key], 'Process') }
}

$deadline = (Get-Date).AddSeconds(30)
while ($proc.MainWindowHandle -eq 0 -and -not $proc.HasExited -and (Get-Date) -lt $deadline) {
  Start-Sleep -Milliseconds 250
  $proc.Refresh()
}
if ($proc.HasExited -or $proc.MainWindowHandle -eq 0) { throw "Window did not open (exited: $($proc.HasExited))" }
$title = $proc.MainWindowTitle
Start-Sleep -Seconds $WaitSeconds
$handle = $proc.MainWindowHandle
[void][SyncodeWin]::SetForegroundWindow($handle)
Start-Sleep -Milliseconds 600
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

[ordered]@{
  ok = -not $poisoned
  exe = $exePath
  windowTitle = $title
  isolated = [bool]$Isolated
  poisonImported = $poisoned
  screenshot = $outPath
  size = "${width}x${height}"
  childProcessesAtCapture = $children
} | ConvertTo-Json
if ($poisoned) { exit 1 }
