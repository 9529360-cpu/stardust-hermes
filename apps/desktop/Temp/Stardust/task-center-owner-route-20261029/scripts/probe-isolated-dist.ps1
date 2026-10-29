$ErrorActionPreference = 'Stop'
$paths = @(
  'D:\Development\Temp\Stardust\task-center-owner-route-20261029\project-cwd-electron\dist',
  'D:\Development\Temp\Stardust\task-center-owner-route-20261029\project-cwd-electron\dist\electron-main.mjs',
  'D:\Development\Temp\Stardust\task-center-owner-route-20261029\project-cwd-electron-owner-fix\dist',
  'D:\Development\Temp\Stardust\task-center-owner-route-20261029\project-cwd-electron-owner-fix\dist\electron-main.mjs',
  'D:\Development\Reports\Stardust\task-center-owner-route-20261029\owner-selection-final'
)
foreach ($path in $paths) {
  Write-Output "PATH $path"
  if (Test-Path -LiteralPath $path) {
    Get-Item -Force -LiteralPath $path | Format-List FullName,Attributes,Length,LinkType,Target
    Get-Acl -LiteralPath $path | Format-List Owner,AccessToString
  } else {
    Write-Output 'MISSING'
  }
}
Write-Output "IDENTITY $([System.Security.Principal.WindowsIdentity]::GetCurrent().Name)"
$probe = 'D:\Development\Temp\Stardust\task-center-owner-route-20261029\project-cwd-electron-owner-fix\dist\write-probe.tmp'
try {
  [System.IO.File]::WriteAllText($probe, 'probe')
  Write-Output "WRITE_OK $probe"
  Remove-Item -LiteralPath $probe -Force
} catch {
  Write-Output "WRITE_FAILED $($_.Exception.GetType().FullName): $($_.Exception.Message)"
}
