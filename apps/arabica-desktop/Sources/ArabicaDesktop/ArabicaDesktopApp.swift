import SwiftUI

/// Global typography system with 3 unified font styles:
/// 1. Serif (衬线体) - Dedicated to body text (正文: chat message text, thoughts, composer draft)
/// 2. Sans-serif (非衬线体) - Used for all UI chrome, titles, navigation, buttons, labels
/// 3. Monospaced / Code (代码字体) - Used for code, diffs, terminal outputs, file paths, config keys
enum AppFont {
    /// 衬线体 (Serif) - 专用于正文
    static func serif(_ size: CGFloat, weight: Font.Weight = .regular) -> Font {
        .system(size: size, weight: weight, design: .serif)
    }

    /// 非衬线体 (Sans-Serif) - 用于正文之外的所有界面元素
    static func sans(_ size: CGFloat, weight: Font.Weight = .regular) -> Font {
        .system(size: size, weight: weight, design: .default)
    }

    /// 代码字体 (Monospaced) - 用于代码、终端、diff、路径、配置键等
    static func code(_ size: CGFloat, weight: Font.Weight = .regular) -> Font {
        .system(size: size, weight: weight, design: .monospaced)
    }
}

enum Palette {
    static let canvas = Color(red: 22 / 255, green: 23 / 255, blue: 25 / 255)
    static let sidebar = Color(red: 25 / 255, green: 26 / 255, blue: 29 / 255)
    static let raised = Color(red: 30 / 255, green: 31 / 255, blue: 34 / 255)
    static let ink = Color(red: 232 / 255, green: 231 / 255, blue: 228 / 255)
    static let muted = Color(red: 156 / 255, green: 156 / 255, blue: 162 / 255)
    static let rule = Color.white.opacity(0.12)
}

@main
struct ArabicaDesktopApp: App {
    @StateObject private var model = DesktopModel()

    init() {
        _ = PermissionNotifications.shared
    }

    var body: some Scene {
        WindowGroup {
            DesktopView()
                .environmentObject(model)
                .frame(minWidth: 850, minHeight: 580)
                .preferredColorScheme(.dark)
        }
        .windowStyle(.titleBar)
        .commands {
            CommandGroup(replacing: .newItem) {
                Button("New Conversation") { Task { await model.newSession() } }
                    .keyboardShortcut("n")
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
                .frame(width: 660, height: 700)
                .preferredColorScheme(.dark)
        }
    }
}

private struct DesktopView: View {
    @EnvironmentObject var model: DesktopModel
    @AppStorage("showEvaluationInspector") private var showEvaluation = false

    var body: some View {
        NavigationSplitView {
            sidebar
                .navigationSplitViewColumnWidth(min: 238, ideal: 260, max: 310)
        } detail: {
            conversation
        }
        .background(Palette.canvas)
        .sheet(item: $model.permission) { prompt in
            PermissionView(prompt: prompt) { option in model.resolvePermission(option) }
                .frame(minWidth: 500)
        }
        .onChange(of: model.permission?.id) { _, _ in
            guard let prompt = model.permission,
                  let sessionID = prompt.params["sessionId"] as? String else { return }
            PermissionNotifications.shared.post(
                sessionID: sessionID,
                title: prompt.title,
                detail: prompt.detail
            )
        }
        .onReceive(NotificationCenter.default.publisher(for: .arabicaPermissionNotificationOpened)) { event in
            guard let sessionID = event.userInfo?["sessionId"] as? String else { return }
            Task { await model.selectSession(sessionID) }
        }
        .inspector(isPresented: $showEvaluation) {
            EvaluationPanel(model: model.evaluation, isVisible: showEvaluation)
                .inspectorColumnWidth(min: 280, ideal: 330, max: 440)
        }
        .toolbar {
            ToolbarItem(placement: .primaryAction) {
                Button { showEvaluation.toggle() } label: {
                    Label("Background evaluation", systemImage: "chart.bar.doc.horizontal")
                }
                .keyboardShortcut("i", modifiers: [.command, .option])
                .help(showEvaluation ? "Hide background evaluation" : "Show background evaluation")
                .accessibilityValue(showEvaluation ? "Visible" : "Hidden")
            }
            ToolbarItem(placement: .primaryAction) {
                Button { Task { await model.newSession() } } label: {
                    Label("New Conversation", systemImage: "square.and.pencil")
                }
                .disabled(!model.isConnected || model.isConnecting)
            }
        }
    }

