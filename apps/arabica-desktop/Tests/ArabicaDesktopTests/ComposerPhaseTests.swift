import XCTest

@testable import ArabicaDesktop

@MainActor
final class ComposerPhaseTests: XCTestCase {
    private func readyModel(
        request: DesktopModel.SessionRequest? = nil,
        notify: DesktopModel.SessionNotification? = nil
    ) async -> DesktopModel {
        let model = DesktopModel(
            restoreWorkspace: false, sessionRequest: request ?? { _, _ in [:] },
            sessionNotification: notify, sidebarLibrary: .testInstance())
        model.sessions = [DesktopSession(id: "chat", cwd: "/tmp", title: "Chat")]
        model.isConnected = true
        await model.selectSession("chat")
        return model
    }

    func testConnectionAndConversationStatusesExplainWhySendingIsBlocked() {
        let model = DesktopModel(restoreWorkspace: false, sidebarLibrary: .testInstance())
        XCTAssertEqual(model.composerPhase, .chooseWorkspace)
        model.workspace = URL(fileURLWithPath: "/tmp")
        XCTAssertEqual(model.composerPhase, .disconnected)
        model.isConnecting = true
        XCTAssertEqual(model.composerPhase, .connecting)
        model.isConnected = true
        model.isConnecting = false
        XCTAssertEqual(model.composerPhase, .createConversation)
        model.selectedSessionID = "not-loaded"
        XCTAssertEqual(model.composerPhase, .conversationUnavailable)
        XCTAssertFalse(model.canSendMessage)
    }

    func testReadyStatusDistinguishesEmptyAndAttachmentOnlyDrafts() async {
        let model = await readyModel()
        XCTAssertEqual(model.composerPhase, .empty)
        XCTAssertFalse(model.canSendMessage)
        model.attachFiles([URL(fileURLWithPath: "/tmp/note.txt")])
        XCTAssertEqual(model.composerPhase, .ready)
        XCTAssertTrue(model.canSendMessage)
        model.attachedFiles = []
        model.draft = "  \n  "
        XCTAssertEqual(model.composerPhase, .empty)
        model.draft = "Send this"
        XCTAssertEqual(model.composerPhase, .ready)
    }

    func testApplyingConfigurationBlocksSendingUntilAgentConfirms() async {
        let started = expectation(description: "Option update started")
        var completion: CheckedContinuation<[String: Any], Error>?
        var updateRequests = 0
        let model = await readyModel(request: { method, _ in
            guard method == "session/set_config_option" else { return [:] }
            updateRequests += 1
            return try await withCheckedThrowingContinuation { continuation in
                completion = continuation
                started.fulfill()
            }
        })
        model.draft = "Keep this draft"
        let update = Task { await model.setConfigOption("model", value: "new") }
        await fulfillment(of: [started], timeout: 1)
        XCTAssertEqual(model.composerPhase, .updatingOptions)
        XCTAssertFalse(model.canSendMessage)
        XCTAssertFalse(model.canChangeSessionConfiguration)
        await model.send()
        await model.setConfigOption("thinking", value: "high")
        XCTAssertEqual(updateRequests, 1)
        XCTAssertEqual(model.draft, "Keep this draft")
        completion?.resume(returning: ["configOptions": [["id": "model", "currentValue": "new"]]])
        await update.value
        XCTAssertEqual(model.configOptions.first?["currentValue"] as? String, "new")
        XCTAssertEqual(model.composerPhase, .ready)
        XCTAssertTrue(model.canSendMessage)
    }

    func testFailedConfigurationChangeUnlocksComposerAndKeepsDraft() async {
        let model = await readyModel(request: { method, _ in
            if method == "session/set_config_option" { throw ACPError.remote("Option rejected") }
            return [:]
        })
        model.draft = "Keep typing"
        await model.setConfigOption("model", value: "new")
        XCTAssertEqual(model.errorText, "Option rejected")
        XCTAssertFalse(model.isUpdatingSessionConfiguration)
        XCTAssertTrue(model.canSendMessage)
        XCTAssertEqual(model.draft, "Keep typing")
    }

