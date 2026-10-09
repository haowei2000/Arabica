import SwiftUI

/// Global typography system with 3 unified font styles:
/// 1. Serif (衬线体) - Dedicated to body text (正文: chat message text, thoughts, composer draft)
/// 2. Sans-serif (非衬线体) - Used for all UI chrome, titles, navigation, buttons, labels
/// 3. Monospaced / Code (代码字体) - Used for code, diffs, terminal outputs, file paths, config keys
enum AppFont {
    /// 现代正文专用字体 (Sans)
    static func body(_ size: CGFloat = 13.5, weight: Font.Weight = .regular) -> Font {
        let delta = ThemeManager.shared.fontSizeDelta
        let design = ThemeManager.shared.fontFamily.design
        return .system(size: size + delta, weight: weight, design: design)
    }

    /// 非衬线体 (Sans-Serif) - 用于界面元素与标题
    static func sans(_ size: CGFloat, weight: Font.Weight = .regular) -> Font {
        let delta = ThemeManager.shared.fontSizeDelta
        let design = ThemeManager.shared.fontFamily.design
        return .system(size: size + delta, weight: weight, design: design)
    }

    /// 代码字体 (Monospaced) - 用于代码、终端、diff、路径、配置键等
    static func code(_ size: CGFloat, weight: Font.Weight = .regular) -> Font {
        let delta = ThemeManager.shared.fontSizeDelta
        return .system(size: size + delta, weight: weight, design: .monospaced)
    }

    /// 衬线体 (Serif)
    static func serif(_ size: CGFloat, weight: Font.Weight = .regular) -> Font {
        let delta = ThemeManager.shared.fontSizeDelta
        return .system(size: size + delta, weight: weight, design: .serif)
    }
}

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
    @StateObject private var model = DesktopModel()
    @ObservedObject private var theme = ThemeManager.shared

    var body: some Scene {
        WindowGroup {
            DesktopView()
                .environmentObject(model)
                .environmentObject(theme)
                .frame(minWidth: 540, minHeight: 480)
                .preferredColorScheme(theme.colorScheme)
                .ignoresSafeArea(.all, edges: .top)
        }
        .windowStyle(.hiddenTitleBar)
        .commands {
            CommandGroup(replacing: .newItem) {
                Button("New Conversation") { Task { await model.newSession() } }
                    .keyboardShortcut("n")
            }
            CommandGroup(replacing: .sidebar) {
                Button("Toggle Sidebar") {
                    NSApp.sendAction(#selector(NSSplitViewController.toggleSidebar(_:)), to: nil, from: nil)
                }
                .keyboardShortcut("s", modifiers: [.command, .option])
            }
            CommandMenu("Workspace") {
                Button("Open Workspace…") { model.chooseWorkspace() }
                    .keyboardShortcut("o")
                Button("Stop Run") { model.cancel() }
                    .keyboardShortcut(".")
                    .disabled(!model.isRunning)
            }
        }
        Settings {
            SettingsView()
                .environmentObject(model)
                .environmentObject(theme)
                .frame(width: 660, height: 700)
                .preferredColorScheme(theme.colorScheme)
        }
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
        window.titlebarAppearsTransparent = true
        window.styleMask.insert(.fullSizeContentView)
        window.toolbar = nil
        window.isMovableByWindowBackground = true

        let isDark = ThemeManager.shared.isDark
        window.appearance = NSAppearance(named: isDark ? .darkAqua : .aqua)
    }
}

private struct DesktopView: View {
    @EnvironmentObject var model: DesktopModel
    @ObservedObject private var theme = ThemeManager.shared
    @AppStorage("showEvaluationInspector") private var showEvaluation = false
    @State private var columnVisibility: NavigationSplitViewVisibility = .all
    @State private var sidebarWidth: CGFloat = 260

    var body: some View {
        HStack(spacing: 0) {
            if columnVisibility != .detailOnly {
                ProjectsSidebarView(model: model, columnVisibility: $columnVisibility)
                    .frame(width: sidebarWidth)
                    .transition(.move(edge: .leading))

                Rectangle()
                    .fill(Palette.rule)
                    .frame(width: 1)
            }

            conversation
                .frame(maxWidth: .infinity)
        }
        .background(WindowConfigurator())
        .ignoresSafeArea(.all, edges: .top)
        .background(Palette.canvas)
        .inspector(isPresented: $showEvaluation) {
            EvaluationPanel(model: model.evaluation, isVisible: showEvaluation)
                .inspectorColumnWidth(min: 280, ideal: 330, max: 440)
        }
        .sheet(item: $model.permission) { prompt in
            PermissionView(prompt: prompt) { option in model.resolvePermission(option) }
        }
    }

