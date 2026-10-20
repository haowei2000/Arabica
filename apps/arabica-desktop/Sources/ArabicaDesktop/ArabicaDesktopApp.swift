import SwiftUI

/// Global typography system with 3 unified font styles:
/// 1. Serif (衬线体) - Dedicated to body text (正文: chat message text, thoughts, composer draft)
// MARK: - Design System Tokens & Palette (Theme Aware)

enum Palette {
    static var canvas: Color { ThemeManager.shared.currentColors.canvas }
    static var sidebar: Color { ThemeManager.shared.currentColors.sidebar }
    static var raised: Color { ThemeManager.shared.currentColors.raised }
    static var card: Color { ThemeManager.shared.currentColors.card }
    static var userBubble: Color { ThemeManager.shared.currentColors.userBubble }
    static var ink: Color { ThemeManager.shared.currentColors.ink }
    static var muted: Color { ThemeManager.shared.currentColors.muted }
    static var subtle: Color { ThemeManager.shared.currentColors.subtle }
    static var rule: Color { ThemeManager.shared.currentColors.rule }
    static var accent: Color { ThemeManager.shared.currentColors.accent }
    static var codeBg: Color { ThemeManager.shared.currentColors.codeBg }
    static var toolHeaderBg: Color { ThemeManager.shared.currentColors.toolHeaderBg }
    static var composerBg: Color { ThemeManager.shared.currentColors.composerBg }
    static var hoverBg: Color { ThemeManager.shared.currentColors.hoverBg }
    static var selectionBg: Color { ThemeManager.shared.currentColors.selectionBg }
}

@main
struct ArabicaDesktopApp: App {
    @StateObject private var windows: DesktopWindowRegistry
    @StateObject private var updater: AppUpdater
    @ObservedObject private var theme = ThemeManager.shared
    @FocusedObject private var model: DesktopModel?
    @FocusedBinding(\.projectSidebarVisible) private var sidebarVisible
    @FocusedValue(\.searchConversations) private var searchConversations
    @Environment(\.openWindow) private var openWindow

    init() {
        let windows = DesktopWindowRegistry()
        _windows = StateObject(wrappedValue: windows)
        _updater = StateObject(wrappedValue: AppUpdater(windows: windows))
        _ = PermissionNotifications.shared
    }

    var body: some Scene {
        WindowGroup("Arabica", id: "conversation") {
            DesktopWindowView(windows: windows)
                .environmentObject(theme)
                .environmentObject(updater)
                .frame(minWidth: 540, minHeight: 480)
                .preferredColorScheme(theme.colorScheme)
        }
        .windowStyle(.titleBar)
        .windowToolbarStyle(.unified)
        .commands {
            CommandGroup(after: .appInfo) {
                Button("Check for Updates…") { updater.checkForUpdates() }
                    .disabled(!updater.canCheckForUpdates)
            }
            CommandGroup(replacing: .newItem) {
                Button("New Conversation") {
                    guard let model else { return }
                    Task { await model.newSession() }
                }
                .keyboardShortcut("n")
                .disabled(model?.isConnected != true || model?.isConnecting == true || model?.isCreatingSession == true)
                Button("New Window") { openWindow(id: "conversation") }
                    .keyboardShortcut("n", modifiers: [.command, .shift])
            }
            CommandGroup(replacing: .sidebar) {
                Button("Toggle Sidebar") {
                    sidebarVisible?.toggle()
                }
                .keyboardShortcut("s", modifiers: [.command, .option])
                .disabled(sidebarVisible == nil)
            }
            CommandGroup(after: .textEditing) {
                Button("Search Conversations") { searchConversations?() }
                    .keyboardShortcut("f", modifiers: [.command, .shift])
                    .disabled(searchConversations == nil)
            }
            CommandMenu("Workspace") {
                Button("Open Workspace…") { model?.chooseWorkspace() }
                    .keyboardShortcut("o")
                    .disabled(model == nil)
                Button("Stop Run") { model?.cancel() }
                    .keyboardShortcut(".")
                    .disabled(model?.isRunning != true || model?.isStopping == true)
            }
        }
        Settings {
            DesktopSettingsWindowView(windows: windows)
                .environmentObject(updater)
                .environmentObject(theme)
                .frame(minWidth: 660, idealWidth: 760, minHeight: 620, idealHeight: 700)
                .preferredColorScheme(theme.colorScheme)
        }
    }
}

private struct ProjectSidebarVisibleKey: FocusedValueKey {
    typealias Value = Binding<Bool>
}

private struct SearchConversationsKey: FocusedValueKey {
    typealias Value = () -> Void
}

extension FocusedValues {
    fileprivate var searchConversations: (() -> Void)? {
        get { self[SearchConversationsKey.self] }
        set { self[SearchConversationsKey.self] = newValue }
    }

    fileprivate var projectSidebarVisible: Binding<Bool>? {
        get { self[ProjectSidebarVisibleKey.self] }
        set { self[ProjectSidebarVisibleKey.self] = newValue }
    }
}

struct WindowConfigurator: NSViewRepresentable {
    @ObservedObject var theme = ThemeManager.shared

    func makeNSView(context: Context) -> WindowConfiguratorView {
        let view = WindowConfiguratorView()
        view.updateWindowAppearance()
        return view
    }

    func updateNSView(_ nsView: WindowConfiguratorView, context: Context) {
        nsView.updateWindowAppearance()
    }
}

final class WindowConfiguratorView: NSView {
    override func viewDidMoveToWindow() {
        super.viewDidMoveToWindow()
        updateWindowAppearance()
    }

    func updateWindowAppearance() {
        guard let window = self.window else { return }
        window.titleVisibility = .hidden
        window.titlebarAppearsTransparent = false
        window.isMovableByWindowBackground = false

        let isDark = ThemeManager.shared.isDark
        window.appearance = NSAppearance(named: isDark ? .darkAqua : .aqua)
    }
}

struct DesktopView: View {
    @EnvironmentObject var model: DesktopModel
    @Environment(\.desktopWindowID) private var windowID
    @ObservedObject private var theme = ThemeManager.shared
    @AppStorage("showEvaluationInspector") private var showEvaluation = false
    @State private var sidebarVisible = true
    @State private var windowWidth: CGFloat = 900
    @State private var preferredSidebarWidth = SidebarLayout.defaultWidth
    @StateObject private var sidebarState = SidebarState()

    var body: some View {
        GeometryReader { geometry in
            HStack(spacing: 0) {
                if sidebarVisible && SidebarLayout.canShowSidebar(availableWidth: geometry.size.width) {
                    ProjectsSidebarView(model: model, state: sidebarState)
                        .frame(
                            width: SidebarLayout.resolvedWidth(
                                preferredSidebarWidth, availableWidth: geometry.size.width)
                        )
                        .frame(maxHeight: .infinity)
                        .transition(.move(edge: .leading))

                    SidebarResizeHandle(preferredWidth: $preferredSidebarWidth, availableWidth: geometry.size.width)
                }

                conversation
                    .frame(maxWidth: .infinity)
            }
            .onChange(of: geometry.size.width, initial: true) { _, width in
                windowWidth = width
            }
        }
        .background(WindowConfigurator())
        .background(Palette.canvas)
        .focusedSceneValue(\.projectSidebarVisible, $sidebarVisible)
        .focusedSceneValue(\.searchConversations, presentConversationSearch)
        .toolbar {
            ToolbarItemGroup(placement: .navigation) {
                Button {
                    withAnimation(.easeInOut(duration: 0.18)) {
                        sidebarVisible.toggle()
                    }
                } label: {
                    Label("Toggle Sidebar", systemImage: "sidebar.left")
                }
                .help("Toggle Sidebar (Cmd+Opt+S)")

                Button(action: presentConversationSearch) {
                    Label("Search Conversations", systemImage: "magnifyingglass")
                }
                .tint(sidebarState.hasSearchQuery ? .accentColor : nil)
                .accessibilityValue(sidebarState.hasSearchQuery ? "Filtering: \(sidebarState.searchText)" : "No filter")
                .help("Search Conversations (Cmd+Shift+F)")
                .popover(isPresented: $sidebarState.isSearchPresented, arrowEdge: .bottom) {
                    conversationSearch
                }

                Button(action: model.chooseWorkspace) {
                    Label("Open Workspace", systemImage: "folder.badge.plus")
                }
                .help("Open Workspace (Cmd+O)")

                workspaceChooser
            }
            if windowWidth >= 760 {
                ToolbarItem(placement: .principal) {
                    workspaceBreadcrumb
                }
            }
            ToolbarItemGroup(placement: .primaryAction) {
                buttonSection
            }
        }
        .toolbarBackground(Palette.canvas, for: .windowToolbar)
        .toolbarBackground(.visible, for: .windowToolbar)
        .sheet(item: Binding(get: { model.permission }, set: model.dismissPermission)) { prompt in
            PermissionView(prompt: prompt) { option in model.resolvePermission(option) }
                .frame(minWidth: 500)
        }
        .onChange(of: model.permission?.id) { _, _ in
            guard let prompt = model.permission,
                let sessionID = prompt.params["sessionId"] as? String
            else { return }
            PermissionNotifications.shared.post(
                sessionID: sessionID,
                windowID: windowID,
                title: prompt.title,
                detail: prompt.detail
            )
        }
        .inspector(isPresented: $showEvaluation) {
            EvaluationPanel(model: model.evaluation, isVisible: showEvaluation)
                .inspectorColumnWidth(min: 280, ideal: 330, max: 440)
        }
    }