    private var sidebar: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack(spacing: 10) {
                if let icon = Bundle.main.url(forResource: "arabica-icon", withExtension: "png"),
                   let image = NSImage(contentsOf: icon) {
                    Image(nsImage: image).resizable().frame(width: 27, height: 27)
                }
                Text("Arabica")
                    .font(AppFont.sans(25, weight: .semibold))
                    .foregroundStyle(Palette.ink)
                Spacer()
            }
            .padding(.horizontal, 18)
            .padding(.top, 25)
            .padding(.bottom, 22)

            Button(action: model.chooseWorkspace) {
                HStack(spacing: 9) {
                    Image(systemName: "folder")
                    Text(model.workspace?.lastPathComponent ?? "Open workspace")
                        .lineLimit(1)
                    Spacer(minLength: 4)
                    Image(systemName: "chevron.up.chevron.down").font(AppFont.sans(10))
                }
                .font(AppFont.sans(13, weight: .medium))
                .padding(.horizontal, 12)
                .frame(height: 38)
                .background(Palette.raised, in: RoundedRectangle(cornerRadius: 10))
            }
            .buttonStyle(.plain)
            .padding(.horizontal, 12)
            .padding(.bottom, 24)

            HStack {
                Text("Conversations")
                    .font(AppFont.sans(12, weight: .semibold))
                    .foregroundStyle(Palette.muted)
                Spacer()
                if model.isConnecting { ProgressView().controlSize(.small) }
            }
            .padding(.horizontal, 18)
            .padding(.bottom, 9)

            ScrollView {
                LazyVStack(spacing: 2) {
                    ForEach(model.sessions) { session in
                        Button { Task { await model.selectSession(session.id) } } label: {
                            HStack(spacing: 10) {
                                Image(systemName: "text.bubble")
                                    .font(AppFont.sans(14))
                                    .foregroundStyle(Palette.muted)
                                Text(session.title)
                                    .font(AppFont.sans(13))
                                    .lineLimit(1)
                                Spacer(minLength: 0)
                            }
                            .foregroundStyle(Palette.ink)
                            .padding(.horizontal, 11)
                            .frame(height: 35)
                            .background(
                                model.selectedSessionID == session.id
                                    ? Color.white.opacity(0.11) : .clear,
                                in: RoundedRectangle(cornerRadius: 9)
                            )
                        }
                        .buttonStyle(.plain)
                        .contextMenu {
                            Button("Close Conversation") {
                                Task { await model.closeSession(session.id) }
                            }
                        }
                    }
                }
                .padding(.horizontal, 9)
            }

