@echo off
rem SC_ENGINE_VERSION=2.9.0
if defined SC_WAIT_GATE set /p SC_GATE= >nul
setlocal EnableExtensions DisableDelayedExpansion
set "ORIGEM="
if defined SC_PATHS_CONFIG set "ORIGEM=%SC_ORIGEM%"
set "DESTINO1="
if defined SC_PATHS_CONFIG set "DESTINO1=%SC_DESTINO1%"
set "DESTINO2="
if defined SC_PATHS_CONFIG set "DESTINO2=%SC_DESTINO2%"
set "DESTINO3="
if defined SC_PATHS_CONFIG set "DESTINO3=%SC_DESTINO3%"
set "LIXEIRA="
if defined SC_PATHS_CONFIG set "LIXEIRA=%SC_LIXEIRA%"
set "LOGDIR=%LOCALAPPDATA%\SincronizadorRede\logs"
if defined SC_LOGDIR set "LOGDIR=%SC_LOGDIR%"
set "RETENCAO_DIAS=5"
set "DRYRUN=0"
set "TENTATIVAS=3"
set "ESPERA=5"
if not defined ORIGEM exit /b 10
if not defined DESTINO1 if not defined DESTINO2 if not defined DESTINO3 exit /b 11
if not defined LIXEIRA exit /b 12
if not defined SC_ENGINE_EXE set "SC_ENGINE_EXE=%~dp0SincronizadorRede.exe"
if not exist "%SC_ENGINE_EXE%" exit /b 99
set "SC_ORIGEM=%ORIGEM%"
set "SC_DESTINO1=%DESTINO1%"
set "SC_DESTINO2=%DESTINO2%"
set "SC_DESTINO3=%DESTINO3%"
set "SC_LIXEIRA=%LIXEIRA%"
set "SC_RETENCAO_DIAS=%RETENCAO_DIAS%"
set "SC_DRYRUN=%DRYRUN%"
for /f "delims=" %%I in ('powershell -NoProfile -Command "Get-Date -Format yyyy-MM-dd_HH-mm-ss"') do set "DATAHORA=%%I"
if not defined DATAHORA set "DATAHORA=%SC_RUN_TOKEN%"
if not exist "%LOGDIR%\." mkdir "%LOGDIR%" >nul 2>&1
set "LOG=%LOGDIR%\Sincronizacao_%DATAHORA%.log"
set "SC_TRASH_LOG=%LOG%"
set "LOCK=%LOGDIR%\_SINCRONIZACAO_EM_EXECUCAO.lock"
if exist "%LOCK%" exit /b 20
if "%DRYRUN%"=="0" >"%LOCK%" echo %SC_RUN_TOKEN% Inicio: %date% %time%
(
echo ============================================================
echo SINCRONIZADOR DE REDE 2.9.0
echo Inicio: %date% %time%
echo Origem: %ORIGEM%
echo Destino1: %DESTINO1%
echo Destino2: %DESTINO2%
echo Destino3: %DESTINO3%
echo Lixeira: %LIXEIRA%
echo Retencao desde a entrada na lixeira: %RETENCAO_DIAS% dias
echo ============================================================
)>>"%LOG%"
if not exist "%ORIGEM%\." goto :ORIGEM_ERRO
set /a DESTINOS_OK=0,DESTINOS_ERRO=0,EXCLUIDOS_TOTAL=0
if defined DESTINO1 call :PROCESSAR "%DESTINO1%" "DESTINO1"
if defined DESTINO2 call :PROCESSAR "%DESTINO2%" "DESTINO2"
if defined DESTINO3 call :PROCESSAR "%DESTINO3%" "DESTINO3"
"%SC_ENGINE_EXE%" %SC_ENGINE_ARGS% --trash-operation cleanup
if errorlevel 1 set /a DESTINOS_ERRO+=1
(
echo.
echo RESULTADO FINAL
echo Destinos OK: %DESTINOS_OK%
echo Erros: %DESTINOS_ERRO%
echo Movidos com sucesso para lixeira: %EXCLUIDOS_TOTAL%
echo Fim: %date% %time%
)>>"%LOG%"
if exist "%LOCK%" del /q "%LOCK%" >nul 2>&1
if %DESTINOS_ERRO% GTR 0 exit /b 1
exit /b 0

:ORIGEM_ERRO
(echo ERRO_ORIGEM: %ORIGEM%)>>"%LOG%"
if exist "%LOCK%" del /q "%LOCK%" >nul 2>&1
exit /b 10

:PROCESSAR
set "DESTINO=%~1"
set "NOME=%~2"
set "SC_CURRENT_NOME=%NOME%"
set "SC_TRASH_RESULT=%LOGDIR%\SC_Trash_%SC_RUN_TOKEN%_%NOME%.txt"
echo SC_DEST_START %NOME%
"%SC_ENGINE_EXE%" %SC_ENGINE_ARGS% --trash-operation archive
set "RC=%ERRORLEVEL%"
set "SC_MOVIDOS=0"
if exist "%SC_TRASH_RESULT%" set /p SC_MOVIDOS= <"%SC_TRASH_RESULT%"
set /a EXCLUIDOS_TOTAL+=SC_MOVIDOS
if exist "%SC_TRASH_RESULT%" del /q "%SC_TRASH_RESULT%" >nul 2>&1
if not "%RC%"=="0" goto :DESTINO_ERRO
echo SC_COPY_START %NOME%
if "%DRYRUN%"=="1" (
 robocopy "%ORIGEM%" "%DESTINO%" /E /Z /COPY:DAT /DCOPY:DAT /R:1 /W:2 /XJ /L /V /FP /BYTES /NDL /TEE /LOG+:"%LOG%"
) else (
 robocopy "%ORIGEM%" "%DESTINO%" /E /Z /COPY:DAT /DCOPY:DAT /R:%TENTATIVAS% /W:%ESPERA% /XJ /V /FP /BYTES /NDL /TEE /LOG+:"%LOG%"
)
set "RC=%ERRORLEVEL%"
(echo Codigo Robocopy %NOME%: %RC%)>>"%LOG%"
if %RC% GEQ 8 goto :DESTINO_ERRO
set /a DESTINOS_OK+=1
echo SC_DEST_END %NOME%
exit /b 0

:DESTINO_ERRO
set /a DESTINOS_ERRO+=1
(echo ERRO_DESTINO: %NOME%)>>"%LOG%"
echo SC_DEST_END %NOME%
exit /b 0
