; filepath: /Users/aburneikis/Code/GoodPlayer/installer.nsi
; NSIS installer script for GoodPlayer
; Requires NSIS: https://nsis.sourceforge.io/

!define APPNAME "GoodPlayer"
!define COMPANYNAME "GoodPlayer"
!define DESCRIPTION "Video player with frame-by-frame navigation and multi-track audio"
!define VERSIONMAJOR 1
!define VERSIONMINOR 0
!define VERSIONBUILD 0
!define INSTALLSIZE 150000

RequestExecutionLevel admin
InstallDir "$PROGRAMFILES\${APPNAME}"
Name "${APPNAME}"
OutFile "dist\GoodPlayer-Setup.exe"
ShowInstDetails show

!include "MUI2.nsh"

!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH

!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES

!insertmacro MUI_LANGUAGE "English"

Section "Install"
    SetOutPath $INSTDIR
    
    ; Copy all files from dist/GoodPlayer
    File /r "dist\GoodPlayer\*.*"
    
    ; Create start menu shortcut
    CreateDirectory "$SMPROGRAMS\${APPNAME}"
    CreateShortcut "$SMPROGRAMS\${APPNAME}\${APPNAME}.lnk" "$INSTDIR\GoodPlayer.exe"
    CreateShortcut "$SMPROGRAMS\${APPNAME}\Uninstall.lnk" "$INSTDIR\uninstall.exe"
    
    ; Create desktop shortcut
    CreateShortcut "$DESKTOP\${APPNAME}.lnk" "$INSTDIR\GoodPlayer.exe"
    
    ; Write uninstaller
    WriteUninstaller "$INSTDIR\uninstall.exe"
    
    ; Register with Windows
    WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APPNAME}" "DisplayName" "${APPNAME}"
    WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APPNAME}" "UninstallString" "$\"$INSTDIR\uninstall.exe$\""
    WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APPNAME}" "InstallLocation" "$INSTDIR"
    WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APPNAME}" "Publisher" "${COMPANYNAME}"
    WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APPNAME}" "DisplayVersion" "${VERSIONMAJOR}.${VERSIONMINOR}.${VERSIONBUILD}"
    WriteRegDWORD HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APPNAME}" "EstimatedSize" ${INSTALLSIZE}
    
    ; Register file associations
    WriteRegStr HKCR ".mp4\OpenWithProgids" "GoodPlayer.mp4" ""
    WriteRegStr HKCR ".mkv\OpenWithProgids" "GoodPlayer.mkv" ""
    WriteRegStr HKCR ".avi\OpenWithProgids" "GoodPlayer.avi" ""
    WriteRegStr HKCR ".mov\OpenWithProgids" "GoodPlayer.mov" ""
    WriteRegStr HKCR ".webm\OpenWithProgids" "GoodPlayer.webm" ""
    
    WriteRegStr HKCR "GoodPlayer.mp4" "" "MP4 Video"
    WriteRegStr HKCR "GoodPlayer.mp4\shell\open\command" "" "$\"$INSTDIR\GoodPlayer.exe$\" $\"%1$\""
    
SectionEnd

Section "Uninstall"
    ; Remove files
    RMDir /r "$INSTDIR"
    
    ; Remove shortcuts
    RMDir /r "$SMPROGRAMS\${APPNAME}"
    Delete "$DESKTOP\${APPNAME}.lnk"
    
    ; Remove registry entries
    DeleteRegKey HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APPNAME}"
    DeleteRegKey HKCR "GoodPlayer.mp4"
    DeleteRegValue HKCR ".mp4\OpenWithProgids" "GoodPlayer.mp4"
    DeleteRegValue HKCR ".mkv\OpenWithProgids" "GoodPlayer.mkv"
    DeleteRegValue HKCR ".avi\OpenWithProgids" "GoodPlayer.avi"
    DeleteRegValue HKCR ".mov\OpenWithProgids" "GoodPlayer.mov"
    DeleteRegValue HKCR ".webm\OpenWithProgids" "GoodPlayer.webm"
    
SectionEnd