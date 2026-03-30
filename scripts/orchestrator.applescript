-- ============================================================
-- Crowe Logic Agent — macOS AppleScript Orchestrator
-- ============================================================
-- One-click automation for agent development workflow:
--   1. Deploy/update the agent on Azure AI Foundry
--   2. Launch interactive chat in Terminal
--   3. Open Azure dashboard for monitoring
--   4. Run test suite
--   5. Full pipeline (deploy + test + chat)
--
-- Usage:
--   osascript scripts/orchestrator.applescript deploy
--   osascript scripts/orchestrator.applescript chat
--   osascript scripts/orchestrator.applescript dashboard
--   osascript scripts/orchestrator.applescript test
--   osascript scripts/orchestrator.applescript pipeline
--   osascript scripts/orchestrator.applescript (shows menu)
-- ============================================================

property projectPath : "/Users/crowelogic/Projects/crowe-logic-foundry"
property venvPath : "/Users/crowelogic/Projects/crowe-logic-foundry/.venv"
property azureDashboard : "https://ai.azure.com/nextgen/r/mVYda6I5Q7uWtGK2g4Co8A,rg-crowelogicos-7858,,crowelogicos-7858-resource,crowelogicos-7858/build/agents/crowe-logic/build"
property pythonCmd : "source .venv/bin/activate 2>/dev/null || true && python3"

-- ============================================================
-- MAIN ENTRY POINT
-- ============================================================

on run argv
	if (count of argv) > 0 then
		set action to item 1 of argv
	else
		set action to showMenu()
	end if

	if action is "deploy" then
		doDeployAgent()
	else if action is "chat" then
		doLaunchChat()
	else if action is "dashboard" then
		doOpenDashboard()
	else if action is "test" then
		doRunTests()
	else if action is "pipeline" then
		doFullPipeline()
	else if action is "setup" then
		doInitialSetup()
	else if action is "status" then
		doShowStatus()
	else
		display dialog "Unknown action: " & action buttons {"OK"} default button "OK" with icon caution
	end if
end run

-- ============================================================
-- MENU
-- ============================================================

on showMenu()
	set menuItems to {"Deploy Agent", "Launch Chat", "Open Azure Dashboard", "Run Tests", "Full Pipeline (Deploy + Test + Chat)", "Initial Setup", "Show Status"}
	set menuActions to {"deploy", "chat", "dashboard", "test", "pipeline", "setup", "status"}

	set chosen to choose from list menuItems with prompt "Crowe Logic Agent — Choose Action:" with title "Crowe Logic" default items {"Launch Chat"}

	if chosen is false then
		error number -128 -- User cancelled
	end if

	set chosenItem to item 1 of chosen
	repeat with i from 1 to count of menuItems
		if item i of menuItems is chosenItem then
			return item i of menuActions
		end if
	end repeat

	return "chat"
end showMenu

-- ============================================================
-- ACTIONS
-- ============================================================

on doInitialSetup()
	display notification "Setting up Crowe Logic..." with title "Crowe Logic"

	runInTerminal("cd " & quoted form of projectPath & " && " & ¬
		"python3 -m venv .venv && " & ¬
		"source .venv/bin/activate && " & ¬
		"pip install -r requirements.txt && " & ¬
		"pip install -e . && " & ¬
		"echo '' && echo '========================================' && " & ¬
		"echo '  Setup complete!' && " & ¬
		"echo '  Next: cp .env.example .env && edit .env' && " & ¬
		"echo '  Then: crowe-logic deploy' && " & ¬
		"echo '========================================'")

	display notification "Setup complete! Edit .env next." with title "Crowe Logic"
end doInitialSetup

on doDeployAgent()
	display notification "Deploying Crowe Logic agent..." with title "Crowe Logic"

	runInTerminal("cd " & quoted form of projectPath & " && " & ¬
		pythonCmd & " scripts/create_agent.py --verbose")

	display notification "Agent deployed successfully!" with title "Crowe Logic" sound name "Glass"
end doDeployAgent

on doLaunchChat()
	runInTerminal("cd " & quoted form of projectPath & " && " & ¬
		pythonCmd & " -m cli.crowe_logic chat")
end doLaunchChat

on doOpenDashboard()
	open location azureDashboard
	display notification "Azure AI Foundry dashboard opened" with title "Crowe Logic"
end doOpenDashboard

on doRunTests()
	display notification "Running agent tests..." with title "Crowe Logic"

	runInTerminal("cd " & quoted form of projectPath & " && " & ¬
		pythonCmd & " scripts/test_agent.py")
end doRunTests

on doFullPipeline()
	display notification "Starting full pipeline..." with title "Crowe Logic"

	-- Step 1: Deploy
	runInTerminal("cd " & quoted form of projectPath & " && " & ¬
		pythonCmd & " scripts/create_agent.py && " & ¬
		"echo '' && echo 'Agent deployed. Running tests...' && " & ¬
		"echo '' && " & ¬
		pythonCmd & " scripts/test_agent.py && " & ¬
		"echo '' && echo '========================================' && " & ¬
		"echo '  Pipeline complete! Starting chat...' && " & ¬
		"echo '========================================' && " & ¬
		"echo '' && " & ¬
		pythonCmd & " -m cli.crowe_logic chat")

	-- Step 2: Open dashboard in background
	delay 2
	open location azureDashboard

	display notification "Pipeline complete!" with title "Crowe Logic" sound name "Glass"
end doFullPipeline

on doShowStatus()
	runInTerminal("cd " & quoted form of projectPath & " && " & ¬
		pythonCmd & " -m cli.crowe_logic status")
end doShowStatus

-- ============================================================
-- HELPERS
-- ============================================================

on runInTerminal(cmd)
	tell application "Terminal"
		activate
		do script cmd
	end tell
end runInTerminal
