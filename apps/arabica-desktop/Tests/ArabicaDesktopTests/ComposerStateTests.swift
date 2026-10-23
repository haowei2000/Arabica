import XCTest

@testable import ArabicaDesktop

@MainActor
final class ComposerStateTests: XCTestCase {
    func testSwitchingConversationsRestoresEachDraftAndAttachments() {
        let model = DesktopModel(restoreWorkspace: false, sidebarLibrary: .testInstance())
        let file = URL(fileURLWithPath: "/tmp/draft.txt")
        model.selectedSessionID = "first"
        model.draft = "First draft"
        model.attachFiles([file])
        model.selectedSessionID = "second"
        XCTAssertEqual(model.draft, "")
        XCTAssertTrue(model.attachedFiles.isEmpty)
        model.draft = "Second draft"
        model.selectedSessionID = "first"
        XCTAssertEqual(model.draft, "First draft")
        XCTAssertEqual(model.attachedFiles, [file])
        model.selectedSessionID = "second"
        XCTAssertEqual(model.draft, "Second draft")
        XCTAssertTrue(model.attachedFiles.isEmpty)
    }

    func testSelectingSameConversationKeepsCurrentDraft() {
        let model = DesktopModel(restoreWorkspace: false, sidebarLibrary: .testInstance())
        model.selectedSessionID = "first"
        model.draft = "Keep typing"
        model.selectedSessionID = "first"
        XCTAssertEqual(model.draft, "Keep typing")
    }

    func testDisconnectedSubmitDoesNotConsumeDraftOrAttachments() async {
        let model = DesktopModel(restoreWorkspace: false, sidebarLibrary: .testInstance())
        let file = URL(fileURLWithPath: "/tmp/draft.txt")
        model.selectedSessionID = "first"
        model.draft = "Keep this message"
        model.attachFiles([file])
        XCTAssertFalse(model.canSendMessage)
        await model.send()
        XCTAssertEqual(model.draft, "Keep this message")
        XCTAssertEqual(model.attachedFiles, [file])
        XCTAssertTrue(model.items.isEmpty)
        XCTAssertFalse(model.isRunning)
    }

    func testSendAvailabilityIncludesConnectionAndRunState() async {
        let model = DesktopModel(restoreWorkspace: false, sessionRequest: { _, _ in [:] }, sidebarLibrary: .testInstance())
        model.sessions = [DesktopSession(id: "first", cwd: "/tmp", title: "First")]
        await model.selectSession("first")
        model.draft = "Hello"
        model.isConnected = true
        XCTAssertTrue(model.canSendMessage)
        model.isConnecting = true
        XCTAssertFalse(model.canSendMessage)
        model.isConnecting = false
        let run = model.beginRun(for: "first")
        XCTAssertFalse(model.canSendMessage)
        model.finishRun(for: "first", runID: run)
        model.draft = " \n "
        XCTAssertFalse(model.canSendMessage)
        model.attachFiles([URL(fileURLWithPath: "/tmp/draft.txt")])
        XCTAssertTrue(model.canSendMessage)
    }

    func testAttachmentsAreDeduplicatedByStandardizedPath() {
        let model = DesktopModel(restoreWorkspace: false, sidebarLibrary: .testInstance())
        let file = URL(fileURLWithPath: "/tmp/draft.txt")
        model.attachFiles([file, file])
        model.attachFiles([URL(fileURLWithPath: "/tmp/other/../draft.txt")])
        XCTAssertEqual(model.attachedFiles, [file])
    }

    func testProjectsWithSameNameKeepDistinctPathsAndSessions() {
        let model = DesktopModel(restoreWorkspace: false, sidebarLibrary: .testInstance())
        model.workspace = URL(fileURLWithPath: "/tmp/second/demo")
        model.sessions = [
            DesktopSession(id: "first", cwd: "/tmp/first/demo", title: "First"),
            DesktopSession(id: "second", cwd: "/tmp/second/demo", title: "Second"),
        ]
        let groups = model.projectGroups
        XCTAssertEqual(groups.count, 2)
        XCTAssertEqual(Set(groups.map(\.id)).count, 2)
        XCTAssertEqual(groups.first?.path, "/tmp/second/demo")
        XCTAssertEqual(groups.first?.sessions.map(\.id), ["second"])
    }
}
