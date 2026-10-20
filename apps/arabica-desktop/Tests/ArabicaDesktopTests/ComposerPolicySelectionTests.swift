import XCTest

@testable import ArabicaDesktop

final class ComposerPolicySelectionTests: XCTestCase {
    private func option(_ id: String, current: String, choices: [(String, String)]) -> ComposerConfigurationOption {
        ComposerConfigurationOption([
            "id": id,
            "currentValue": current,
            "options": choices.map { ["value": $0.0, "name": $0.1] },
        ])!
    }

    private var model: ComposerConfigurationOption {
        option("model", current: "fast", choices: [("fast", "Fast"), ("strong", "Strong")])
    }

    private var thinking: ComposerConfigurationOption {
        option("thinking", current: "high", choices: [("low", "Low"), ("high", "High")])
    }

    private func policy(_ current: String) -> ComposerConfigurationOption {
        option("blend.policy", current: current, choices: [("soe", "SOE"), ("coding", "Coding")])
    }

    func testNamedPolicyUsesItsConfirmedNameAndHidesModelAndThinkingControls() {
        let selection = ComposerPolicySelection(options: [model, policy("coding"), thinking])

        XCTAssertEqual(selection.currentName, "Coding")
        XCTAssertFalse(selection.isSOE)
        XCTAssertNil(selection.model)
        XCTAssertNil(selection.thinking)
        XCTAssertTrue(selection.help.contains("configuration file"))
    }

    func testSOEScopesModelAndThinkingSettingsInsideTheSelectedPolicy() {
        let selection = ComposerPolicySelection(options: [model, policy("soe"), thinking])

        XCTAssertEqual(selection.currentName, "SOE · Fast")
        XCTAssertTrue(selection.isSOE)
        XCTAssertEqual(selection.model?.currentValue, "fast")
        XCTAssertEqual(selection.thinking?.currentValue, "high")
        XCTAssertTrue(selection.help.contains("Thinking: High"))
    }

    func testOlderSingleModelRuntimeWithoutPolicyOptionStillPresentsSOE() {
        let selection = ComposerPolicySelection(options: [model, thinking])

        XCTAssertEqual(selection.currentName, "SOE · Fast")
        XCTAssertTrue(selection.hasOptions)
        XCTAssertNil(selection.policy)
        XCTAssertEqual(selection.model, model)
        XCTAssertEqual(selection.thinking, thinking)
    }

    func testUnknownPolicyRemainsVisibleWithoutExposingSOEOverrides() {
        let selection = ComposerPolicySelection(options: [model, policy("new-policy"), thinking])

        XCTAssertEqual(selection.currentName, "new-policy")
        XCTAssertFalse(selection.isSOE)
        XCTAssertNil(selection.model)
        XCTAssertNil(selection.thinking)
    }

    func testSOEWithUnsupportedThinkingDoesNotInventAThinkingControl() {
        let selection = ComposerPolicySelection(options: [model, policy("soe")])

        XCTAssertEqual(selection.currentName, "SOE · Fast")
        XCTAssertNil(selection.thinking)
        XCTAssertFalse(selection.help.contains("Thinking:"))
    }

    func testNoLoadedConfigurationDoesNotInventAnAvailablePolicy() {
        let selection = ComposerPolicySelection(options: [])

        XCTAssertFalse(selection.hasOptions)
        XCTAssertNil(selection.policy)
        XCTAssertNil(selection.model)
        XCTAssertNil(selection.thinking)
    }

    func testComposerRecoveryActionsRemainAvailableWithoutAStatusBanner() {
        XCTAssertEqual(ComposerPhase.chooseWorkspace.primaryAction, .openWorkspace)
        XCTAssertEqual(ComposerPhase.disconnected.primaryAction, .reconnect)
        XCTAssertEqual(ComposerPhase.createConversation.primaryAction, .createConversation)
        XCTAssertEqual(ComposerPhase.conversationUnavailable.primaryAction, .reloadConversation)

        for phase in [
            ComposerPhase.connecting, .creatingConversation, .loadingConversation, .updatingOptions,
            .waitingForPermission, .empty, .ready,
        ] {
            XCTAssertEqual(phase.primaryAction, .send)
        }
    }
}
