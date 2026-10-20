import XCTest

@testable import ArabicaDesktop

@MainActor
final class DesktopWindowRegistryTests: XCTestCase {
    func testSettingsFollowsMostRecentWindowAndFallsBackWhenItCloses() {
        let windows = DesktopWindowRegistry()
        let first = DesktopModel(restoreWorkspace: false, sidebarLibrary: .testInstance())
        let second = DesktopModel(restoreWorkspace: false, sidebarLibrary: .testInstance())
        let firstID = UUID()
        let secondID = UUID()
        windows.register(firstID, model: first)
        windows.register(secondID, model: second)
        XCTAssertTrue(windows.activeModel === first)
        windows.activate(secondID)
        XCTAssertTrue(windows.activeModel === second)
        windows.activate(firstID)
        XCTAssertTrue(windows.activeModel === first)
        windows.unregister(firstID)
        XCTAssertTrue(windows.activeModel === second)
        windows.unregister(secondID)
        XCTAssertNil(windows.activeModel)
        XCTAssertNil(windows.activeWindowID)
    }

    func testRegistryDoesNotRetainClosedWindowModel() {
        let windows = DesktopWindowRegistry()
        let first = DesktopModel(restoreWorkspace: false, sidebarLibrary: .testInstance())
        windows.register(UUID(), model: first)
        var second: DesktopModel? = DesktopModel(restoreWorkspace: false, sidebarLibrary: .testInstance())
        let secondID = UUID()
        windows.register(secondID, model: second!)
        windows.activate(secondID)
        second = nil
        XCTAssertTrue(windows.activeModel === first)
        XCTAssertNil(windows.permissionWindowID(sessionID: "session", ownerID: secondID))
    }

    func testRelaunchActivityIncludesBackgroundWindowsAndPendingApprovals() {
        let windows = DesktopWindowRegistry()
        let first = DesktopModel(restoreWorkspace: false, sidebarLibrary: .testInstance())
        let second = DesktopModel(restoreWorkspace: false, sidebarLibrary: .testInstance())
        let firstID = UUID()
        let secondID = UUID()
        windows.register(firstID, model: first)
        windows.register(secondID, model: second)
        windows.activate(secondID)
        XCTAssertFalse(windows.hasActivityPreventingRelaunch)

        let run = first.beginRun(for: "background")
        XCTAssertTrue(windows.hasActivityPreventingRelaunch)
        first.finishRun(for: "background", runID: run)
        XCTAssertFalse(windows.hasActivityPreventingRelaunch)

        first.permission = PermissionPrompt(
            id: .int(1), title: "Review tool access", detail: "", options: [],
            method: "session/request_permission", params: ["sessionId": "background"]
        )
        XCTAssertTrue(windows.hasActivityPreventingRelaunch)
        first.permission = nil
        second.isConnecting = true
        XCTAssertTrue(windows.hasActivityPreventingRelaunch)
        second.isConnecting = false
        XCTAssertFalse(windows.hasActivityPreventingRelaunch)
    }

    func testDeferredUpdateWaitsForEveryWindowToBecomeIdle() async {
        let windows = DesktopWindowRegistry()
        let first = DesktopModel(restoreWorkspace: false, sidebarLibrary: .testInstance())
        let second = DesktopModel(restoreWorkspace: false, sidebarLibrary: .testInstance())
        windows.register(UUID(), model: first)
        windows.register(UUID(), model: second)
        let firstRun = first.beginRun(for: "first")
        let secondRun = second.beginRun(for: "second")
        let updater = AppUpdater(windows: windows)
        let installed = expectation(description: "Update resumes after all windows finish")
        var installCount = 0
        XCTAssertTrue(
            updater.postponeInstallationIfNeeded {
                installCount += 1
                installed.fulfill()
            })
        XCTAssertTrue(updater.waitingForRuns)

        first.finishRun(for: "first", runID: firstRun)
        await Task.yield()
        XCTAssertEqual(installCount, 0)
        XCTAssertTrue(updater.waitingForRuns)

        second.finishRun(for: "second", runID: secondRun)
        await fulfillment(of: [installed], timeout: 1)
        XCTAssertEqual(installCount, 1)
        XCTAssertFalse(updater.waitingForRuns)
        second.isConnecting = true
        second.isConnecting = false
        await Task.yield()
        XCTAssertEqual(installCount, 1)
    }

    func testClosingTheOnlyBusyWindowResumesADeferredUpdate() async {
        let windows = DesktopWindowRegistry()
        let model = DesktopModel(restoreWorkspace: false, sidebarLibrary: .testInstance())
        let windowID = UUID()
        windows.register(windowID, model: model)
        model.isConnecting = true
        let updater = AppUpdater(windows: windows)
        let installed = expectation(description: "Closed window no longer blocks installation")
        XCTAssertTrue(updater.postponeInstallationIfNeeded { installed.fulfill() })

        windows.unregister(windowID)
        await fulfillment(of: [installed], timeout: 1)
        XCTAssertFalse(windows.hasActivityPreventingRelaunch)
        XCTAssertFalse(updater.waitingForRuns)
    }

    func testIdleApplicationDoesNotPostponeSparkleInstallation() {
        let windows = DesktopWindowRegistry()
        let updater = AppUpdater(windows: windows)
        var installed = false
        XCTAssertFalse(updater.postponeInstallationIfNeeded { installed = true })
        XCTAssertFalse(updater.waitingForRuns)
        XCTAssertFalse(installed)
    }

    func testPermissionNotificationUsesOwnerEvenWhenSessionIsSelectedInAnotherWindow() {
        let windows = DesktopWindowRegistry()
        let first = DesktopModel(restoreWorkspace: false, sidebarLibrary: .testInstance())
        let second = DesktopModel(restoreWorkspace: false, sidebarLibrary: .testInstance())
        first.selectedSessionID = "shared"
        second.selectedSessionID = "shared"
        let firstID = UUID()
        let secondID = UUID()
        windows.register(firstID, model: first)
        windows.register(secondID, model: second)
        windows.activate(secondID)
        XCTAssertEqual(windows.permissionWindowID(sessionID: "shared", ownerID: firstID), firstID)
        windows.unregister(firstID)
        XCTAssertNil(windows.permissionWindowID(sessionID: "shared", ownerID: firstID))
    }

    func testLegacyNotificationPrefersPendingPermissionOverSharedHistoryOrSelection() {
        let windows = DesktopWindowRegistry()
        let first = DesktopModel(restoreWorkspace: false, sidebarLibrary: .testInstance())
        let second = DesktopModel(restoreWorkspace: false, sidebarLibrary: .testInstance())
        first.permission = PermissionPrompt(
            id: .int(1), title: "Run a tool", detail: "", options: [],
            method: "session/request_permission", params: ["sessionId": "pending"]
        )
        second.selectedSessionID = "pending"
        second.sessions = [DesktopSession(id: "history", cwd: "/tmp", title: "History")]
        let firstID = UUID()
        let secondID = UUID()
        windows.register(firstID, model: first)
        windows.register(secondID, model: second)
        windows.activate(secondID)
        XCTAssertEqual(windows.permissionWindowID(sessionID: "pending", ownerID: nil), firstID)
        XCTAssertNil(windows.permissionWindowID(sessionID: "history", ownerID: nil))
        first.permission = nil
        XCTAssertEqual(windows.permissionWindowID(sessionID: "pending", ownerID: nil), secondID)
    }
}
