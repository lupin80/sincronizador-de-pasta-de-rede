[Setup]
AppId={{260DE437-147F-4B84-B163-85D5AE918DC0}
AppName=Sincronizador de Rede
AppVersion=2.9.0
DefaultDirName={localappdata}\SincronizadorRede
UsePreviousAppDir=no
DefaultGroupName=Sincronizador de Rede
UsePreviousGroup=no
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..
OutputBaseFilename=Instalar_Sincronizador_de_Rede
SetupIconFile=app.ico
UninstallDisplayIcon={app}\SincronizadorRede.exe
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=no
AppMutex=Local\SincronizadorRede.App.2,Local\SincronizadorCurtume.App.2

[Languages]
Name: "brazilianportuguese"; MessagesFile: "compiler:Languages\BrazilianPortuguese.isl"

[Tasks]
Name: "startup"; Description: "Iniciar com o Windows (na bandeja)"; GroupDescription: "Inicialização:"
Name: "desktopicon"; Description: "Criar atalho na área de trabalho"; GroupDescription: "Atalhos:"; Flags: unchecked

[Files]
Source: "..\SincronizadorRede.exe"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\Sincronizador de Rede"; Filename: "{app}\SincronizadorRede.exe"; Parameters: "--tray"
Name: "{group}\Desinstalar Sincronizador de Rede"; Filename: "{uninstallexe}"
Name: "{userdesktop}\Sincronizador de Rede"; Filename: "{app}\SincronizadorRede.exe"; Parameters: "--tray"; Tasks: desktopicon
Name: "{userstartup}\Sincronizador de Rede"; Filename: "{app}\SincronizadorRede.exe"; Parameters: "--tray"; WorkingDir: "{app}"; Tasks: startup

[InstallDelete]
Type: files; Name: "{userdesktop}\Sincronizador Curtume.lnk"
Type: files; Name: "{userstartup}\Sincronizador Curtume.lnk"
Type: files; Name: "{userprograms}\Sincronizador Curtume\Sincronizador Curtume.lnk"
Type: files; Name: "{userprograms}\Sincronizador Curtume\Desinstalar Sincronizador Curtume.lnk"
Type: dirifempty; Name: "{userprograms}\Sincronizador Curtume"

[Run]
Filename: "{app}\SincronizadorRede.exe"; Parameters: "--tray"; Flags: nowait skipifsilent

[UninstallRun]
Filename: "{app}\SincronizadorRede.exe"; Parameters: "--remove-schedule"; Flags: runhidden waituntilterminated; RunOnceId: "RemoveAutomaticSchedule"

[Code]
procedure CurStepChanged(CurStep: TSetupStep);
var
  EngineResult: Integer;
begin
  if CurStep = ssPostInstall then
  begin
    if not Exec(ExpandConstant('{app}\SincronizadorRede.exe'), '--upgrade-engine',
      ExpandConstant('{app}'), SW_HIDE, ewWaitUntilTerminated, EngineResult) then
      RaiseException('Nao foi possivel iniciar a atualizacao do motor: ' + SysErrorMessage(EngineResult));
    if EngineResult <> 0 then
      RaiseException('Nao foi possivel atualizar o motor. Aguarde a sincronizacao terminar e execute o instalador novamente.');
  end;
end;


