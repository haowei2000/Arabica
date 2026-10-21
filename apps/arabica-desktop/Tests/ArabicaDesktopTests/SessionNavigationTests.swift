import XCTest

@testable import ArabicaDesktop

@MainActor
private final class DeferredSessionRequests {
    var onRequest: ((String) -> Void)?
    private(set) var requestCounts: [String: Int] = [:]
    private var pending: [String: CheckedContinuation<[String: Any], Error>] = [:]

    func request(_ method: String, _ params: [String: Any]) async throws -> [String: Any] {
        let key = params["sessionId"] as? String ?? method
        requestCounts[key, default: 0] += 1
        guard pending[key] == nil else { throw ACPError.remote("Conversation is already loading") }
        return try await withCheckedThrowingContinuation { continuation in
            pending[key] = continuation
            onRequest?(key)
        }
    }

    func succeed(_ key: String, result: [String: Any] = [:]) {
        pending.removeValue(forKey: key)?.resume(returning: result)
    }

    func fail(_ key: String) {
        pending.removeValue(forKey: key)?.resume(throwing: ACPError.remote("Load failed"))
    }
}

@MainActor
final class SessionNavigationTests: XCTestCase {
    private func makeModel(_ requests: DeferredSessionRequests) -> DesktopModel {
        let model = DesktopModel(restoreWorkspace: false, sessionRequest: requests.request, sidebarLibrary: .testInstance())
        model.isConnected = true
        model.workspace = URL(fileURLWithPath: "/tmp")
        model.sessions = [
            DesktopSession(id: "first", cwd: "/tmp", title: "First"),
            DesktopSession(id: "second", cwd: "/tmp", title: "Second"),
        ]
        return model
    }

    func testSendingDuringLoadOrAfterFailurePreservesDraft() async {
        let requests = DeferredSessionRequests()
        let model = makeModel(requests)
        let started = expectation(description: "Session load started")
        requests.onRequest = { _ in started.fulfill() }
        let load = Task { await model.selectSession("first") }
        await fulfillment(of: [started], timeout: 1)
        model.draft = "Keep typing"
        model.attachFiles([URL(fileURLWithPath: "/tmp/note.txt")])
        XCTAssertTrue(model.isLoadingSession)
        XCTAssertFalse(model.canSendMessage)
        await model.send()
        XCTAssertEqual(model.draft, "Keep typing")
        XCTAssertEqual(model.attachedFiles.count, 1)
        XCTAssertTrue(model.items.isEmpty)
        requests.fail("first")
        await load.value
        XCTAssertFalse(model.isLoadingSession)
        XCTAssertFalse(model.canSendMessage)
        XCTAssertEqual(model.draft, "Keep typing")
    }

    func testEarlierLoadCannotReplaceSelectionOrClearNewerLoadingState() async {
        let requests = DeferredSessionRequests()
        let model = makeModel(requests)
        let firstStarted = expectation(description: "First load started")
        let secondStarted = expectation(description: "Second load started")
        requests.onRequest = { key in
            if key == "first" { firstStarted.fulfill() } else { secondStarted.fulfill() }
        }
        let first = Task { await model.selectSession("first") }
        await fulfillment(of: [firstStarted], timeout: 1)
        model.draft = "First draft"
        model.handleNotification(
            "session/update",
            params: [
                "sessionId": "first",
                "update": [
                    "sessionUpdate": "agent_message_chunk", "content": ["type": "text", "text": "First reply"],
                ],
            ])
        let second = Task { await model.selectSession("second") }
        await fulfillment(of: [secondStarted], timeout: 1)
        model.draft = "Second draft"
        requests.succeed("first", result: ["modes": ["currentModeId": "first-mode"]])
        await first.value
        XCTAssertEqual(model.selectedSessionID, "second")
        XCTAssertNil(model.currentModeID)
        XCTAssertTrue(model.isLoadingSession)
        XCTAssertFalse(model.canSendMessage)
        requests.succeed("second", result: ["modes": ["currentModeId": "second-mode"]])
        await second.value
        XCTAssertEqual(model.currentModeID, "second-mode")
        XCTAssertFalse(model.isLoadingSession)
        XCTAssertTrue(model.canSendMessage)
        await model.selectSession("first")
        XCTAssertEqual(model.currentModeID, "first-mode")
        XCTAssertEqual(model.draft, "First draft")
        XCTAssertEqual(model.items.map(\.text), ["First reply"])
    }