            Rectangle().fill(Palette.rule).frame(height: 1)
            HStack(spacing: 8) {
                Circle()
                    .fill(model.isConnected ? Color.green.opacity(0.85) : (model.errorText != nil ? Color.red : Color.orange))
                    .frame(width: 6, height: 6)
                Text(model.isConnected ? "Local agent" : "Connection attention")
                    .font(AppFont.sans(11))
                    .foregroundStyle(Palette.muted)
                Spacer()
            }
            .padding(16)
            .help(model.isConnected
                  ? "Connected: Local agent ready"
                  : (model.errorText ?? "Disconnected: Reopen the workspace to reconnect"))
        }
        .background(Palette.sidebar)
    }

    private var conversation: some View {
        VStack(spacing: 0) {
            HStack {
                VStack(alignment: .leading, spacing: 3) {
                    Text(model.sessions.first(where: { $0.id == model.selectedSessionID })?.title ?? "Workspace")
                        .font(AppFont.sans(15, weight: .semibold))
                    if let workspace = model.workspace {
                        Text(workspace.path)
                            .font(AppFont.code(11))
                            .foregroundStyle(Palette.muted)
                            .lineLimit(1)
                            .truncationMode(.middle)
                    }
                }
                Spacer()
                if let used = model.usage["used"] as? Int,
                   let size = model.usage["size"] as? Int {
                    Text("\(used) / \(size) tokens")
                        .font(AppFont.sans(10))
                        .foregroundStyle(Palette.muted)
                }
                if model.isRunning {
                    Button("Stop", systemImage: "stop.fill", action: model.cancel)
                        .controlSize(.small)
                        .font(AppFont.sans(12))
                }
            }
            .padding(.horizontal, 28)
            .frame(height: 68)
            Rectangle().fill(Palette.rule).frame(height: 1)

            if let prompt = model.permission {
                PermissionView(prompt: prompt) { option in model.resolvePermission(option) }
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(.horizontal, 28)
                    .padding(.vertical, 12)
                Rectangle().fill(Palette.rule).frame(height: 1)
            }

            if !model.plan.isEmpty {
                VStack(alignment: .leading, spacing: 5) {
                    Text("PLAN").font(AppFont.sans(10, weight: .bold)).foregroundStyle(Palette.muted)
                    ForEach(Array(model.plan.enumerated()), id: \.offset) { _, entry in
                        let status = entry["status"] as? String
                        let isCompleted = status == "completed" || status == "success"
                        let isFailed = status == "failed" || status == "error"
                        HStack(spacing: 7) {
                            if isCompleted {
                                Image(systemName: "checkmark.circle.fill")
                                    .foregroundStyle(Color.green.opacity(0.85))
                                    .help("Completed")
                            } else if isFailed {
                                Image(systemName: "exclamationmark.circle.fill")
                                    .foregroundStyle(Color.red.opacity(0.85))
                                    .help("Failed")
                            } else {
                                Image(systemName: "circle")
                                    .foregroundStyle(Palette.muted)
                                    .help("Pending")
                            }
                            Text(entry["content"] as? String ?? "Step")
                        }
                        .font(AppFont.sans(11))
                        .foregroundStyle(Palette.muted)
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.horizontal, 28)
                .padding(.vertical, 10)
            }

            if model.selectedSessionID == nil {
                emptyState
            } else {
                ScrollViewReader { proxy in
                    ScrollView {
                        LazyVStack(alignment: .leading, spacing: 20) {
                            ForEach(model.items) { item in
                                ChatRow(item: item)
                                    .id(item.id)
                            }
                            if model.isRunning {
                                HStack(spacing: 8) {
                                    ProgressView().controlSize(.small)
                                    Text(model.isWaitingForPermission ? "Waiting for permission…" : "Working…").foregroundStyle(Palette.muted)
                                }
                                .font(AppFont.sans(12))
                            }
                        }
                        .frame(maxWidth: 760)
                        .frame(maxWidth: .infinity)
                        .padding(.horizontal, 30)
                        .padding(.vertical, 28)
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
                .padding(.horizontal, 30)
                .padding(.bottom, 8)
            }
            composer
        }
        .background(Palette.canvas)
    }

    private var emptyState: some View {
        VStack(alignment: .leading, spacing: 14) {
            Spacer()
            Text(model.workspace == nil ? "Start with a workspace." : "What would you like to work on?")
                .font(AppFont.sans(32, weight: .semibold))
                .frame(maxWidth: .infinity, alignment: .leading)
                .fixedSize(horizontal: false, vertical: true)
                .foregroundStyle(Palette.ink)
            Text(model.workspace == nil
                 ? "Choose a folder to open a workspace."
                 : "Create a conversation to ask a question or start a task.")
                .font(AppFont.sans(14))
                .foregroundStyle(Palette.muted)
            Button(model.workspace == nil || !model.isConnected ? "Open workspace" : "New conversation") {
                if model.workspace == nil || !model.isConnected { model.chooseWorkspace() }
                else { Task { await model.newSession() } }
            }
            .buttonStyle(.borderedProminent)
            .tint(Palette.ink)
            .foregroundStyle(Palette.canvas)
            .font(AppFont.sans(13, weight: .medium))
            .padding(.top, 9)
            Spacer()
        }
        .frame(maxWidth: 600, alignment: .leading)
        .padding(.horizontal, 28)
        .frame(maxWidth: .infinity)
    }

    private var composer: some View {
        VStack(spacing: 0) {
            Rectangle().fill(Palette.rule).frame(height: 1)
            if !model.attachedFiles.isEmpty {
                HStack {
                    ForEach(model.attachedFiles, id: \.self) { file in
                        Text("@\(file.lastPathComponent)")
                            .font(AppFont.code(11))
                            .padding(5)
                            .background(Palette.raised, in: RoundedRectangle(cornerRadius: 5))
                    }
                    Spacer()
                    Button("Clear") { model.attachedFiles = [] }
                        .font(AppFont.sans(11))
                }
                .padding(.bottom, 8)
            }
            if hasSessionControls {
                ScrollView(.horizontal) {
                    HStack(spacing: 8) {
                        Image(systemName: "slider.horizontal.3")
                            .font(AppFont.sans(11))
                            .foregroundStyle(Palette.muted)
                            .help("This conversation: Options configured here apply to the current session")
                        ForEach(Array(model.configOptions.enumerated()), id: \.offset) { _, option in
                            if let id = option["id"] as? String,
                               let choices = option["options"] as? [[String: Any]],
                               !choices.isEmpty {
                                let name = option["name"] as? String ?? id
                                let currentValue = option["currentValue"] as? String
                                let currentName = choices.first(where: {
                                    $0["value"] as? String == currentValue
                                })?["name"] as? String ?? currentValue ?? "Choose"
                                Menu {
                                    ForEach(Array(choices.enumerated()), id: \.offset) { _, choice in
                                        if let value = choice["value"] as? String {
                                            Button {
                                                Task { await model.setConfigOption(id, value: value) }
                                            } label: {
                                                if value == currentValue {
                                                    Label(choice["name"] as? String ?? value, systemImage: "checkmark")
                                                } else {
                                                    Text(choice["name"] as? String ?? value)
                                                }
                                            }
                                        }
                                    }
                                } label: {
                                    sessionControlLabel(name, value: currentName)
                                }
                                .disabled(model.isRunning)
                            }
                        }
                        if !model.availableSessionModes.isEmpty {
                            let currentModeName = model.availableSessionModes.first(where: {
                                $0["id"] as? String == model.currentModeID
                            })?["name"] as? String ?? model.currentModeID ?? "Choose"
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
                                        .help(mode["description"] as? String ?? "")
                                    }
                                }
                            } label: {
                                sessionControlLabel("Mode", value: currentModeName)
                            }
                        }
                    }
                }
                .scrollIndicators(.hidden)
                .padding(.bottom, 10)
                .accessibilityElement(children: .contain)
                .accessibilityLabel("Current conversation options")
            }
            HStack(alignment: .bottom, spacing: 10) {
                Button(action: model.chooseFiles) {
                    Image(systemName: "paperclip")
                        .foregroundStyle(Palette.muted)
                        .frame(width: 22, height: 31)
                }
                .buttonStyle(.plain)
                .help("Attach files")

                if !model.availableCommands.isEmpty {
                    Menu {
                        ForEach(Array(model.availableCommands.enumerated()), id: \.offset) { _, command in
                            if let name = command["name"] as? String {
                                Button(name) { model.draft = "/\(name) " }
                            }
                        }
                    } label: {
                        Image(systemName: "slash.circle")
                            .foregroundStyle(Palette.muted)
                            .frame(width: 22, height: 31)
                    }
                    .menuStyle(.borderlessButton)
                    .help("Commands")
                }

                Image(systemName: "lock.shield")
                    .font(AppFont.sans(12))
                    .foregroundStyle(Palette.muted.opacity(0.6))
                    .frame(width: 20, height: 31)
                    .help("Workspace sandbox: Files can be read and edited within this workspace. Prompts for permission before writes and commands.")

                TextField("Send a message…", text: $model.draft, axis: .vertical)
                    .textFieldStyle(.plain)
                    .font(AppFont.serif(14))
                    .lineLimit(1...6)
                    .padding(.vertical, 11)
                    .onSubmit { Task { await model.send() } }

                Button { Task { await model.send() } } label: {
                    Image(systemName: "arrow.up")
                        .font(AppFont.sans(14, weight: .semibold))
                        .foregroundStyle(Palette.canvas)
                        .frame(width: 31, height: 31)
                        .background(Palette.ink, in: RoundedRectangle(cornerRadius: 9))
                }
                .buttonStyle(.plain)
                .disabled(model.selectedSessionID == nil || !model.isConnected || model.isRunning ||
                          (model.draft.isEmpty && model.attachedFiles.isEmpty))
                .opacity(model.selectedSessionID == nil || !model.isConnected || model.isRunning ||
                         (model.draft.isEmpty && model.attachedFiles.isEmpty) ? 0.4 : 1)
                .padding(.bottom, 5)
            }
            .padding(.horizontal, 15)
            .background(Palette.raised, in: RoundedRectangle(cornerRadius: 12))
        }
        .padding(.horizontal, 28)
        .padding(.top, 14)
        .padding(.bottom, 18)
        .frame(maxWidth: 820)
        .frame(maxWidth: .infinity)
    }

    private var hasSessionControls: Bool {
        model.configOptions.contains {
            ($0["id"] as? String) != nil && !(($0["options"] as? [[String: Any]]) ?? []).isEmpty
        } || !model.availableSessionModes.isEmpty
    }

    private func sessionControlLabel(_ name: String, value: String) -> some View {
        HStack(spacing: 6) {
            Text(name).foregroundStyle(Palette.muted)
            Text(value).foregroundStyle(Palette.ink).lineLimit(1)
            Image(systemName: "chevron.down")
                .font(AppFont.sans(8, weight: .semibold))
                .foregroundStyle(Palette.muted)
        }
        .font(AppFont.sans(11, weight: .medium))
        .padding(.horizontal, 10)
        .frame(height: 28)
        .background(Palette.raised, in: Capsule())
        .overlay(Capsule().stroke(Palette.rule, lineWidth: 1))
    }
}

