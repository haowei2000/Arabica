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

private enum Palette {
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
        .inspector(isPresented: $showEvaluation) {
            EvaluationPanel(model: model.evaluation, isVisible: showEvaluation)
                .inspectorColumnWidth(min: 280, ideal: 330, max: 440)
        }
        .sheet(item: $model.permission) { prompt in
            PermissionView(prompt: prompt) { option in model.resolvePermission(option) }
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
                                    Text("Working…").foregroundStyle(Palette.muted)
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
        .frame(width: 540)
        .background(Palette.raised)
        .interactiveDismissDisabled()
    }
}

private struct SettingsView: View {
    @EnvironmentObject var model: DesktopModel
    @StateObject private var config = ConfigEditor()

    var body: some View {
        Form {
            Section {
                HStack {
                    Picker("provider", selection: Binding(
                        get: { config.providerName },
                        set: { config.selectProvider($0) }
                    )) {
                        ForEach(config.providerOptions, id: \.self) { name in
                            Text(name).tag(name)
                        }
                    }
                    Button("Add provider", systemImage: "plus") { config.addProvider() }
                        .disabled(!config.canAddEntries)
                }
                Picker("api_type", selection: $config.apiType) {
                    let types = ["open_ai_responses", "open_ai_chat_completions", "anthropic_messages"]
                    if !types.contains(config.apiType) { Text(config.apiType).tag(config.apiType) }
                    ForEach(types, id: \.self) { Text($0).tag($0) }
                }
                TextField("base_url", text: $config.baseURL)
                    .textContentType(.URL)
                TextField("api_key_env", text: $config.apiKeyEnv)
                    .textContentType(.username)
                SecureField(config.hasSavedKey && !config.clearSavedKey
                            ? "api_key (leave blank to keep saved key)" : "api_key", text: $config.apiKey)
                    .textContentType(.password)
                if config.hasSavedKey {
                    Toggle("Remove saved api_key", isOn: $config.clearSavedKey)
                }
                Text("The environment variable takes precedence over api_key. Secrets are never shown after saving.")
                    .font(AppFont.sans(11))
                    .foregroundStyle(Palette.muted)
                Picker("thinking", selection: $config.thinking) {
                    Text("Provider default").tag("")
                    let values = ConfigEditor.supportedThinkingValues(apiType: config.apiType)
                    if !config.thinking.isEmpty && !values.contains(config.thinking) {
                        Text("\(config.thinking) (unsupported)").tag(config.thinking)
                    }
                    ForEach(values, id: \.self) { Text($0).tag($0) }
                }
                if let error = ConfigEditor.thinkingValidationError(apiType: config.apiType, thinking: config.thinking) {
                    HStack(spacing: 5) {
                        Image(systemName: "exclamationmark.triangle.fill")
                            .foregroundStyle(.orange)
                        Text(error)
                            .font(AppFont.sans(11))
                            .foregroundStyle(.orange)
                    }
                    .help(error)
                    .fixedSize(horizontal: false, vertical: true)
                }
            } header: {
                configSectionHeader("[providers.\(config.providerName)]", detail: "Choose a provider first, then manage its model aliases below")
            }

            Section {
                TextField("model_id", text: $config.modelID)
                HStack {
                    Picker("alias", selection: Binding(
                        get: { config.modelAlias },
                        set: { config.selectModel($0) }
                    )) {
                        if config.modelsForSelectedProvider.isEmpty {
                            Text("No aliases yet").tag("")
                        }
                        ForEach(config.modelsForSelectedProvider, id: \.self) { alias in
                            Text(alias).tag(alias)
                        }
                    }
                    Button("Add alias", systemImage: "plus") { config.addModel() }
                        .disabled(!config.canAddEntries)
                }
            } header: {
                configSectionHeader("Model and alias for [providers.\(config.providerName)]", detail: "Each [models.<alias>] entry maps to this model_id for blend routing")
            }

            Section {
                if !config.policyOptions.isEmpty {
                    Picker("default_policy", selection: Binding(
                        get: { config.defaultPolicy },
                        set: { config.selectPolicy($0) }
                    )) {
                        ForEach(config.policyOptions, id: \.self) { policy in
                            Text(policy).tag(policy)
                        }
                    }
                    if !config.modelOptions.contains(config.policyDefaultModel), !config.policyDefaultModel.isEmpty {
                        let warning = "Policy default_model references missing alias \(config.policyDefaultModel). Choose a configured model below."
                        HStack(spacing: 5) {
                            Image(systemName: "exclamationmark.triangle.fill")
                                .foregroundStyle(.orange)
                            Text(warning)
                                .font(AppFont.sans(11))
                                .foregroundStyle(.orange)
                        }
                        .help(warning)
                    }
                    Text("New conversations use this policy. A conversation can choose another available policy in the composer.")
                        .font(AppFont.sans(11))
                        .foregroundStyle(Palette.muted)
                } else {
                    modelAliasPicker(label: "default_model")
                }
            } header: {
                configSectionHeader("[blend]", detail: "Selects the default policy for new sessions")
            }

            if !config.policyOptions.isEmpty {
                Section {
                    modelAliasPicker(label: "default_model")
                    Text("The policy's routes and capability lists remain in config.toml and are preserved when the form saves.")
                        .font(AppFont.sans(11))
                        .foregroundStyle(Palette.muted)
                } header: {
                    configSectionHeader("[blend.policies.\(config.defaultPolicy)]", detail: "Policy fields for the selected default policy")
                }
            }

            Section {
                LabeledContent("Agent", value: "Bundled ACP")
                LabeledContent("Workspace", value: model.workspace?.lastPathComponent ?? "None selected")
                Text("Runtime context is managed by the desktop app, not config.toml.")
                    .font(AppFont.sans(11))
                    .foregroundStyle(Palette.muted)
            } header: {
                Text("Desktop context")
                    .font(AppFont.sans(12))
            }

            if !config.status.isEmpty {
                let isSaved = config.status.hasPrefix("Saved")
                HStack(spacing: 5) {
                    Circle()
                        .fill(isSaved ? Color.green.opacity(0.85) : Color.orange)
                        .frame(width: 6, height: 6)
                    Text(config.status)
                        .font(AppFont.sans(12))
                        .foregroundStyle(isSaved ? Palette.muted : .orange)
                        .textSelection(.enabled)
                }
                .help(config.status)
            }

            Text("Provider and model selectors cover every configured table. Other config.toml fields, including policy routes and [[mcp]] servers, are kept when this form saves.")
                .font(AppFont.sans(11))
                .foregroundStyle(Palette.muted)
                .fixedSize(horizontal: false, vertical: true)

            HStack {
                Button("Open config.toml") { config.openConfigFile() }
                    .font(AppFont.sans(13))
                Spacer()
                Button("Reload") { config.reload() }
                    .font(AppFont.sans(13))
                Button("Save config.toml") { config.save() }
                    .buttonStyle(.borderedProminent)
                    .font(AppFont.sans(13))
                    .disabled(config.isSaving)
            }
        }
        .formStyle(.grouped)
        .font(AppFont.sans(13))
        .padding(18)
        .frame(width: 610, height: 650)
        .onAppear { config.reload() }
    }

    private func configSectionHeader(_ path: String, detail: String) -> some View {
        VStack(alignment: .leading, spacing: 3) {
            Text(path)
                .font(AppFont.code(13, weight: .semibold))
            Text(detail)
                .font(AppFont.sans(11))
                .foregroundStyle(Palette.muted)
        }
    }

    @ViewBuilder
    private func modelAliasPicker(label: String) -> some View {
        Picker(label, selection: $config.policyDefaultModel) {
            ForEach(config.modelOptions, id: \.self) { alias in
                Text(alias).tag(alias)
            }
            if !config.policyDefaultModel.isEmpty && !config.modelOptions.contains(config.policyDefaultModel) {
                Text("Missing: \(config.policyDefaultModel)").tag(config.policyDefaultModel)
            }
        }
    }
}