    func testEarlierFailureDoesNotOverwriteNewerSession() async {
        let requests = DeferredSessionRequests()
        let model = makeModel(requests)
        let firstStarted = expectation(description: "First load started")
        let secondStarted = expectation(description: "Second load started")
        requests.onRequest = { key in
            if key == "first" { firstStarted.fulfill() } else { secondStarted.fulfill() }
        }
        let first = Task { await model.selectSession("first") }
        await fulfillment(of: [firstStarted], timeout: 1)
        let second = Task { await model.selectSession("second") }
        await fulfillment(of: [secondStarted], timeout: 1)
        requests.succeed("second")
        await second.value
        requests.fail("first")
        await first.value
        XCTAssertEqual(model.selectedSessionID, "second")
        XCTAssertNil(model.errorText)
        XCTAssertFalse(model.isLoadingSession)
    }

    func testReturningToPendingConversationReusesItsLoadAndRestoresControls() async {
        let requests = DeferredSessionRequests()
        let model = makeModel(requests)
        let firstStarted = expectation(description: "First load started")
        let secondStarted = expectation(description: "Second load started")
        requests.onRequest = { key in
            if key == "first" { firstStarted.fulfill() } else { secondStarted.fulfill() }
        }
        let first = Task { await model.selectSession("first") }
        await fulfillment(of: [firstStarted], timeout: 1)
        model.draft = "First draft"
        let second = Task { await model.selectSession("second") }
        await fulfillment(of: [secondStarted], timeout: 1)
        model.draft = "Second draft"

        await model.selectSession("first")
        XCTAssertEqual(requests.requestCounts["first"], 1)
        XCTAssertEqual(model.selectedSessionID, "first")
        XCTAssertEqual(model.draft, "First draft")
        XCTAssertTrue(model.isLoadingSession)
        XCTAssertFalse(model.isSessionReady)

        requests.succeed("second", result: ["modes": ["currentModeId": "second-mode"]])
        await second.value
        XCTAssertTrue(model.isLoadingSession)
        XCTAssertNil(model.currentModeID)
        requests.succeed(
            "first",
            result: [
                "modes": ["currentModeId": "first-mode"],
                "configOptions": [["id": "blend.policy", "currentValue": "soe"]],
            ])
        await first.value
        XCTAssertEqual(model.selectedSessionID, "first")
        XCTAssertEqual(model.currentModeID, "first-mode")
        XCTAssertEqual(model.configOptions.first?["currentValue"] as? String, "soe")
        XCTAssertTrue(model.isSessionReady)
        XCTAssertTrue(model.canSendMessage)
        XCTAssertFalse(model.isLoadingSession)
        XCTAssertNil(model.errorText)
        await model.selectSession("second")
        XCTAssertEqual(requests.requestCounts["second"], 1)
        XCTAssertEqual(model.draft, "Second draft")
        XCTAssertEqual(model.currentModeID, "second-mode")
    }

    func testReturningToPendingConversationFailureCanBeRetried() async {
        let requests = DeferredSessionRequests()
        let model = makeModel(requests)
        let firstStarted = expectation(description: "First load started")
        let secondStarted = expectation(description: "Second load started")
        requests.onRequest = { key in
            if key == "first" { firstStarted.fulfill() } else { secondStarted.fulfill() }
        }
        let first = Task { await model.selectSession("first") }
        await fulfillment(of: [firstStarted], timeout: 1)
        let second = Task { await model.selectSession("second") }
        await fulfillment(of: [secondStarted], timeout: 1)
        await model.selectSession("first")
        requests.fail("first")
        await first.value
        XCTAssertEqual(model.selectedSessionID, "first")
        XCTAssertEqual(model.sessionStatus("first"), .loadFailed)
        XCTAssertFalse(model.isLoadingSession)
        XCTAssertFalse(model.isSessionReady)

        let retryStarted = expectation(description: "Retry started")
        requests.onRequest = { _ in retryStarted.fulfill() }
        let retry = Task { await model.selectSession("first") }
        await fulfillment(of: [retryStarted], timeout: 1)
        XCTAssertEqual(requests.requestCounts["first"], 2)
        XCTAssertTrue(model.isLoadingSession)
        requests.succeed("first", result: ["modes": ["currentModeId": "retried-mode"]])
        await retry.value
        requests.succeed("second")
        await second.value
        XCTAssertEqual(model.selectedSessionID, "first")
        XCTAssertEqual(model.currentModeID, "retried-mode")
        XCTAssertTrue(model.isSessionReady)
        XCTAssertFalse(model.isLoadingSession)
        XCTAssertNil(model.errorText)
    }

