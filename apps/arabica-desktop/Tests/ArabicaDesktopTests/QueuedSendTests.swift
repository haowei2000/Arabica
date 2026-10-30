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
}
