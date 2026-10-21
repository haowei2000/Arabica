import AppKit
import SwiftUI

struct ComposerPolicyMenuMode: Equatable {
    let id: String
    let name: String
}

struct ComposerPolicyMenuState: Equatable {
    let contextID: String?
    let selection: ComposerPolicySelection
    let modes: [ComposerPolicyMenuMode]
    let currentModeID: String?
    let isEnabled: Bool
    let help: String

    var title: String { selection.hasOptions ? "Policy: \(selection.currentName)" : "Policy" }
    var canOpen: Bool { isEnabled && selection.hasOptions }

    func allows(_ action: ComposerPolicyMenuAction) -> Bool {
        guard canOpen else { return false }
        switch action {
        case .configuration(let id, let value):
            let option = [selection.policy, selection.model, selection.thinking]
                .compactMap { $0 }.first { $0.id == id }
            return option.map { $0.currentValue != value && $0.choices.contains { $0.value == value } } ?? false
        case .mode(let id):
            return id != currentModeID && modes.contains { $0.id == id }
        }
    }
}

enum ComposerPolicyMenuAction: Equatable {
    case configuration(id: String, value: String)
    case mode(String)
}

/// Own one native menu, including its action targets, throughout submenu tracking.
struct ComposerPolicyMenu: NSViewRepresentable {
    let state: ComposerPolicyMenuState
    let onSelectConfiguration: (String, String) -> Void
    let onSelectMode: (String) -> Void

    func makeCoordinator() -> Coordinator {
        Coordinator(state: state, onSelectConfiguration: onSelectConfiguration, onSelectMode: onSelectMode)
    }

    func makeNSView(context: Context) -> ComposerPolicyPopupButton {
        let button = ComposerPolicyPopupButton(frame: .zero, pullsDown: true)
        context.coordinator.attach(button)
        return button
    }

    func updateNSView(_ button: ComposerPolicyPopupButton, context: Context) {
        context.coordinator.update(
            state: state, onSelectConfiguration: onSelectConfiguration, onSelectMode: onSelectMode)
    }

    func sizeThatFits(
        _ proposal: ProposedViewSize, nsView: ComposerPolicyPopupButton, context: Context
    ) -> CGSize? {
        CGSize(width: min(nsView.intrinsicContentSize.width, max(44, proposal.width ?? 280)), height: 28)
    }

    @MainActor
    final class Coordinator: NSObject, NSMenuDelegate {
        let menu = NSMenu()
        private(set) var state: ComposerPolicyMenuState
        private(set) var isTracking = false
        private var renderedState: ComposerPolicyMenuState?
        private weak var button: ComposerPolicyPopupButton?
        private var targets: [ComposerPolicyMenuTarget] = []
        private var onSelectConfiguration: (String, String) -> Void
        private var onSelectMode: (String) -> Void

        init(
            state: ComposerPolicyMenuState,
            onSelectConfiguration: @escaping (String, String) -> Void,
            onSelectMode: @escaping (String) -> Void
        ) {
            self.state = state
            self.onSelectConfiguration = onSelectConfiguration
            self.onSelectMode = onSelectMode
            super.init()
            menu.autoenablesItems = false
            menu.delegate = self
        }

        func attach(_ button: ComposerPolicyPopupButton) {
            self.button = button
            button.menu = menu
            updateAppearance()
            rebuildMenuIfNeeded()
        }

        func update(
            state: ComposerPolicyMenuState,
            onSelectConfiguration: @escaping (String, String) -> Void,
            onSelectMode: @escaping (String) -> Void
        ) {
            self.state = state
            self.onSelectConfiguration = onSelectConfiguration
            self.onSelectMode = onSelectMode
            guard !isTracking else { return }
            updateAppearance()
            rebuildMenuIfNeeded()
        }

        func menuNeedsUpdate(_ menu: NSMenu) {
            guard menu === self.menu, !isTracking else { return }
            rebuildMenuIfNeeded()
        }

        func menuWillOpen(_ menu: NSMenu) {
            guard menu === self.menu else { return }
            rebuildMenuIfNeeded()
            isTracking = true
        }

        func menuDidClose(_ menu: NSMenu) {
            guard menu === self.menu else { return }
            isTracking = false
            updateAppearance()
            // AppKit can still dispatch the clicked item as tracking unwinds.
            DispatchQueue.main.async { [weak self] in
                guard let self, !self.isTracking else { return }
                self.rebuildMenuIfNeeded()
            }
        }

        fileprivate func dispatch(_ action: ComposerPolicyMenuAction, contextID: String?) {
            guard contextID == state.contextID, state.allows(action) else { return }
            switch action {
            case .configuration(let id, let value): onSelectConfiguration(id, value)
            case .mode(let id): onSelectMode(id)
            }
        }

