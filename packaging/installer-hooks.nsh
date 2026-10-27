; Informational page, not a new EULA. Keep default user data on uninstall.
!define MUI_LICENSEPAGE_BUTTON "$(^NextBtn)"
!macro NSIS_HOOK_PREUNINSTALL
  ; The stock Tauri template can delete data when a checkbox is selected.
  ; This candidate's policy is preservation, including silent uninstall.
  StrCpy $DeleteAppDataCheckboxState 0
!macroend
