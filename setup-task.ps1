<#
.SYNOPSIS
    Registers the daily digest as a Windows scheduled task, with the settings that make it survive
    a laptop that is not switched on at the scheduled moment.

.DESCRIPTION
    A bare `schtasks /Create` produces a task that silently skips any day the machine is off, and
    gives up on the first transient failure. The three settings that matter here:

      StartWhenAvailable  a missed run fires the next time the machine is available, instead of
                          being skipped. Note that Task Scheduler holds catch-up runs briefly
                          rather than firing them the instant you log in, and that several missed
                          days produce ONE catch-up run, not one per day. That is the behaviour you
                          want: the ledger means a single run sends the best unsent postings.
      RestartCount        retries on failure, which covers logging in before the network is up.
      ExecutionTimeLimit  a stuck run cannot wedge the scheduler indefinitely.

    Task Scheduler cannot wake a powered-off machine. If the laptop stays shut for a week there is
    no digest that week; run it somewhere always-on if that matters.

.EXAMPLE
    .\setup-task.ps1
    .\setup-task.ps1 -Time 06:45 -TaskName MyDigest
#>
[CmdletBinding()]
param(
    [string]$TaskName = "JobDigest",
    [string]$Time     = "07:12"
)

$ErrorActionPreference = "Stop"
$repo = $PSScriptRoot
$cmd  = Join-Path $repo "run-digest.cmd"

if (-not (Test-Path $cmd)) { throw "run-digest.cmd not found next to this script ($repo)" }

# An off-minute on purpose. Everyone who asks for "7am" gets 07:00, and schedulers everywhere fire
# at once; a few minutes either side costs nothing and spreads the load.
$trigger = New-ScheduledTaskTrigger -Daily -At $Time
$action  = New-ScheduledTaskAction -Execute $cmd -WorkingDirectory $repo
$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -DontStopIfGoingOnBatteries `
    -AllowStartIfOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Hours 2) `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 10)

Register-ScheduledTask -TaskName $TaskName -Trigger $trigger -Action $action `
    -Settings $settings -Description "Daily job digest" -Force | Out-Null

$info = Get-ScheduledTaskInfo -TaskName $TaskName
Write-Host "Registered '$TaskName'"
Write-Host "  runs   : daily at $Time"
Write-Host "  script : $cmd"
Write-Host "  next   : $($info.NextRunTime)"
Write-Host ""
Write-Host "Run it now : Start-ScheduledTask -TaskName $TaskName"
Write-Host "Inspect    : Get-ScheduledTaskInfo -TaskName $TaskName"
Write-Host "Remove     : Unregister-ScheduledTask -TaskName $TaskName -Confirm:`$false"
Write-Host "Logs       : $(Join-Path $repo 'work\digest-task.log')"