    func testPendingLoadCompletingAfterWorkspaceSelectionKeepsWorkspaceSelected() async {
        let requests = DeferredSessionRequests()
        let model = makeModel(requests)
        let started = expectation(description: "Load started")
        requests.onRequest = { _ in started.fulfill() }
        let load = Task { await model.selectSession("first") }
        await fulfillment(of: [started], timeout: 1)
        model.activateProject("/tmp/another-project")
        requests.succeed("first", result: ["modes": ["currentModeId": "first-mode"]])
        await load.value
        XCTAssertNil(model.selectedSessionID)
        XCTAssertEqual(model.workspace?.path, "/tmp/another-project")
        XCTAssertNil(model.currentModeID)
        XCTAssertFalse(model.isLoadingSession)
        XCTAssertFalse(model.isSessionReady)
        await model.selectSession("first")
        XCTAssertEqual(requests.requestCounts["first"], 1)
        XCTAssertEqual(model.currentModeID, "first-mode")
        XCTAssertTrue(model.isSessionReady)
    }

    func testPendingLoadCompletingAfterWindowCloseCannotRestoreControls() async {
        let requests = DeferredSessionRequests()
        let model = makeModel(requests)
        let started = expectation(description: "Load started")
        requests.onRequest = { _ in started.fulfill() }
        let load = Task { await model.selectSession("first") }
        await fulfillment(of: [started], timeout: 1)
        model.closeWindow()
        requests.succeed("first", result: ["modes": ["currentModeId": "first-mode"]])
        await load.value
        XCTAssertFalse(model.isConnected)
        XCTAssertFalse(model.isSessionOpen("first"))
        XCTAssertFalse(model.isLoadingSession)
        XCTAssertFalse(model.isSessionReady)
        XCTAssertNil(model.currentModeID)
    }

    func testNewConversationTransfersEditsMadeWhileRequestWasPending() async {
        let requests = DeferredSessionRequests()
        let model = makeModel(requests)
        model.draft = "Initial draft"
        let started = expectation(description: "Creation started")
        requests.onRequest = { _ in started.fulfill() }
        let creation = Task { await model.newSession() }
        await fulfillment(of: [started], timeout: 1)
        model.draft = "Latest draft"
        model.attachFiles([URL(fileURLWithPath: "/tmp/latest.txt")])
        requests.succeed("session/new", result: ["sessionId": "created"])
        await creation.value
        XCTAssertEqual(model.selectedSessionID, "created")
        XCTAssertEqual(model.draft, "Latest draft")
        XCTAssertEqual(model.attachedFiles.first?.lastPathComponent, "latest.txt")
        XCTAssertTrue(model.canSendMessage)
    }

    func testCreationFinishingInBackgroundDoesNotReplaceSelectedSession() async {
        let requests = DeferredSessionRequests()
        let model = makeModel(requests)
        let creationStarted = expectation(description: "Creation started")
        let selectionStarted = expectation(description: "Selection started")
        requests.onRequest = { key in
            if key == "session/new" { creationStarted.fulfill() } else { selectionStarted.fulfill() }
        }
        let creation = Task { await model.newSession() }
        await fulfillment(of: [creationStarted], timeout: 1)
        let selection = Task { await model.selectSession("second") }
        await fulfillment(of: [selectionStarted], timeout: 1)
        requests.succeed("second", result: ["modes": ["currentModeId": "second-mode"]])
        await selection.value
        model.draft = "Second draft"
        requests.succeed("session/new", result: ["sessionId": "created", "modes": ["currentModeId": "created-mode"]])
        await creation.value
        XCTAssertEqual(model.selectedSessionID, "second")
        XCTAssertEqual(model.currentModeID, "second-mode")
        XCTAssertEqual(model.draft, "Second draft")
        XCTAssertTrue(model.sessions.contains(where: { $0.id == "created" }))
        XCTAssertFalse(model.isLoadingSession)
    }
}