private struct ChatRow: View {
    let item: ChatItem
    @State private var expanded = false

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            if item.kind == .thought {
                Button { expanded.toggle() } label: {
                    HStack(spacing: 7) {
                        Image(systemName: "brain")
                            .font(AppFont.sans(12))
                            .foregroundStyle(Palette.muted)
                        Image(systemName: expanded ? "chevron.up" : "chevron.down")
                            .font(AppFont.sans(9, weight: .semibold))
                            .foregroundStyle(Palette.muted)
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .help(expanded ? "Hide reasoning" : "Show reasoning")
                .accessibilityValue(expanded ? "Expanded" : "Collapsed")
            } else if item.kind == .tool {
                HStack(spacing: 8) {
                    Image(systemName: toolIcon)
                        .font(AppFont.sans(12))
                        .foregroundStyle(Palette.muted)
                        .help("Tool: \(item.toolKind ?? "call")")
                    if hasDetails {
                        Button { expanded.toggle() } label: {
                            HStack(alignment: .firstTextBaseline, spacing: 4) {
                                Text(item.text)
                                    .font(AppFont.code(12))
                                    .foregroundStyle(Palette.ink)
                                Image(systemName: expanded ? "chevron.up" : "chevron.down")
                                    .font(AppFont.sans(9, weight: .semibold))
                                    .foregroundStyle(Palette.muted)
                            }
                        }
                        .buttonStyle(.plain)
                        .help(expanded ? "Hide output" : "Show output")
                        .accessibilityValue(expanded ? "Expanded" : "Collapsed")
                    } else {
                        Text(item.text)
                            .font(AppFont.code(12))
                            .foregroundStyle(Palette.muted)
                            .textSelection(.enabled)
                    }

                    if let status = item.status {
                        statusBadge(status)
                    }
                }
            }

            if item.kind != .tool, item.kind != .thought || expanded {
                MarkdownMessage(text: item.text)
                    .font(AppFont.serif(14))
                    .foregroundStyle(item.kind == .thought ? Palette.muted : Palette.ink)
                    .frame(maxWidth: .infinity, alignment: .leading)
            }

            if item.kind == .tool, let path = item.path {
                Text(path)
                    .font(AppFont.code(10))
                    .foregroundStyle(Palette.muted)
                    .lineLimit(1)
                    .truncationMode(.middle)
            }

            if hasDetails, expanded, let detail = item.detail {
                ScrollView(.horizontal) {
                    Text(detail)
                        .font(AppFont.code(11))
                        .textSelection(.enabled)
                        .frame(maxWidth: .infinity, alignment: .leading)
                }
                .padding(10)
                .background(Palette.raised, in: RoundedRectangle(cornerRadius: 8))
            }
        }
        .padding(item.kind == .user ? 16 : 0)
        .background(item.kind == .user ? Palette.raised : .clear,
                    in: RoundedRectangle(cornerRadius: 10))
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private var hasDetails: Bool {
        item.kind == .tool && !(item.detail ?? "").isEmpty
    }

    @ViewBuilder
    private func statusBadge(_ status: String) -> some View {
        let normalized = status.lowercased()
        let isCompleted = normalized == "completed" || normalized == "success" || normalized == "ok"
        let isRunning = normalized == "in_progress" || normalized == "running" || normalized == "started"
        let isFailed = normalized == "failed" || normalized == "error"
        let isCancelled = normalized == "cancelled" || normalized == "canceled"

        let color: Color = {
            if isCompleted { return Color.green.opacity(0.85) }
            if isRunning { return Color.blue.opacity(0.85) }
            if isFailed { return Color.red.opacity(0.85) }
            if isCancelled { return Palette.muted.opacity(0.5) }
            return Palette.muted
        }()

        let tooltipText: String = {
            if isCompleted { return "Status: Completed" }
            if isRunning { return "Status: Running…" }
            if isFailed {
                if let detail = item.detail, !detail.isEmpty {
                    return "Failed: \(detail.trimmingCharacters(in: .whitespacesAndNewlines).prefix(180))"
                }
                return "Status: Failed"
            }
            if isCancelled { return "Status: Cancelled" }
            return "Status: \(status.replacingOccurrences(of: "_", with: " ").capitalized)"
        }()

        HStack(spacing: 4) {
            if isRunning {
                ProgressView().controlSize(.mini)
            } else {
                Circle()
                    .fill(color)
                    .frame(width: 6, height: 6)
            }
        }
        .help(tooltipText)
    }

    private var toolIcon: String {
        switch item.toolKind {
        case "read": "doc.text"
        case "search": "magnifyingglass"
        case "edit": "pencil.line"
        case "delete": "trash"
        case "move": "arrow.right.doc.on.clipboard"
        case "execute": "terminal"
        case "fetch": "globe"
        case "think": "brain"
        case "switch_mode": "arrow.triangle.2.circlepath"
        default: "wrench.and.screwdriver"
        }
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
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Palette.raised, in: RoundedRectangle(cornerRadius: 10))
    }
}

private struct SettingsView: View {
    @EnvironmentObject var model: DesktopModel
    @StateObject private var config = ConfigEditor()
    @State private var selectedTab: SettingsTab = .config

    private enum SettingsTab: String, CaseIterable, Identifiable {
        case config = "Config"
        case policy = "Policy"
        case context = "Context"

        var id: String { rawValue }
        var icon: String {
            switch self {
            case .config: return "gearshape"
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
