import XCTest
@testable import ArabicaDesktop

@MainActor
final class SessionRunStateTests: XCTestCase {
    func testDismissedPermissionIsResolvedAndWaitingIsSessionScoped() {
        let model = DesktopModel(restoreWorkspace: false)
        model.selectedSessionID = "old"
        model.permission = PermissionPrompt(
            id: .integer(1), title: "Run a tool", detail: "", options: [],
            method: "session/request_permission", params: ["sessionId": "old"]
        )
        XCTAssertTrue(model.isWaitingForPermission)
        model.selectedSessionID = "new"
        XCTAssertFalse(model.isWaitingForPermission)
        model.dismissPermission(nil)
        XCTAssertNil(model.permission)
    }

    func testStringRequestIDCreatesPermissionPrompt() {
        let model = DesktopModel(restoreWorkspace: false)
        let id = ACPRequestID("permission-uuid")!
        let client = ACPClient()
        client.onRequest = { id, method, params in
            model.handleRequest(id, method: method, params: params)
        }
        client.route([
            "jsonrpc": "2.0", "id": "permission-uuid", "method": "session/request_permission",
            "params": [
                "sessionId": "old", "toolCall": ["title": "Inspect source"],
                "options": [["optionId": "allow_once", "name": "Allow"]],
            ],
        ])
        XCTAssertEqual(model.permission?.id, .string("permission-uuid"))
        XCTAssertEqual(model.permission?.options.first?.id, "allow_once")
        XCTAssertEqual(id.jsonValue as? String, "permission-uuid")
        XCTAssertEqual(ACPRequestID(42), .integer(42))
    }

    func testWorkingBelongsToSelectedSession() {
        let model = DesktopModel(restoreWorkspace: false)
        model.selectedSessionID = "old"
        let oldRun = model.beginRun(for: "old")
        XCTAssertTrue(model.isRunning)
        model.selectedSessionID = "new"
        XCTAssertFalse(model.isRunning)
        let newRun = model.beginRun(for: "new")
        model.finishRun(for: "old", runID: oldRun)
        XCTAssertTrue(model.isRunning)
        model.finishRun(for: "new", runID: newRun)
        XCTAssertFalse(model.isRunning)
    }

    func testStaleCompletionDoesNotClearReplacementRun() {
        let model = DesktopModel(restoreWorkspace: false)
        model.selectedSessionID = "session"
        let oldRun = model.beginRun(for: "session")
        let newRun = model.beginRun(for: "session")
        model.finishRun(for: "session", runID: oldRun)
        XCTAssertTrue(model.isRunning)
        model.finishRun(for: "session", runID: newRun)
        XCTAssertFalse(model.isRunning)
    }

    func testBackgroundChunksDoNotChangeSelectedConversation() async {
        let model = DesktopModel(restoreWorkspace: false)
        model.workspace = URL(fileURLWithPath: "/tmp")
        model.selectedSessionID = "new"
        model.items = [ChatItem(kind: .user, text: "New conversation")]
        let oldRun = model.beginRun(for: "old")
        model.handleNotification("session/update", params: [
            "sessionId": "old",
            "update": ["sessionUpdate": "agent_message_chunk",
                       "content": ["type": "text", "text": "Background reply"]],
        ])
        model.finishRun(for: "old", runID: oldRun)
        XCTAssertEqual(model.items.map(\.text), ["New conversation"])
        await model.selectSession("old")
        XCTAssertEqual(model.items.map(\.text), ["Background reply"])
    }
}
