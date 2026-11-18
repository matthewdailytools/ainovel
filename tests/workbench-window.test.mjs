import assert from 'node:assert/strict'
import { spawnSync } from 'node:child_process'
import test from 'node:test'

test(
  'Windows workbench app mode uses the selected port and workbench-specific browser profile',
  {
    skip: process.platform !== 'win32',
  },
  () => {
    const shell = spawnSync(
      'pwsh',
      [
        '-NoProfile',
        '-NonInteractive',
        '-Command',
        `
        $ErrorActionPreference = 'Stop'
        . ./scripts/workbench-window.ps1
        @{
          arguments = @(Get-WorkbenchWindowArguments 53133 'C:\\Isolated Manuscript Data')
          ownsThisProcess = Test-WorkbenchLauncherOwnsProcess $PID $PID
          acceptsUnknownLauncher = Test-WorkbenchLauncherOwnsProcess 0 $PID
        } | ConvertTo-Json -Compress
      `,
      ],
      { cwd: new URL('../', import.meta.url), encoding: 'utf8', windowsHide: true },
    )
    assert.equal(shell.status, 0, shell.stderr)
    assert.deepEqual(JSON.parse(shell.stdout), {
      arguments: [
        '--no-first-run',
        '--no-default-browser-check',
        '--user-data-dir="C:\\Isolated Manuscript Data\\desktop-browser"',
        '--app=http://127.0.0.1:53133',
      ],
      ownsThisProcess: true,
      acceptsUnknownLauncher: false,
    })
  },
)
