import XCTest

@testable import ArabicaDesktop

@MainActor
final class ComposerConfigurationTests: XCTestCase {
    private func options(model: String = "fast", policy: String = "coding", thinking: String = "off") -> [[String: Any]]
    {
        [
            [
                "id": "model", "name": "Model", "type": "select", "category": "model", "currentValue": model,
                "options": [["value": "fast", "name": "Fast"], ["value": "strong", "name": "Strong"]],
            ],
            [
                "id": "blend.policy", "name": "Policy", "type": "select", "category": "model_config",
                "currentValue": policy,
                "options": [["value": "coding", "name": "Coding"], ["value": "review", "name": "Review"]],
            ],
            [
                "id": "thinking", "name": "Thinking", "type": "select", "category": "thought_level",
                "currentValue": thinking,
                "options": [["value": "off", "name": "Off"], ["value": "high", "name": "High"]],
            ],
        ]
    }

    private func readyModel(request: @escaping DesktopModel.SessionRequest) async -> DesktopModel {
        let model = DesktopModel(restoreWorkspace: false, sessionRequest: request, sidebarLibrary: .testInstance())
        model.isConnected = true
        model.sessions = [
            DesktopSession(id: "first", cwd: "/tmp", title: "First"),
            DesktopSession(id: "second", cwd: "/tmp", title: "Second"),
        ]
        await model.selectSession("first")
        return model
    }

    func testEverySelectorDisplaysTheConfirmedNameAfterEachFullResponse() async {
        var values = options()
        let model = await readyModel { method, params in
            if method == "session/set_config_option" {
                let optionID = params["configId"] as? String
                let index = values.firstIndex { $0["id"] as? String == optionID }!
                values[index]["currentValue"] = params["value"]
            }
            return ["configOptions": values]
        }
        for (id, value, name) in [
            ("model", "strong", "Strong"), ("blend.policy", "review", "Review"), ("thinking", "high", "High"),
        ] {
            await model.setConfigOption(id, value: value)
            XCTAssertEqual(model.composerConfigurationOptions.first { $0.id == id }?.currentName, name)
            XCTAssertEqual(model.composerConfigurationFeedback?.kind, .applied)
            XCTAssertTrue(model.composerConfigurationFeedback?.title.contains(name) == true)
        }
        XCTAssertEqual(model.composerConfigurationOptions.map(\.currentName), ["Strong", "Review", "High"])
        await model.selectSession("second")
        await model.selectSession("first")
        XCTAssertEqual(model.composerConfigurationOptions.map(\.currentName), ["Strong", "Review", "High"])
    }

    func testFullResponseUpdatesDependentControlsAndRemovesUnavailableThinking() async {
        let initial = options()
        let changed = Array(options(model: "strong", policy: "review").prefix(2))
        let model = await readyModel { method, _ in
            ["configOptions": method == "session/set_config_option" ? changed : initial]
        }
        await model.setConfigOption("blend.policy", value: "review")
        XCTAssertEqual(model.composerConfigurationOptions.map(\.currentName), ["Strong", "Review"])
        XCTAssertEqual(model.composerConfigurationFeedback?.kind, .applied)
    }

    func testGroupedACPChoicesResolveTheSelectedNameAndRemainSelectable() {
        let option = ComposerConfigurationOption([
            "id": "model", "name": "Model", "type": "select", "currentValue": "strong",
            "options": [
                ["group": "quick", "name": "Quick", "options": [["value": "fast", "name": "Fast"]]],
                ["group": "deep", "name": "Deep", "options": [["value": "strong", "name": "Strong"]]],
            ],
        ])
        XCTAssertEqual(option?.currentName, "Strong")
        XCTAssertEqual(option?.choices.map(\.value), ["fast", "strong"])
        XCTAssertEqual(option?.choices.map(\.group), ["Quick", "Deep"])
    }

    func testUnknownCurrentValueRemainsVisibleWithoutInventingASelection() {
        var raw = options()[0]
        raw["currentValue"] = "new-agent-model"
        XCTAssertEqual(ComposerConfigurationOption(raw)?.currentName, "new-agent-model")
    }

    func testFailedChangeKeepsCommittedValuesAndShowsFailureBesideComposer() async {
        let initial = options()
        let model = await readyModel { method, _ in
            if method == "session/set_config_option" { throw ACPError.remote("Model unavailable") }
            return ["configOptions": initial]
        }
        model.draft = "Keep this draft"
        await model.setConfigOption("model", value: "strong")
        XCTAssertEqual(model.composerConfigurationOptions.first?.currentName, "Fast")
        XCTAssertEqual(model.composerConfigurationFeedback?.kind, .failed)
        XCTAssertEqual(model.composerConfigurationFeedback?.title, "Model change failed")
        XCTAssertEqual(model.composerConfigurationFeedback?.detail, "Model unavailable")
        XCTAssertEqual(model.draft, "Keep this draft")
        XCTAssertTrue(model.canSendMessage)
    }

