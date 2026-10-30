import XCTest

@testable import ArabicaDesktop

@MainActor
final class ComposerMessageActionsTests: XCTestCase {
    private struct PromptFailure: Error {}

    private func promptText(_ params: [String: Any]) -> String? {
        (params["prompt"] as? [[String: Any]])?.first?["text"] as? String
    }

    func testRetryResendsTheFailedPromptWithoutTouchingTheDraft() async {
        var prompts: [String] = []
        var failNext = true
        let model = DesktopModel(
            restoreWorkspace: false,
            sessionRequest: { method, params in
                if method == "session/prompt" {
                    prompts.append(self.promptText(params) ?? "")
                    if failNext {
                        failNext = false
                        throw PromptFailure()
                    }
                }
                return [:]
            },
            sidebarLibrary: .testInstance())
        model.isConnected = true
        model.sessions = [DesktopSession(id: "a", cwd: "/tmp", title: "A")]
        await model.selectSession("a")

        model.draft = "run the tests"
        await model.send()
        XCTAssertTrue(model.canRetryLastPrompt)

        model.draft = "unrelated draft"
        await model.retryLastPrompt()
        XCTAssertEqual(prompts, ["run the tests", "run the tests"])
        XCTAssertEqual(model.draft, "unrelated draft")
        XCTAssertFalse(model.canRetryLastPrompt, "a successful retry clears the failure")
    }

    func testReturnWithoutConversationCreatesOneAndSends() async {
        var methods: [String] = []
        var sent: String?
        let model = DesktopModel(
            restoreWorkspace: false,
            sessionRequest: { method, params in
                methods.append(method)
                if method == "session/new" { return ["sessionId": "fresh"] }
                if method == "session/prompt" { sent = self.promptText(params) }
                return [:]
            },
            sidebarLibrary: .testInstance())
        model.isConnected = true
        model.workspace = URL(fileURLWithPath: "/tmp")
        model.draft = "hello"
        await model.submitDraft()
        XCTAssertEqual(model.selectedSessionID, "fresh")
        XCTAssertEqual(methods, ["session/new", "session/prompt"])
        XCTAssertEqual(sent, "hello")
        XCTAssertEqual(model.draft, "")
    }

    func testQueuedDraftSendsWhenTheRunCompletes() async throws {
        var prompts: [String] = []
        let model = DesktopModel(
            restoreWorkspace: false,
            sessionRequest: { method, params in
                if method == "session/prompt" {
                    prompts.append(self.promptText(params) ?? "")
                    try await Task.sleep(for: .milliseconds(100))
                }
                return [:]
            },
            sidebarLibrary: .testInstance())
        model.isConnected = true
        model.sessions = [DesktopSession(id: "a", cwd: "/tmp", title: "A")]
        await model.selectSession("a")

        model.draft = "first"
        let firstRun = Task { await model.send() }
        while !model.isRunning { await Task.yield() }
        model.draft = "second"
        await model.submitDraft()
        XCTAssertTrue(model.isSendQueued)
        await firstRun.value
        for _ in 0..<100 where prompts.count < 2 { try await Task.sleep(for: .milliseconds(10)) }
        XCTAssertEqual(prompts, ["first", "second"])
        XCTAssertFalse(model.isSendQueued)
    }

    func testEditAsNewMessageKeepsAnExistingDraft() {
        let model = DesktopModel(restoreWorkspace: false, sidebarLibrary: .testInstance())
        model.editAsNewMessage("fix the bug")
        XCTAssertEqual(model.draft, "fix the bug")
        model.draft = "first"
        model.editAsNewMessage("second")
        XCTAssertEqual(model.draft, "first\n\nsecond")
    }
}
