import AppKit
import Combine
import SwiftUI

private struct DesktopWindowIDKey: EnvironmentKey {
    static let defaultValue: UUID? = nil
}

extension EnvironmentValues {
    var desktopWindowID: UUID? {
        get { self[DesktopWindowIDKey.self] }
        set { self[DesktopWindowIDKey.self] = newValue }
    }
}

/// Each conversation window owns its connection, navigation, and unsent drafts.
struct DesktopWindowView: View {
    @StateObject private var model = DesktopModel()
    @State private var windowID = UUID()
    let windows: DesktopWindowRegistry

    var body: some View {
        DesktopView()
            .environmentObject(model)
            .environment(\.desktopWindowID, windowID)
            .focusedSceneObject(model)
            .navigationTitle(conversationTitle)
            .navigationSubtitle(workspaceSubtitle)
            .background(DesktopWindowRegistration(id: windowID, model: model, windows: windows, title: windowTitle))
    }

    private var conversationTitle: String {
        let sessionTitle = model.sessions.first { $0.id == model.selectedSessionID }?
            .title.trimmingCharacters(in: .whitespacesAndNewlines)
        return sessionTitle.flatMap { $0.isEmpty ? nil : $0 } ?? "New Conversation"
    }

    private var workspaceSubtitle: String {
        guard let workspace = model.workspace else { return "Arabica" }
        let workspaceName = DesktopModel.projectName(for: workspace.path)
        if model.isConnecting {
            return "\(workspaceName) — Connecting…"
        } else if !model.isConnected {
            return "\(workspaceName) — Disconnected"
        }
        return workspaceName
    }

    private var windowTitle: String {
        guard let workspace = model.workspace else { return "Arabica — \(conversationTitle)" }
        let workspaceName = DesktopModel.projectName(for: workspace.path)
        return "\(workspaceName) — \(conversationTitle)"
    }
}

/// Keeping this wrapper stable preserves a configuration draft when its target changes.
struct DesktopSettingsWindowView: View {
    @ObservedObject var windows: DesktopWindowRegistry
    @StateObject private var standaloneModel = DesktopModel(restoreWorkspace: false)

    var body: some View {
        SettingsView()
            .environmentObject(windows.activeModel ?? standaloneModel)
    }
}

@MainActor
final class DesktopWindowRegistry: NSObject, ObservableObject {
    private struct Entry {
        weak var model: DesktopModel?
        weak var window: NSWindow?
    }

    @Published private(set) var activeWindowID: UUID?
    private var entries: [UUID: Entry] = [:]
    private var activityObservers: [UUID: AnyCancellable] = [:]
    private var recentWindowIDs: [UUID] = []

    override init() {
        super.init()
        NotificationCenter.default.addObserver(
            self,
            selector: #selector(permissionNotificationOpened(_:)),
            name: .arabicaPermissionNotificationOpened,
            object: nil
        )
    }

    var activeModel: DesktopModel? {
        recentWindowIDs.lazy.compactMap { self.entries[$0]?.model }.first
    }

    var hasActivityPreventingRelaunch: Bool {
        entries.values.contains { $0.model?.hasActivityPreventingRelaunch == true }
    }

    func register(_ id: UUID, model: DesktopModel, window: NSWindow? = nil) {
        if entries[id]?.model !== model {
            activityObservers[id] = model.objectWillChange.sink { [weak self] in
                // Published model properties still hold their old value in willChange.
                // Forward after the mutation so the updater sees the complete window state.
                Task { @MainActor in self?.objectWillChange.send() }
            }
        }
        entries[id] = Entry(model: model, window: window)
        objectWillChange.send()
        if !recentWindowIDs.contains(id) { recentWindowIDs.append(id) }
        if activeWindowID == nil || window?.isKeyWindow == true { activate(id) }
    }

    func activate(_ id: UUID) {
        guard entries[id]?.model != nil else { return }
        recentWindowIDs.removeAll { $0 == id || entries[$0]?.model == nil }
        recentWindowIDs.insert(id, at: 0)
        activeWindowID = id
    }