    func testMissingOrUnconfirmedResponseCannotSilentlyClaimSuccess() async {
        for response in [[:], ["configOptions": options()]] {
            let initial = options()
            let model = await readyModel { method, _ in
                method == "session/set_config_option" ? response : ["configOptions": initial]
            }
            await model.setConfigOption("model", value: "strong")
            XCTAssertEqual(model.composerConfigurationOptions.first?.currentName, "Fast")
            XCTAssertEqual(model.composerConfigurationFeedback?.kind, .failed)
            XCTAssertNotNil(model.errorText)
            XCTAssertFalse(model.isUpdatingSessionConfiguration)
        }
    }

    func testReturningToConversationBeforeConfirmationRefreshesVisibleSelection() async {
        let started = expectation(description: "Change started")
        var completion: CheckedContinuation<[String: Any], Error>?
        let initial = options()
        let model = await readyModel { method, _ in
            if method == "session/set_config_option" {
                return try await withCheckedThrowingContinuation {
                    completion = $0
                    started.fulfill()
                }
            }
            return ["configOptions": initial]
        }
        let update = Task { await model.setConfigOption("model", value: "strong") }
        await fulfillment(of: [started], timeout: 1)
        XCTAssertEqual(model.composerConfigurationFeedback?.title, "Applying Model: Strong…")
        XCTAssertEqual(model.composerConfigurationOptions.first?.currentName, "Fast")
        await model.selectSession("second")
        await model.selectSession("first")
        completion?.resume(returning: ["configOptions": options(model: "strong")])
        await update.value
        XCTAssertEqual(model.composerConfigurationOptions.first?.currentName, "Strong")
        XCTAssertEqual(model.composerConfigurationFeedback?.kind, .applied)
    }

    func testBackgroundControlUpdatesRestoreWhenConversationIsSelected() async {
        let initial = options()
        let model = await readyModel { _, _ in ["configOptions": initial] }
        await model.selectSession("second")
        model.handleNotification(
            "session/update",
            params: [
                "sessionId": "first",
                "update": ["sessionUpdate": "config_option_update", "configOptions": options(thinking: "high")],
            ])
        model.handleNotification(
            "session/update",
            params: [
                "sessionId": "first", "update": ["sessionUpdate": "current_mode_update", "currentModeId": "plan"],
            ])
        XCTAssertEqual(model.composerConfigurationOptions.last?.currentName, "Off")
        await model.selectSession("first")
        XCTAssertEqual(model.composerConfigurationOptions.last?.currentName, "High")
        XCTAssertEqual(model.currentModeID, "plan")
    }

    func testSelectingCommittedValueDoesNotSendDuplicateRequest() async {
        var changes = 0
        let initial = options()
        let model = await readyModel { method, _ in
            if method == "session/set_config_option" { changes += 1 }
            return ["configOptions": initial]
        }
        await model.setConfigOption("model", value: "fast")
        XCTAssertEqual(changes, 0)
        XCTAssertNil(model.composerConfigurationFeedback)
    }

    func testBusyConfigurationMustFinishBeforeConversationCanClose() async {
        let started = expectation(description: "Change started")
        var completion: CheckedContinuation<[String: Any], Error>?
        let initial = options()
        let model = await readyModel { method, _ in
            if method == "session/set_config_option" {
                return try await withCheckedThrowingContinuation {
                    completion = $0
                    started.fulfill()
                }
            }
            return ["configOptions": initial]
        }
        let update = Task { await model.setConfigOption("model", value: "strong") }
        await fulfillment(of: [started], timeout: 1)
        XCTAssertFalse(model.canCloseSession("first"))
        await model.closeSession("first")
        XCTAssertTrue(model.isSessionOpen("first"))
        XCTAssertEqual(model.selectedSessionID, "first")
        completion?.resume(returning: ["configOptions": options(model: "strong")])
        await update.value
        XCTAssertEqual(model.composerConfigurationOptions.first?.currentName, "Strong")
        XCTAssertTrue(model.canCloseSession("first"))
        await model.closeSession("first")
        XCTAssertFalse(model.isSessionOpen("first"))
        XCTAssertNil(model.selectedSessionID)
        await model.selectSession("first")
        XCTAssertEqual(model.composerConfigurationOptions.first?.currentName, "Fast")
        XCTAssertNil(model.composerConfigurationFeedback)
    }
}
