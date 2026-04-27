-- Crowe Logic Code launcher.
-- Opens the Crowe Logic-branded VS Code build (or stock VS Code as fallback)
-- on the Foundry workspace. Tolerant of pre-patch and post-patch states so
-- the Desktop icon keeps working before, during, and after patch-local-install.sh.
--
-- Compile to ~/Desktop/Crowe Logic Code.app/Contents/Resources/Scripts/main.scpt:
--   osacompile -o "$HOME/Desktop/Crowe Logic Code.app/Contents/Resources/Scripts/main.scpt" \
--     vscode/scripts/desktop-launcher.applescript

set workspacePath to (POSIX path of (path to home folder)) & "Projects/crowe-logic-foundry"

try
    do shell script "test -d " & quoted form of workspacePath
on error
    display dialog "Foundry workspace not found: " & workspacePath buttons {"OK"} default button "OK" with icon stop
    return
end try

-- Resolution order:
--   1. `code` CLI shim (works pre-patch and post-patch; renaming product.json
--      does not move the shim).
--   2. `crowe-logic` CLI shim (post-patch, if user re-runs Shell Command:
--      Install 'crowe-logic' command in PATH).
--   3. open -b io.crowelogic.code (post-patch by bundle id; survives renames).
--   4. open -a 'Crowe Logic Code' (post-patch by display name).
--   5. open -a 'Visual Studio Code' (pre-patch fallback).
set launchScript to "export PATH=\"/opt/homebrew/bin:/usr/local/bin:$PATH\"; " & ¬
    "if command -v code >/dev/null 2>&1; then exec code " & quoted form of workspacePath & "; " & ¬
    "elif command -v crowe-logic >/dev/null 2>&1; then exec crowe-logic " & quoted form of workspacePath & "; " & ¬
    "elif open -b io.crowelogic.code " & quoted form of workspacePath & " >/dev/null 2>&1; then exit 0; " & ¬
    "elif open -a 'Crowe Logic Code' " & quoted form of workspacePath & " >/dev/null 2>&1; then exit 0; " & ¬
    "else exec open -a 'Visual Studio Code' " & quoted form of workspacePath & "; fi"

try
    do shell script launchScript
on error errMsg
    display dialog "Failed to launch Crowe Logic Code: " & errMsg buttons {"OK"} default button "OK" with icon stop
end try
