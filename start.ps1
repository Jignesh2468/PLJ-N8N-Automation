# Local Windows start. Keys are read from .env — do NOT paste keys into this file.
if (Test-Path .env) {
  Get-Content .env | Where-Object { $_ -match '^\s*[^#].*=' } | ForEach-Object {
    $k, $v = $_ -split '=', 2; [Environment]::SetEnvironmentVariable($k.Trim(), $v.Trim(), 'Process')
  }
}
python -m uvicorn main:app --host 127.0.0.1 --port 8000 --workers 1
