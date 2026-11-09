import XCTest

@testable import ArabicaDesktop

@MainActor
final class ComposerSubmissionTests: XCTestCase {
    func testNavigationDuringCreationDoesNotSubmitAnotherConversationsDraft() async {
        var creation: CheckedContinuation<[String: Any], Error>?
        var sentSessions: [String] = []
        let started = expectation(description: "Conversation creation started")
        let model = DesktopModel(
            restoreWorkspace: false,
            sessionRequest: { method, params in
                if method == "session/new" {
                    return try await withCheckedThrowingContinuation {
                        creation = $0
                        started.fulfill()
                    }
                }
                if method == "session/prompt" { sentSessions.append(params["sessionId"] as? String ?? "") }
                return [:]
            }, sidebarLibrary: .testInstance())
        model.isConnected = true
        model.workspace = URL(fileURLWithPath: "/tmp")
        model.sessions = [DesktopSession(id: "b", cwd: "/tmp", title: "B")]
        model.draft = "Start a new conversation"
        let submit = Task { await model.submitDraft() }
        await fulfillment(of: [started], timeout: 1)
        await model.selectSession("b")
        model.draft = "Unfinished draft in B"
        let attachment = URL(fileURLWithPath: "/tmp/b.txt")
        model.attachFiles([attachment])
        creation?.resume(returning: ["sessionId": "fresh"])
        await submit.value

        XCTAssertTrue(sentSessions.isEmpty)
        XCTAssertEqual(model.selectedSessionID, "b")
        XCTAssertEqual(model.draft, "Unfinished draft in B")
        XCTAssertEqual(model.attachedFiles, [attachment])
        model.activateProject("/tmp")
        XCTAssertEqual(model.draft, "Start a new conversation")
    }

    func testEditingDuringCreationKeepsTheChangedDraftUnsent() async {
        var creation: CheckedContinuation<[String: Any], Error>?
        var promptCount = 0
        let started = expectation(description: "Conversation creation started")
        let model = DesktopModel(
            restoreWorkspace: false,
            sessionRequest: { method, _ in
                if method == "session/new" {
                    return try await withCheckedThrowingContinuation {
                        creation = $0
                        started.fulfill()
                    }
                }
                if method == "session/prompt" { promptCount += 1 }
                return [:]
            }, sidebarLibrary: .testInstance())
        model.isConnected = true
        model.workspace = URL(fileURLWithPath: "/tmp")
        model.draft = "Original message"
        let submit = Task { await model.submitDraft() }
        await fulfillment(of: [started], timeout: 1)
        model.draft = "Still editing this message"
        creation?.resume(returning: ["sessionId": "fresh"])
        await submit.value

        XCTAssertEqual(model.selectedSessionID, "fresh")
        XCTAssertEqual(promptCount, 0)
        XCTAssertEqual(model.draft, "Still editing this message")
    }

    func testQueuedSendCannotFollowNavigationAtRunCompletion() async {
        var sentSessions: [String] = []
        let model = DesktopModel(
            restoreWorkspace: false,
            sessionRequest: { method, params in
                if method == "session/prompt" { sentSessions.append(params["sessionId"] as? String ?? "") }
                return [:]
            }, sidebarLibrary: .testInstance())
        model.isConnected = true
        model.sessions = [
            DesktopSession(id: "a", cwd: "/tmp", title: "A"),
            DesktopSession(id: "b", cwd: "/tmp", title: "B"),
        ]
        await model.selectSession("b")
        model.draft = "Unsent B draft"
        await model.selectSession("a")
        let run = model.beginRun(for: "a")
        model.draft = "Queued A draft"
        await model.submitDraft()
        model.finishRun(for: "a", runID: run)
        await model.selectSession("b")
        for _ in 0..<10 { await Task.yield() }

        XCTAssertTrue(sentSessions.isEmpty)
        XCTAssertEqual(model.draft, "Unsent B draft")
        await model.selectSession("a")
        XCTAssertEqual(model.draft, "Queued A draft")
        XCTAssertFalse(model.isSendQueued)
    }

    func testQueueCanBeCancelledAfterCompletionBeforeDispatch() async {
        var promptCount = 0
        let model = DesktopModel(
            restoreWorkspace: false,
            sessionRequest: { method, _ in
                if method == "session/prompt" { promptCount += 1 }
                return [:]
            }, sidebarLibrary: .testInstance())
        model.isConnected = true
        model.sessions = [DesktopSession(id: "a", cwd: "/tmp", title: "A")]
        await model.selectSession("a")
        let run = model.beginRun(for: "a")
        model.draft = "Keep this draft"
        await model.submitDraft()
        model.finishRun(for: "a", runID: run)
        model.cancelQueuedSend()
        for _ in 0..<10 { await Task.yield() }

        XCTAssertEqual(promptCount, 0)
        XCTAssertEqual(model.draft, "Keep this draft")
        XCTAssertFalse(model.isSendQueued)
    }

    func testNavigatingAwayAndBackCannotReviveAQueuedSend() async {
        var promptCount = 0
        let model = DesktopModel(
            restoreWorkspace: false,
            sessionRequest: { method, _ in
                if method == "session/prompt" { promptCount += 1 }
                return [:]
            }, sidebarLibrary: .testInstance())
        model.isConnected = true
        model.sessions = [DesktopSession(id: "a", cwd: "/tmp", title: "A")]
        await model.selectSession("a")
        let run = model.beginRun(for: "a")
        model.draft = "Keep this draft"
        model.queueDraft()
        model.finishRun(for: "a", runID: run)
        model.selectedSessionID = nil
        model.selectedSessionID = "a"
        for _ in 0..<10 { await Task.yield() }

        XCTAssertEqual(promptCount, 0)
        XCTAssertEqual(model.draft, "Keep this draft")
        XCTAssertFalse(model.isSendQueued)
    }
}
