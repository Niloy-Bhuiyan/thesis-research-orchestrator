# Windows setup

Developed and verified on Windows 10. WSL is **not** required.

## Windows-specific issues, already handled

**npm installs CLIs as `.cmd` shims.** Python's `subprocess` does not consult
`PATHEXT`, so `subprocess.run(["codex", ...])` raises `FileNotFoundError` even though
`codex` works in your shell. Every CLI is resolved through `shutil.which` first. If
you add a provider, do the same.

**Git Bash mangles Windows-style flags.** `icacls "file" /reset` becomes a path. Use
PowerShell for `icacls`.

**`icacls /inheritance:r` can lock a file from your own processes.** Files under your
user profile are already user-scoped; over-hardening `kaggle.json` this way made the
Kaggle CLI fail with `PermissionError`.

## Autostart with Task Scheduler

```powershell
$action  = New-ScheduledTaskAction -Execute "D:\path\.venv\Scripts\researchos.exe" -Argument "start"
$trigger = New-ScheduledTaskTrigger -AtLogOn
Register-ScheduledTask -TaskName "ResearchOS" -Action $action -Trigger $trigger
```

Remove with `Unregister-ScheduledTask -TaskName "ResearchOS"`.

## Line endings

The repo is authored with LF. Git warns about CRLF conversion on checkout; this is
expected and harmless.
