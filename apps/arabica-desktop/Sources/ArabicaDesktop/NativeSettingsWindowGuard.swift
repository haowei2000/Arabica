import AppKit
import SwiftUI

/// Closing Settings first flushes its draft. Invalid or conflicted edits stay
/// available until the user explicitly chooses to discard them.
struct NativeSettingsWindowGuard: NSViewRepresentable {
    @ObservedObject var store: SettingsFormStore
    @EnvironmentObject private var applicationDelegate: NativeSettingsApplicationDelegate

    func makeCoordinator() -> Coordinator { Coordinator(store: store, applicationDelegate: applicationDelegate) }

    func makeNSView(context: Context) -> SettingsWindowGuardView {
        let view = SettingsWindowGuardView()
        let coordinator = context.coordinator
        view.windowChanged = { [weak coordinator] in coordinator?.attach(to: $0) }
        return view
    }

    func updateNSView(_ view: SettingsWindowGuardView, context: Context) {
        context.coordinator.attach(to: view.window)
    }

    static func dismantleNSView(_ view: SettingsWindowGuardView, coordinator: Coordinator) {
        coordinator.attach(to: nil)
    }

    @MainActor
    final class Coordinator: NSObject, NSWindowDelegate {
        private let store: SettingsFormStore
        private let applicationDelegate: NativeSettingsApplicationDelegate
        private weak var window: NSWindow?
        private weak var previousDelegate: NSWindowDelegate?
        private var closing = false
        private var mayClose = false

        init(store: SettingsFormStore, applicationDelegate: NativeSettingsApplicationDelegate) {
            self.store = store
            self.applicationDelegate = applicationDelegate
        }

        func attach(to newWindow: NSWindow?) {
            if window !== newWindow {
                if let window { applicationDelegate.unregister(store: store, window: window) }
                if window?.delegate === self { window?.delegate = previousDelegate }
                window = newWindow
                previousDelegate = newWindow?.delegate
                mayClose = false
                closing = false
            }
            guard let window else { return }
            applicationDelegate.register(store: store, window: window)
            if window.delegate !== self {
                previousDelegate = window.delegate
                window.delegate = self
            }
            window.isDocumentEdited = store.hasUnsavedChanges || store.isSaving
        }

        func windowDidBecomeKey(_ notification: Notification) {
            previousDelegate?.windowDidBecomeKey?(notification)
            guard let sender = notification.object as? NSWindow,
                sender === window, sender.isVisible,
                !store.hasUnsavedChanges, !store.isBusy
            else { return }
            Task { @MainActor [weak self, weak sender] in
                guard let self, let sender, self.window === sender, sender.isVisible,
                    !self.store.hasUnsavedChanges, !self.store.isBusy
                else { return }
                _ = await self.store.reload()
            }
        }

        func windowShouldClose(_ sender: NSWindow) -> Bool {
            if mayClose {
                mayClose = false
                if !store.hasUnsavedChanges && !store.isSaving {
                    return previousDelegate?.windowShouldClose?(sender) ?? true
                }
            }
            guard store.hasUnsavedChanges || store.isSaving else {
                return previousDelegate?.windowShouldClose?(sender) ?? true
            }
            guard !closing else { return false }
            closing = true
            Task { [weak self, weak sender] in
                guard let self, let sender, self.window === sender else { return }
                let saved = await store.flush()
                guard self.window === sender else { return }
                if saved {
                    finishClosing(sender)
                } else {
                    showUnresolvedDraft(sender)
                }
            }
            return false
        }

        private func showUnresolvedDraft(_ sender: NSWindow) {
            guard window === sender else { return }
            let alert = NSAlert()
            alert.messageText = "Changes could not be saved"
            alert.informativeText =
                store.status.isEmpty
                ? "Keep editing to finish your changes, or discard this draft and close Settings."
                : "\(store.status)\n\nKeep editing to resolve this issue, or discard the draft."
            alert.addButton(withTitle: "Keep Editing")
            alert.addButton(withTitle: "Discard Changes")
            alert.beginSheetModal(for: sender) { [weak self, weak sender] response in
                guard let self, let sender, self.window === sender else { return }
                if response == .alertSecondButtonReturn {
                    Task { @MainActor in
                        guard self.window === sender else { return }
                        let discarded = await self.store.discardChanges()
                        guard self.window === sender else { return }
                        if discarded {
                            self.finishClosing(sender)
                        } else {
                            self.closing = false
                        }
                    }
                } else {
                    self.closing = false
                }
            }
        }

        private func finishClosing(_ sender: NSWindow) {
            guard window === sender else { return }
            closing = false
            mayClose = true
            sender.isDocumentEdited = false
            DispatchQueue.main.async { [weak self, weak sender] in
                guard let self, let sender, self.window === sender else { return }
                sender.performClose(nil)
            }
        }

        override func responds(to selector: Selector!) -> Bool {
            super.responds(to: selector) || previousDelegate?.responds(to: selector) == true
        }

        override func forwardingTarget(for selector: Selector!) -> Any? {
            previousDelegate?.responds(to: selector) == true
                ? previousDelegate : super.forwardingTarget(for: selector)
        }
    }
}
