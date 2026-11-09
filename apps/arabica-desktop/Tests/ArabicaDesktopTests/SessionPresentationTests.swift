import XCTest

@testable import ArabicaDesktop

@MainActor
final class SessionPresentationTests: XCTestCase {
    private func makeModel() -> DesktopModel {
        let model = DesktopModel(
            restoreWorkspace: false, sessionRequest: { _, _ in [:] }, sidebarLibrary: .testInstance())
        model.isConnected = true
        model.sessions = [
            DesktopSession(id: "a", cwd: "/tmp", title: "A"),
            DesktopSession(id: "b", cwd: "/tmp", title: "B"),
        ]
        return model
    }

    private func update(_ model: DesktopModel, session: String, status: String, used: Int, command: String) {
        for payload: [String: Any] in [
            ["sessionUpdate": "plan", "entries": [["content": "Run tests", "status": status]]],
            ["sessionUpdate": "usage_update", "used": used, "size": 1000],
            ["sessionUpdate": "available_commands_update", "availableCommands": [["name": command]]],
        ] {
            model.handleNotification("session/update", params: ["sessionId": session, "update": payload])
        }
    }

    func testReturningToAnOpenConversationRestoresItsPresentation() async {
        let model = makeModel()
        await model.selectSession("a")
        update(model, session: "a", status: "in_progress", used: 12, command: "help")
        await model.selectSession("b")
        XCTAssertTrue(model.plan.isEmpty)
        XCTAssertTrue(model.usage.isEmpty)
        XCTAssertTrue(model.availableCommands.isEmpty)
        update(model, session: "b", status: "pending", used: 24, command: "review")
        await model.selectSession("a")

        XCTAssertEqual(model.plan.first?["status"] as? String, "in_progress")
        XCTAssertEqual(model.usage["used"] as? Int, 12)
        XCTAssertEqual(model.availableCommands.first?["name"] as? String, "help")
        await model.selectSession("b")
        XCTAssertEqual(model.usage["used"] as? Int, 24)
        XCTAssertEqual(model.availableCommands.first?["name"] as? String, "review")
    }

    func testBackgroundUpdatesAreRetainedWithoutChangingTheForeground() async {
        let model = makeModel()
        await model.selectSession("a")
        update(model, session: "a", status: "in_progress", used: 12, command: "help")
        let run = model.beginRun(for: "a")
        await model.selectSession("b")
        update(model, session: "a", status: "completed", used: 99, command: "check")
        model.finishRun(for: "a", runID: run)
        XCTAssertTrue(model.plan.isEmpty)
        XCTAssertTrue(model.usage.isEmpty)
        XCTAssertTrue(model.availableCommands.isEmpty)
        await model.selectSession("a")

        XCTAssertEqual(model.plan.first?["status"] as? String, "completed")
        XCTAssertEqual(model.usage["used"] as? Int, 99)
        XCTAssertEqual(model.availableCommands.first?["name"] as? String, "check")
    }

    func testProjectNavigationRestoresConversationPresentation() async {
        let model = makeModel()
        await model.selectSession("a")
        update(model, session: "a", status: "completed", used: 42, command: "help")
        model.activateProject("/tmp/other-project")
        XCTAssertTrue(model.plan.isEmpty)
        XCTAssertTrue(model.usage.isEmpty)
        XCTAssertTrue(model.availableCommands.isEmpty)
        await model.selectSession("a")

        XCTAssertEqual(model.plan.first?["status"] as? String, "completed")
        XCTAssertEqual(model.usage["used"] as? Int, 42)
        XCTAssertEqual(model.availableCommands.first?["name"] as? String, "help")
    }

    func testClosingAConversationClearsItsPresentationBeforeResume() async {
        let model = makeModel()
        await model.selectSession("a")
        update(model, session: "a", status: "completed", used: 42, command: "help")
        await model.closeSession("a")
        XCTAssertNil(model.selectedSessionID)
        XCTAssertTrue(model.plan.isEmpty)
        XCTAssertTrue(model.usage.isEmpty)
        XCTAssertTrue(model.availableCommands.isEmpty)
        await model.selectSession("a")
        XCTAssertTrue(model.plan.isEmpty)
        XCTAssertTrue(model.usage.isEmpty)
        XCTAssertTrue(model.availableCommands.isEmpty)
    }

    func testCreationKeepsCommandsReportedBeforeTheSessionResponse() async {
        var creation: CheckedContinuation<[String: Any], Error>?
        let started = expectation(description: "Conversation creation started")
        let model = DesktopModel(
            restoreWorkspace: false,
            sessionRequest: { _, _ in
                try await withCheckedThrowingContinuation {
                    creation = $0
                    started.fulfill()
                }
            }, sidebarLibrary: .testInstance())
        model.isConnected = true
        model.workspace = URL(fileURLWithPath: "/tmp")
        let task = Task { await model.newSession() }
        await fulfillment(of: [started], timeout: 1)
        update(model, session: "fresh", status: "pending", used: 0, command: "help")
        creation?.resume(returning: ["sessionId": "fresh"])
        await task.value

        XCTAssertEqual(model.selectedSessionID, "fresh")
        XCTAssertEqual(model.availableCommands.first?["name"] as? String, "help")
    }
}
