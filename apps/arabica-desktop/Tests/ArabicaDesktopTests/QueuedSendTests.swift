import XCTest

@testable import ArabicaDesktop

@MainActor
final class QueuedSendTests: XCTestCase {
    func testReturnDuringRunQueuesTheDraft() async {
        let model = DesktopModel(restoreWorkspace: false, sidebarLibrary: .testInstance())
        model.selectedSessionID = "session"
        model.beginRun(for: "session")
        model.draft = "next step"
        await model.submitDraft()
        XCTAssertTrue(model.isSendQueued)
        XCTAssertEqual(model.draft, "next step", "a queued draft stays editable")
    }

    func testEmptyDraftOrSwitchingConversationsCancelsTheQueue() async {
        let model = DesktopModel(restoreWorkspace: false, sidebarLibrary: .testInstance())
        model.selectedSessionID = "session"
        model.beginRun(for: "session")
        model.draft = "next step"
        await model.submitDraft()
        model.draft = "  "
        XCTAssertFalse(model.isSendQueued)

        model.draft = "next step"
        await model.submitDraft()
        model.selectedSessionID = "other"
        XCTAssertFalse(model.isSendQueued)
    }

    func testFailedOrStoppedRunDoesNotSendTheQueuedDraft() async {
        let model = DesktopModel(restoreWorkspace: false, sidebarLibrary: .testInstance())
        model.selectedSessionID = "session"
        let run = model.beginRun(for: "session")
        model.draft = "next step"
        await model.submitDraft()
        model.finishRun(for: "session", runID: run, outcome: .failed, error: "boom")
        XCTAssertFalse(model.isSendQueued)
        XCTAssertEqual(model.draft, "next step")
    }

    func testDraftWithoutConversationWaitsForAConnection() async {
        let model = DesktopModel(restoreWorkspace: false, sidebarLibrary: .testInstance())
        model.draft = "hello"
        await model.submitDraft()
        XCTAssertNil(model.selectedSessionID)
        XCTAssertEqual(model.draft, "hello", "the draft is kept until a conversation can be created")
    }

    func testQueueButtonRequiresADraftAndAnActiveRun() {
        let model = DesktopModel(restoreWorkspace: false, sidebarLibrary: .testInstance())
        model.selectedSessionID = "session"
        model.draft = "next step"
        XCTAssertFalse(model.canQueueMessage)
        model.queueDraft()
        XCTAssertFalse(model.isSendQueued)

        model.beginRun(for: "session")
        XCTAssertTrue(model.canQueueMessage)
        model.queueDraft()
        XCTAssertTrue(model.isSendQueued)
        XCTAssertFalse(model.canQueueMessage)
        XCTAssertEqual(model.draft, "next step")
        model.cancelQueuedSend()
        XCTAssertTrue(model.canQueueMessage)
        model.draft = " \n "
        XCTAssertFalse(model.canQueueMessage)
    }

    func testRemovingTheLastAttachmentCancelsAnAttachmentOnlyQueue() {
        let model = DesktopModel(restoreWorkspace: false, sidebarLibrary: .testInstance())
        model.selectedSessionID = "session"
        model.beginRun(for: "session")
        model.attachFiles([URL(fileURLWithPath: "/tmp/note.txt")])
        XCTAssertTrue(model.canQueueMessage)
        model.queueDraft()
        XCTAssertTrue(model.isSendQueued)
        model.attachedFiles = []
        XCTAssertFalse(model.isSendQueued)
        XCTAssertFalse(model.canQueueMessage)
    }

    func testStoppingCancelsTheQueueEvenIfTheHostReportsCompletion() async {
        var promptCount = 0
        let model = DesktopModel(
            restoreWorkspace: false,
            sessionRequest: { method, _ in
                if method == "session/prompt" { promptCount += 1 }
                return [:]
            }, sessionNotification: { _, _ in }, sidebarLibrary: .testInstance())
        model.isConnected = true
        model.sessions = [DesktopSession(id: "session", cwd: "/tmp", title: "Session")]
        await model.selectSession("session")
        let run = model.beginRun(for: "session")
        model.draft = "next step"
        model.queueDraft()
        model.cancel()
        XCTAssertTrue(model.isStopping)
        XCTAssertFalse(model.canQueueMessage)
        XCTAssertFalse(model.isSendQueued)
        model.finishRun(for: "session", runID: run)
        for _ in 0..<10 { await Task.yield() }
        XCTAssertEqual(promptCount, 0)
        XCTAssertEqual(model.draft, "next step")
    }
}
