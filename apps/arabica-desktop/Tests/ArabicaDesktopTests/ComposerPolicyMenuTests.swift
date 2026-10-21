import AppKit
import XCTest

@testable import ArabicaDesktop

@MainActor
final class ComposerPolicyMenuTests: XCTestCase {
    func testNativeModelItemDispatchesAfterSwitchingFromNamedPolicyToSOE() throws {
        let fixture = Fixture(policy: "coding")
        let menu = fixture.coordinator.menu
        XCTAssertTrue(fixture.button.isEnabled)
        XCTAssertTrue(menu.items[0].isEnabled)
        XCTAssertNil(menu.items.first { $0.title.hasPrefix("SOE Model:") })
        try activate("SOE", in: menu)
        XCTAssertEqual(fixture.actions, [.configuration(id: "blend.policy", value: "soe")])

        fixture.refresh(policy: "soe")
        XCTAssertTrue(fixture.coordinator.menu === menu)
        let models = try submenu("SOE Model:", in: menu)
        XCTAssertFalse(models.autoenablesItems)
        XCTAssertTrue(try item("Strong", in: models).isEnabled)
        XCTAssertEqual(try item("Fast", in: models).state, .on)
        try activate("Strong", in: models)
        XCTAssertEqual(fixture.actions.last, .configuration(id: "model", value: "strong"))
        XCTAssertEqual(fixture.button.title, "Policy: SOE · Fast")

        fixture.refresh(policy: "soe", model: "strong")
        XCTAssertEqual(fixture.button.title, "Policy: SOE · Strong")
        XCTAssertEqual(fixture.button.accessibilityLabel(), "Policy: SOE · Strong")
        XCTAssertEqual(try item("Strong", in: submenu("SOE Model:", in: menu)).state, .on)
    }

    func testNativeThinkingAndSessionModeItemsDispatchTheirOwnIdentifiers() throws {
        let fixture = Fixture()
        let models = try submenu("SOE Model:", in: fixture.coordinator.menu)
        let thinking = try submenu("Thinking Level:", in: models)
        try activate("High", in: thinking)
        let modes = try submenu("Session Mode", in: fixture.coordinator.menu)
        try activate("Edit", in: modes)
        XCTAssertEqual(
            fixture.actions,
            [.configuration(id: "thinking", value: "high"), .mode("edit")])
        XCTAssertEqual(try item("Low", in: thinking).state, .on)
        XCTAssertEqual(try item("Plan", in: modes).state, .on)
        XCTAssertEqual(fixture.button.title, "Policy: SOE · Fast")
    }

    func testDisabledControlAndStaleEnabledItemCannotDispatch() throws {
        let fixture = Fixture()
        let oldModels = try submenu("SOE Model:", in: fixture.coordinator.menu)
        let oldStrong = try item("Strong", in: oldModels)
        fixture.refresh(enabled: false)
        XCTAssertFalse(fixture.button.isEnabled)
        XCTAssertFalse(fixture.coordinator.menu.items[0].isEnabled)
        let models = try submenu("SOE Model:", in: fixture.coordinator.menu)
        XCTAssertFalse(try item("Strong", in: models).isEnabled)

        // Force a previously enabled AppKit action to verify the latest busy guard too.
        let action = try XCTUnwrap(oldStrong.action)
        XCTAssertTrue(NSApp.sendAction(action, to: oldStrong.target, from: oldStrong))
        XCTAssertTrue(fixture.actions.isEmpty)
    }

    func testTrackingKeepsNativeItemsAndTargetsAliveUntilCloseThenUsesLatestPolicy() throws {
        let fixture = Fixture()
        let menu = fixture.coordinator.menu
        let models = try submenu("SOE Model:", in: menu)
        let strong = try item("Strong", in: models)
        let target = strong.target
        fixture.coordinator.menuWillOpen(menu)

        fixture.refresh(policy: "coding")
        XCTAssertTrue(fixture.coordinator.isTracking)
        XCTAssertTrue(try item("Strong", in: models) === strong)
        XCTAssertTrue(strong.target === target)
        try activate("Strong", in: models)
        XCTAssertTrue(fixture.actions.isEmpty)

        fixture.coordinator.menuDidClose(menu)
        fixture.coordinator.menuNeedsUpdate(menu)
        XCTAssertFalse(fixture.coordinator.isTracking)
        XCTAssertEqual(fixture.button.title, "Policy: Coding")
        XCTAssertNil(menu.items.first { $0.title.hasPrefix("SOE Model:") })
        XCTAssertEqual(try item("Coding", in: menu).state, .on)
    }

