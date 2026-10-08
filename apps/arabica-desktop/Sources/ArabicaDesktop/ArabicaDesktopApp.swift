import SwiftUI

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
                    .font(.system(size: 25, weight: .semibold, design: .serif))
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
                    Image(systemName: "chevron.up.chevron.down").font(.system(size: 10))
                }
                .font(.system(size: 13, weight: .medium))
                .padding(.horizontal, 12)
                .frame(height: 38)
                .background(Palette.raised, in: RoundedRectangle(cornerRadius: 10))
            }
            .buttonStyle(.plain)
            .padding(.horizontal, 12)
            .padding(.bottom, 24)

            HStack {
                Text("Conversations")
                    .font(.system(size: 12, weight: .semibold))
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
                                    .font(.system(size: 14))
                                    .foregroundStyle(Palette.muted)
                                Text(session.title)
                                    .font(.system(size: 13))
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
                    .fill(model.isConnected ? Color.green.opacity(0.75) : Color.orange)
                    .frame(width: 6, height: 6)
                Text(model.isConnected ? "Local agent" : "Connection needs attention")
                    .font(.system(size: 11))
                    .foregroundStyle(Palette.muted)
                Spacer()
            }
            .padding(16)
        }
        .background(Palette.sidebar)
    }

    private var conversation: some View {
        VStack(spacing: 0) {
            HStack {
                VStack(alignment: .leading, spacing: 3) {
                    Text(model.sessions.first(where: { $0.id == model.selectedSessionID })?.title ?? "Workspace")
                        .font(.system(size: 15, weight: .semibold))
                    if let workspace = model.workspace {
                        Text(workspace.path)
                            .font(.system(size: 11))
                            .foregroundStyle(Palette.muted)
                            .lineLimit(1)
                            .truncationMode(.middle)
                    }
                }
                Spacer()
                if let used = model.usage["used"] as? Int,
                   let size = model.usage["size"] as? Int {
                    Text("\(used) / \(size) tokens")
                        .font(.system(size: 10))
                        .foregroundStyle(Palette.muted)
                }
                if model.isRunning {
                    Button("Stop", systemImage: "stop.fill", action: model.cancel)
                        .controlSize(.small)
                }
            }
            .padding(.horizontal, 28)
            .frame(height: 68)
            Rectangle().fill(Palette.rule).frame(height: 1)

            if !model.plan.isEmpty {
                VStack(alignment: .leading, spacing: 5) {
                    Text("PLAN").font(.system(size: 10, weight: .bold)).foregroundStyle(Palette.muted)
                    ForEach(Array(model.plan.enumerated()), id: \.offset) { _, entry in
                        HStack(spacing: 7) {
                            Image(systemName: entry["status"] as? String == "completed" ? "checkmark.circle.fill" : "circle")
                            Text(entry["content"] as? String ?? "Step")
                        }
                        .font(.system(size: 11))
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
                                .font(.system(size: 12))
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
                Text(error)
                    .font(.system(size: 12))
                    .foregroundStyle(.orange)
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
                .font(.system(size: 33, weight: .medium, design: .serif))
                .frame(maxWidth: .infinity, alignment: .leading)
                .fixedSize(horizontal: false, vertical: true)
                .foregroundStyle(Palette.ink)
            Text(model.workspace == nil
                 ? "Choose a folder to give Arabica a place to work."
                 : "Create a conversation to ask a question or start a task.")
                .font(.system(size: 14))
                .foregroundStyle(Palette.muted)
            Button(model.workspace == nil || !model.isConnected ? "Open workspace" : "New conversation") {
                if model.workspace == nil || !model.isConnected { model.chooseWorkspace() }
                else { Task { await model.newSession() } }
            }
            .buttonStyle(.borderedProminent)
            .tint(Palette.ink)
            .foregroundStyle(Palette.canvas)
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
                            .font(.system(size: 11))
                            .padding(5)
                            .background(Palette.raised, in: RoundedRectangle(cornerRadius: 5))
                    }
                    Spacer()
                    Button("Clear") { model.attachedFiles = [] }.font(.system(size: 11))
                }
                .padding(.bottom, 8)
            }
            if hasSessionControls {
                ScrollView(.horizontal) {
                    HStack(spacing: 8) {
                        Text("This conversation")
                            .font(.system(size: 11, weight: .medium))
                            .foregroundStyle(Palette.muted)
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
            HStack(alignment: .bottom, spacing: 12) {
                Button(action: model.chooseFiles) {
                    Image(systemName: "paperclip")
                        .foregroundStyle(Palette.muted)
                        .frame(width: 22, height: 31)
                }
                .buttonStyle(.plain)
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
                }
                TextField("Message Arabica", text: $model.draft, axis: .vertical)
                    .textFieldStyle(.plain)
                    .font(.system(size: 14))
                    .lineLimit(1...6)
                    .padding(.vertical, 11)
                    .onSubmit { Task { await model.send() } }
                Button { Task { await model.send() } } label: {
                    Image(systemName: "arrow.up")
                        .font(.system(size: 14, weight: .semibold))
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
            Text("Arabica can read and change files in this workspace. It asks before writes and commands.")
                .font(.system(size: 11))
                .foregroundStyle(Palette.muted)
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.top, 8)
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
                .font(.system(size: 8, weight: .semibold))
                .foregroundStyle(Palette.muted)
        }
        .font(.system(size: 11, weight: .medium))
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
            HStack(spacing: 8) {
                if item.kind == .tool {
                    Image(systemName: toolIcon).frame(width: 15)
                }
                Text(label)
                    .font(.system(size: 11, weight: .semibold))
                    .foregroundStyle(Palette.muted)
                if let status = item.status {
                    Text(status.replacingOccurrences(of: "_", with: " "))
                        .font(.system(size: 11))
                        .foregroundStyle(Palette.muted)
                }
            }
            Text(item.text)
                .font(.system(size: item.kind == .tool ? 12 : 14, design: item.kind == .assistant ? .serif : .default))
                .foregroundStyle(item.kind == .thought || item.kind == .tool ? Palette.muted : Palette.ink)
                .textSelection(.enabled)
                .frame(maxWidth: .infinity, alignment: .leading)
            if item.kind == .tool, let path = item.path {
                Text(path)
                    .font(.system(size: 10, design: .monospaced))
                    .foregroundStyle(Palette.muted)
                    .lineLimit(1)
                    .truncationMode(.middle)
            }
            if item.kind == .tool, let detail = item.detail, !detail.isEmpty {
                DisclosureGroup("Details", isExpanded: $expanded) {
                    ScrollView(.horizontal) {
                        Text(detail)
                            .font(.system(size: 11, design: .monospaced))
                            .textSelection(.enabled)
                            .frame(maxWidth: .infinity, alignment: .leading)
                    }
                    .padding(10)
                    .background(Palette.raised, in: RoundedRectangle(cornerRadius: 8))
                }
                .font(.system(size: 11))
                .foregroundStyle(Palette.muted)
            }
        }
        .padding(item.kind == .user ? 16 : 0)
        .background(item.kind == .user ? Palette.raised : .clear,
                    in: RoundedRectangle(cornerRadius: 10))
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private var label: String {
        switch item.kind {
        case .user: "You"
        case .assistant: "Arabica"
        case .thought: "Reasoning"
        case .tool: "Tool"
        }
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
                .font(.system(size: 23, weight: .semibold, design: .serif))
            Text(prompt.title).font(.system(size: 14, weight: .medium))
            if !prompt.detail.isEmpty {
                ScrollView {
                    Text(prompt.detail)
                        .font(.system(size: 11, design: .monospaced))
                        .textSelection(.enabled)
                        .frame(maxWidth: .infinity, alignment: .leading)
                }
                .frame(maxHeight: 150)
                .padding(12)
                .background(Palette.canvas, in: RoundedRectangle(cornerRadius: 8))
            }
            HStack {
                Button("Cancel") { resolve(nil) }
                Spacer()
                ForEach(prompt.options, id: \.id) { option in
                    if option.id == "allow_once" {
                        Button(option.name) { resolve(option.id) }
                            .buttonStyle(.borderedProminent)
                    } else {
                        Button(option.name) { resolve(option.id) }
                            .buttonStyle(.bordered)
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
                LabeledContent("Agent", value: "Bundled Arabica ACP")
                LabeledContent("Workspace", value: model.workspace?.lastPathComponent ?? "None selected")
            } header: {
                Text("Arabica")
            }

            if !config.policyOptions.isEmpty {
                Section {
                    Picker("Default policy", selection: $config.defaultPolicy) {
                        ForEach(config.policyOptions, id: \.self) { policy in
                            Text(policy).tag(policy)
                        }
                    }
                    Text("Used for new conversations. You can choose another available policy for an individual conversation in the composer.")
                        .font(.system(size: 11))
                        .foregroundStyle(Palette.muted)
                    if !config.policyStatus.isEmpty {
                        Text(config.policyStatus)
                            .font(.system(size: 11))
                            .foregroundStyle(config.policyStatus.hasPrefix("Default policy saved") ? Palette.muted : .orange)
                            .textSelection(.enabled)
                    }
                    HStack {
                        Spacer()
                        Button("Save default policy") { config.saveDefaultPolicy() }
                    }
                } header: {
                    Text("New conversations")
                }
            }

            Section {
                TextField("Provider name", text: $config.providerName)
                    .help("A short name used in the provider and model sections.")
                Picker("API type", selection: $config.apiType) {
                    let types = ["open_ai_responses", "open_ai_chat_completions", "anthropic_messages"]
                    if !types.contains(config.apiType) { Text(config.apiType).tag(config.apiType) }
                    ForEach(types, id: \.self) { Text($0).tag($0) }
                }
                TextField("Base URL", text: $config.baseURL)
                    .textContentType(.URL)
                TextField("API key environment variable", text: $config.apiKeyEnv)
                    .textContentType(.username)
                SecureField(config.hasSavedKey && !config.clearSavedKey
                            ? "Saved key (leave blank to keep)" : "API key", text: $config.apiKey)
                    .textContentType(.password)
                if config.hasSavedKey {
                    Toggle("Remove saved API key", isOn: $config.clearSavedKey)
                }
                Text("If both are set, the environment variable takes precedence. The API key is never shown after saving.")
                    .font(.system(size: 11))
                    .foregroundStyle(Palette.muted)
            } header: {
                Text("Provider")
            }

            Section {
                TextField("Model alias", text: $config.modelAlias)
                TextField("Model ID", text: $config.modelID)
                Picker("Thinking", selection: $config.thinking) {
                    Text("Provider default").tag("")
                    let values = ConfigEditor.supportedThinkingValues(apiType: config.apiType)
                    if !config.thinking.isEmpty && !values.contains(config.thinking) {
                        Text("\(config.thinking) (unsupported)").tag(config.thinking)
                    }
                    ForEach(values, id: \.self) { Text($0).tag($0) }
                }
                if let error = ConfigEditor.thinkingValidationError(apiType: config.apiType, thinking: config.thinking) {
                    Text(error)
                        .font(.system(size: 11))
                        .foregroundStyle(.orange)
                        .fixedSize(horizontal: false, vertical: true)
                }
            } header: {
                Text("Default model")
            }

            if config.isUnsupported {
                Text(config.status)
                    .font(.system(size: 12))
                    .foregroundStyle(.orange)
            } else if !config.status.isEmpty {
                Text(config.status)
                    .font(.system(size: 12))
                    .foregroundStyle(config.status.hasPrefix("Saved") ? Palette.muted : .orange)
                .textSelection(.enabled)
            }

            if config.isUnsupported {
                Text("Advanced provider, model, policy routes, and MCP settings stay in this file. Open it here to edit them without the basic form rewriting those sections.")
                    .font(.system(size: 11))
                    .foregroundStyle(Palette.muted)
                    .fixedSize(horizontal: false, vertical: true)
            }

            HStack {
                Button(config.isUnsupported ? "Open advanced config.toml" : "Open config.toml") { config.openConfigFile() }
                Spacer()
                Button("Reload") { config.reload() }
                Button("Save") { config.save() }
                    .buttonStyle(.borderedProminent)
                    .disabled(config.isUnsupported || config.isSaving)
            }
        }
        .formStyle(.grouped)
        .padding(18)
        .frame(width: 610, height: 650)
        .onAppear { config.reload() }
    }
}