    func testStopRemainsPendingUntilTheRunCompletes() async {
        var cancelledSessions: [String] = []
        let model = await readyModel(notify: { method, params in
            XCTAssertEqual(method, "session/cancel")
            cancelledSessions.append(params["sessionId"] as? String ?? "")
        })
        model.draft = "Next message"
        let run = model.beginRun(for: "chat")
        XCTAssertEqual(model.composerPhase, .working)
        model.cancel()
        XCTAssertEqual(model.composerPhase, .stopping)
        XCTAssertTrue(model.isRunning)
        XCTAssertFalse(model.canSendMessage)
        model.cancel()
        XCTAssertEqual(cancelledSessions, ["chat"])
        model.finishRun(for: "chat", runID: run)
        XCTAssertEqual(model.composerPhase, .ready)
        XCTAssertFalse(model.isStopping)
        XCTAssertEqual(model.draft, "Next message")
    }

    func testPermissionAndStopStateBelongToTheSelectedConversation() async {
        let model = await readyModel(notify: { _, _ in })
        model.draft = "Next message"
        let run = model.beginRun(for: "chat")
        model.permission = PermissionPrompt(
            id: .int(1), title: "Run command", detail: "", options: [],
            method: "session/request_permission", params: ["sessionId": "chat"]
        )
        XCTAssertEqual(model.composerPhase, .waitingForPermission)
        XCTAssertFalse(model.canSendMessage)
        model.selectedSessionID = "other"
        model.cancel()
        XCTAssertNotNil(model.permission)
        model.selectedSessionID = "chat"
        model.permission = nil
        model.cancel()
        XCTAssertEqual(model.composerPhase, .stopping)
        model.finishRun(for: "chat", runID: run)
    }

    func testClosingWindowInvalidatesAnOutstandingConfigurationUpdate() async {
        let started = expectation(description: "Option update started")
        var completion: CheckedContinuation<[String: Any], Error>?
        let model = await readyModel(request: { method, _ in
            guard method == "session/set_config_option" else { return [:] }
            return try await withCheckedThrowingContinuation { continuation in
                completion = continuation
                started.fulfill()
            }
        })
        let update = Task { await model.setConfigOption("model", value: "new") }
        await fulfillment(of: [started], timeout: 1)
        model.closeWindow()
        completion?.resume(returning: ["configOptions": [["id": "model", "currentValue": "new"]]])
        await update.value
        XCTAssertFalse(model.isConnected)
        XCTAssertFalse(model.isUpdatingSessionConfiguration)
        XCTAssertEqual(model.composerPhase, .disconnected)
        XCTAssertTrue(model.configOptions.isEmpty)
    }

    func testCreatingFromExistingConversationHasDistinctStateAndIgnoresDuplicateRequests() async {
        let started = expectation(description: "Creation started")
        var completion: CheckedContinuation<[String: Any], Error>?
        var creations = 0
        let model = await readyModel(request: { method, _ in
            guard method == "session/new" else { return [:] }
            creations += 1
            return try await withCheckedThrowingContinuation { continuation in
                completion = continuation
                started.fulfill()
            }
        })
        model.draft = "Existing conversation draft"
        let creation = Task { await model.newSession() }
        await fulfillment(of: [started], timeout: 1)
        XCTAssertEqual(model.composerPhase, .creatingConversation)
        XCTAssertFalse(model.canSendMessage)
        await model.newSession()
        XCTAssertEqual(creations, 1)
        completion?.resume(returning: ["sessionId": "new"])
        await creation.value
        XCTAssertFalse(model.isCreatingSession)
        XCTAssertEqual(model.composerPhase, .empty)
        model.selectedSessionID = "chat"
        XCTAssertEqual(model.draft, "Existing conversation draft")
    }

    func testFailedStopDoesNotClaimTheRunIsStopping() async {
        let model = await readyModel(notify: { _, _ in throw ACPError.remote("Cancel failed") })
        let run = model.beginRun(for: "chat")
        model.cancel()
        XCTAssertEqual(model.composerPhase, .working)
        XCTAssertFalse(model.isStopping)
        XCTAssertEqual(model.errorText, "Cancel failed")
        model.finishRun(for: "chat", runID: run)
    }
}
