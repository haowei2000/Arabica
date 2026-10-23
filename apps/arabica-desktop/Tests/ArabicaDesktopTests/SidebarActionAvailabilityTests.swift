import XCTest

@testable import ArabicaDesktop

@MainActor
final class SidebarActionAvailabilityTests: XCTestCase {
    private func model(
        request: DesktopModel.SessionRequest? = nil, library: SidebarLibrary? = nil
    ) -> DesktopModel {
        let model = DesktopModel(
            restoreWorkspace: false, sessionRequest: request ?? { _, _ in [:] },
            sidebarLibrary: library ?? .testInstance())
        model.isConnected = true
        model.workspace = URL(fileURLWithPath: "/tmp/project-a")
        model.sessions = [DesktopSession(id: "a", cwd: "/tmp/project-a", title: "A")]
        return model
    }

    private func enableRename(_ model: DesktopModel) {
        model.configureSessionCapabilities([
            "_meta": ["arabica.session": ["schemaVersion": 1, "rename": "_arabica/session/rename"]]
        ])
    }

    func testDisconnectedActionsExplainHowToReconnectAndAllowProjectSelection() {
        let model = model()
        model.isConnected = false
        XCTAssertFalse(model.newConversationAvailability.isEnabled)
        XCTAssertEqual(model.newConversationAvailability.explanation, "Open a project to reconnect to the agent.")
        XCTAssertFalse(model.canCreateSession)
        XCTAssertEqual(
            model.renameSessionAvailability("a").explanation,
            "Reconnect to the agent to rename this conversation.")
        XCTAssertEqual(
            model.closeSessionAvailability("a").explanation,
            "Reconnect to the agent to close this conversation in this window.")
        XCTAssertTrue(model.selectProjectAvailability("/tmp/project-a").isEnabled)
        XCTAssertTrue(model.archiveSessionAvailability("a", archived: false).isEnabled)
    }

    func testConnectingActionsExplainThatConnectionMustFinish() {
        let model = model()
        model.isConnecting = true
        XCTAssertFalse(model.newConversationAvailability.isEnabled)
        XCTAssertEqual(model.newConversationAvailability.explanation, "Wait for the agent to connect.")
        XCTAssertFalse(model.selectProjectAvailability("/tmp/project-b").isEnabled)
        XCTAssertFalse(model.renameSessionAvailability("a").isEnabled)
        XCTAssertFalse(model.closeSessionAvailability("a").isEnabled)
    }

    func testCurrentProjectCanReturnFromConversationToProjectLanding() async {
        let model = model()
        XCTAssertTrue(model.selectProjectAvailability("/tmp/project-a/.").isEnabled)
        await model.selectSession("a")
        XCTAssertTrue(model.selectProjectAvailability("/tmp/project-a").isEnabled)
        model.activateProject("/tmp/project-a")
        XCTAssertNil(model.selectedSessionID)
        XCTAssertTrue(model.selectProjectAvailability("/tmp/project-a").isEnabled)
    }

    func testCreationExplainsUnavailableActionsAndTracksTheTargetProject() async {
        let started = expectation(description: "Creation started")
        var completion: CheckedContinuation<[String: Any], Error>?
        let model = model { method, _ in
            if method == "session/new" {
                return try await withCheckedThrowingContinuation {
                    completion = $0
                    started.fulfill()
                }
            }
            return [:]
        }
        await model.selectSession("a")
        let task = Task { await model.newSession(in: "/tmp/project-b/.") }
        await fulfillment(of: [started], timeout: 1)
        XCTAssertEqual(model.creatingProjectPath, "/tmp/project-b")
        XCTAssertEqual(model.newConversationAvailability.explanation, "Wait for the new conversation to open.")
        XCTAssertFalse(model.closeSessionAvailability("a").isEnabled)
        completion?.resume(returning: ["sessionId": "b"])
        await task.value
        XCTAssertNil(model.creatingProjectPath)
        XCTAssertTrue(model.newConversationAvailability.isEnabled)
    }

    func testRenameDistinguishesUnsupportedUnknownAndBusyConversations() async {
        let model = model()
        XCTAssertEqual(
            model.renameSessionAvailability("a").explanation,
            "The connected agent does not support renaming conversations.")
        enableRename(model)
        XCTAssertFalse(model.renameSessionAvailability("missing").isEnabled)
        XCTAssertTrue(model.renameSessionAvailability("a").isEnabled)
        let run = model.beginRun(for: "a")
        let reason = "Stop this conversation’s task or wait for it to finish."
        XCTAssertEqual(model.renameSessionAvailability("a").explanation, reason)
        XCTAssertEqual(model.archiveSessionAvailability("a", archived: true).explanation, reason)
        let renamed = await model.renameSession("a", title: "New A")
        XCTAssertFalse(renamed)
        XCTAssertEqual(model.errorText, reason)
        model.finishRun(for: "a", runID: run)
    }

