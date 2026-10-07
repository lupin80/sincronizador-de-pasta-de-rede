"""Per-user Windows Task Scheduler integration; no credentials are stored."""
import base64
import json
import os
from pathlib import Path
import subprocess

MAX_MINUTES = 31 * 24 * 60
SCRIPT = r'''
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
try {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $name = 'SincronizadorRede-Automatico-' + $identity.User.Value
    if ($env:SC_SCHEDULE_TEST_NAME) { $name = $env:SC_SCHEDULE_TEST_NAME }
    $service = New-Object -ComObject 'Schedule.Service'
    $service.Connect()
    $folder = $null
    if ($env:SC_SCHEDULE_ACTION -ne 'definition') { $folder = $service.GetFolder('\') }
    function Read-State {
        try { $task = $folder.GetTask($name) }
        catch {
            if ($_.Exception.GetBaseException().HResult -in @(-2147024894, -2147024893)) {
                return @{exists=$false; enabled=$false; interval_minutes=60; name=$name}
            }
            throw
        }
        $minutes = [int][Xml.XmlConvert]::ToTimeSpan($task.Definition.Triggers.Item(1).Repetition.Interval).TotalMinutes
        return @{exists=$true; enabled=[bool]$task.Enabled; interval_minutes=$minutes; name=$name}
    }
    if ($env:SC_SCHEDULE_ACTION -eq 'enable' -or $env:SC_SCHEDULE_ACTION -eq 'definition') {
        $minutes = [int]$env:SC_SCHEDULE_MINUTES
        if ($minutes -lt 1 -or $minutes -gt 44640) { throw 'Intervalo deve ficar entre 1 minuto e 31 dias.' }
        $definition = $service.NewTask(0)
        $definition.RegistrationInfo.Description = 'Sincronizador de Rede: sincronizacao automatica dos caminhos salvos.'
        $definition.RegistrationInfo.Author = $identity.Name
        $definition.Principal.UserId = $identity.User.Value
        $definition.Principal.LogonType = 3
        $definition.Principal.RunLevel = 0
        $definition.Settings.Enabled = $true
        $definition.Settings.StartWhenAvailable = $true
        $definition.Settings.DisallowStartIfOnBatteries = $false
        $definition.Settings.StopIfGoingOnBatteries = $false
        $definition.Settings.ExecutionTimeLimit = 'PT0S'
        # Each trigger may dispatch a request to the resident tray app. Its mutex
        # and running/lock guards enforce one synchronization at a time.
        $definition.Settings.MultipleInstances = 0
        $trigger = $definition.Triggers.Create(1)
        $trigger.StartBoundary = [DateTime]::Now.AddMinutes($minutes).ToString('yyyy-MM-ddTHH:mm:sszzz')
        $trigger.Repetition.Interval = 'PT' + $minutes + 'M'
        $trigger.Repetition.StopAtDurationEnd = $false
        $trigger.Enabled = $true
        $action = $definition.Actions.Create(0)
        $action.Path = $env:SC_SCHEDULE_EXE
        $action.Arguments = $env:SC_SCHEDULE_ARGS
        $action.WorkingDirectory = $env:SC_SCHEDULE_CWD
        if ($env:SC_SCHEDULE_ACTION -eq 'definition') {
            $result = @{xml=$definition.XmlText; name=$name}
        } else {
            $null = $folder.RegisterTaskDefinition($name, $definition, 6, $identity.User.Value, $null, 3, $null)
            $result = Read-State
        }
    } elseif ($env:SC_SCHEDULE_ACTION -eq 'disable') {
        $result = Read-State
        if ($result.exists) {
            $task = $folder.GetTask($name)
            $task.Enabled = $false
            $result = Read-State
        }
    } elseif ($env:SC_SCHEDULE_ACTION -eq 'query') {
        $result = Read-State
    } elseif ($env:SC_SCHEDULE_ACTION -eq 'delete') {
        $result = Read-State
        if ($result.exists) { $folder.DeleteTask($name, 0) }
        $result = @{exists=$false; enabled=$false; interval_minutes=60; name=$name}
    } else { throw 'Operacao de agendamento invalida.' }
    @{ok=$true; data=$result} | ConvertTo-Json -Depth 5 -Compress
} catch {
    @{ok=$false; error=$_.Exception.Message} | ConvertTo-Json -Compress
    exit 1
}
'''


def interval_minutes(amount, unit):
    factors = {"Minutos": 1, "Horas": 60, "Dias": 1440}
    try:
        minutes = int(str(amount)) * factors[unit]
    except (ValueError, KeyError):
        raise ValueError("Informe um número inteiro e selecione minutos, horas ou dias.") from None
    if not 1 <= minutes <= MAX_MINUTES:
        raise ValueError("Escolha um intervalo entre 1 minuto e 31 dias.")
    return minutes


def _run(operation, minutes=60, executable=None, arguments="--scheduled --tray", task_name=None):
    env = os.environ.copy()
    env.pop("SC_SCHEDULE_TEST_NAME", None)
    env.update(SC_SCHEDULE_ACTION=operation, SC_SCHEDULE_MINUTES=str(minutes),
               SC_SCHEDULE_EXE=str(executable or ""), SC_SCHEDULE_ARGS=arguments,
               SC_SCHEDULE_CWD=str(Path(executable).parent) if executable else "")
    if task_name is not None:
        if not task_name.startswith("SincronizadorRede-Teste-"):
            raise ValueError("Nome de tarefa de teste inválido.")
        env["SC_SCHEDULE_TEST_NAME"] = task_name
    encoded = base64.b64encode(SCRIPT.encode("utf-16-le")).decode("ascii")
    completed = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive",
        "-EncodedCommand", encoded], env=env, capture_output=True, text=True,
        encoding="utf-8-sig", errors="replace", timeout=45, creationflags=subprocess.CREATE_NO_WINDOW)
    try:
        result = json.loads(completed.stdout.strip())
    except ValueError:
        raise RuntimeError("O Agendador do Windows não respondeu corretamente. " + completed.stderr.strip()) from None
    if completed.returncode or not result.get("ok"):
        raise RuntimeError("Não foi possível atualizar ou consultar o Agendador do Windows: " + result.get("error", "erro desconhecido"))
    return result["data"]


def get_schedule():
    return _run("query")


def remove_schedule():
    return _run("delete")


def set_schedule(enabled, minutes, executable, arguments="--scheduled --tray"):
    if enabled:
        minutes = interval_minutes(minutes, "Minutos")
    return _run("enable" if enabled else "disable", minutes, executable, arguments)