    private var conversation: some View {
        VStack(spacing: 0) {
            topBar
            Divider().overlay(Palette.rule)

            if !model.plan.isEmpty {
                PlanProgressView(plan: model.plan)
            }

            if model.selectedSessionID == nil {
                emptyState
            } else {
                ScrollViewReader { proxy in
                    ScrollView {
                        LazyVStack(alignment: .leading, spacing: 16) {
                            ForEach(model.items.groupedForFeed) { group in
                                ChatGroupRow(group: group, isRunning: model.isRunning)
                                    .id(group.id)
                            }
                            if model.isRunning {
                                HStack(spacing: 8) {
                                    ProgressView().controlSize(.small)
                                    Text("Arabica is working…")
                                        .font(AppFont.sans(12))
                                        .foregroundStyle(Palette.muted)
                                }
                                .padding(.top, 4)
                            }
                        }
                        .frame(maxWidth: 760)
                        .frame(maxWidth: .infinity)
                        .padding(.horizontal, 24)
                        .padding(.vertical, 20)
                    }
                    .onChange(of: model.items.count) { _, _ in
                        if let last = model.items.last { proxy.scrollTo(last.id, anchor: .bottom) }
                    }
                }
            }

            if let error = model.errorText {
                HStack(spacing: 6) {
                    Image(systemName: "exclamationmark.triangle.fill")
                        .foregroundStyle(.orange)
                    Text(error)
                        .font(AppFont.sans(12))
                        .foregroundStyle(.orange)
                        .lineLimit(2)
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

    // MARK: - Single-Line Unified Breadcrumb Top Bar (38pt)

    private var topBar: some View {
        HStack(spacing: 8) {
            if columnVisibility == .detailOnly {
                Color.clear.frame(width: 68, height: 1)
                Button {
                    withAnimation(.easeInOut(duration: 0.18)) {
                        columnVisibility = .all
                    }
                } label: {
                    Image(systemName: "sidebar.left")
                        .font(AppFont.sans(11))
                        .foregroundStyle(Palette.muted)
                }
                .buttonStyle(.plain)
                .help("Show Sidebar (Cmd+Opt+S)")
            }

            // Breadcrumb: ProjectName / SessionTitle
            HStack(spacing: 7) {
                let projectName = model.workspace.map { DesktopModel.projectName(for: $0.path) } ?? "Workspace"
                Button(action: model.chooseWorkspace) {
                    Text(projectName)
                        .font(AppFont.sans(12.5, weight: .regular))
                        .foregroundStyle(Palette.muted)
                }
                .buttonStyle(.plain)
                .help("Workspace: \(model.workspace?.path ?? "None") (Click to change)")

                Text("/")
                    .font(AppFont.sans(11, weight: .regular))
                    .foregroundStyle(Palette.subtle)

                let currentTitle = model.sessions.first(where: { $0.id == model.selectedSessionID })?.title ?? "New Conversation"
                Text(currentTitle)
                    .font(AppFont.sans(12.5, weight: .semibold))
                    .foregroundStyle(Palette.ink)
                    .lineLimit(1)

                Circle()
                    .fill(model.isConnected ? Color.green.opacity(0.85) : (model.errorText != nil ? Color.red : Color.orange))
                    .frame(width: 5.5, height: 5.5)
                    .help(model.isConnected ? "Agent connected" : (model.errorText ?? "Agent connecting/disconnected"))
            }

            Spacer(minLength: 8)

            buttonSection
        }
        .padding(.leading, columnVisibility == .detailOnly ? 0 : 12)
        .padding(.trailing, 12)
        .frame(height: 38)
        .background(Palette.canvas)
    }

    private var buttonSection: some View {
        HStack(spacing: 6) {
            if let used = model.usage["used"] as? Int,
               let size = model.usage["size"] as? Int {
                HStack(spacing: 3) {
                    Image(systemName: "gauge.with.needle")
                        .font(AppFont.sans(8))
                    Text("\(used)/\(size)")
                        .font(AppFont.code(9))
                }
                .foregroundStyle(Palette.muted)
                .padding(.horizontal, 5)
                .padding(.vertical, 2)
                .background(Palette.raised.opacity(0.6), in: Capsule())
                .help("\(used) of \(size) tokens used")
            }

            if model.isRunning {
                Button(action: model.cancel) {
                    HStack(spacing: 3) {
                        Image(systemName: "stop.fill")
                            .font(AppFont.sans(7.5))
                        Text("Stop")
                            .font(AppFont.sans(10, weight: .medium))
                    }
                    .foregroundStyle(.red)
                    .padding(.horizontal, 6)
                    .padding(.vertical, 2)
                    .background(Color.red.opacity(0.12), in: Capsule())
                    .overlay(Capsule().stroke(Color.red.opacity(0.3), lineWidth: 1))
                }
                .buttonStyle(.plain)
                .help("Stop execution (Cmd+.)")
            }

            Button {
                Task { await model.newSession() }
            } label: {
                Image(systemName: "square.and.pencil")
                    .font(AppFont.sans(11))
                    .foregroundStyle(Palette.ink)
                    .frame(width: 20, height: 20)
                    .background(Palette.raised.opacity(0.6), in: RoundedRectangle(cornerRadius: 4))
            }
            .buttonStyle(.plain)
            .disabled(!model.isConnected || model.isConnecting)
            .help("New Conversation (Cmd+N)")

            Button {
                showEvaluation.toggle()
            } label: {
                Image(systemName: "chart.bar.doc.horizontal")
                    .font(AppFont.sans(11))
                    .foregroundStyle(showEvaluation ? Palette.accent : Palette.muted)
                    .frame(width: 20, height: 20)
                    .background(showEvaluation ? Palette.accent.opacity(0.15) : Palette.raised.opacity(0.6), in: RoundedRectangle(cornerRadius: 4))
            }
            .buttonStyle(.plain)
            .help(showEvaluation ? "Hide evaluation panel (Cmd+Opt+I)" : "Show evaluation panel (Cmd+Opt+I)")

            Button {
                withAnimation(.easeInOut(duration: 0.18)) {
                    theme.cycleMode()
                }
            } label: {
                Image(systemName: theme.mode.icon)
                    .font(AppFont.sans(10.5))
                    .foregroundStyle(Palette.muted)
                    .frame(width: 20, height: 20)
                    .background(Palette.raised.opacity(0.6), in: RoundedRectangle(cornerRadius: 4))
            }
            .buttonStyle(.plain)
            .help("Theme: \(theme.mode.rawValue) (Click to switch)")
        }
    }

    private var emptyState: some View {
        VStack(spacing: 16) {
            Spacer()
            Image(systemName: model.workspace == nil ? "folder.badge.gearshape" : "sparkles")
                .font(.system(size: 36))
                .foregroundStyle(Palette.accent.opacity(0.85))

            Text(model.workspace == nil ? "Open a workspace to begin" : "How can I help you today?")
                .font(AppFont.sans(22, weight: .semibold))
                .foregroundStyle(Palette.ink)

            Text(model.workspace == nil
                 ? "Select a workspace folder to start working with Arabica."
                 : "Ask a question, generate code, inspect files, or run tests.")
                .font(AppFont.sans(13))
                .foregroundStyle(Palette.muted)
                .multilineTextAlignment(.center)
                .frame(maxWidth: 380)

            Button(model.workspace == nil || !model.isConnected ? "Open Workspace" : "New Conversation") {
                if model.workspace == nil || !model.isConnected {
                    model.chooseWorkspace()
                } else {
                    Task { await model.newSession() }
                }
            }
            .buttonStyle(.borderedProminent)
            .tint(Palette.ink)
            .foregroundStyle(Palette.canvas)
            .font(AppFont.sans(13, weight: .medium))
            .padding(.top, 6)

            Spacer()
        }
        .frame(maxWidth: .infinity)
        .padding(.horizontal, 28)
    }
}

// MARK: - Projects Sidebar View

private struct ProjectsSidebarView: View {
    @ObservedObject var model: DesktopModel
    @Binding var columnVisibility: NavigationSplitViewVisibility
    @State private var expandedProjects = Set<String>()

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            // Header: Traffic lights clearance (68pt) + Action Icons + Menu
            HStack(spacing: 7) {
                // Reserved for macOS traffic lights (🔴 🟡 🟢)
                Color.clear.frame(width: 68, height: 1)

                Button {
                    withAnimation(.easeInOut(duration: 0.18)) {
                        columnVisibility = (columnVisibility == .detailOnly ? .all : .detailOnly)
                    }
                } label: {
                    Image(systemName: "sidebar.left")
                        .font(AppFont.sans(11))
                        .foregroundStyle(Palette.muted)
                }
                .buttonStyle(.plain)
                .help("Toggle Sidebar (Cmd+Opt+S)")

                Button { } label: {
                    Image(systemName: "chevron.left")
                        .font(AppFont.sans(10))
                        .foregroundStyle(Palette.subtle.opacity(0.45))
                }
                .buttonStyle(.plain)
                .disabled(true)

                Button { } label: {
                    Image(systemName: "chevron.right")
                        .font(AppFont.sans(10))
                        .foregroundStyle(Palette.subtle.opacity(0.45))
                }
                .buttonStyle(.plain)
                .disabled(true)

                Button { } label: {
                    Image(systemName: "magnifyingglass")
                        .font(AppFont.sans(11))
                        .foregroundStyle(Palette.muted)
                }
                .buttonStyle(.plain)
                .help("Search")

                Spacer()

                Menu {
                    Button("Open Project Folder…") {
                        model.chooseWorkspace()
                    }
                    Button("Refresh Projects") {
                        Task { await model.refreshSessions() }
                    }
                } label: {
                    Image(systemName: "ellipsis")
                        .font(AppFont.sans(11))
                        .foregroundStyle(Palette.muted)
                }
                .menuStyle(.borderlessButton)
                .menuIndicator(.hidden)
                .frame(width: 18, height: 18)

                Button {
                    model.chooseWorkspace()
                } label: {
                    Image(systemName: "folder.badge.plus")
                        .font(AppFont.sans(11))
                        .foregroundStyle(Palette.muted)
                }
                .buttonStyle(.plain)
                .help("Open Project Folder…")
            }
            .padding(.trailing, 10)
            .frame(height: 38)

            Divider().overlay(Palette.rule)

            if model.projectGroups.isEmpty {
                VStack(spacing: 8) {
                    Spacer()
                    Image(systemName: "folder")
                        .font(.system(size: 26))
                        .foregroundStyle(Palette.subtle)
                    Text("No Projects")
                        .font(AppFont.sans(12))
                        .foregroundStyle(Palette.muted)
                    Button("Open Workspace…") {
                        model.chooseWorkspace()
                    }
                    .font(AppFont.sans(11.5))
                    .buttonStyle(.plain)
                    .foregroundStyle(Palette.accent)
                    Spacer()
                }
                .frame(maxWidth: .infinity)
            } else {
                ScrollView {
                    LazyVStack(alignment: .leading, spacing: 14) {
                        ForEach(model.projectGroups) { project in
                            ProjectSectionView(
                                project: project,
                                selectedSessionID: model.selectedSessionID,
                                isRunning: model.isRunning,
                                isExpanded: expandedProjects.contains(project.id),
                                onToggleExpanded: {
                                    if expandedProjects.contains(project.id) {
                                        expandedProjects.remove(project.id)
                                    } else {
                                        expandedProjects.insert(project.id)
                                    }
                                },
                                onSelectSession: { session in
                                    Task { await model.selectSession(session.id) }
                                },
                                onNewSession: {
                                    Task { await model.newSession(in: project.path) }
                                }
                            )
                        }
                    }
                    .padding(.horizontal, 10)
                    .padding(.top, 10)
                    .padding(.bottom, 16)
                }
            }
        }
        .background(Palette.sidebar)
    }
}

private struct ProjectSectionView: View {
    let project: ProjectGroup
    let selectedSessionID: String?
    let isRunning: Bool
    let isExpanded: Bool
    let onToggleExpanded: () -> Void
    let onSelectSession: (DesktopSession) -> Void
    let onNewSession: () -> Void

    private var visibleSessions: [DesktopSession] {
        if isExpanded || project.sessions.count <= 5 {
            return project.sessions
        }
        return Array(project.sessions.prefix(5))
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 3) {
            // Project Header Row
            HStack(spacing: 7) {
                Image(systemName: "folder")
                    .font(AppFont.sans(12))
                    .foregroundStyle(Palette.ink.opacity(0.85))
                Text(project.name)
                    .font(AppFont.sans(13, weight: .medium))
                    .foregroundStyle(Palette.ink)
                    .lineLimit(1)
                Spacer()
            }
            .padding(.horizontal, 8)
            .padding(.vertical, 4)
            .contentShape(Rectangle())
            .contextMenu {
                Button("New Conversation in \(project.name)") {
                    onNewSession()
                }
                Button("Reveal in Finder") {
                    NSWorkspace.shared.activateFileViewerSelecting([URL(fileURLWithPath: project.path)])
                }
            }

            // Sessions under this project
            VStack(alignment: .leading, spacing: 2) {
                ForEach(visibleSessions) { session in
                    SessionRowView(
                        session: session,
                        isSelected: session.id == selectedSessionID,
                        isRunning: isRunning && session.id == selectedSessionID,
                        onSelect: { onSelectSession(session) }
                    )
                }

                if project.sessions.count > 5 {
                    Button(action: onToggleExpanded) {
                        Text(isExpanded ? "Show less" : "Show more")
                            .font(AppFont.sans(12))
                            .foregroundStyle(Palette.subtle)
                            .padding(.leading, 12)
                            .padding(.vertical, 4)
                    }
                    .buttonStyle(.plain)
                }
            }
            .padding(.leading, 8)
        }
    }
}

private struct SessionRowView: View {
    let session: DesktopSession
    let isSelected: Bool
    let isRunning: Bool
    let onSelect: () -> Void
    @State private var isHovered = false