    func testApprovalRequiresResolutionWhileRestoreRemainsAvailable() async {
        let model = model()
        await model.selectSession("a")
        model.handleRequest(
            .int(1), method: "fs/write_text_file",
            params: ["sessionId": "a", "path": "/tmp/project-a/note.txt", "content": "Hello"])
        XCTAssertEqual(
            model.closeSessionAvailability("a").explanation, "Resolve this conversation’s approval request first.")
        XCTAssertFalse(model.canArchiveSession("a"))
        XCTAssertTrue(model.archiveSessionAvailability("a", archived: false).isEnabled)
        model.dismissPermission(nil)
    }

    func testBusyOtherWindowDisablesRenameArchiveAndCloseWithoutSendingRequests() async {
        let library = SidebarLibrary.testInstance()
        let first = model(library: library)
        var closeRequests = 0
        var renameRequests = 0
        let second = model(
            request: { method, _ in
                if method == "session/close" { closeRequests += 1 }
                if method == "_arabica/session/rename" { renameRequests += 1 }
                return [:]
            }, library: library)
        enableRename(second)
        await second.selectSession("a")
        let run = first.beginRun(for: "a")
        let reason = "This conversation is busy in another window. Wait for its current operation to finish."
        XCTAssertEqual(second.renameSessionAvailability("a").explanation, reason)
        XCTAssertEqual(second.closeSessionAvailability("a").explanation, reason)
        XCTAssertEqual(second.archiveSessionAvailability("a", archived: true).explanation, reason)
        XCTAssertFalse(second.canCloseSession("a"))
        await second.closeSession("a")
        let renamed = await second.renameSession("a", title: "New A")
        XCTAssertFalse(renamed)
        XCTAssertEqual(closeRequests, 0)
        XCTAssertEqual(renameRequests, 0)
        first.finishRun(for: "a", runID: run)
        XCTAssertTrue(second.canCloseSession("a"))
    }

    func testRemoveProjectDistinguishesCurrentBusyAndOtherWindowProjects() {
        let library = SidebarLibrary.testInstance()
        let first = model(library: library)
        XCTAssertEqual(
            first.removeProjectAvailability("/tmp/project-a/.").explanation,
            "Select another project before removing this project from the sidebar.")
        let second = model(library: library)
        second.workspace = URL(fileURLWithPath: "/tmp/project-b")
        XCTAssertEqual(
            second.removeProjectAvailability("/tmp/project-a").explanation,
            "This project is open in another window. Select another project there before removing it from the sidebar.")
        let run = first.beginRun(for: "a")
        XCTAssertEqual(
            second.removeProjectAvailability("/tmp/project-a").explanation,
            "Wait for this project’s current operations to finish before removing it from the sidebar.")
        first.finishRun(for: "a", runID: run)
        first.closeWindow()
        XCTAssertTrue(second.canRemoveProject("/tmp/project-a"))
    }

    func testSuccessfulClosePersistsVisibleStatusAndReopeningClearsIt() async {
        let model = model()
        await model.selectSession("a")
        model.draft = "Keep this draft"
        await model.closeSession("a")
        XCTAssertEqual(model.sessionStatus("a"), .closed)
        XCTAssertEqual(model.sessionStatus("a").title, "Closed in This Window")
        XCTAssertFalse(model.isSessionOpen("a"))
        XCTAssertNil(model.selectedSessionID)
        XCTAssertTrue(model.sessions.contains { $0.id == "a" })
        XCTAssertEqual(
            model.closeSessionAvailability("a").explanation, "This conversation is already closed in this window.")
        await model.selectSession("a")
        XCTAssertEqual(model.sessionStatus("a"), .idle)
        XCTAssertTrue(model.isSessionReady)
        XCTAssertEqual(model.draft, "Keep this draft")
    }

    func testFailedCloseRetainsTheExistingFailureAndOpenSession() async {
        let model = model { method, _ in
            if method == "session/close" { throw ACPError.remote("The conversation could not be closed") }
            return [:]
        }
        await model.selectSession("a")
        let run = model.beginRun(for: "a")
        model.finishRun(for: "a", runID: run, outcome: .failed, error: "Provider unavailable")
        await model.closeSession("a")
        XCTAssertEqual(model.sessionStatus("a"), .failed)
        XCTAssertEqual(model.sessionError("a"), "Provider unavailable")
        XCTAssertEqual(model.errorText, "The conversation could not be closed")
        XCTAssertTrue(model.isSessionOpen("a"))
        XCTAssertEqual(model.selectedSessionID, "a")
    }
}
