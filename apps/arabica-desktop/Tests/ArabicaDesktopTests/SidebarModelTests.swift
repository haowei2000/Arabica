import XCTest

@testable import ArabicaDesktop

@MainActor
final class SidebarModelTests: XCTestCase {
    private func model(request: DesktopModel.SessionRequest? = nil) -> DesktopModel {
        let model = DesktopModel(
            restoreWorkspace: false, sessionRequest: request ?? { _, _ in [:] }, sidebarLibrary: .testInstance())
        model.isConnected = true
        model.workspace = URL(fileURLWithPath: "/tmp/project-a")
        model.sessions = [
            DesktopSession(id: "a", cwd: "/tmp/project-a", title: "A"),
            DesktopSession(id: "b", cwd: "/tmp/project-b", title: "B"),
        ]
        return model
    }

    func testOpeningAnotherProjectKeepsBackgroundRunAndConversationDraft() async {
        let model = model()
        await model.selectSession("a")
        model.draft = "A draft"
        let run = model.beginRun(for: "a")
        await model.openWorkspace(URL(fileURLWithPath: "/tmp/new-project"))
        XCTAssertTrue(model.isConnected)
        XCTAssertTrue(model.isRunning(sessionID: "a"))
        XCTAssertNil(model.selectedSessionID)
        XCTAssertEqual(model.workspace?.path, "/tmp/new-project")
        XCTAssertTrue(model.sidebarLibrary.projectPaths.contains("/tmp/new-project"))
        model.finishRun(for: "a", runID: run)
        await model.selectSession("a")
        XCTAssertEqual(model.draft, "A draft")
        XCTAssertTrue(model.isSessionReady)
    }