    var body: some View {
        Button(action: onSelect) {
            HStack(spacing: 6) {
                Text(session.title)
                    .font(AppFont.sans(12.5))
                    .foregroundStyle(isSelected ? Palette.ink : Palette.ink.opacity(0.82))
                    .lineLimit(1)
                    .truncationMode(.tail)

                Spacer(minLength: 4)

                HStack(spacing: 5) {
                    if isHovered || session.cwd.contains("worktrees") || session.cwd.contains("develop") {
                        Image(systemName: "arrow.up.right")
                            .font(AppFont.sans(9.5))
                            .foregroundStyle(Palette.muted)
                    }

                    if isRunning {
                        ProgressView()
                            .controlSize(.mini)
                    } else if isSelected {
                        Circle()
                            .strokeBorder(Palette.subtle, lineWidth: 1.2)
                            .frame(width: 9, height: 9)
                    } else {
                        Image(systemName: "point.3.connected.trianglepath")
                            .font(AppFont.sans(10))
                            .foregroundStyle(Color(red: 175 / 255, green: 110 / 255, blue: 245 / 255))
                    }
                }
            }
            .padding(.horizontal, 10)
            .padding(.vertical, 6)
            .background(
                isSelected
                    ? Palette.selectionBg
                    : (isHovered ? Palette.hoverBg : Color.clear),
                in: RoundedRectangle(cornerRadius: 6)
            )
        }
        .buttonStyle(.plain)
        .onHover { isHovered = $0 }
        .contextMenu {
            Button("Select Conversation") { onSelect() }
            Button("Reveal in Finder") {
                NSWorkspace.shared.activateFileViewerSelecting([URL(fileURLWithPath: session.cwd)])
            }
            Button("Copy Title") {
                NSPasteboard.general.clearContents()
                NSPasteboard.general.setString(session.title, forType: .string)
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
                .font(AppFont.body(13.5))
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
                        .font(AppFont.sans(11.5, weight: .medium))
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

        if k == "execute" || t.contains("terminal") || t.contains("bash") || t.contains("command") || t.hasPrefix("cargo") || t.hasPrefix("run") {
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
        var url = Bundle.main.url(forResource: name, withExtension: "png", subdirectory: "tool-icons")
            ?? Bundle.main.url(forResource: name, withExtension: "png")
        #if SWIFT_PACKAGE
        if url == nil {
            url = Bundle.module.url(forResource: name, withExtension: "png", subdirectory: "tool-icons")
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

        if k == "execute" || t.contains("terminal") || t.contains("bash") || t.contains("command") || t.hasPrefix("cargo") || t.hasPrefix("test") {
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
                        .font(AppFont.code(12))
                        .foregroundStyle(Palette.ink)
                        .lineLimit(1)

                    Spacer(minLength: 8)

                    if isAnyRunning {
                        HStack(spacing: 5) {
                            ProgressView().controlSize(.mini)
                            Text("Running…")
                                .font(AppFont.sans(11))
                                .foregroundStyle(Palette.accent)
                        }
                    } else if hasAnyFailed {
                        HStack(spacing: 4) {
                            Image(systemName: "exclamationmark.circle.fill")
                                .font(AppFont.sans(11))
                                .foregroundStyle(.red)
                            Text("Failed")
                                .font(AppFont.sans(11))
                                .foregroundStyle(.red)
                        }
                    } else {
                        HStack(spacing: 4) {
                            Image(systemName: "checkmark.circle.fill")
                                .font(AppFont.sans(11))
                                .foregroundStyle(Color.green.opacity(0.85))
                            if tools.count > 1 {
                                Text("\(tools.count)")
                                    .font(AppFont.code(10))
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
                        .font(AppFont.code(11.5))
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
                        .font(AppFont.sans(12, weight: .semibold))
                        .foregroundStyle(Palette.ink)
                    Text("\(completedCount)/\(plan.count) completed")
                        .font(AppFont.sans(11))
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
                                .font(AppFont.sans(11.5))
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

// MARK: - Composer View

private struct ComposerView: View {
    @ObservedObject var model: DesktopModel
    @ObservedObject private var themeManager = ThemeManager.shared

    var body: some View {
        VStack(spacing: 0) {
            VStack(alignment: .leading, spacing: 8) {
                // Attached files chips
                if !model.attachedFiles.isEmpty {
                    ScrollView(.horizontal) {
                        HStack(spacing: 6) {
                            ForEach(model.attachedFiles, id: \.self) { file in
                                HStack(spacing: 5) {
                                    Image(systemName: "paperclip")
                                        .font(AppFont.sans(10))
                                    Text(file.lastPathComponent)
                                        .font(AppFont.code(11))
                                        .lineLimit(1)
                                    Button {
                                        model.attachedFiles.removeAll { $0 == file }
                                    } label: {
                                        Image(systemName: "xmark")
                                            .font(AppFont.sans(9, weight: .bold))
                                    }
                                    .buttonStyle(.plain)
                                }
                                .padding(.horizontal, 8)
                                .padding(.vertical, 4)
                                .background(Palette.card, in: Capsule())
                                .overlay(Capsule().stroke(Palette.rule, lineWidth: 1))
                            }
                        }
                    }
                    .scrollIndicators(.hidden)
                }

                // Input text field
                TextField("Send a message… (⏎ to send)", text: $model.draft, axis: .vertical)
                    .textFieldStyle(.plain)
                    .font(AppFont.body(13.5))
                    .foregroundStyle(Palette.ink)
                    .lineLimit(1...8)
                    .padding(.horizontal, 2)
                    .padding(.top, 2)
                    .onSubmit { Task { await model.send() } }

                // Bottom toolbar inside composer card
                HStack(spacing: 8) {
                    Button(action: model.chooseFiles) {
                        Image(systemName: "paperclip")
                            .font(AppFont.sans(13))
                            .foregroundStyle(Palette.muted)
                            .frame(width: 26, height: 26)
                    }
                    .buttonStyle(.plain)
                    .help("Attach files")

                    if !model.availableCommands.isEmpty {
                        Menu {
                            ForEach(Array(model.availableCommands.enumerated()), id: \.offset) { _, cmd in
                                if let name = cmd["name"] as? String {
                                    Button("/\(name)") { model.draft = "/\(name) " }
                                }
                            }
                        } label: {
                            Image(systemName: "slash.circle")
                                .font(AppFont.sans(13))
                                .foregroundStyle(Palette.muted)
                                .frame(width: 26, height: 26)
                        }
                        .menuStyle(.borderlessButton)
                        .help("Slash commands")
                    }

                    // Mode picker if available
                    if !model.availableSessionModes.isEmpty {
                        let currentModeName = model.availableSessionModes.first(where: {
                            $0["id"] as? String == model.currentModeID
                        })?["name"] as? String ?? model.currentModeID ?? "Mode"
                        Menu {
                            ForEach(Array(model.availableSessionModes.enumerated()), id: \.offset) { _, mode in
                                if let id = mode["id"] as? String {
                                    Button {
                                        Task { await model.setSessionMode(id) }
                                    } label: {
                                        if id == model.currentModeID {
                                            Label(mode["name"] as? String ?? id, systemImage: "checkmark")
                                        } else {
                                            Text(mode["name"] as? String ?? id)
                                        }
                                    }
                                }
                            }
                        } label: {
                            HStack(spacing: 4) {
                                Text(currentModeName)
                                    .font(AppFont.sans(11, weight: .medium))
                                Image(systemName: "chevron.up.chevron.down")
                                    .font(AppFont.sans(8))
                            }
                            .foregroundStyle(Palette.muted)
                            .padding(.horizontal, 8)
                            .frame(height: 24)
                            .background(Palette.card, in: Capsule())
                        }
                        .menuStyle(.borderlessButton)
                        .disabled(model.isRunning)
                    }

                    // Config options dropdown if available
                    ForEach(Array(model.configOptions.enumerated()), id: \.offset) { _, option in
                        if let id = option["id"] as? String,
                           let choices = option["options"] as? [[String: Any]],
                           !choices.isEmpty {
                            let name = option["name"] as? String ?? id
                            let currentValue = option["currentValue"] as? String
                            let currentName = choices.first(where: {
                                $0["value"] as? String == currentValue
                            })?["name"] as? String ?? currentValue ?? name
                            Menu {
                                ForEach(Array(choices.enumerated()), id: \.offset) { _, choice in
                                    if let val = choice["value"] as? String {
                                        Button {
                                            Task { await model.setConfigOption(id, value: val) }
                                        } label: {
                                            if val == currentValue {
                                                Label(choice["name"] as? String ?? val, systemImage: "checkmark")
                                            } else {
                                                Text(choice["name"] as? String ?? val)
                                            }
                                        }
                                    }
                                }
                            } label: {
                                HStack(spacing: 4) {
                                    Text(currentName)
                                        .font(AppFont.sans(11, weight: .medium))
                                    Image(systemName: "chevron.up.chevron.down")
                                        .font(AppFont.sans(8))
                                }
                                .foregroundStyle(Palette.muted)
                                .padding(.horizontal, 8)
                                .frame(height: 24)
                                .background(Palette.card, in: Capsule())
                            }
                            .menuStyle(.borderlessButton)
                            .disabled(model.isRunning)
                        }
                    }

                    Spacer()

                    Image(systemName: "lock.shield")
                        .font(AppFont.sans(11))
                        .foregroundStyle(Palette.subtle.opacity(0.8))
                        .help("Workspace sandbox: Files can be read and edited within this workspace.")

                    // Send or Stop button
                    if model.isRunning {
                        Button(action: model.cancel) {
                            Image(systemName: "stop.fill")
                                .font(AppFont.sans(11, weight: .semibold))
                                .foregroundStyle(.white)
                                .frame(width: 28, height: 28)
                                .background(Color.red.opacity(0.85), in: Circle())
                        }
                        .buttonStyle(.plain)
                        .help("Stop run")
                    } else {
                        let canSend = model.selectedSessionID != nil &&
                                      model.isConnected &&
                                      (!model.draft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || !model.attachedFiles.isEmpty)
                        Button {
                            Task { await model.send() }
                        } label: {
                            Image(systemName: "arrow.up")
                                .font(AppFont.sans(12, weight: .semibold))
                                .foregroundStyle(canSend ? Palette.canvas : Palette.subtle)
                                .frame(width: 28, height: 28)
                                .background(canSend ? Palette.ink : Palette.card, in: Circle())
                        }
                        .buttonStyle(.plain)
                        .disabled(!canSend)
                    }
                }
            }
            .padding(.horizontal, 14)
            .padding(.vertical, 10)
            .background(Palette.composerBg, in: RoundedRectangle(cornerRadius: 14))
            .overlay(RoundedRectangle(cornerRadius: 14).stroke(Palette.rule, lineWidth: 1))
            .shadow(color: themeManager.isDark ? Color.clear : Color.black.opacity(0.04), radius: 8, x: 0, y: 2)
        }
        .padding(.horizontal, 24)
        .padding(.top, 8)
        .padding(.bottom, 16)
        .frame(maxWidth: 820)
        .frame(maxWidth: .infinity)
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
        .padding(24)
        .frame(width: 540)
        .background(Palette.raised)
        .interactiveDismissDisabled()
    }
}

private struct SettingsView: View {
    @EnvironmentObject var model: DesktopModel
    @StateObject private var config = ConfigEditor()
    @State private var selectedTab: SettingsTab = .config

    private enum SettingsTab: String, CaseIterable, Identifiable {
        case config = "Config"
        case appearance = "Appearance"
        case policy = "Policy"
        case context = "Context"

        var id: String { rawValue }
        var icon: String {
            switch self {
            case .config: return "gearshape"
            case .appearance: return "paintpalette"
            case .policy: return "point.3.filled.connected.trianglepath.dotted"
            case .context: return "square.stack.3d.up"
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
            .padding(.horizontal, 20)
            .padding(.top, 16)
            .padding(.bottom, 12)

            Divider().background(Palette.rule)

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
                }
            }

            Divider().background(Palette.rule)

            // Persistent bottom status and action toolbar
            HStack(spacing: 12) {
                if !config.status.isEmpty {
                    let isSaved = config.status.hasPrefix("Saved")
                    HStack(spacing: 6) {
                        Circle()
                            .fill(isSaved ? Color.green : Color.orange)
                            .frame(width: 7, height: 7)
                        Text(config.status)
                            .font(AppFont.sans(11))
                            .foregroundStyle(isSaved ? Palette.muted : Color.orange)
                            .lineLimit(1)
                    }
                }
                Spacer()
                Button("Open config.toml") { config.openConfigFile() }
                    .font(AppFont.sans(12))
                Button("Reload") { config.reload() }
                    .font(AppFont.sans(12))
                if selectedTab == .config {
                    Button("Save") { config.save() }
                        .buttonStyle(.borderedProminent)
                        .font(AppFont.sans(12))
                        .disabled(config.isSaving)
                }
            }
            .padding(.horizontal, 20)
            .padding(.vertical, 12)
            .background(Palette.raised.opacity(0.6))
        }
        .frame(width: 660, height: 700)
        .onAppear { config.reload() }
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
                    title: "Provider Settings",
                    subtitle: "Configure upstream LLM endpoint credentials and execution mode"
                ) {
                    SettingRow(label: "Provider", description: "Configured provider instance name") {
                        HStack(spacing: 8) {
                            Picker("", selection: Binding(
                                get: { config.providerName },
                                set: { config.selectProvider($0) }
                            )) {
                                ForEach(config.providerOptions, id: \.self) { Text($0).tag($0) }
                            }
                            .frame(width: 140)
                            Button("Add", systemImage: "plus") { config.addProvider() }
                                .font(AppFont.sans(11))
                                .disabled(!config.canAddEntries)
                        }
                    }
                    Divider().background(Palette.rule)

                    SettingRow(label: "API Protocol", description: "Payload format used to talk to the provider") {
                        Picker("", selection: $config.apiType) {
                            let types = ["open_ai_responses", "open_ai_chat_completions", "anthropic_messages"]
                            if !types.contains(config.apiType) { Text(config.apiType).tag(config.apiType) }
                            ForEach(types, id: \.self) { Text($0).tag($0) }
                        }
                        .frame(width: 220)
                    }
                    Divider().background(Palette.rule)

                    SettingRow(label: "Base URL", description: "Target endpoint root URL") {
                        TextField("https://api.openai.com/v1", text: $config.baseURL)
                            .font(AppFont.code(12))
                            .frame(width: 240)
                    }
                    Divider().background(Palette.rule)

                    SettingRow(label: "API Key Env Var", description: "Preferred: environment variable holding secret") {
                        TextField("ARABICA_PROVIDER_PRIMARY_API_KEY", text: $config.apiKeyEnv)
                            .font(AppFont.code(11))
                            .frame(width: 240)
                    }
                    Divider().background(Palette.rule)

                    SettingRow(label: "Direct API Key", description: "Encrypted file fallback (never displayed after save)") {
                        HStack(spacing: 8) {
                            SecureField(config.hasSavedKey && !config.clearSavedKey ? "•••••••••••• (Saved)" : "sk-...", text: $config.apiKey)
                                .font(AppFont.code(12))
                                .frame(width: 170)
                            if config.hasSavedKey {
                                Toggle("Clear", isOn: $config.clearSavedKey)
                                    .font(AppFont.sans(11))
                            }
                        }
                    }
                    Divider().background(Palette.rule)

                    SettingRow(label: "Reasoning Effort", description: "Thinking / reasoning token budget level") {
                        Picker("", selection: $config.thinking) {
                            Text("Default").tag("")
                            let values = ConfigEditor.supportedThinkingValues(apiType: config.apiType)
                            ForEach(values, id: \.self) { Text($0).tag($0) }
                        }
                        .frame(width: 140)
                    }
                }

                // Model Aliases Section
                SettingSection(
                    title: "Model Aliases",
                    subtitle: "Map abstract logical aliases (e.g. default, fast) to upstream model IDs"
                ) {
                    SettingRow(label: "Model ID", description: "Upstream vendor identifier (e.g. gpt-4o, claude-3-7-sonnet)") {
                        TextField("model id", text: $config.modelID)
                            .font(AppFont.code(12))
                            .frame(width: 220)
                    }
                    Divider().background(Palette.rule)

                    SettingRow(label: "Alias", description: "Routing alias mapped to this model") {
                        HStack(spacing: 8) {
                            Picker("", selection: Binding(
                                get: { config.modelAlias },
                                set: { config.selectModel($0) }
                            )) {
                                ForEach(config.modelsForSelectedProvider, id: \.self) { Text($0).tag($0) }
                            }
                            .frame(width: 140)
                            Button("Add", systemImage: "plus") { config.addModel() }
                                .font(AppFont.sans(11))
                                .disabled(!config.canAddEntries)
                        }
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
                    Picker("", selection: $selectedPolicyID) {
                        if policyCategory == .configured {
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
                    .frame(maxWidth: .infinity)

                    if policyCategory == .configured, !selectedPolicyID.isEmpty {
                        Button(selectedPolicyID == config.defaultPolicy ? "Active Default" : "Set as Default") {
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
                        if let policy = config.configuredPolicies.first(where: { $0.id == selectedPolicyID }) ?? config.configuredPolicies.first {
                            configuredPolicyDetailView(policy)
                        } else {
                            emptyPolicyPlaceholder("No configured policies found in config.toml.")
                        }
                    } else {
                        let records = evaluation.snapshot?.policies ?? []
                        if let record = records.first(where: { $0.id == selectedPolicyID }) ?? records.first {
                            candidatePolicyDetailView(record)
                        } else {
                            emptyPolicyPlaceholder("No evolved candidates have been produced by background evaluation yet.")
                        }
                    }
                }
                .padding(20)
            }
        }
        .onAppear {
            if selectedPolicyID.isEmpty {
                selectedPolicyID = config.defaultPolicy.isEmpty ? (config.configuredPolicies.first?.id ?? "") : config.defaultPolicy
            }
        }
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
            SettingRow(label: "Minimum Model Dwell Steps", description: "Consecutive steps a model must retain before switching") {
                Text("\(policy.dwellSteps ?? 1) steps").font(AppFont.code(12)).foregroundStyle(Palette.ink)
            }
            Divider().background(Palette.rule)
            SettingRow(label: "Stall Recovery Trigger", description: "Unproductive steps before recovery model takes over") {
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
            SettingRow(label: "Evolution Reason", description: "Trigger condition detected during trajectory analysis") {
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
                Text("\(record.policy.minimum_model_dwell_steps ?? 1) steps").font(AppFont.code(12)).foregroundStyle(Palette.ink)
            }
            Divider().background(Palette.rule)
            SettingRow(label: "Stall Recovery Trigger", description: "No-progress threshold") {
                Text("\(record.policy.recovery_after_no_progress_steps ?? 2) steps").font(AppFont.code(12)).foregroundStyle(Palette.ink)
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
                Text("Enabled").font(AppFont.code(11, weight: .bold)).foregroundStyle(Color.green)
            }
            Divider().background(Palette.rule)
            SettingRow(label: "Terminal & Subprocess", description: "Persistent interactive bash sessions with PTY") {
                Text("Protected").font(AppFont.code(11, weight: .bold)).foregroundStyle(Color.blue)
            }
            Divider().background(Palette.rule)
            SettingRow(label: "Session Short Memory", description: "Prefix cache, bounded short-memory projection & GC") {
                Text("Active").font(AppFont.code(11, weight: .bold)).foregroundStyle(Color.green)
            }
            Divider().background(Palette.rule)
            SettingRow(label: "Context Unfold Engine", description: "Lazy discovery of folded tool directories") {
                Text("Active").font(AppFont.code(11, weight: .bold)).foregroundStyle(Color.green)
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
                SettingRow(label: "No Roots Configured", description: "Add `[skills] roots = [\"skills\"]` to config.toml") {
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
                            SettingRow(label: "Environment Keys", description: "Inherited secrets") {
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