    private var conversation: some View {
        VStack(spacing: 0) {
            if !model.plan.isEmpty {
                PlanProgressView(plan: model.plan)
            }

            if model.selectedSessionID == nil {
                emptyState
            } else {
                ConversationFeedView(model: model)
            }

            if let error = model.errorText {
                HStack(spacing: 6) {
                    Image(systemName: "exclamationmark.triangle.fill")
                        .foregroundStyle(.orange)
                    Text(error)
                        .font(AppFont.sans(12))
                        .foregroundStyle(.orange)
                        .lineLimit(2)
                        .textSelection(.enabled)
                    Spacer(minLength: 8)
                    if !model.isConnected, let workspace = model.workspace {
                        Button("Reconnect") {
                            Task { await model.openWorkspace(workspace) }
                        }
                        .disabled(model.isConnecting)
                    } else if !model.isSessionReady, let sessionID = model.selectedSessionID {
                        Button("Retry") {
                            Task { await model.selectSession(sessionID) }
                        }
                        .disabled(model.isLoadingSession)
                    }
                    Button {
                        model.errorText = nil
                    } label: {
                        Label("Dismiss Error", systemImage: "xmark")
                            .labelStyle(.iconOnly)
                            .frame(width: 24, height: 24)
                    }
                    .buttonStyle(.plain)
                    .help("Dismiss error")
                }
                .help(error)
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.horizontal, 24)
                .padding(.bottom, 6)
            }

            ComposerView(model: model)
        }
        .background(Palette.canvas)
    }

    private func presentConversationSearch() {
        sidebarVisible = true
        sidebarState.presentSearch()
    }

    private var conversationSearch: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("Search Conversations")
                .font(.headline)
            ToolbarConversationSearchField(
                text: $sidebarState.searchText,
                onCommit: { sidebarState.focusConversationResults() },
                onEscape: { sidebarState.escapeSearch() }
            )
            .frame(height: 28)
            Text(
                "Filter by title, project name, path, or conversation ID. Press Return or Down Arrow to browse results."
            )
            .font(.caption)
            .foregroundStyle(.secondary)
            .fixedSize(horizontal: false, vertical: true)
        }
        .padding(16)
        .frame(width: 320)
    }

    private var workspaceChooser: some View {
        Menu {
            if !model.projectGroups.isEmpty {
                Picker(
                    "Current Workspace",
                    selection: Binding(
                        get: { model.workspace?.standardizedFileURL.path ?? "" },
                        set: { model.activateProject($0) }
                    )
                ) {
                    ForEach(model.projectGroups) { project in
                        Text("\(project.name) — \(project.path)")
                            .tag(project.path)
                            .disabled(!model.selectProjectAvailability(project.path).isEnabled)
                            .help(model.selectProjectAvailability(project.path).explanation ?? project.path)
                    }
                }
                .pickerStyle(.inline)
                Divider()
            }
            Button("Open Other Workspace…", systemImage: "folder.badge.plus", action: model.chooseWorkspace)
            Button("Refresh Projects", systemImage: "arrow.clockwise") {
                Task { await model.refreshSessions() }
            }
            .disabled(!model.isConnected || model.isConnecting)
            .help(
                model.isConnected
                    ? "Refresh projects and conversation history." : "Connect to a workspace before refreshing.")
        } label: {
            Label {
                Text("Choose Workspace")
            } icon: {
                Image(
                    nsImage: NSImage(systemSymbolName: "folder", accessibilityDescription: "Choose Workspace")
                        ?? NSImage()
                )
                .accessibilityLabel("Choose Workspace")
            }
            .accessibilityLabel("Choose Workspace")
        }
        .accessibilityLabel("Choose Workspace")
        .accessibilityValue(model.workspace?.path ?? "No workspace selected")
        .help("Choose Workspace: \(model.workspace?.path ?? "None")")
    }

    private var workspaceBreadcrumb: some View {
        HStack(spacing: 7) {
            let currentTitle =
                model.sessions.first(where: { $0.id == model.selectedSessionID })?.title ?? "New Conversation"
            Text(currentTitle)
                .font(AppFont.sans(12.5, weight: .semibold))
                .foregroundStyle(Palette.ink)
                .lineLimit(1)
                .help(currentTitle)
            Image(
                systemName: model.isConnecting
                    ? "arrow.triangle.2.circlepath"
                    : (model.isConnected ? "checkmark.circle.fill" : "exclamationmark.circle.fill")
            )
            .font(AppFont.sans(11))
            .foregroundStyle(model.isConnected ? Color.green : (model.errorText != nil ? Color.red : Color.orange))
            .accessibilityLabel(connectionStatus)
            .help(connectionStatus)
        }
        .frame(maxWidth: 240)
    }

    private var connectionStatus: String {
        if model.isConnecting { return "Connecting to agent…" }
        if model.isConnected { return "Agent connected" }
        return model.errorText ?? "Agent disconnected. Open a workspace to reconnect."
    }

    private var buttonSection: some View {
        Group {
            if model.isRunning {
                Button(action: model.cancel) {
                    Label(model.isStopping ? "Stopping…" : "Stop Run", systemImage: "stop.fill")
                }
                .disabled(model.isStopping)
                .tint(.red)
                .help("Stop execution (Cmd+.)")
            }

            Button {
                Task { await model.newSession() }
            } label: {
                Label("New Conversation", systemImage: "square.and.pencil")
            }
            .disabled(!model.isConnected || model.isConnecting || model.isCreatingSession)
            .help("New Conversation (Cmd+N)")

            Menu {
                if let used = model.usage["used"] as? Int,
                    let size = model.usage["size"] as? Int
                {
                    Text("Context usage: \(used) of \(size) tokens")
                    Divider()
                }
                Toggle("Show Archived Conversations", isOn: Binding(
                    get: { sidebarState.showsArchived },
                    set: { sidebarState.showsArchived = $0; sidebarVisible = true }
                ))
                Divider()
                Toggle("Show Evaluation Panel", isOn: $showEvaluation)
                    .keyboardShortcut("i", modifiers: [.command, .option])
                Button {
                    withAnimation(.easeInOut(duration: 0.18)) { theme.cycleMode() }
                } label: {
                    Label("Change Appearance (\(theme.mode.rawValue))", systemImage: theme.mode.icon)
                }
            } label: {
                Label {
                    Text("View Options")
                } icon: {
                    Image(
                        nsImage: NSImage(systemSymbolName: "ellipsis.circle", accessibilityDescription: "View Options")
                            ?? NSImage()
                    )
                    .accessibilityLabel("View Options")
                }
                .accessibilityLabel("View Options")
            }
            .accessibilityLabel("View Options")
            .help("View Options")
        }
    }

    private var emptyState: some View {
        VStack(spacing: 16) {
            Spacer()
            Image(systemName: model.workspace == nil ? "folder.badge.gearshape" : "sparkles")
                .font(.system(size: 36))
                .foregroundStyle(Palette.accent.opacity(0.85))

            Text(
                model.isConnecting
                    ? "Connecting to workspace…"
                    : (model.workspace == nil ? "Open a workspace to begin" : "How can I help you today?")
            )
            .font(AppFont.sans(22, weight: .semibold))
            .foregroundStyle(Palette.ink)

            Text(
                model.workspace == nil
                    ? "Select a workspace folder to start working with Arabica."
                    : "Ask a question, generate code, inspect files, or run tests."
            )
            .font(AppFont.sans(13))
            .foregroundStyle(Palette.muted)
            .multilineTextAlignment(.center)
            .frame(maxWidth: 380)

            Button(model.workspace == nil ? "Open Workspace" : (model.isConnected ? "New Conversation" : "Reconnect")) {
                if let workspace = model.workspace, !model.isConnected {
                    Task { await model.openWorkspace(workspace) }
                } else if model.workspace == nil {
                    model.chooseWorkspace()
                } else {
                    Task { await model.newSession() }
                }
            }
            .buttonStyle(.borderedProminent)
            .tint(Palette.ink)
            .foregroundStyle(Palette.canvas)
            .font(AppFont.sans(13, weight: .medium))
            .disabled(model.isConnecting || model.isCreatingSession)
            .padding(.top, 6)

            Spacer()
        }
        .frame(maxWidth: .infinity)
        .padding(.horizontal, 28)
    }
}

private struct ConversationScrollObserver: NSViewRepresentable {
    var onUserScroll: (Bool) -> Void

    func makeNSView(context: Context) -> ConversationScrollTrackingView {
        let view = ConversationScrollTrackingView()
        view.onUserScroll = onUserScroll
        return view
    }

    func updateNSView(_ nsView: ConversationScrollTrackingView, context: Context) {
        nsView.onUserScroll = onUserScroll
        nsView.observeScrollView()
    }
}

private final class ConversationScrollTrackingView: NSView {
    var onUserScroll: ((Bool) -> Void)?
    private weak var observedScrollView: NSScrollView?

    override func viewDidMoveToWindow() {
        super.viewDidMoveToWindow()
        observeScrollView()
    }

    override func viewDidMoveToSuperview() {
        super.viewDidMoveToSuperview()
        observeScrollView()
    }

    override func hitTest(_ point: NSPoint) -> NSView? { nil }

    func observeScrollView() {
        let scrollView = window == nil ? nil : enclosingScrollView
        guard scrollView !== observedScrollView else { return }
        NotificationCenter.default.removeObserver(self)
        observedScrollView = scrollView
        guard let scrollView else { return }
        // AppKit sends this only for user-initiated scrolling. Content growth and
        // scrollTo must never change whether the reader wants to follow output.
        NotificationCenter.default.addObserver(
            self,
            selector: #selector(userScrolled(_:)),
            name: NSScrollView.didLiveScrollNotification,
            object: scrollView
        )
    }

    @objc private func userScrolled(_ notification: Notification) {
        guard let scrollView = observedScrollView,
            let document = scrollView.documentView
        else { return }
        let visible = scrollView.documentVisibleRect
        let distance =
            document.isFlipped
            ? document.bounds.maxY - visible.maxY
            : visible.minY - document.bounds.minY
        onUserScroll?(distance <= 2)
    }

    deinit {
        NotificationCenter.default.removeObserver(self)
    }
}

private struct ConversationFeedView: View {
    @ObservedObject var model: DesktopModel
    @State private var followsLatest = true
    private let endID = "conversation-end"

    var body: some View {
        ScrollViewReader { proxy in
            ScrollView {
                VStack(spacing: 0) {
                    if model.isLoadingSession {
                        HStack(spacing: 8) {
                            ProgressView().controlSize(.small)
                            Text("Loading conversation…")
                                .font(AppFont.sans(12))
                                .foregroundStyle(Palette.muted)
                        }
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .padding(.bottom, 16)
                    }
                    LazyVStack(alignment: .leading, spacing: 16) {
                        ForEach(model.items.groupedForFeed) { group in
                            ChatGroupRow(group: group, isRunning: model.isRunning)
                                .id(group.id)
                        }
                        if model.isRunning {
                            HStack(spacing: 8) {
                                ProgressView().controlSize(.small)
                                Text(model.isWaitingForPermission ? "Waiting for permission…" : "Working…")
                                    .foregroundStyle(Palette.muted)
                            }
                            .padding(.top, 4)
                        }
                    }
                    Color.clear
                        .frame(height: 1)
                        .id(endID)
                        .background(ConversationScrollObserver { followsLatest = $0 })
                }
                .frame(maxWidth: 760)
                .frame(maxWidth: .infinity)
                .padding(.horizontal, 24)
                .padding(.vertical, 20)
            }
            .overlay(alignment: .bottomTrailing) {
                if !followsLatest {
                    Button {
                        followsLatest = true
                        proxy.scrollTo(endID, anchor: .bottom)
                    } label: {
                        Label("Jump to Latest", systemImage: "arrow.down")
                    }
                    .buttonStyle(.bordered)
                    .background(Palette.canvas, in: Capsule())
                    .padding(16)
                }
            }
            .onAppear { proxy.scrollTo(endID, anchor: .bottom) }
            .onChange(of: model.selectedSessionID) { _, _ in
                followsLatest = true
                proxy.scrollTo(endID, anchor: .bottom)
            }
            .onChange(of: model.items.count) { _, _ in
                if followsLatest { proxy.scrollTo(endID, anchor: .bottom) }
            }
            .onChange(of: model.items.last?.text.count) { _, _ in
                if followsLatest { proxy.scrollTo(endID, anchor: .bottom) }
            }
            .onChange(of: model.items.last?.detail?.count) { _, _ in
                if followsLatest { proxy.scrollTo(endID, anchor: .bottom) }
            }
        }
    }
}