    func testMenuFromPreviousConversationCannotChangeCurrentConversation() throws {
        let fixture = Fixture()
        let oldModels = try submenu("SOE Model:", in: fixture.coordinator.menu)
        fixture.coordinator.menuWillOpen(fixture.coordinator.menu)
        fixture.refresh(contextID: "second")
        try activate("Strong", in: oldModels)
        XCTAssertTrue(fixture.actions.isEmpty)

        fixture.coordinator.menuDidClose(fixture.coordinator.menu)
        fixture.coordinator.menuNeedsUpdate(fixture.coordinator.menu)
        try activate("Strong", in: submenu("SOE Model:", in: fixture.coordinator.menu))
        XCTAssertEqual(fixture.actions, [.configuration(id: "model", value: "strong")])
    }

    func testExistingNativeItemUsesLatestCallbackAndDoesNotRepeatConfirmedSelection() throws {
        let fixture = Fixture()
        let models = try submenu("SOE Model:", in: fixture.coordinator.menu)
        var refreshedActions: [ComposerPolicyMenuAction] = []
        fixture.coordinator.update(
            state: fixture.state,
            onSelectConfiguration: { refreshedActions.append(.configuration(id: $0, value: $1)) },
            onSelectMode: { refreshedActions.append(.mode($0)) })
        try activate("Fast", in: models)
        XCTAssertTrue(refreshedActions.isEmpty)
        try activate("Strong", in: models)
        XCTAssertEqual(refreshedActions, [.configuration(id: "model", value: "strong")])
        XCTAssertTrue(fixture.actions.isEmpty)
    }

    func testPolicyCaptionHasBoundedNaturalWidthAndUnavailableConfigurationStaysDisabled() {
        let fixture = Fixture()
        XCTAssertEqual(fixture.button.intrinsicContentSize.height, 28)
        XCTAssertLessThanOrEqual(fixture.button.intrinsicContentSize.width, 280)
        XCTAssertEqual(fixture.button.cell?.lineBreakMode, .byTruncatingMiddle)
        fixture.state = ComposerPolicyMenuState(
            contextID: nil, selection: ComposerPolicySelection(options: []), modes: [], currentModeID: nil,
            isEnabled: true, help: "Load a conversation.")
        fixture.update()
        XCTAssertFalse(fixture.button.isEnabled)
        XCTAssertEqual(fixture.button.title, "Policy")
        XCTAssertEqual(fixture.button.toolTip, "Load a conversation.")
    }

    private func item(_ title: String, in menu: NSMenu) throws -> NSMenuItem {
        try XCTUnwrap(menu.items.first { $0.title == title })
    }

    private func submenu(_ prefix: String, in menu: NSMenu) throws -> NSMenu {
        try XCTUnwrap(menu.items.first { $0.title.hasPrefix(prefix) }?.submenu)
    }

    private func activate(_ title: String, in menu: NSMenu) throws {
        let selected = try item(title, in: menu)
        XCTAssertTrue(selected.isEnabled)
        menu.performActionForItem(at: menu.index(of: selected))
    }

    @MainActor
    private final class Fixture {
        let button: ComposerPolicyPopupButton
        let coordinator: ComposerPolicyMenu.Coordinator
        var state: ComposerPolicyMenuState
        var actions: [ComposerPolicyMenuAction] = []

        init(policy: String = "soe") {
            _ = NSApplication.shared
            state = Self.makeState(policy: policy)
            button = ComposerPolicyPopupButton(frame: .zero, pullsDown: true)
            coordinator = ComposerPolicyMenu.Coordinator(
                state: state, onSelectConfiguration: { _, _ in }, onSelectMode: { _ in })
            coordinator.attach(button)
            update()
        }

        func refresh(
            policy: String = "soe", model: String = "fast", enabled: Bool = true, contextID: String = "first"
        ) {
            state = Self.makeState(policy: policy, model: model, enabled: enabled, contextID: contextID)
            update()
        }

        func update() {
            coordinator.update(
                state: state,
                onSelectConfiguration: { [weak self] in self?.actions.append(.configuration(id: $0, value: $1)) },
                onSelectMode: { [weak self] in self?.actions.append(.mode($0)) })
        }

        private static func makeState(
            policy: String, model: String = "fast", enabled: Bool = true, contextID: String = "first"
        ) -> ComposerPolicyMenuState {
            func option(_ id: String, _ value: String, _ choices: [(String, String)]) -> ComposerConfigurationOption {
                ComposerConfigurationOption([
                    "id": id, "currentValue": value,
                    "options": choices.map { ["value": $0.0, "name": $0.1] },
                ])!
            }
            let selection = ComposerPolicySelection(options: [
                option("blend.policy", policy, [("soe", "SOE"), ("coding", "Coding")]),
                option("model", model, [("fast", "Fast"), ("strong", "Strong")]),
                option("thinking", "low", [("low", "Low"), ("high", "High")]),
            ])
            return ComposerPolicyMenuState(
                contextID: contextID, selection: selection,
                modes: [.init(id: "plan", name: "Plan"), .init(id: "edit", name: "Edit")], currentModeID: "plan",
                isEnabled: enabled, help: selection.help)
        }
    }
}
