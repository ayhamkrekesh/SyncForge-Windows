# SyncForge Windows GitHub Build

GitHub Actions compiles SyncForge into a Windows application and bundles FFmpeg. No Python setup is required on the destination Windows computer.

## Build
Push the files to `main`, then open GitHub repository > Actions > Build Windows EXE > latest run > Artifacts > SyncForge-Windows.

Download the Actions artifact ZIP, extract the complete folder and run `SyncForge.exe`. Keep the `_internal` and `bin` folders beside the EXE. Windows may prompt about unsigned apps.

This is an untested early prototype. Automatic matching requires reference camera audio. Verify audio sync and Premiere Pro XML before using for important footage.