/// Uses the system search field, including its standard clear button and search semantics.
private struct ToolbarConversationSearchField: NSViewRepresentable {
    @Binding var text: String
    let onCommit: () -> Void
    let onEscape: () -> Void

    func makeNSView(context: Context) -> ToolbarSearchField {
        let field = ToolbarSearchField()
        field.placeholderString = "Filter conversations"
        field.sendsSearchStringImmediately = true
        field.sendsWholeSearchString = false
        field.delegate = context.coordinator
        field.setAccessibilityLabel("Filter Conversations")
        field.toolTip = "Filter by conversation title, project name, path, or conversation ID"
        return field
    }

    func updateNSView(_ field: ToolbarSearchField, context: Context) {
        context.coordinator.parent = self
        if field.stringValue != text { field.stringValue = text }
    }

    func makeCoordinator() -> Coordinator { Coordinator(parent: self) }

    final class Coordinator: NSObject, NSSearchFieldDelegate {
        var parent: ToolbarConversationSearchField
        init(parent: ToolbarConversationSearchField) { self.parent = parent }

        func controlTextDidChange(_ notification: Notification) {
            guard let field = notification.object as? NSSearchField else { return }
            parent.text = field.stringValue
        }

        func control(_ control: NSControl, textView: NSTextView, doCommandBy commandSelector: Selector) -> Bool {
            switch commandSelector {
            case #selector(NSResponder.insertNewline(_:)), #selector(NSResponder.moveDown(_:)):
                parent.onCommit()
                return true
            case #selector(NSResponder.cancelOperation(_:)):
                parent.onEscape()
                return true
            default:
                return false
            }
        }
    }
}

private final class ToolbarSearchField: NSSearchField {
    override func viewDidMoveToWindow() {
        super.viewDidMoveToWindow()
        guard let window else { return }
        DispatchQueue.main.async { [weak self, weak window] in
            guard let self, self.window === window else { return }
            window?.makeFirstResponder(self)
        }
    }
}

// MARK: - Projects Sidebar View

private struct ProjectsSidebarView: View {
    @ObservedObject var model: DesktopModel
    @ObservedObject var state: SidebarState
    @ObservedObject private var theme = ThemeManager.shared
    @State private var renamingSession: DesktopSession?

    private var archivedSessionIDs: Set<String> {
        Set(model.sessions.filter { model.sidebarLibrary.isArchived($0.id) }.map(\.id))
    }

    private var filteredProjects: [ProjectGroup] {
        let ordered = SidebarNavigation.orderedProjects(model.projectGroups, preferredOrder: state.projectOrder)
        let pinned = Set(model.sessions.filter { model.sidebarLibrary.isPinned($0.id) }.map(\.id))
        return SidebarNavigation.filteredProjects(
            ordered, query: state.searchText, archivedSessionIDs: archivedSessionIDs,
            showingArchived: state.showsArchived, pinnedSessionIDs: pinned
        )
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            if state.showsArchived { archiveHeading }
            if state.hasSearchQuery { activeFilter }

            if model.projectGroups.isEmpty {
                ContentUnavailableView {
                    Label("No Workspaces", systemImage: "folder")
                } description: {
                    Text("Open a workspace from the toolbar to begin.")
                }
            } else if filteredProjects.isEmpty {
                if state.hasSearchQuery {
                    ContentUnavailableView.search(text: state.searchText)
                } else {
                    ContentUnavailableView {
                        Label("No Archived Conversations", systemImage: "archivebox")
                    } description: {
                        Text("Archived conversations remain in your history and can be restored here.")
                    }
                }
            } else {
                SidebarOutlineView(
                    projects: SidebarOutlineContent.projects(
                        filteredProjects, model: model,
                        onSelectConversation: selectConversation,
                        onRenameConversation: { renamingSession = $0 },
                        onSelectProject: selectProject
                    ),
                    selectedProjectID: model.workspace?.standardizedFileURL.path,
                    selectedConversationID: state.navigationSessionID,
                    collapsedProjectIDs: state.collapsedProjects,
                    showsAllConversationProjectIDs: state.expandedProjects,
                    isSearching: state.hasSearchQuery,
                    focusRequest: state.isSearchPresented ? nil : state.conversationFocusRequest,
                    onSelectProject: selectProject,
                    onSelectConversation: selectConversation,
                    onSetProjectExpanded: { id, expanded in
                        guard !state.hasSearchQuery else { return }
                        if expanded { state.collapsedProjects.remove(id) } else { state.collapsedProjects.insert(id) }
                    },
                    onShowMore: { id in
                        if state.expandedProjects.contains(id) {
                            state.expandedProjects.remove(id)
                        } else {
                            state.expandedProjects.insert(id)
                        }
                    }
                )
                .padding(.top, 8)
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(Palette.sidebar)
        .sheet(item: $renamingSession) { session in
            RenameConversationSheet(model: model, session: session)
        }
        .onAppear {
            state.reconcileProjects(model.projectGroups.map(\.id))
            state.reconcileSelection(model.selectedSessionID)
            revealSelectedConversation()
        }
        .onChange(of: model.selectedSessionID) { _, selectedID in
            if let selectedID { state.showsArchived = model.sidebarLibrary.isArchived(selectedID) }
            state.reconcileSelection(selectedID)
            revealSelectedConversation()
        }
        .onChange(of: archivedSessionIDs) { _, archivedIDs in
            if let selectedID = model.selectedSessionID { state.showsArchived = archivedIDs.contains(selectedID) }
        }
        .onChange(of: model.projectGroups.map(\.id)) { _, ids in state.reconcileProjects(ids) }
    }

    private var archiveHeading: some View {
        HStack {
            Button {
                state.showsArchived = false
            } label: {
                Label("Conversations", systemImage: "chevron.left")
            }
            .buttonStyle(.borderless)
            .help("Back to active conversations")
            Spacer(minLength: 4)
            Text("Archived").foregroundStyle(Palette.muted)
        }
        .font(AppFont.sans(11.5))
        .padding(.horizontal, 12)
        .frame(height: 32)
    }

    private var activeFilter: some View {
        HStack(spacing: 6) {
            Image(systemName: "magnifyingglass").accessibilityHidden(true)
            Text(state.searchText).lineLimit(1)
            Spacer(minLength: 0)
            Button {
                state.searchText = ""
            } label: {
                Label("Clear Filter", systemImage: "xmark.circle.fill")
                    .labelStyle(.iconOnly)
            }
            .buttonStyle(.borderless)
            .help("Clear conversation filter")
        }
        .font(AppFont.sans(11.5))
        .foregroundStyle(Palette.muted)
        .padding(.horizontal, 12)
        .frame(height: 30)
        .help("Filtering conversations by: \(state.searchText)")
    }

    private func revealSelectedConversation() {
        state.revealSelection(state.navigationSessionID, in: filteredProjects)
    }

    private func selectProject(_ path: String) {
        guard model.selectProjectAvailability(path).isEnabled else { return }
        state.reconcileSelection(nil)
        model.activateProject(path)
    }

    private func selectConversation(_ sessionID: String) {
        let request = state.requestSelection(sessionID)
        Task {
            guard state.isCurrent(request) else { return }
            await model.selectSession(sessionID)
            state.completeSelection(request, selectedID: model.selectedSessionID)
        }
    }
}

private struct RenameConversationSheet: View {
    @ObservedObject var model: DesktopModel
    let session: DesktopSession
    @Environment(\.dismiss) private var dismiss
    @FocusState private var titleHasFocus: Bool
    @State private var title: String
    @State private var isSaving = false
    @State private var saveError: String?

    init(model: DesktopModel, session: DesktopSession) {
        self.model = model
        self.session = session
        _title = State(initialValue: session.title)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("Rename Conversation").font(AppFont.sans(16, weight: .semibold))
            TextField("Conversation title", text: $title)
                .textFieldStyle(.roundedBorder)
                .focused($titleHasFocus)
                .disabled(isSaving)
                .onSubmit { save() }
            if let saveError {
                Text(saveError).font(AppFont.sans(12)).foregroundStyle(.red).textSelection(.enabled)
            }
            HStack {
                if isSaving { ProgressView().controlSize(.small) }
                Spacer()
                Button("Cancel") { dismiss() }.keyboardShortcut(.cancelAction).disabled(isSaving)
                Button(isSaving ? "Saving…" : "Save", action: save)
                    .keyboardShortcut(.defaultAction)
                    .disabled(isSaving || title.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
            }
        }
        .padding(24).frame(width: 380)
        .interactiveDismissDisabled(isSaving)
        .onAppear { titleHasFocus = true }
    }

    private func save() {
        let title = title.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !isSaving, !title.isEmpty else { return }
        isSaving = true
        saveError = nil
        Task {
            if await model.renameSession(session.id, title: title) {
                dismiss()
            } else {
                saveError = model.errorText ?? "The conversation could not be renamed. Try again."
                isSaving = false
            }
        }
    }
}

// MARK: - Feed Grouping & Rows

enum ChatFeedItem: Identifiable {
    case user(ChatItem)
    case assistant(ChatItem)
    case thought(ChatItem)
    case toolGroup(id: UUID, tools: [ChatItem])

    var id: UUID {
        switch self {
        case .user(let item): return item.id
        case .assistant(let item): return item.id
        case .thought(let item): return item.id
        case .toolGroup(let id, _): return id
        }
    }
}

extension Array where Element == ChatItem {
    var groupedForFeed: [ChatFeedItem] {
        var feed: [ChatFeedItem] = []
        var currentTools: [ChatItem] = []

        func flushTools() {
            if !currentTools.isEmpty {
                feed.append(.toolGroup(id: currentTools.first!.id, tools: currentTools))
                currentTools.removeAll()
            }
        }

        for item in self {
            if item.kind == .tool {
                currentTools.append(item)
            } else {
                flushTools()
                switch item.kind {
                case .user: feed.append(.user(item))
                case .assistant: feed.append(.assistant(item))
                case .thought: feed.append(.thought(item))
                case .tool: break
                }
            }
        }
        flushTools()
        return feed
    }
}

private struct ChatGroupRow: View {
    let group: ChatFeedItem
    let isRunning: Bool