    func unregister(_ id: UUID) {
        entries.removeValue(forKey: id)
        activityObservers.removeValue(forKey: id)
        objectWillChange.send()
        recentWindowIDs.removeAll { $0 == id || entries[$0]?.model == nil }
        if activeWindowID == id { activeWindowID = recentWindowIDs.first }
    }

    func permissionWindowID(sessionID: String, ownerID: UUID?) -> UUID? {
        if let ownerID {
            // A notification from a closed window must not affect another connection.
            return entries[ownerID]?.model != nil ? ownerID : nil
        }
        // Older notifications have no window ID. Prefer the window actually waiting
        // for approval; a shared history list does not establish session ownership.
        return recentWindowIDs.first {
            entries[$0]?.model?.permission?.params["sessionId"] as? String == sessionID
        }
            ?? recentWindowIDs.first {
                entries[$0]?.model?.selectedSessionID == sessionID
            }
    }

    @objc private func permissionNotificationOpened(_ notification: Notification) {
        guard let sessionID = notification.userInfo?["sessionId"] as? String else { return }
        let ownerID = (notification.userInfo?["windowId"] as? String).flatMap(UUID.init(uuidString:))
        guard let id = permissionWindowID(sessionID: sessionID, ownerID: ownerID),
            let entry = entries[id], let model = entry.model
        else { return }
        activate(id)
        entry.window?.makeKeyAndOrderFront(nil)
        Task { await model.selectSession(sessionID) }
    }
}

private struct DesktopWindowRegistration: NSViewRepresentable {
    let id: UUID
    @ObservedObject var model: DesktopModel
    let windows: DesktopWindowRegistry
    let title: String

    func makeNSView(context: Context) -> DesktopWindowRegistrationView {
        DesktopWindowRegistrationView(id: id, model: model, windows: windows, title: title)
    }

    func updateNSView(_ nsView: DesktopWindowRegistrationView, context: Context) {
        nsView.updateTitle(title)
    }
}

private final class DesktopWindowRegistrationView: NSView {
    private let id: UUID
    private weak var model: DesktopModel?
    private weak var windows: DesktopWindowRegistry?
    private weak var registeredWindow: NSWindow?
    private var isClosed = false
    private var windowTitle: String

    init(id: UUID, model: DesktopModel, windows: DesktopWindowRegistry, title: String) {
        self.id = id
        self.model = model
        self.windows = windows
        self.windowTitle = title
        super.init(frame: .zero)
    }

    required init?(coder: NSCoder) { fatalError("init(coder:) has not been implemented") }

    func updateTitle(_ title: String) {
        windowTitle = title
        applyTitle()
        // SwiftUI can restore its scene title later in the same update cycle.
        DispatchQueue.main.async { [weak self] in self?.applyTitle() }
    }

    private func applyTitle() {
        guard !isClosed, let window else { return }
        window.title = windowTitle
        window.titleVisibility = .visible
    }

    override func viewDidMoveToWindow() {
        super.viewDidMoveToWindow()
        guard let window, window !== registeredWindow else { return }
        NotificationCenter.default.removeObserver(self)
        registeredWindow = window
        NotificationCenter.default.addObserver(
            self, selector: #selector(windowBecameKey), name: NSWindow.didBecomeKeyNotification, object: window
        )
        NotificationCenter.default.addObserver(
            self, selector: #selector(windowWillClose), name: NSWindow.willCloseNotification, object: window
        )
        // Avoid publishing an active-window change while SwiftUI attaches this view.
        DispatchQueue.main.async { [weak self, weak window] in
            guard let self, !self.isClosed, let window, self.window === window, let model = self.model else { return }
            self.applyTitle()
            self.windows?.register(self.id, model: model, window: window)
        }
    }

    @objc private func windowBecameKey() {
        guard let model, let window else { return }
        windows?.register(id, model: model, window: window)
        windows?.activate(id)
    }

    @objc private func windowWillClose() {
        isClosed = true
        windows?.unregister(id)
        model?.closeWindow()
        NotificationCenter.default.removeObserver(self)
    }

    deinit { NotificationCenter.default.removeObserver(self) }
}
