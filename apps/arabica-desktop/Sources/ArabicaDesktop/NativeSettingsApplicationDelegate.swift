import AppKit
import Combine

enum SettingsQuitAction {
    case keepEditing
    case discardChanges
}

/// Shared by the app delegate and tests; no application termination occurs here.
@MainActor
enum SettingsQuitPreparation {
    static func prepare(
        store: SettingsFormStore,
        resolveFailure: @MainActor () async -> SettingsQuitAction
    ) async -> Bool {
        while store.hasUnsavedChanges || store.isSaving {
            if await store.flush() { continue }
            switch await resolveFailure() {
            case .keepEditing:
                return false
            case .discardChanges:
                guard await store.discardChanges() else { return false }
            }
        }
        return true
    }
}

/// Settings registers weakly so app-wide Quit can protect its draft without
/// extending the lifetime of the Settings view, store, or window.
@MainActor
final class NativeSettingsApplicationDelegate: NSObject, NSApplicationDelegate, ObservableObject {
    private weak var settingsStore: SettingsFormStore?
    private weak var settingsWindow: NSWindow?
    private var isPreparingTermination = false

    func register(store: SettingsFormStore, window: NSWindow) {
        settingsStore = store
        settingsWindow = window
    }

    func unregister(store: SettingsFormStore, window: NSWindow) {
        guard settingsStore === store, settingsWindow === window else { return }
        settingsStore = nil
        settingsWindow = nil
    }

    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        if isPreparingTermination { return .terminateLater }
        guard let store = settingsStore, store.hasUnsavedChanges || store.isSaving else {
            return .terminateNow
        }
        isPreparingTermination = true
        Task { @MainActor [weak self, weak sender] in
            guard let sender else { return }
            guard let self else {
                sender.reply(toApplicationShouldTerminate: false)
                return
            }
            var candidate = store
            while true {
                let preparingStore = candidate
                let prepared = await SettingsQuitPreparation.prepare(store: preparingStore) { [weak self] in
                    guard let self else { return .keepEditing }
                    return await self.resolveFailure(store: preparingStore)
                }
                guard prepared else {
                    finishTermination(sender, allowing: false)
                    return
                }
                // A field edit or a replacement Settings view can appear while
                // awaiting a save or alert. Recheck on the main actor immediately
                // before replying, then flush that draft as well.
                if candidate.hasUnsavedChanges || candidate.isSaving { continue }
                if let current = settingsStore, current !== candidate,
                    current.hasUnsavedChanges || current.isSaving
                {
                    candidate = current
                    continue
                }
                finishTermination(sender, allowing: true)
                return
            }
        }
        return .terminateLater
    }

    private func resolveFailure(store: SettingsFormStore) async -> SettingsQuitAction {
        let alert = NSAlert()
        alert.messageText = "Settings changes could not be saved"
        alert.informativeText =
            store.status.isEmpty
            ? "Keep editing to finish your changes, or discard this draft and quit Arabica."
            : "\(store.status)\n\nKeep editing to resolve the issue, or discard the draft and quit."
        alert.addButton(withTitle: "Keep Editing")
        alert.addButton(withTitle: "Discard Changes and Quit")
        let response: NSApplication.ModalResponse
        if settingsStore === store, let window = settingsWindow,
            window.isVisible, window.attachedSheet == nil
        {
            response = await withCheckedContinuation { continuation in
                alert.beginSheetModal(for: window) { continuation.resume(returning: $0) }
            }
        } else {
            response = alert.runModal()
        }
        return response == .alertSecondButtonReturn ? .discardChanges : .keepEditing
    }

    private func finishTermination(_ sender: NSApplication, allowing: Bool) {
        isPreparingTermination = false
        if !allowing { settingsWindow?.makeKeyAndOrderFront(nil) }
        sender.reply(toApplicationShouldTerminate: allowing)
    }
}