    var body: some View {
        switch group {
        case .user(let item):
            UserMessageView(item: item)
        case .assistant(let item):
            AssistantMessageView(item: item)
        case .thought(let item):
            ThoughtCardView(item: item, isRunning: isRunning)
        case .toolGroup(_, let tools):
            ToolGroupCardView(tools: tools)
        }
    }
}

private struct UserMessageView: View {
    let item: ChatItem

    var body: some View {
        HStack {
            Spacer(minLength: 64)
            Text(item.text)
                .font(AppFont.message(13.5))
                .lineSpacing(3.5)
                .foregroundStyle(Palette.ink)
                .textSelection(.enabled)
                .padding(.horizontal, 14)
                .padding(.vertical, 10)
                .background(Palette.userBubble, in: RoundedRectangle(cornerRadius: 14))
                .overlay(RoundedRectangle(cornerRadius: 14).stroke(Palette.rule, lineWidth: 1))
        }
        .frame(maxWidth: .infinity, alignment: .trailing)
    }
}

private struct AssistantMessageView: View {
    let item: ChatItem

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            MarkdownMessage(text: item.text)
                .font(AppFont.body(13.5))
                .foregroundStyle(Palette.ink)
                .frame(maxWidth: .infinity, alignment: .leading)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

private struct ThoughtCardView: View {
    let item: ChatItem
    let isRunning: Bool
    @State private var isExpanded = false

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Button {
                withAnimation(.easeInOut(duration: 0.16)) {
                    isExpanded.toggle()
                }
            } label: {
                HStack(spacing: 7) {
                    ToolIconView(kind: "think", title: "think", size: 13)
                        .foregroundStyle(Color.purple.opacity(0.85))
                    Text(isRunning && item.text.isEmpty ? "Thinking…" : "Thought process")
                        .font(AppFont.event(11.5, weight: .medium))
                        .foregroundStyle(Palette.muted)
                    Spacer()
                    Image(systemName: isExpanded ? "chevron.up" : "chevron.down")
                        .font(AppFont.sans(9, weight: .semibold))
                        .foregroundStyle(Palette.subtle)
                }
                .padding(.horizontal, 10)
                .padding(.vertical, 6)
                .background(Palette.raised.opacity(0.8), in: RoundedRectangle(cornerRadius: 7))
                .overlay(RoundedRectangle(cornerRadius: 7).stroke(Palette.rule, lineWidth: 1))
            }
            .buttonStyle(.plain)

