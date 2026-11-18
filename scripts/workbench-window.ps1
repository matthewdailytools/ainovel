function Find-WorkbenchChromium {
    foreach ($installRoot in @(${env:ProgramFiles(x86)}, $env:ProgramFiles, $env:LOCALAPPDATA)) {
        if (-not $installRoot) { continue }
        foreach ($relativePath in @('Microsoft/Edge/Application/msedge.exe', 'Google/Chrome/Application/chrome.exe')) {
            $candidate = Join-Path $installRoot $relativePath
            if (Test-Path -LiteralPath $candidate -PathType Leaf) { return $candidate }
        }
    }
    $command = Get-Command msedge.exe, chrome.exe -CommandType Application -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if ($command) { return $command.Source }
}

function Get-WorkbenchWindowArguments {
    param([ValidateRange(1, 65535)][int]$Port, [string]$DataDirectory)
    $profile = Join-Path $DataDirectory 'desktop-browser'
    '--no-first-run'
    '--no-default-browser-check'
    "--user-data-dir=`"$profile`""
    "--app=http://127.0.0.1:$Port"
}

function Test-WorkbenchLauncherOwnsProcess {
    param([int]$LauncherId, [int]$ProcessId)
    for ($depth = 0; $depth -lt 10 -and $ProcessId -gt 0; $depth++) {
        if ($ProcessId -eq $LauncherId) { return $true }
        $row = Get-CimInstance -ClassName Win32_Process -Filter "ProcessId=$ProcessId" -ErrorAction SilentlyContinue
        if (-not $row) { return $false }
        $ProcessId = $row.ParentProcessId
    }
    return $false
}

function Get-WorkbenchReadyWindowArguments {
    param([ValidateRange(1, 65535)][int]$Port, [int]$LauncherId)
    $deadline = [DateTimeOffset]::UtcNow.AddSeconds(20)
    $endpoint = "http://127.0.0.1:$Port/api/session"
    do {
        $listener = Get-NetTCPConnection -State Listen -LocalAddress 127.0.0.1 -LocalPort $Port -ErrorAction SilentlyContinue
        if ($listener -and (Test-WorkbenchLauncherOwnsProcess $LauncherId $listener.OwningProcess)) {
            try {
                $session = Invoke-RestMethod -Uri $endpoint -TimeoutSec 2 -NoProxy -MaximumRedirection 0
                if ($session.data_dir -is [string] -and [IO.Path]::IsPathFullyQualified($session.data_dir)) {
                    return Get-WorkbenchWindowArguments $Port $session.data_dir
                }
            } catch {
                # The server can be listening while its API is still starting.
            }
        }
        Start-Sleep -Milliseconds 250
    } while ([DateTimeOffset]::UtcNow -lt $deadline)
    throw "Desktop window timed out waiting for this launcher's workbench on port $Port."
}