        private func updateAppearance() {
            guard let button else { return }
            button.isEnabled = state.canOpen
            if !menu.items.isEmpty { button.selectItem(at: 0) }
            button.title = state.title
            button.toolTip = state.help
            button.setAccessibilityLabel(state.title)
            button.setAccessibilityHelp(state.help)
            button.invalidateIntrinsicContentSize()
        }

        private func rebuildMenuIfNeeded() {
            guard !isTracking, renderedState != state else { return }
            renderedState = state
            menu.removeAllItems()
            targets.removeAll()

            // Pull-down buttons display their first item as the persistent caption.
            let caption = NSMenuItem(title: state.title, action: nil, keyEquivalent: "")
            caption.isEnabled = state.canOpen
            menu.addItem(caption)
            let selection = state.selection
            if let policy = selection.policy {
                addChoices(policy, to: menu)
            } else if selection.hasOptions {
                let soe = NSMenuItem(title: "SOE", action: nil, keyEquivalent: "")
                soe.state = .on
                soe.isEnabled = false
                menu.addItem(soe)
            }

            if selection.isSOE, let model = selection.model {
                menu.addItem(.separator())
                let models = submenu("SOE Model: \(model.currentName)", in: menu)
                addChoices(model, to: models)
                if let thinking = selection.thinking {
                    models.addItem(.separator())
                    let levels = submenu("Thinking Level: \(thinking.currentName)", in: models)
                    addChoices(thinking, to: levels)
                }
            }

            if !state.modes.isEmpty {
                menu.addItem(.separator())
                let modes = submenu("Session Mode", in: menu)
                for mode in state.modes {
                    addAction(
                        .mode(mode.id), title: mode.name, selected: mode.id == state.currentModeID, to: modes)
                }
            }
            // Assigning menu items must not replace the visible caption with a model choice.
            updateAppearance()
        }

        private func submenu(_ title: String, in parent: NSMenu) -> NSMenu {
            let item = NSMenuItem(title: title, action: nil, keyEquivalent: "")
            item.isEnabled = state.canOpen
            let child = NSMenu(title: title)
            child.autoenablesItems = false
            item.submenu = child
            parent.addItem(item)
            return child
        }

        private func addChoices(_ option: ComposerConfigurationOption, to menu: NSMenu) {
            for choice in option.choices {
                let title = choice.group.map { "\($0) · \(choice.name)" } ?? choice.name
                addAction(
                    .configuration(id: option.id, value: choice.value), title: title,
                    selected: choice.value == option.currentValue, to: menu)
            }
        }

        private func addAction(_ action: ComposerPolicyMenuAction, title: String, selected: Bool, to menu: NSMenu) {
            let target = ComposerPolicyMenuTarget(coordinator: self, action: action, contextID: state.contextID)
            targets.append(target)
            let item = NSMenuItem(
                title: title, action: #selector(ComposerPolicyMenuTarget.invokeMenuAction), keyEquivalent: "")
            item.target = target
            // NSMenuItem.target is weak. Keep an item-owned target while a closing menu dispatches its action.
            item.representedObject = target
            item.isEnabled = state.canOpen
            item.state = selected ? .on : .off
            menu.addItem(item)
        }
    }
}

@MainActor
final class ComposerPolicyPopupButton: NSPopUpButton {
    override init(frame buttonFrame: NSRect, pullsDown flag: Bool) {
        super.init(frame: buttonFrame, pullsDown: flag)
        isBordered = false
        controlSize = .small
        font = .systemFont(ofSize: 11, weight: .medium)
        cell?.lineBreakMode = .byTruncatingMiddle
        cell?.wraps = false
        setContentHuggingPriority(.defaultHigh, for: .horizontal)
        setContentCompressionResistancePriority(.defaultLow, for: .horizontal)
    }

    required init?(coder: NSCoder) { return nil }

    override var intrinsicContentSize: NSSize {
        let titleWidth = (title as NSString).size(withAttributes: [.font: font ?? NSFont.systemFont(ofSize: 11)]).width
        return NSSize(width: min(280, max(44, ceil(titleWidth) + 24)), height: 28)
    }
}

@MainActor
private final class ComposerPolicyMenuTarget: NSObject {
    private weak var coordinator: ComposerPolicyMenu.Coordinator?
    private let action: ComposerPolicyMenuAction
    private let contextID: String?

    init(coordinator: ComposerPolicyMenu.Coordinator, action: ComposerPolicyMenuAction, contextID: String?) {
        self.coordinator = coordinator
        self.action = action
        self.contextID = contextID
    }

    @objc func invokeMenuAction(_ sender: NSMenuItem) {
        coordinator?.dispatch(action, contextID: contextID)
    }
}