            if isExpanded {
                ScrollView {
                    Text(item.text)
                        .font(AppFont.body(12.5))
                        .lineSpacing(3)
                        .foregroundStyle(Palette.muted)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .textSelection(.enabled)
                        .padding(10)
                }
                .frame(maxHeight: 220)
                .background(Palette.codeBg, in: RoundedRectangle(cornerRadius: 8))
                .overlay(RoundedRectangle(cornerRadius: 8).stroke(Palette.rule, lineWidth: 1))
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

// MARK: - Tool Visuals & Group Cards (Borderless with Custom Icons)

enum ToolVisuals {
    static func iconResourceName(for kind: String?, title: String = "") -> String {
        let k = kind?.lowercased() ?? ""
        let t = title.lowercased()

        if k == "execute" || t.contains("terminal") || t.contains("bash") || t.contains("command")
            || t.hasPrefix("cargo") || t.hasPrefix("run")
        {
            return "terminal_command"
        }
        if k == "read" || t.contains("read") || t.contains("view") || t.contains("cat") {
            return "read_file"
        }
        if k == "edit" || t.contains("edit") || t.contains("patch") || t.contains("replace") {
            return "edit_file"
        }
        if k == "write" || t.contains("write") || t.contains("create") {
            return "write_file"
        }
        if k == "search" || t.contains("search") || t.contains("find") || t.contains("grep") {
            return "search_code"
        }
        if k == "delete" || t.contains("delete") || t.contains("remove") || t.contains("trash") {
            return "delete_file"
        }
        if k == "move" || t.contains("move") || t.contains("rename") {
            return "directory_folder"
        }
        if k == "fetch" || t.contains("fetch") || t.contains("http") || t.contains("web") || t.contains("url") {
            return "web_fetch"
        }
        if k == "think" || t.contains("think") {
            return "think_reasoning"
        }
        if k == "switch_mode" || t.contains("mode") {
            return "switch_mode"
        }
        if t.contains("git") {
            return "git_control"
        }
        if t.contains("test") {
            return "test_runner"
        }
        if t.contains("diff") {
            return "inspect_diff"
        }
        if t.contains("plan") {
            return "task_plan"
        }
        if t.contains("config") || t.contains("setting") {
            return "settings_config"
        }
        if t.contains("mcp") {
            return "mcp_server"
        }
        return "settings_config"
    }

    static func iconImage(for kind: String?, title: String = "") -> NSImage? {
        let name = iconResourceName(for: kind, title: title)
        var url =
            Bundle.main.url(forResource: name, withExtension: "png", subdirectory: "tool-icons")
            ?? Bundle.main.url(forResource: name, withExtension: "png")
        #if SWIFT_PACKAGE
            if url == nil {
                url =
                    Bundle.module.url(forResource: name, withExtension: "png", subdirectory: "tool-icons")
                    ?? Bundle.module.url(forResource: name, withExtension: "png")
            }
        #endif
        if let url, let img = NSImage(contentsOf: url) {
            img.isTemplate = true
            return img
        }
        return nil
    }

    static func icon(for kind: String?, title: String = "") -> String {
        let k = kind?.lowercased() ?? ""
        let t = title.lowercased()

        if k == "execute" || t.contains("terminal") || t.contains("bash") || t.contains("command")
            || t.hasPrefix("cargo") || t.hasPrefix("test")
        {
            return "terminal"
        }
        if k == "read" || t.contains("read") || t.contains("view") || t.contains("cat") {
            return "doc.text.magnifyingglass"
        }
        if k == "edit" || t.contains("edit") || t.contains("write") || t.contains("patch") || t.contains("replace") {
            return "square.and.pencil"
        }
        if k == "search" || t.contains("search") || t.contains("find") || t.contains("grep") {
            return "magnifyingglass"
        }
        if k == "delete" || t.contains("delete") || t.contains("remove") || t.contains("trash") {
            return "trash"
        }
        if k == "move" || t.contains("move") || t.contains("rename") {
            return "arrow.right.doc.on.clipboard"
        }
        if k == "fetch" || t.contains("fetch") || t.contains("http") || t.contains("web") || t.contains("url") {
            return "globe"
        }
        if k == "think" || t.contains("think") {
            return "brain.head.profile"
        }
        if k == "switch_mode" || t.contains("mode") {
            return "arrow.triangle.2.circlepath"
        }
        if t.contains("git") {
            return "arrow.triangle.branch"
        }
        return "wrench.and.screwdriver"
    }
}

struct ToolIconView: View {
    @ObservedObject private var theme = ThemeManager.shared
    let kind: String?
    var title: String = ""
    var size: CGFloat = 13

    var body: some View {
        let tint = theme.toolColor(for: kind)
        Group {
            if theme.iconStyle == .sfSymbols {
                Image(systemName: ToolVisuals.icon(for: kind, title: title))
                    .font(AppFont.sans(size))
                    .foregroundStyle(tint)
            } else if let nsImage = ToolVisuals.iconImage(for: kind, title: title) {
                Image(nsImage: nsImage)
                    .resizable()
                    .renderingMode(.template)
                    .aspectRatio(contentMode: .fit)
                    .frame(width: size, height: size)
                    .foregroundStyle(tint)
            } else {
                Image(systemName: ToolVisuals.icon(for: kind, title: title))
                    .font(AppFont.sans(size))
                    .foregroundStyle(tint)
            }
        }
    }
}

private struct ToolGroupCardView: View {
    let tools: [ChatItem]
    @State private var isExpanded = false

    private var isAnyRunning: Bool {
        tools.contains {
            let status = ($0.status ?? "").lowercased()
            return status == "running" || status == "in_progress" || status == "started"
        }
    }

    private var hasAnyFailed: Bool {
        tools.contains {
            let status = ($0.status ?? "").lowercased()
            return status == "failed" || status == "error"
        }
    }

    private var summaryTitle: String {
        if tools.count == 1 {
            let item = tools[0]
            if let path = item.path, !path.isEmpty {
                let filename = URL(fileURLWithPath: path).lastPathComponent
                switch item.toolKind {
                case "read": return "Read \(filename)"
                case "edit": return "Edited \(filename)"
                case "delete": return "Deleted \(filename)"
                default: return item.text
                }
            }
            return item.text
        }
        let kinds = Set(tools.compactMap(\.toolKind))
        if kinds.count == 1, let singleKind = kinds.first {
            switch singleKind {
            case "read": return "Read \(tools.count) files"
            case "edit": return "Edited \(tools.count) files"
            case "search": return "Executed \(tools.count) searches"
            case "execute": return "Ran \(tools.count) commands"
            default: return "Used \(tools.count) tools"
            }
        }
        return "Used \(tools.count) tools"
    }

    var body: some View {
        VStack(spacing: 0) {
            Button {
                withAnimation(.easeInOut(duration: 0.16)) {
                    isExpanded.toggle()
                }
            } label: {
                HStack(spacing: 8) {
                    ToolIconView(
                        kind: tools.count == 1 ? tools[0].toolKind : "settings_config",
                        title: tools.count == 1 ? tools[0].text : "tools",
                        size: 13
                    )
                    .foregroundStyle(isAnyRunning ? Palette.accent : Palette.muted)

                    Text(summaryTitle)
                        .font(AppFont.event(12, weight: .medium))
                        .foregroundStyle(Palette.ink)
                        .lineLimit(1)

                    Spacer(minLength: 8)

                    if isAnyRunning {
                        HStack(spacing: 5) {
                            ProgressView().controlSize(.mini)
                            Text("Running…")
                                .font(AppFont.event(11))
                                .foregroundStyle(Palette.accent)
                        }
                    } else if hasAnyFailed {
                        HStack(spacing: 4) {
                            Image(systemName: "exclamationmark.circle.fill")
                                .font(AppFont.sans(11))
                                .foregroundStyle(.red)
                            Text("Failed")
                                .font(AppFont.event(11))
                                .foregroundStyle(.red)
                        }
                    } else {
                        HStack(spacing: 4) {
                            Image(systemName: "checkmark.circle.fill")
                                .font(AppFont.sans(11))
                                .foregroundStyle(Color.green.opacity(0.85))
                            if tools.count > 1 {
                                Text("\(tools.count)")
                                    .font(AppFont.event(10))
                                    .foregroundStyle(Palette.muted)
                            }
                        }
                    }

                    Image(systemName: isExpanded ? "chevron.up" : "chevron.down")
                        .font(AppFont.sans(9, weight: .semibold))
                        .foregroundStyle(Palette.subtle)
                }
                .padding(.horizontal, 10)
                .padding(.vertical, 7)
                .background(Palette.card, in: RoundedRectangle(cornerRadius: 8))
                .overlay(RoundedRectangle(cornerRadius: 8).stroke(Palette.rule, lineWidth: 1))
            }
            .buttonStyle(.plain)

            if isExpanded {
                VStack(spacing: 2) {
                    ForEach(tools) { tool in
                        ToolItemDetailView(item: tool, isSingle: tools.count == 1)
                    }
                }
                .padding(6)
                .background(Palette.raised, in: RoundedRectangle(cornerRadius: 8))
                .overlay(RoundedRectangle(cornerRadius: 8).stroke(Palette.rule, lineWidth: 1))
                .padding(.top, 4)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

private struct ToolItemDetailView: View {
    let item: ChatItem
    let isSingle: Bool
    @State private var showDetail: Bool
    @State private var isHovered: Bool = false

    init(item: ChatItem, isSingle: Bool = false) {
        self.item = item
        self.isSingle = isSingle
        self._showDetail = State(initialValue: isSingle)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Button {
                withAnimation(.easeInOut(duration: 0.15)) {
                    showDetail.toggle()
                }
            } label: {
                HStack(spacing: 7) {
                    ToolIconView(kind: item.toolKind, title: item.text, size: 12)
                        .foregroundStyle(Palette.muted)

                    Text(item.text)
                        .font(AppFont.event(11.5))
                        .foregroundStyle(Palette.ink)
                        .lineLimit(1)
                        .truncationMode(.middle)

                    if let path = item.path, !path.isEmpty {
                        Text(URL(fileURLWithPath: path).lastPathComponent)
                            .font(AppFont.code(10))
                            .foregroundStyle(Palette.muted)
                            .padding(.horizontal, 5)
                            .padding(.vertical, 2)
                            .background(Palette.raised, in: RoundedRectangle(cornerRadius: 4))
                    }

                    Spacer(minLength: 4)

                    if let status = item.status {
                        statusIndicator(status)
                    }

                    Image(systemName: showDetail ? "chevron.up" : "chevron.down")
                        .font(AppFont.sans(8, weight: .semibold))
                        .foregroundStyle(Palette.subtle)
                }
                .padding(.horizontal, 6)
                .padding(.vertical, 4)
                .background(isHovered ? Palette.hoverBg : Color.clear, in: RoundedRectangle(cornerRadius: 5))
            }
            .buttonStyle(.plain)
            .onHover { isHovered = $0 }

            if showDetail {
                let detailText: String = {
                    if let d = item.detail, !d.isEmpty { return d }
                    if let p = item.path, !p.isEmpty { return "File: \(p)" }
                    return "No additional output available for this tool call."
                }()

                VStack(alignment: .leading, spacing: 4) {
                    HStack {
                        if let path = item.path, !path.isEmpty {
                            Text(path)
                                .font(AppFont.code(10))
                                .foregroundStyle(Palette.muted)
                                .lineLimit(1)
                        }
                        Spacer()
                        Button {
                            NSPasteboard.general.clearContents()
                            NSPasteboard.general.setString(detailText, forType: .string)
                        } label: {
                            HStack(spacing: 3) {
                                Image(systemName: "doc.on.doc")
                                    .font(AppFont.sans(9))
                                Text("Copy")
                                    .font(AppFont.sans(10))
                            }
                            .foregroundStyle(Palette.muted)
                            .padding(.horizontal, 6)
                            .padding(.vertical, 2)
                            .background(Palette.raised.opacity(0.8), in: RoundedRectangle(cornerRadius: 4))
                        }
                        .buttonStyle(.plain)
                        .help("Copy tool output to clipboard")
                    }

                    ScrollView([.horizontal, .vertical]) {
                        Text(detailText)
                            .font(AppFont.code(11))
                            .lineSpacing(2.5)
                            .foregroundStyle(Palette.ink)
                            .textSelection(.enabled)
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .padding(8)
                    }
                    .frame(maxHeight: 260)
                    .background(Palette.codeBg, in: RoundedRectangle(cornerRadius: 6))
                    .overlay(RoundedRectangle(cornerRadius: 6).stroke(Palette.rule, lineWidth: 1))
                }
                .padding(.horizontal, 4)
                .padding(.top, 2)
                .padding(.bottom, 4)
            }
        }
        .padding(.horizontal, 2)
        .padding(.vertical, 2)
    }

    @ViewBuilder
    private func statusIndicator(_ status: String) -> some View {
        let norm = status.lowercased()
        if norm == "completed" || norm == "success" || norm == "ok" {
            Image(systemName: "checkmark")
                .font(AppFont.sans(9, weight: .bold))
                .foregroundStyle(Color.green.opacity(0.85))
        } else if norm == "in_progress" || norm == "running" || norm == "started" {
            ProgressView().controlSize(.mini)
        } else if norm == "failed" || norm == "error" {
            Image(systemName: "xmark")
                .font(AppFont.sans(9, weight: .bold))
                .foregroundStyle(Color.red)
        } else {
            Circle()
                .fill(Palette.subtle)
                .frame(width: 5, height: 5)
        }
    }
}

// MARK: - Plan Progress View

private struct PlanProgressView: View {
    let plan: [[String: Any]]
    @State private var isExpanded = false

    private var completedCount: Int {
        plan.filter {
            let status = ($0["status"] as? String)?.lowercased()
            return status == "completed" || status == "success"
        }.count
    }

    private var progressRatio: Double {
        guard !plan.isEmpty else { return 0 }
        return Double(completedCount) / Double(plan.count)
    }

    var body: some View {
        VStack(spacing: 0) {
            Button {
                withAnimation(.easeInOut(duration: 0.18)) {
                    isExpanded.toggle()
                }
            } label: {
                HStack(spacing: 8) {
                    Image(systemName: "checklist")
                        .font(AppFont.sans(12))
                        .foregroundStyle(Palette.accent)
                    Text("Plan")
                        .font(AppFont.event(12, weight: .semibold))
                        .foregroundStyle(Palette.ink)
                    Text("\(completedCount)/\(plan.count) completed")
                        .font(AppFont.event(11))
                        .foregroundStyle(Palette.muted)
                    Spacer()
                    ZStack(alignment: .leading) {
                        Capsule().fill(Palette.rule).frame(width: 60, height: 4)
                        Capsule()
                            .fill(completedCount == plan.count ? Color.green : Palette.accent)
                            .frame(width: max(4, 60 * progressRatio), height: 4)
                    }
                    .frame(width: 60, height: 4)

                    Image(systemName: isExpanded ? "chevron.up" : "chevron.down")
                        .font(AppFont.sans(10, weight: .semibold))
                        .foregroundStyle(Palette.muted)
                }
                .padding(.horizontal, 14)
                .padding(.vertical, 8)
                .background(Palette.card, in: RoundedRectangle(cornerRadius: 8))
                .overlay(RoundedRectangle(cornerRadius: 8).stroke(Palette.rule, lineWidth: 1))
            }
            .buttonStyle(.plain)

            if isExpanded {
                VStack(alignment: .leading, spacing: 6) {
                    ForEach(Array(plan.enumerated()), id: \.offset) { _, entry in
                        let status = (entry["status"] as? String)?.lowercased()
                        let isCompleted = status == "completed" || status == "success"
                        let isFailed = status == "failed" || status == "error"
                        let isRunning = status == "in_progress" || status == "running"
                        HStack(spacing: 8) {
                            if isRunning {
                                ProgressView().controlSize(.mini)
                            } else if isCompleted {
                                Image(systemName: "checkmark.circle.fill")
                                    .foregroundStyle(Color.green.opacity(0.9))
                            } else if isFailed {
                                Image(systemName: "exclamationmark.circle.fill")
                                    .foregroundStyle(Color.red.opacity(0.9))
                            } else {
                                Image(systemName: "circle")
                                    .foregroundStyle(Palette.subtle)
                            }
                            Text(entry["content"] as? String ?? "Step")
                                .font(AppFont.event(11.5))
                                .foregroundStyle(isCompleted ? Palette.muted : Palette.ink)
                        }
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(12)
                .background(Palette.card.opacity(0.7), in: RoundedRectangle(cornerRadius: 8))
                .overlay(RoundedRectangle(cornerRadius: 8).stroke(Palette.rule, lineWidth: 1))
                .padding(.top, 4)
            }
        }
        .padding(.horizontal, 24)
        .padding(.vertical, 6)
    }
}

private struct PermissionView: View {
    let prompt: PermissionPrompt
    let resolve: (String?) -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text("Permission required")
                .font(AppFont.sans(23, weight: .semibold))
            Text(prompt.title).font(AppFont.sans(14, weight: .medium))
            if let workspace = prompt.workspace {
                VStack(alignment: .leading, spacing: 4) {
                    Label("Project: \(DesktopModel.projectName(for: workspace.path))", systemImage: "folder")
                        .font(AppFont.sans(12, weight: .medium))
                        .foregroundStyle(Palette.ink)
                    Text(workspace.path)
                        .font(AppFont.code(11))
                        .foregroundStyle(Palette.muted)
                        .textSelection(.enabled)
                        .fixedSize(horizontal: false, vertical: true)
                }
                .help(workspace.path)
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            if !prompt.detail.isEmpty {
                ScrollView {
                    Text(prompt.detail)
                        .font(AppFont.code(11))
                        .textSelection(.enabled)
                        .frame(maxWidth: .infinity, alignment: .leading)
                }
                .frame(maxHeight: 150)
                .padding(12)
                .background(Palette.canvas, in: RoundedRectangle(cornerRadius: 8))
            }
            HStack {
                Button("Cancel") { resolve(nil) }
                    .font(AppFont.sans(13))
                Spacer()
                if prompt.options.isEmpty {
                    Button("Allow") { resolve("allow_once") }
                        .buttonStyle(.borderedProminent)
                        .font(AppFont.sans(13))
                } else {
                    ForEach(prompt.options, id: \.id) { option in
                        if option.id == "allow_once" {
                            Button(option.name) { resolve(option.id) }
                                .buttonStyle(.borderedProminent)
                                .font(AppFont.sans(13))
                        } else {
                            Button(option.name) { resolve(option.id) }
                                .buttonStyle(.bordered)
                                .font(AppFont.sans(13))
                        }
                    }
                }
            }
        }
        .padding(24)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Palette.raised, in: RoundedRectangle(cornerRadius: 10))
    }
}

struct SettingsView: View {
    @EnvironmentObject var model: DesktopModel
    @EnvironmentObject var updater: AppUpdater
    @StateObject private var config = ConfigEditor()
    @State private var selectedTab: SettingsTab = .config

    private enum SettingsTab: String, CaseIterable, Identifiable {
        case config = "Models"
        case appearance = "Appearance"
        case policy = "Routing"
        case context = "Tools & Skills"
        case updates = "Updates"

        var id: String { rawValue }
        var icon: String {
            switch self {
            case .config: return "gearshape"
            case .appearance: return "paintpalette"
            case .policy: return "point.3.filled.connected.trianglepath.dotted"
            case .context: return "square.stack.3d.up"
            case .updates: return "arrow.down.circle"
            }
        }

        var title: String {
            switch self {
            case .config: return "Models & Providers"
            case .appearance: return "Appearance"
            case .policy: return "Model Routing"
            case .context: return "Tools & Skills"
            case .updates: return "Software Updates"
            }
        }

        var detail: String {
            switch self {
            case .config: return "Manage provider connections and the models available to Arabica."
            case .appearance: return "Theme and typography changes are saved automatically."
            case .policy: return "Review routing policies and choose the default for future connections."
            case .context: return "View configured capabilities. Edit config.toml to change tools and skill roots."
            case .updates: return "Manage automatic checks and installation for packaged Arabica releases."
            }
        }
    }

    var body: some View {
        VStack(spacing: 0) {
            Picker("Settings Tab", selection: $selectedTab) {
                ForEach(SettingsTab.allCases) { tab in
                    Label(tab.rawValue, systemImage: tab.icon).tag(tab)
                }
            }
            .pickerStyle(.segmented)
            .labelsHidden()
            .padding(.horizontal, 20)
            .padding(.top, 16)
            .padding(.bottom, 12)

            Divider().background(Palette.rule)

            VStack(alignment: .leading, spacing: 5) {
                Text(selectedTab.title)
                    .font(AppFont.sans(18, weight: .semibold))
                    .foregroundStyle(Palette.ink)
                Text(selectedTab.detail)
                    .font(AppFont.sans(12))
                    .foregroundStyle(Palette.muted)
                    .fixedSize(horizontal: false, vertical: true)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.horizontal, 20)
            .padding(.top, 16)
            .padding(.bottom, 4)

            Group {
                switch selectedTab {
                case .config:
                    ConfigPageView(config: config, workspaceName: model.workspace?.lastPathComponent)
                case .appearance:
                    AppearancePageView()
                case .policy:
                    PolicyPageView(config: config, evaluation: model.evaluation)
                case .context:
                    ContextPageView(config: config)
                case .updates:
                    VStack(alignment: .leading, spacing: 18) {
                        Text(
                            "Arabica \(Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "Development")"
                        )
                        .foregroundStyle(Palette.muted)
                        Toggle(
                            "Automatically check for updates",
                            isOn: Binding(
                                get: { updater.automaticallyChecksForUpdates },
                                set: { updater.setAutomaticChecks($0) }
                            ))
                        Toggle(
                            "Download updates and install when quitting",
                            isOn: Binding(
                                get: { updater.automaticallyDownloadsUpdates },
                                set: { updater.setAutomaticDownloads($0) }
                            )
                        )
                        .disabled(!updater.automaticallyChecksForUpdates)
                        Button("Check for Updates…") { updater.checkForUpdates() }
                            .disabled(!updater.canCheckForUpdates)
                        if updater.waitingForRuns {
                            Text("The update will restart Arabica after activity in all conversation windows finishes.")
                                .foregroundStyle(Palette.muted)
                        }
                        if !updater.isAvailable {
                            Text("Updates are available in packaged Arabica releases.")
                                .foregroundStyle(Palette.muted)
                        }
                        Spacer()
                    }
                    .padding(24)
                    .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
                    .disabled(!updater.isAvailable)
                }
            }

            Divider().background(Palette.rule)

            VStack(alignment: .leading, spacing: 10) {
                if !config.status.isEmpty,
                    config.statusKind != .success || !config.hasUnsavedChanges
                {
                    HStack(alignment: .top, spacing: 7) {
                        Image(
                            systemName: config.statusKind == .error
                                ? "exclamationmark.triangle.fill"
                                : config.statusKind == .success ? "checkmark.circle.fill" : "info.circle"
                        )
                        .foregroundStyle(config.statusKind == .error ? Color.orange : Palette.accent)
                        Text(config.status)
                            .foregroundStyle(config.statusKind == .error ? Color.orange : Palette.muted)
                            .fixedSize(horizontal: false, vertical: true)
                            .textSelection(.enabled)
                        if config.statusKind == .success, let workspace = model.workspace {
                            Spacer(minLength: 4)
                            Button("Reconnect") { Task { await model.reconnectWorkspace(workspace) } }
                                .disabled(model.hasActivityPreventingRelaunch)
                                .help(
                                    model.hasActivityPreventingRelaunch
                                        ? "Wait for current activity and approvals to finish before reconnecting"
                                        : "Reconnect this workspace to apply the saved configuration")
                        }
                    }
                    .font(AppFont.sans(11))
                    .accessibilityElement(children: .combine)
                }

                HStack(spacing: 10) {
                    if config.hasUnsavedChanges {
                        Label("Unsaved configuration changes", systemImage: "circle.fill")
                            .font(AppFont.sans(11))
                            .foregroundStyle(Palette.muted)
                    } else if selectedTab == .appearance {
                        Label("Saved automatically", systemImage: "checkmark.circle")
                            .font(AppFont.sans(11))
                            .foregroundStyle(Palette.muted)
                    }
                    Spacer(minLength: 8)
                    if selectedTab != .appearance && selectedTab != .updates {
                        Button("Edit config.toml…") { config.openConfigFile() }
                            .help(config.configURL.path)
                        Button("Reload…") { config.requestReload() }
                            .help("Read configuration from disk and refresh discovered skills")
                    }
                    if selectedTab == .config || selectedTab == .policy || config.hasUnsavedChanges {
                        Button("Save Changes") { config.save() }
                            .buttonStyle(.borderedProminent)
                            .keyboardShortcut("s", modifiers: .command)
                            .disabled(
                                config.isSaving || config.isChatGPTAuthRunning || config.isSyncingModels
                                    || !config.hasUnsavedChanges
                            )
                            .help(
                                config.isChatGPTAuthRunning || config.isSyncingModels
                                    ? "Wait for the account or model operation to finish before saving."
                                    : "Save this configuration draft.")
                    }
                }
                .font(AppFont.sans(12))
            }
        }
        .frame(minWidth: 660, idealWidth: 760, minHeight: 620, idealHeight: 700)
        .background(Palette.canvas)
        .background(SettingsWindowGuard(config: config))
        .confirmationDialog(
            "Save configuration changes?",
            isPresented: Binding(
                get: { config.pendingAction != nil },
                set: { if !$0 { config.pendingAction = nil } }
            ), titleVisibility: .visible, presenting: config.pendingAction
        ) { action in
            Button("Save Changes and Continue") { config.saveAndContinue(action) }
            Button("Discard Changes", role: .destructive) { config.discardAndContinue(action) }
            Button("Cancel", role: .cancel) { config.pendingAction = nil }
        } message: { _ in
            Text(
                "Switching entries or reloading will replace the current draft. Save it or discard it before continuing."
            )
        }
    }
}

// MARK: - 1. Config Page

private struct ConfigPageView: View {
    @ObservedObject var config: ConfigEditor
    let workspaceName: String?

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                // Provider Section
                SettingSection(
                    title: "Provider Connection",
                    subtitle: "Changes stay in this draft until you choose Save Changes."
                ) {
                    SettingRow(label: "Provider", description: "Configured provider instance name") {
                        HStack(spacing: 8) {
                            Picker(
                                "Provider",
                                selection: Binding(
                                    get: { config.providerName },
                                    set: { config.selectProvider($0) }
                                )
                            ) {
                                ForEach(
                                    config.providerOptions.isEmpty ? [config.providerName] : config.providerOptions,
                                    id: \.self
                                ) { Text($0).tag($0) }
                            }
                            .labelsHidden()
                            .frame(width: 140)
                            Button("Add Provider", systemImage: "plus") { config.addProvider() }
                                .font(AppFont.sans(11))
                                .disabled(!config.canAddEntries)
                                .help(
                                    config.canAddEntries
                                        ? "Create a provider and its first model"
                                        : "Save the first provider and model before adding another")
                        }
                    }
                    Divider().background(Palette.rule)

                    SettingRow(label: "Authentication", description: "Use an API key or sign in with a ChatGPT account")
                    {
                        Picker(
                            "Authentication",
                            selection: Binding(
                                get: { config.authMode },
                                set: { config.selectAuthMode($0) }
                            )
                        ) {
                            Text("API Key").tag("api_key")
                            Text("ChatGPT Login").tag("chatgpt")
                        }
                        .labelsHidden()
                        .frame(width: 180)
                    }
                    Divider().background(Palette.rule)

                    SettingRow(label: "API Protocol", description: "Payload format used to talk to the provider") {
                        Picker(
                            "API Protocol", selection: Binding(get: { config.apiType }, set: { config.setAPIType($0) })
                        ) {
                            let types = ["open_ai_responses", "open_ai_chat_completions", "anthropic_messages"]
                            if !types.contains(config.apiType) { Text(config.apiType).tag(config.apiType) }
                            Text("OpenAI Responses").tag("open_ai_responses")
                            Text("OpenAI Chat Completions").tag("open_ai_chat_completions")
                            Text("Anthropic Messages").tag("anthropic_messages")
                        }
                        .labelsHidden()
                        .frame(width: 220)
                        .disabled(config.authMode == "chatgpt")
                    }
                    Divider().background(Palette.rule)

                    SettingRow(label: "Base URL", description: "Target endpoint root URL") {
                        TextField("https://api.openai.com/v1", text: $config.baseURL)
                            .accessibilityLabel("Base URL")
                            .font(AppFont.code(12))
                            .frame(width: 240)
                            .disabled(config.authMode == "chatgpt")
                    }
                    Divider().background(Palette.rule)

                    if config.authMode == "chatgpt" {
                        SettingRow(
                            label: "ChatGPT Account",
                            description: "OAuth credentials are stored in your local Arabica home"
                        ) {
                            HStack(spacing: 10) {
                                Image(
                                    systemName: config.chatGPTConnected ? "checkmark.circle.fill" : "person.crop.circle"
                                )
                                .foregroundStyle(config.chatGPTConnected ? Color.green : Palette.muted)
                                Text(config.chatGPTConnected ? "Connected" : "Not connected")
                                    .font(AppFont.sans(12))
                                Button(config.chatGPTAuthButtonTitle) {
                                    if config.chatGPTConnected {
                                        config.disconnectChatGPT()
                                    } else {
                                        config.connectChatGPT()
                                    }
                                }
                                .disabled(!config.canChangeChatGPTConnection)
                                .help(config.chatGPTAuthHelp)
                            }
                        }
                    } else {
                        SettingRow(
                            label: "API Key Variable",
                            description: "Recommended: name of the environment variable containing your key"
                        ) {
                            TextField("ARABICA_PROVIDER_PRIMARY_API_KEY", text: $config.apiKeyEnv)
                                .accessibilityLabel("API Key Variable")
                                .font(AppFont.code(11))
                                .frame(width: 240)
                        }
                        Divider().background(Palette.rule)

                        SettingRow(
                            label: "API Key",
                            description: "Optional fallback stored in config.toml with owner-only file access"
                        ) {
                            HStack(spacing: 8) {
                                SecureField(
                                    config.hasSavedKey && !config.clearSavedKey ? "•••••••••••• (Saved)" : "sk-...",
                                    text: $config.apiKey
                                )
                                .accessibilityLabel("API Key")
                                .font(AppFont.code(12))
                                .frame(width: 170)
                                if config.hasSavedKey {
                                    Toggle("Remove saved key", isOn: $config.clearSavedKey)
                                        .font(AppFont.sans(11))
                                }
                            }
                        }
                    }
                    Divider().background(Palette.rule)

                    SettingRow(label: "Reasoning Effort", description: "Thinking / reasoning token budget level") {
                        Picker("Reasoning Effort", selection: $config.thinking) {
                            Text("Default").tag("")
                            let values = ConfigEditor.supportedThinkingValues(apiType: config.apiType)
                            if !config.thinking.isEmpty, !values.contains(config.thinking) {
                                Text("\(config.thinking) (unsupported)").tag(config.thinking)
                            }
                            ForEach(values, id: \.self) { Text($0).tag($0) }
                        }
                        .labelsHidden()
                        .frame(width: 140)
                    }
                }

                // Model Aliases Section
                SettingSection(
                    title: "Models for \(config.providerName)",
                    subtitle: "Choose a model alias, then set the provider's exact model ID."
                ) {
                    SettingRow(label: "Alias", description: "Routing alias mapped to this model") {
                        HStack(spacing: 8) {
                            Picker(
                                "Model Alias",
                                selection: Binding(
                                    get: { config.modelAlias },
                                    set: { config.selectModel($0) }
                                )
                            ) {
                                if config.modelAlias.isEmpty { Text("No models").tag("") }
                                ForEach(config.modelsForSelectedProvider, id: \.self) { Text($0).tag($0) }
                            }
                            .labelsHidden()
                            .frame(width: 140)
                            Button("Add Model", systemImage: "plus") { config.addModel() }
                                .font(AppFont.sans(11))
                                .disabled(!config.canAddEntries)
                        }
                    }
                    Divider().background(Palette.rule)
                    SettingRow(label: "Model ID", description: "Exact model identifier accepted by this provider") {
                        TextField("Enter a model ID", text: $config.modelID)
                            .accessibilityLabel("Model ID")
                            .font(AppFont.code(12))
                            .frame(width: 240)
                            .disabled(config.modelAlias.isEmpty)
                    }
                    Divider().background(Palette.rule)

                    SettingRow(
                        label: "Discover Models",
                        description: "Fetch available models from this provider and register them"
                    ) {
                        Button {
                            config.syncModels()
                        } label: {
                            HStack(spacing: 6) {
                                if config.isSyncingModels {
                                    ProgressView()
                                        .controlSize(.small)
                                } else {
                                    Image(systemName: "arrow.triangle.2.circlepath")
                                }
                                Text(config.isSyncingModels ? "Syncing…" : "Sync Provider Models")
                            }
                        }
                        .font(AppFont.sans(11))
                        .disabled(
                            config.isSyncingModels || config.isChatGPTAuthRunning || config.providerName.isEmpty
                                || config.hasUnsavedChanges
                        )
                        .help(
                            config.hasUnsavedChanges
                                ? "Save Changes or discard your draft before syncing provider models."
                                : "Discover and register models from the selected provider.")
                    }
                }

                // Desktop Context Section
                SettingSection(title: "Client & Workspace Context") {
                    SettingRow(label: "Agent Process", description: "Active agent protocol process host") {
                        Text("Bundled ACP").font(AppFont.code(12)).foregroundStyle(Palette.ink)
                    }
                    Divider().background(Palette.rule)
                    SettingRow(label: "Current Workspace", description: "Directory root selected in the main window") {
                        Text(workspaceName ?? "None selected").font(AppFont.code(12)).foregroundStyle(Palette.muted)
                    }
                }
            }
            .padding(20)
        }
        .textFieldStyle(.roundedBorder)
    }
}

// MARK: - 2. Policy Page (Configured & Evolved Policies with Flow Breakdown)

private struct PolicyPageView: View {
    @ObservedObject var config: ConfigEditor
    @ObservedObject var evaluation: EvaluationModel

    @State private var policyCategory: PolicyCategory = .configured
    @State private var selectedPolicyID: String = ""

    private enum PolicyCategory: String, CaseIterable, Identifiable {
        case configured = "Configured Policies"
        case candidate = "Evolved Candidates"
        var id: String { rawValue }
    }

    var body: some View {
        VStack(spacing: 0) {
            // Category & Policy Selector Header
            VStack(spacing: 12) {
                Picker("Policy Category", selection: $policyCategory) {
                    ForEach(PolicyCategory.allCases) { cat in
                        Text(cat.rawValue).tag(cat)
                    }
                }
                .pickerStyle(.segmented)

                HStack(spacing: 10) {
                    Text("Select Policy:")
                        .font(AppFont.sans(12, weight: .medium))
                        .foregroundStyle(Palette.ink)
                    Picker("Policy", selection: $selectedPolicyID) {
                        if policyCategory == .configured {
                            if config.configuredPolicies.isEmpty { Text("No configured policies").tag("") }
                            ForEach(config.configuredPolicies) { item in
                                Text(item.id + (item.id == config.defaultPolicy ? " (Default)" : "")).tag(item.id)
                            }
                        } else {
                            let records = evaluation.snapshot?.policies ?? []
                            if !records.isEmpty {
                                ForEach(records) { record in
                                    Text("\(record.policy_id) v\(record.version) [\(record.status)]").tag(record.id)
                                }
                            } else {
                                Text("No candidates available").tag("")
                            }
                        }
                    }
                    .labelsHidden()
                    .frame(maxWidth: .infinity)

                    if policyCategory == .configured, !selectedPolicyID.isEmpty {
                        Button(selectedPolicyID == config.defaultPolicy ? "Default Policy" : "Use as Default") {
                            config.selectPolicy(selectedPolicyID)
                        }
                        .font(AppFont.sans(11))
                        .disabled(selectedPolicyID == config.defaultPolicy)
                    }
                }
            }
            .padding(.horizontal, 20)
            .padding(.vertical, 14)
            .background(Palette.raised.opacity(0.4))

            Divider().background(Palette.rule)

            // Policy Details & Route Distribution Flow
            ScrollView {
                VStack(alignment: .leading, spacing: 18) {
                    if policyCategory == .configured {
                        if let policy = config.configuredPolicies.first(where: { $0.id == selectedPolicyID })
                            ?? config.configuredPolicies.first
                        {
                            configuredPolicyDetailView(policy)
                        } else {
                            emptyPolicyPlaceholder("No configured policies found in config.toml.")
                        }
                    } else {
                        let records = evaluation.snapshot?.policies ?? []
                        if let record = records.first(where: { $0.id == selectedPolicyID }) ?? records.first {
                            candidatePolicyDetailView(record)
                        } else {
                            emptyPolicyPlaceholder(
                                "No evolved candidates have been produced by background evaluation yet.")
                        }
                    }
                }
                .padding(20)
            }
        }
        .onAppear { synchronizeSelection() }
        .onChange(of: policyCategory) { _, _ in synchronizeSelection() }
        .onChange(of: config.configuredPolicies.map(\.id)) { _, _ in synchronizeSelection() }
        .onChange(of: evaluation.snapshot?.policies?.map(\.id)) { _, _ in synchronizeSelection() }
    }

    private func synchronizeSelection() {
        let options =
            policyCategory == .configured
            ? config.configuredPolicies.map(\.id)
            : evaluation.snapshot?.policies?.map(\.id) ?? []
        guard !options.contains(selectedPolicyID) else { return }
        selectedPolicyID =
            policyCategory == .configured && options.contains(config.defaultPolicy)
            ? config.defaultPolicy : options.first ?? ""
    }

    @ViewBuilder
    private func configuredPolicyDetailView(_ policy: ConfiguredPolicyItem) -> some View {
        SettingSection(
            title: "Policy: \(policy.id)",
            subtitle: "Routing allocation and state transition flow for this baseline policy"
        ) {
            SettingRow(label: "Policy ID", description: "Table key in [blend.policies.<id>]") {
                Text(policy.id).font(AppFont.code(12, weight: .semibold)).foregroundStyle(Palette.ink)
            }
            if let ver = policy.version {
                Divider().background(Palette.rule)
                SettingRow(label: "Version", description: "Configured version tag") {
                    Text("v\(ver)").font(AppFont.code(12)).foregroundStyle(Palette.muted)
                }
            }
        }

        // Routing Allocation Breakdown Flow
        SettingSection(
            title: "Routing Allocation & Transitions",
            subtitle: "Model dispatch targets for initial requests and lifecycle execution events"
        ) {
            VStack(spacing: 8) {
                FlowRouteStepRow(
                    icon: "target",
                    iconColor: .green,
                    title: "Initial / Default Model",
                    targetModel: policy.defaultModel,
                    detail: "Dispatched at the start of each conversation turn"
                )
                Divider().background(Palette.rule)
                FlowRouteStepRow(
                    icon: "checkmark.circle.fill",
                    iconColor: .blue,
                    title: "After Tool Success",
                    targetModel: policy.afterToolSuccess,
                    detail: "Transitions immediately when a tool call finishes successfully"
                )
                Divider().background(Palette.rule)
                FlowRouteStepRow(
                    icon: "exclamationmark.triangle.fill",
                    iconColor: .orange,
                    title: "After Tool Error",
                    targetModel: policy.afterToolError,
                    detail: "Switches to stronger reasoning model when a tool encounters an error"
                )
                Divider().background(Palette.rule)
                FlowRouteStepRow(
                    icon: "arrow.counterclockwise.circle.fill",
                    iconColor: .purple,
                    title: "Stall Recovery Model",
                    targetModel: policy.recoveryModel,
                    detail: "Intervenes if the conversation makes no forward progress"
                )
                if let planning = policy.planningModel {
                    Divider().background(Palette.rule)
                    FlowRouteStepRow(
                        icon: "map.fill",
                        iconColor: .teal,
                        title: "Planning Model",
                        targetModel: planning,
                        detail: "Dedicated model assigned for multi-step execution plans"
                    )
                }
            }
            .padding(12)
        }

        // Safeguards & Thresholds
        SettingSection(
            title: "Stability Safeguards",
            subtitle: "Hysteresis step controls to prevent model thrashing"
        ) {
            SettingRow(
                label: "Minimum Model Dwell Steps",
                description: "Consecutive steps a model must retain before switching"
            ) {
                Text("\(policy.dwellSteps ?? 1) steps").font(AppFont.code(12)).foregroundStyle(Palette.ink)
            }
            Divider().background(Palette.rule)
            SettingRow(
                label: "Stall Recovery Trigger", description: "Unproductive steps before recovery model takes over"
            ) {
                Text("\(policy.stallThreshold ?? 2) steps").font(AppFont.code(12)).foregroundStyle(Palette.ink)
            }
        }
    }

    @ViewBuilder
    private func candidatePolicyDetailView(_ record: EvaluationSnapshot.PolicyRecord) -> some View {
        SettingSection(
            title: "\(record.policy_id) (v\(record.version))",
            subtitle: "Evolved candidate proposed by background evaluation engine"
        ) {
            SettingRow(label: "Lifecycle Status", description: "Evaluation candidate state") {
                Text(record.status.uppercased())
                    .font(AppFont.code(11, weight: .bold))
                    .foregroundStyle(record.status == "accepted" ? Color.green : Color.orange)
            }
            Divider().background(Palette.rule)
            SettingRow(label: "Evolution Reason", description: "Trigger condition detected during trajectory analysis")
            {
                Text(record.reason)
                    .font(AppFont.sans(11))
                    .foregroundStyle(Palette.muted)
                    .multilineTextAlignment(.trailing)
            }
        }

        // Flow breakdown
        SettingSection(
            title: "Candidate Routing Flow",
            subtitle: "Model routing parameters evolved for this candidate version"
        ) {
            VStack(spacing: 8) {
                FlowRouteStepRow(
                    icon: "target",
                    iconColor: .green,
                    title: "Initial / Default Model",
                    targetModel: record.policy.default_model,
                    detail: "Candidate primary model"
                )
                Divider().background(Palette.rule)
                FlowRouteStepRow(
                    icon: "checkmark.circle.fill",
                    iconColor: .blue,
                    title: "After Tool Success",
                    targetModel: record.policy.after_tool_success,
                    detail: "Candidate post-tool success model"
                )
                Divider().background(Palette.rule)
                FlowRouteStepRow(
                    icon: "exclamationmark.triangle.fill",
                    iconColor: .orange,
                    title: "After Tool Error",
                    targetModel: record.policy.after_tool_error,
                    detail: "Candidate error recovery model"
                )
                Divider().background(Palette.rule)
                FlowRouteStepRow(
                    icon: "arrow.counterclockwise.circle.fill",
                    iconColor: .purple,
                    title: "Stall Recovery Model",
                    targetModel: record.policy.recovery_model,
                    detail: "Candidate stall intervention model"
                )
            }
            .padding(12)
        }

        SettingSection(title: "Candidate Stability Settings") {
            SettingRow(label: "Minimum Model Dwell Steps", description: "Dwell steps threshold") {
                Text("\(record.policy.minimum_model_dwell_steps ?? 1) steps").font(AppFont.code(12)).foregroundStyle(
                    Palette.ink)
            }
            Divider().background(Palette.rule)
            SettingRow(label: "Stall Recovery Trigger", description: "No-progress threshold") {
                Text("\(record.policy.recovery_after_no_progress_steps ?? 2) steps").font(AppFont.code(12))
                    .foregroundStyle(Palette.ink)
            }
        }
    }

    private func emptyPolicyPlaceholder(_ message: String) -> some View {
        VStack(spacing: 12) {
            Image(systemName: "point.3.filled.connected.trianglepath.dotted")
                .font(.system(size: 34))
                .foregroundStyle(Palette.muted.opacity(0.5))
            Text(message)
                .font(AppFont.sans(12))
                .foregroundStyle(Palette.muted)
                .multilineTextAlignment(.center)
        }
        .frame(maxWidth: .infinity)
        .padding(36)
        .background(RoundedRectangle(cornerRadius: 8).fill(Palette.raised))
    }
}

// MARK: - 3. Context Page (Tool - Skill - MCP)

private struct ContextPageView: View {
    @ObservedObject var config: ConfigEditor
    @State private var subtab: ContextSubtab = .mcp

    private enum ContextSubtab: String, CaseIterable, Identifiable {
        case tools = "Built-in Tools"
        case skills = "Skills"
        case mcp = "MCP Servers"
        var id: String { rawValue }
    }

    var body: some View {
        VStack(spacing: 0) {
            Picker("Context Category", selection: $subtab) {
                ForEach(ContextSubtab.allCases) { tab in
                    Text(tab.rawValue).tag(tab)
                }
            }
            .pickerStyle(.segmented)
            .padding(.horizontal, 20)
            .padding(.vertical, 12)
            .background(Palette.raised.opacity(0.4))

            Divider().background(Palette.rule)

            ScrollView {
                VStack(alignment: .leading, spacing: 18) {
                    switch subtab {
                    case .tools:
                        toolsSection
                    case .skills:
                        skillsSection
                    case .mcp:
                        mcpSection
                    }
                }
                .padding(20)
            }
        }
    }

    @ViewBuilder
    private var toolsSection: some View {
        SettingSection(
            title: "Native Execution Tools",
            subtitle: "System capabilities managed directly by the Structure runtime runner"
        ) {
            SettingRow(label: "Filesystem Execution", description: "read_file, write_file, edit_file, list_dir, grep") {
                Text("Built in").font(AppFont.code(11)).foregroundStyle(Palette.muted)
            }
            Divider().background(Palette.rule)
            SettingRow(label: "Terminal & Subprocess", description: "Persistent interactive bash sessions with PTY") {
                Text("Runtime managed").font(AppFont.code(11)).foregroundStyle(Palette.muted)
            }
            Divider().background(Palette.rule)
            SettingRow(label: "Session Short Memory", description: "Prefix cache, bounded short-memory projection & GC")
            {
                Text("Runtime managed").font(AppFont.code(11)).foregroundStyle(Palette.muted)
            }
            Divider().background(Palette.rule)
            SettingRow(label: "Context Unfold Engine", description: "Lazy discovery of folded tool directories") {
                Text("Runtime managed").font(AppFont.code(11)).foregroundStyle(Palette.muted)
            }
        }
    }

    @ViewBuilder
    private var skillsSection: some View {
        // Configured Roots
        SettingSection(
            title: "Skill Roots",
            subtitle: "Configured directory paths scanned for SKILL.md bundles"
        ) {
            if config.skillRoots.isEmpty {
                SettingRow(
                    label: "No Roots Configured", description: "Add `[skills] roots = [\"skills\"]` to config.toml"
                ) {
                    Text("None").font(AppFont.sans(11)).foregroundStyle(Palette.muted)
                }
            } else {
                ForEach(config.skillRoots) { root in
                    SettingRow(label: root.rawPath, description: root.resolvedURL.path) {
                        if root.exists {
                            Button("Reveal in Finder") {
                                NSWorkspace.shared.activateFileViewerSelecting([root.resolvedURL])
                            }
                            .font(AppFont.sans(11))
                        } else {
                            Text("Missing").font(AppFont.sans(11)).foregroundStyle(Color.orange)
                        }
                    }
                }
            }
        }

        // Discovered Skills
        VStack(alignment: .leading, spacing: 10) {
            Text("Discovered Skills (\(config.discoveredSkills.count))")
                .font(AppFont.sans(13, weight: .semibold))
                .foregroundStyle(Palette.ink)

            if config.discoveredSkills.isEmpty {
                SettingCard {
                    Text("No skills detected in configured roots. Create folders containing a `SKILL.md` file.")
                        .font(AppFont.sans(11))
                        .foregroundStyle(Palette.muted)
                }
            } else {
                ForEach(config.discoveredSkills) { skill in
                    SettingCard(title: skill.name, badge: "SKILL", badgeColor: .purple) {
                        if !skill.description.isEmpty {
                            Text(skill.description)
                                .font(AppFont.sans(11))
                                .foregroundStyle(Palette.muted)
                        }
                        HStack {
                            Text(skill.directoryURL.path)
                                .font(AppFont.code(10))
                                .foregroundStyle(Palette.muted.opacity(0.8))
                                .lineLimit(1)
                            Spacer()
                            Button("Open") {
                                NSWorkspace.shared.activateFileViewerSelecting([skill.directoryURL])
                            }
                            .font(AppFont.sans(11))
                        }
                    }
                }
            }
        }
    }

    @ViewBuilder
    private var mcpSection: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("Configured MCP Servers (\(config.mcpServers.count))")
                .font(AppFont.sans(13, weight: .semibold))
                .foregroundStyle(Palette.ink)

            if config.mcpServers.isEmpty {
                SettingCard {
                    Text("No MCP servers configured. Add `[[mcp]]` tables to config.toml.")
                        .font(AppFont.sans(11))
                        .foregroundStyle(Palette.muted)
                }
            } else {
                ForEach(config.mcpServers) { server in
                    SettingCard(
                        title: server.name,
                        badge: server.transportType.uppercased(),
                        badgeColor: server.transportType == "stdio" ? .blue : .green
                    ) {
                        if let cmd = server.command {
                            SettingRow(label: "Command", description: "Executable with CLI arguments") {
                                Text(([cmd] + server.args).joined(separator: " "))
                                    .font(AppFont.code(11))
                                    .foregroundStyle(Palette.ink)
                                    .textSelection(.enabled)
                            }
                        }
                        if let url = server.url {
                            SettingRow(label: "Endpoint", description: "HTTP SSE endpoint") {
                                Text(url)
                                    .font(AppFont.code(11))
                                    .foregroundStyle(Palette.ink)
                                    .textSelection(.enabled)
                            }
                        }
                        if !server.envKeys.isEmpty {
                            SettingRow(
                                label: "Environment Variables",
                                description: "Configured variable names; values are hidden"
                            ) {
                                Text(server.envKeys.joined(separator: ", "))
                                    .font(AppFont.code(10))
                                    .foregroundStyle(Palette.muted)
                            }
                        }
                    }
                }
            }
        }
    }
}