    func testBackgroundFileAndTerminalRequestsKeepTheirSessionRoot() async throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        let a = root.appendingPathComponent("a")
        let b = root.appendingPathComponent("b")
        try FileManager.default.createDirectory(at: a, withIntermediateDirectories: true)
        try FileManager.default.createDirectory(at: b, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: root) }
        let file = a.appendingPathComponent("note.txt")
        try "A content".write(to: file, atomically: true, encoding: .utf8)
        let model = model()
        model.sessions = [
            DesktopSession(id: "a", cwd: a.path, title: "A"), DesktopSession(id: "b", cwd: b.path, title: "B"),
        ]
        await model.selectSession("a")
        await model.selectSession("b")
        let result = try await model.performClientService(
            "fs/read_text_file", params: ["sessionId": "a", "path": file.path])
        XCTAssertEqual(result["content"] as? String, "A content")
        do {
            _ = try await model.performClientService(
                "fs/read_text_file", params: ["sessionId": "b", "path": file.path])
            XCTFail("Project B must not read project A")
        } catch ServiceError.invalid {}
        let terminal = try await model.performClientService(
            "terminal/create", params: ["sessionId": "a", "command": "/bin/test", "args": [".", "-ef", a.path]])
        let terminalID = try XCTUnwrap(terminal["terminalId"] as? String)
        let exited = try await model.performClientService(
            "terminal/wait_for_exit", params: ["sessionId": "a", "terminalId": terminalID])
        XCTAssertEqual(((exited["exitStatus"] as? [String: Any])?["exitCode"] as? NSNumber)?.intValue, 0)
        _ = try await model.performClientService(
            "terminal/release", params: ["sessionId": "a", "terminalId": terminalID])
        XCTAssertThrowsError(try model.serviceWorkspace(for: ["sessionId": "unknown"]))
    }

    func testApprovalCapturesOriginatingProjectBeforeNavigationChanges() async throws {
        let model = model()
        await model.selectSession("a")
        model.handleRequest(
            .int(1), method: "fs/write_text_file",
            params: ["sessionId": "a", "path": "/tmp/project-a/note.txt", "content": "hello"])
        await model.selectSession("b")
        XCTAssertEqual(model.permission?.workspace?.path, "/tmp/project-a")
        XCTAssertEqual(model.sessionStatus("a"), .needsApproval)
        XCTAssertEqual(model.sessionStatus("b"), .idle)
        XCTAssertFalse(model.canArchiveSession("a"))
        model.dismissPermission(nil)
    }

    func testBackgroundFailurePersistsAndClearsUnreadOnlyWhenVisited() async {
        let model = model { method, _ in
            if method == "session/prompt" { throw ACPError.remote("Provider unavailable") }
            return [:]
        }
        await model.selectSession("a")
        let run = model.beginRun(for: "a")
        await model.selectSession("b")
        model.finishRun(for: "a", runID: run, outcome: .failed, error: "Provider unavailable")
        XCTAssertEqual(model.sessionStatus("a"), .failed)
        XCTAssertEqual(model.sessionError("a"), "Provider unavailable")
        XCTAssertTrue(model.hasUnreadResult("a"))
        XCTAssertNil(model.errorText)
        await model.selectSession("a")
        XCTAssertFalse(model.hasUnreadResult("a"))
        XCTAssertEqual(model.errorText, "Provider unavailable")
        XCTAssertEqual(model.sessionStatus("a"), .failed)
    }

    func testPromptFailureInBackgroundRetainsReasonAndTerminalStatus() async {
        let started = expectation(description: "Prompt started")
        var completion: CheckedContinuation<[String: Any], Error>?
        let model = model { method, _ in
            if method == "session/prompt" {
                return try await withCheckedThrowingContinuation {
                    completion = $0
                    started.fulfill()
                }
            }
            return [:]
        }
        await model.selectSession("a")
        model.draft = "Run"
        let task = Task { await model.send() }
        await fulfillment(of: [started], timeout: 1)
        await model.selectSession("b")
        completion?.resume(throwing: ACPError.remote("Background failure"))
        await task.value
        XCTAssertEqual(model.sessionStatus("a"), .failed)
        XCTAssertEqual(model.sessionError("a"), "Background failure")
        XCTAssertTrue(model.hasUnreadResult("a"))
        XCTAssertFalse(model.isRunning(sessionID: "a"))
    }

    func testCancelledPromptHasDifferentTerminalStatus() async {
        let model = model { _, _ in ["stopReason": "cancelled"] }
        await model.selectSession("a")
        model.draft = "Run"
        await model.send()
        XCTAssertEqual(model.sessionStatus("a"), .cancelled)
        XCTAssertFalse(model.hasUnreadResult("a"))
    }

    func testSendingFromAnArchivedConversationRestoresItToActive() async {
        let model = model()
        model.archiveSession("a", archived: true)
        await model.selectSession("a")
        model.draft = "Continue"
        await model.send()
        XCTAssertFalse(model.sidebarLibrary.isArchived("a"))
        XCTAssertEqual(model.sessionStatus("a"), .completed)
    }

    func testBusyConversationCannotBeClosedArchivedRemovedOrReconnected() async {
        let model = model()
        await model.selectSession("a")
        let run = model.beginRun(for: "a")
        await model.selectSession("b")
        XCTAssertFalse(model.canCloseSession("a"))
        XCTAssertFalse(model.canArchiveSession("a"))
        XCTAssertFalse(model.canRemoveProject("/tmp/project-a"))
        model.archiveSession("a", archived: true)
        await model.closeSession("a")
        await model.reconnectWorkspace(URL(fileURLWithPath: "/tmp/project-b"))
        XCTAssertTrue(model.isSessionOpen("a"))
        XCTAssertTrue(model.isRunning(sessionID: "a"))
        XCTAssertFalse(model.sidebarLibrary.isArchived("a"))
        XCTAssertNotNil(model.errorText)
        model.finishRun(for: "a", runID: run)
    }

    func testArchiveAndCloseKeepHistoryAndPerConversationDrafts() async {
        let model = model()
        await model.selectSession("a")
        model.draft = "Keep this"
        model.archiveSession("a", archived: true)
        XCTAssertNil(model.selectedSessionID)
        XCTAssertTrue(model.sidebarLibrary.isArchived("a"))
        model.archiveSession("a", archived: false)
        await model.selectSession("a")
        XCTAssertEqual(model.draft, "Keep this")
        await model.closeSession("a")
        XCTAssertFalse(model.isSessionOpen("a"))
        XCTAssertTrue(model.sessions.contains { $0.id == "a" })
        await model.selectSession("a")
        XCTAssertEqual(model.draft, "Keep this")
        XCTAssertTrue(model.isSessionReady)
    }

    func testRecentActivityMovesAnOldConversationToTheTop() {
        let model = model()
        model.sessions = [
            DesktopSession(id: "newer", cwd: "/tmp/project-a", title: "Newer", updatedAt: "2025-02-01T00:00:00Z"),
            DesktopSession(id: "older", cwd: "/tmp/project-a", title: "Older", updatedAt: "2025-01-01T00:00:00Z"),
        ]
        let run = model.beginRun(for: "older")
        model.finishRun(for: "older", runID: run)
        XCTAssertEqual(model.projectGroups.first?.sessions.first?.id, "older")
    }

    func testEmptyProjectsSurviveSwitchingAndRemovingOnlyHidesTheirGroup() {
        let model = model()
        model.activateProject("/tmp/empty")
        model.activateProject("/tmp/project-b")
        XCTAssertTrue(model.projectGroups.contains { $0.path == "/tmp/empty" })
        model.removeProject("/tmp/project-a")
        XCTAssertFalse(model.projectGroups.contains { $0.path == "/tmp/project-a" })
        XCTAssertTrue(model.sessions.contains { $0.id == "a" })
        model.activateProject("/tmp/project-a")
        XCTAssertTrue(model.projectGroups.contains { $0.path == "/tmp/project-a" })
    }

    func testRenameRequiresCapabilityAndConfirmedPersistentTitle() async {
        var renameCalls = 0
        let model = model { method, params in
            if method == "_arabica/session/rename" {
                renameCalls += 1
                return params
            }
            return [:]
        }
        XCTAssertFalse(model.canRenameSessions)
        model.configureSessionCapabilities([
            "_meta": ["arabica.session": ["schemaVersion": 1, "rename": "_arabica/session/rename"]]
        ])
        XCTAssertTrue(model.canRenameSessions)
        let renamed = await model.renameSession("a", title: "  New title  ")
        XCTAssertTrue(renamed)
        XCTAssertEqual(model.sessions.first?.title, "New title")
        let invalid = await model.renameSession("a", title: " \n ")
        XCTAssertFalse(invalid)
        XCTAssertEqual(renameCalls, 1)
    }

    func testRejectedRenamePreservesCommittedTitleAndReason() async {
        let model = model { _, _ in throw ACPError.remote("Conversation is in use in another window") }
        model.configureSessionCapabilities([
            "_meta": ["arabica.session": ["schemaVersion": 1, "rename": "_arabica/session/rename"]]
        ])
        let renamed = await model.renameSession("a", title: "New title")
        XCTAssertFalse(renamed)
        XCTAssertEqual(model.sessions.first?.title, "A")
        XCTAssertEqual(model.errorText, "Conversation is in use in another window")
    }

    func testBackgroundSessionInfoUpdateChangesTheCorrectTitle() async {
        let model = model()
        await model.selectSession("b")
        model.handleNotification(
            "session/update",
            params: ["sessionId": "a", "update": ["sessionUpdate": "session_info_update", "title": "Renamed A"]])
        XCTAssertEqual(model.sessions.first { $0.id == "a" }?.title, "Renamed A")
        XCTAssertEqual(model.selectedSessionID, "b")
    }

    func testApprovedWriteCannotExecuteAfterItsWindowCloses() async throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: root) }
        let file = root.appendingPathComponent("must-not-be-written.txt")
        let model = model()
        model.sessions = [DesktopSession(id: "a", cwd: root.path, title: "A")]
        model.handleRequest(
            .int(1), method: "fs/write_text_file",
            params: ["sessionId": "a", "path": file.path, "content": "old request"])
        model.resolvePermission("allow_once")
        XCTAssertFalse(model.canArchiveSession("a"))
        model.closeWindow()
        await Task.yield()
        await Task.yield()
        XCTAssertFalse(FileManager.default.fileExists(atPath: file.path))
    }

    func testAnotherWindowCannotHideBusyConversationsOrTheirProjects() async {
        let library = SidebarLibrary.testInstance()
        let first = DesktopModel(restoreWorkspace: false, sessionRequest: { _, _ in [:] }, sidebarLibrary: library)
        let second = DesktopModel(restoreWorkspace: false, sessionRequest: { _, _ in [:] }, sidebarLibrary: library)
        first.isConnected = true
        second.isConnected = true
        first.sessions = [DesktopSession(id: "a", cwd: "/tmp/project-a", title: "A")]
        second.sessions = first.sessions
        first.workspace = URL(fileURLWithPath: "/tmp/project-a")
        second.workspace = URL(fileURLWithPath: "/tmp/project-b")
        let run = first.beginRun(for: "a")
        XCTAssertFalse(second.canArchiveSession("a"))
        XCTAssertFalse(second.canRemoveProject("/tmp/project-a"))
        second.archiveSession("a", archived: true)
        XCTAssertFalse(library.isArchived("a"))
        first.finishRun(for: "a", runID: run)
        XCTAssertTrue(second.canArchiveSession("a"))
        first.closeWindow()
        XCTAssertTrue(second.canRemoveProject("/tmp/project-a"))
    }

    func testLateRefreshCannotOverwriteAConfirmedRename() async {
        let started = expectation(description: "Refresh started")
        var completion: CheckedContinuation<[String: Any], Error>?
        let model = model { method, params in
            if method == "session/list" {
                return try await withCheckedThrowingContinuation {
                    completion = $0
                    started.fulfill()
                }
            }
            return params
        }
        model.configureSessionCapabilities([
            "_meta": ["arabica.session": ["schemaVersion": 1, "rename": "_arabica/session/rename"]]
        ])
        let refresh = Task { await model.refreshSessions() }
        await fulfillment(of: [started], timeout: 1)
        let renamed = await model.renameSession("a", title: "New A")
        XCTAssertTrue(renamed)
        completion?.resume(returning: ["sessions": [["sessionId": "a", "cwd": "/tmp/project-a", "title": "Old A"]]])
        await refresh.value
        XCTAssertEqual(model.sessions.first?.title, "New A")
        XCTAssertEqual(model.sessions.count, 2)
    }

    func testBackgroundLoadFailureKeepsAnActionableStateAndCanRetry() async {
        let started = expectation(description: "Load started")
        var completion: CheckedContinuation<[String: Any], Error>?
        var firstLoad = true
        let model = model { _, params in
            if params["sessionId"] as? String == "a", firstLoad {
                firstLoad = false
                return try await withCheckedThrowingContinuation {
                    completion = $0
                    started.fulfill()
                }
            }
            return [:]
        }
        let load = Task { await model.selectSession("a") }
        await fulfillment(of: [started], timeout: 1)
        XCTAssertEqual(model.sessionStatus("a"), .loading)
        await model.selectSession("b")
        completion?.resume(throwing: ACPError.remote("History could not be loaded"))
        await load.value
        XCTAssertEqual(model.sessionStatus("a"), .loadFailed)
        XCTAssertTrue(model.hasUnreadResult("a"))
        XCTAssertNil(model.errorText)
        await model.selectSession("a")
        XCTAssertTrue(model.isSessionReady)
        XCTAssertEqual(model.sessionStatus("a"), .idle)
        XCTAssertNil(model.sessionError("a"))
        XCTAssertNil(model.errorText)
    }
}
