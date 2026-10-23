import AppKit
import SwiftUI

private enum NativeSettingsCategory: String, CaseIterable, Identifiable {
    case providers = "Providers"
    case models = "Models"
    case policies = "Policies"
    case mcp = "MCP Servers"
    case skills = "Skills"
    case appearance = "Appearance"
    case updates = "Updates"

    var id: String { rawValue }
    var symbol: String {
        switch self {
        case .providers: return "network"
        case .models: return "cpu"
        case .policies: return "arrow.triangle.branch"
        case .mcp: return "puzzlepiece.extension"
        case .skills: return "folder"
        case .appearance: return "paintpalette"
        case .updates: return "arrow.down.circle"
        }
    }
    var collectionPath: [String] {
        switch self {
        case .providers: return ["providers"]
        case .models: return ["models"]
        case .policies: return ["blend", "policies"]
        case .mcp: return ["mcp"]
        case .skills: return ["skills", "roots"]
        default: return []
        }
    }
    var singular: String {
        switch self {
        case .providers: return "Provider"
        case .models: return "Model"
        case .policies: return "Policy"
        case .mcp: return "MCP Server"
        case .skills: return "Skill Folder"
        default: return rawValue
        }
    }
    var hasItems: Bool { !collectionPath.isEmpty }
}

struct NativeSettingsView: View {
    @EnvironmentObject private var model: DesktopModel
    @StateObject private var store = SettingsFormStore()
    @AppStorage("settings.lastCategory") private var lastCategory = NativeSettingsCategory.providers
        .rawValue
    @State private var category: NativeSettingsCategory? = .providers
    @State private var selectedItems: [NativeSettingsCategory: String] = [:]
    @State private var isAdding = false
    @State private var newName = ""
    @State private var confirmRemoval = false
    @State private var confirmReload = false
    @State private var showingCandidates = false
    @State private var columnVisibility = NavigationSplitViewVisibility.all

    private var current: NativeSettingsCategory { category ?? .providers }
    private var itemIDs: [String] {
        switch current {
        case .policies:
            let names = store.objectKeys(at: current.collectionPath)
            return names.isEmpty ? ["__single_policy__"] : names
        case .mcp, .skills:
            return store.arrayValues(at: current.collectionPath).indices.map(String.init)
        default:
            return store.objectKeys(at: current.collectionPath)
        }
    }
    private var selectedID: String? {
        if let selected = selectedItems[current], itemIDs.contains(selected) { return selected }
        return itemIDs.first
    }
    private var isSinglePolicy: Bool {
        current == .policies && store.objectKeys(at: ["blend", "policies"]).isEmpty
    }
    private var canRemoveSelection: Bool {
        selectedID != nil && store.snapshot != nil && store.phase != .loading && !store.isRunningCommand
            && !(isSinglePolicy && selectedID == "__single_policy__")
    }
    private var selection: Binding<String?> {
        Binding(get: { selectedID }, set: { selectedItems[current] = $0 })
    }

    var body: some View {
        settingsNavigation
            .navigationSplitViewStyle(.balanced)
            .frame(minWidth: 900, idealWidth: 1080, minHeight: 680, idealHeight: 760)
            .background(NativeSettingsWindowGuard(store: store))
            .toolbar { settingsToolbar }
            .task {
                category = NativeSettingsCategory(rawValue: lastCategory) ?? .providers
                await store.start()
            }
            .onChange(of: category) { _, value in lastCategory = (value ?? .providers).rawValue }
            .sheet(isPresented: $isAdding) { addSheet }
            .sheet(isPresented: $showingCandidates) { NativePolicyCandidatesView(evaluation: model.evaluation) }
            .confirmationDialog(
                "Remove \(current.singular)?", isPresented: $confirmRemoval, titleVisibility: .visible
            ) {
                Button("Remove", role: .destructive) { removeSelection() }
                Button("Cancel", role: .cancel) {}
            } message: {
                Text(
                    current == .skills
                        ? "This removes the folder from Settings. Its files stay on disk."
                        : "References to this item may need to be updated before your changes can be saved.")
            }
            .confirmationDialog("Reload Settings?", isPresented: $confirmReload, titleVisibility: .visible) {
                Button("Discard Changes and Reload", role: .destructive) {
                    Task { _ = await store.discardAndReload() }
                }
                Button("Keep Editing", role: .cancel) {}
            } message: {
                Text("Your unsaved draft will be replaced with the saved settings.")
            }
    }

    private var categoryList: some View {
        List(selection: $category) {
            ForEach(NativeSettingsCategory.allCases) { item in
                Label(item.rawValue, systemImage: item.symbol).tag(item)
            }
        }
        .listStyle(.sidebar)
        .accessibilityLabel("Settings categories")
    }

    @ViewBuilder private var settingsNavigation: some View {
        if current.hasItems {
            NavigationSplitView(columnVisibility: $columnVisibility) {
                navigationSidebar
            } content: {
                itemList
                    .navigationTitle(current.rawValue)
                    .navigationSplitViewColumnWidth(min: 160, ideal: 190, max: 220)
            } detail: {
                settingsDetail
            }
        } else {
            NavigationSplitView(columnVisibility: $columnVisibility) {
                navigationSidebar
            } detail: {
                settingsDetail
            }
        }
    }

    private var navigationSidebar: some View {
        categoryList
            .navigationTitle("Settings")
            .navigationSplitViewColumnWidth(min: 150, ideal: 170, max: 190)
    }

    private var itemList: some View {
        List(selection: selection) {
            ForEach(itemIDs, id: \.self) { id in
                VStack(alignment: .leading, spacing: 3) {
                    Text(itemTitle(id)).lineLimit(1)
                    if let subtitle = itemSubtitle(id), !subtitle.isEmpty {
                        Text(subtitle).font(.caption).foregroundStyle(.secondary).lineLimit(1)
                    }
                }
                .padding(.vertical, 3)
                .tag(id)
            }
        }
        .listStyle(.inset)
        .accessibilityLabel("\(current.rawValue) list")
        .onDeleteCommand { if canRemoveSelection { confirmRemoval = true } }
    }

    private var detailTitle: String {
        guard current.hasItems, let id = selectedID else { return current.rawValue }
        return itemTitle(id)
    }

    private var settingsDetail: some View {
        detailContent
            .frame(minWidth: 420, maxWidth: .infinity, maxHeight: .infinity)
            .navigationTitle(detailTitle)
            .navigationSubtitle(current.hasItems ? current.rawValue : "Arabica Settings")
            .safeAreaInset(edge: .bottom, spacing: 0) { saveStatus }
    }

    @ViewBuilder private var detailContent: some View {
        if current == .appearance {
            AppearancePageView()
        } else if current == .updates {
            NativeUpdatesSettingsView()
        } else {
            itemDetail
        }
    }

    @ToolbarContentBuilder private var settingsToolbar: some ToolbarContent {
        ToolbarItemGroup(placement: .primaryAction) {
            if current.hasItems {
                Button(action: beginAdd) { Label("Add \(current.singular)", systemImage: "plus") }
                    .disabled(store.snapshot == nil || store.phase == .loading || store.isRunningCommand)
                    .help("Add \(current.singular.lowercased())")
                Button {
                    confirmRemoval = true
                } label: {
                    Label("Remove \(current.singular)", systemImage: "trash")
                }
                .disabled(!canRemoveSelection)
                .help(removalHelp)
            }
        }
        ToolbarItem(placement: .automatic) {
            Button {
                reload()
            } label: {
                Label("Reload Settings", systemImage: "arrow.clockwise")
            }
            .disabled(store.isBusy)
            .help(
                store.isBusy
                    ? "Wait for the settings operation to finish" : "Refresh saved settings and discovered skills")
        }
        ToolbarItem(placement: .automatic) {
            Button("Reconnect", action: reconnect)
                .disabled(model.workspace == nil || model.hasActivityPreventingRelaunch || store.isBusy)
                .help(reconnectHelp)
        }
        ToolbarItem(placement: .automatic) {
            Menu {
                if current == .skills {
                    Button("Add Folder Path…") {
                        newName = ""
                        isAdding = true
                    }
                    Divider()
                }
                if current == .policies {
                    Button("Blend Policy Candidates…") { showingCandidates = true }
                    Divider()
                }
                Button("Open config.toml…") { NSWorkspace.shared.open(store.configURL) }
                    .help(store.configURL.path)
            } label: {
                Label("Advanced", systemImage: "ellipsis.circle")
            }
            .fixedSize()
            .help("Advanced settings actions")
        }
    }

    private var removalHelp: String {
        if selectedID == "__single_policy__", isSinglePolicy {
            return "The single policy cannot be removed. Add a named policy to replace it."
        }
        if selectedID == nil { return "Select an item to remove" }
        return "Remove selected \(current.singular.lowercased())"
    }

    private func reload() {
        if store.hasUnsavedChanges { confirmReload = true } else { Task { _ = await store.reload() } }
    }

    @ViewBuilder private var itemDetail: some View {
        if store.snapshot == nil {
            VStack(spacing: 12) {
                if store.isBusy { ProgressView() }
                Text(store.status.isEmpty ? "Loading settings…" : store.status)
                    .foregroundStyle(.secondary).multilineTextAlignment(.center)
                Button("Reload") { Task { _ = await store.reload() } }.disabled(store.isBusy)
            }
            .padding(28)
        } else if let id = selectedID {
            selectedForm(id)
                .disabled(store.phase == .loading || store.isRunningCommand)
        } else {
            ContentUnavailableView {
                Label("No \(current.rawValue)", systemImage: current.symbol)
            } description: {
                Text("Add a \(current.singular.lowercased()) to get started.")
            } actions: {
                Button("Add \(current.singular)…", action: beginAdd)
            }
        }
    }

    @ViewBuilder private func selectedForm(_ id: String) -> some View {
        let path = current.collectionPath + [id]
        switch current {
        case .providers: NativeProviderForm(store: store, path: path, name: id)
        case .models: NativeModelForm(store: store, path: path)
        case .policies:
            NativePolicyForm(store: store, path: id == "__single_policy__" && isSinglePolicy ? ["blend"] : path)
        case .mcp: NativeMCPForm(store: store, path: path)
        case .skills: NativeSkillsForm(store: store, path: path)
        default: EmptyView()
        }
    }

    private func itemTitle(_ id: String) -> String {
        if isSinglePolicy, id == "__single_policy__" { return "Single Policy" }
        switch current {
        case .mcp:
            let name = store.string(at: ["mcp", id, "name"])
            return name.isEmpty ? "Untitled Server" : name
        case .skills:
            let path = store.string(at: ["skills", "roots", id])
            return path.isEmpty ? "New Folder" : URL(fileURLWithPath: path).lastPathComponent
        default: return id
        }
    }

    private func itemSubtitle(_ id: String) -> String? {
        if isSinglePolicy, id == "__single_policy__" { return "Default routing" }
        switch current {
        case .providers:
            return store.string(at: ["providers", id, "auth"]) == "chatgpt"
                ? "ChatGPT account" : "API connection"
        case .models: return store.string(at: ["models", id, "model_id"])
        case .policies:
            return store.string(at: ["blend", "default_policy"]) == id
                ? "Default policy" : store.string(at: ["blend", "policies", id, "default_model"])
        case .mcp:
            return store.value(at: ["mcp", id, "url"]) == nil ? "Local process" : "Streamable HTTP"
        case .skills: return store.string(at: ["skills", "roots", id])
        default: return nil
        }
    }

    private var saveStatus: some View {
        HStack(alignment: .top, spacing: 8) {
            if store.isSaving {
                ProgressView().controlSize(.small).accessibilityHidden(true)
            } else {
                Image(
                    systemName: store.statusKind == .error
                        ? "exclamationmark.circle" : store.hasUnsavedChanges ? "circle.dotted" : "checkmark.circle"
                )
                .foregroundStyle(store.statusKind == .error ? Color.red : Color.secondary)
                .accessibilityHidden(true)
            }
            Text(store.status.isEmpty ? (store.hasUnsavedChanges ? "Unsaved changes" : "Saved") : store.status)
                .fixedSize(horizontal: false, vertical: true)
                .textSelection(.enabled)
            Spacer(minLength: 8)
            if store.needsReconnect {
                Text("Reconnect to Apply").foregroundStyle(.secondary)
                    .help("Saved connection changes take effect when you reconnect the workspace")
            }
        }
        .font(.caption)
        .padding(.horizontal, 20).padding(.vertical, 10)
        .background(.bar)
    }

    private var reconnectHelp: String {
        if model.workspace == nil { return "Open a workspace before reconnecting" }
        if model.hasActivityPreventingRelaunch {
            return "Wait for activity and approvals to finish before reconnecting"
        }
        if store.isBusy { return "Wait for the settings operation to finish" }
        return "Save pending changes and reconnect this workspace"
    }

    private func reconnect() {
        guard let workspace = model.workspace, !model.hasActivityPreventingRelaunch else { return }
        Task {
            guard await store.flush() else { return }
            await model.reconnectWorkspace(workspace)
            if model.workspace == workspace, model.isConnected { store.markReconnected() }
        }
    }

    private func beginAdd() {
        if current == .skills {
            let panel = NSOpenPanel()
            panel.canChooseDirectories = true
            panel.canChooseFiles = false
            panel.allowsMultipleSelection = true
            panel.prompt = "Add Folders"
            panel.begin { response in
                guard response == .OK else { return }
                for url in panel.urls { store.append(.string(url.path), at: ["skills", "roots"]) }
            }
        } else {
            newName = ""
            isAdding = true
        }
    }

    private var nameIsAvailable: Bool {
        let name = newName.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !name.isEmpty else { return false }
        if current == .policies, name == "soe" { return false }
        if current == .mcp {
            return !itemIDs.contains { store.string(at: ["mcp", $0, "name"]) == name }
        }
        return !store.objectKeys(at: current.collectionPath).contains(name)
    }

    private var addSheet: some View {
        NativeSettingsNameSheet(
            title: "Add \(current.singular)", label: current == .skills ? "Folder path" : "Name",
            name: $newName, canAdd: nameIsAvailable,
            detail: current == .skills
                ? "Files in this folder will not be changed."
                : current == .policies && store.objectKeys(at: ["blend", "policies"]).isEmpty
                    ? "Your current single policy becomes this named policy. Its models and routing rules are kept."
                    : "Choose a unique name. You can fill in its settings next."
        ) {
            addEntry()
            isAdding = false
        } cancel: {
            isAdding = false
        }
    }

    private static let legacyPolicyFields = [
        "version", "default_model", "after_tool_success", "after_tool_error", "recovery_model",
        "planning_model", "tool_routes", "recovery_after_no_progress_steps", "minimum_model_dwell_steps",
        "tool_call_capable_models", "typed_completion_capable_models",
    ]

    private func addEntry() {
        let name = newName.trimmingCharacters(in: .whitespacesAndNewlines)
        switch current {
        case .providers:
            store.set(.object([:]), at: ["providers", name])
            selectedItems[current] = name
        case .models:
            store.set(.object(["provider": .string(""), "model_id": .string("")]), at: ["models", name])
            selectedItems[current] = name
        case .policies:
            var policy: [String: SettingsValue] = ["default_model": .string("")]
            if store.objectKeys(at: ["blend", "policies"]).isEmpty {
                for key in Self.legacyPolicyFields {
                    if let value = store.value(at: ["blend", key]) {
                        policy[key] = value
                        store.remove(at: ["blend", key])
                    }
                }
                store.remove(at: ["blend", "policy_id"])
                store.set(.string(name), at: ["blend", "default_policy"])
            }
            store.set(.object(policy), at: ["blend", "policies", name])
            selectedItems[current] = name
        case .mcp:
            let index = store.arrayValues(at: ["mcp"]).count
            store.append(.object(["name": .string(name), "command": .string("")]), at: ["mcp"])
            selectedItems[current] = String(index)
        case .skills:
            let index = store.arrayValues(at: ["skills", "roots"]).count
            store.append(.string(name), at: ["skills", "roots"])
            selectedItems[current] = String(index)
        default: break
        }
    }

    private func removeSelection() {
        guard canRemoveSelection, let id = selectedID else { return }
        store.remove(at: current.collectionPath + [id])
        selectedItems[current] = nil
    }
}

private struct NativeSettingsNameSheet: View {
    let title: String
    let label: String
    @Binding var name: String
    let canAdd: Bool
    let detail: String
    let add: () -> Void
    let cancel: () -> Void
    @FocusState private var focused: Bool

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text(title).font(.headline)
            TextField(label, text: $name).textFieldStyle(.roundedBorder).focused($focused)
            Text(detail).font(.callout).foregroundStyle(.secondary)
            HStack {
                Spacer()
                Button("Cancel", action: cancel).keyboardShortcut(.cancelAction)
                Button("Add", action: add).keyboardShortcut(.defaultAction).disabled(!canAdd)
            }
        }
        .padding(24).frame(width: 380)
        .onAppear { focused = true }
    }
}

private struct NativeSettingsField: View {
    @ObservedObject var store: SettingsFormStore
    let title: String
    let path: [String]
    var optional = false
    var prompt = ""

    var body: some View {
        VStack(alignment: .leading, spacing: 5) {
            TextField(
                title,
                text: Binding(
                    get: { store.string(at: path) },
                    set: { value in
                        if optional && value.isEmpty {
                            store.remove(at: path)
                        } else {
                            store.set(.string(value), at: path)
                        }
                    }), prompt: Text(prompt)
            )
            .textFieldStyle(.roundedBorder)
            NativeSettingsFieldError(store: store, path: path)
        }
    }
}

private struct NativeSettingsFieldError: View {
    @ObservedObject var store: SettingsFormStore
    let path: [String]
    var body: some View {
        if let error = store.fieldError(at: path) {
            Label(error, systemImage: "exclamationmark.circle")
                .font(.caption).foregroundStyle(.red).fixedSize(horizontal: false, vertical: true)
        }
    }
}

private struct NativeSettingsChoice: View {
    @ObservedObject var store: SettingsFormStore
    let title: String
    let path: [String]
    let options: [(String, String)]
    var optional = true
    var optionalLabel = "Automatic"
    var onChange: (() -> Void)?

    private var current: String { store.string(at: path) }
    private var includesCurrent: Bool { current.isEmpty || options.contains { $0.0 == current } }

    var body: some View {
        VStack(alignment: .leading, spacing: 5) {
            Picker(
                title,
                selection: Binding(
                    get: { current },
                    set: { value in
                        if optional && value.isEmpty {
                            store.remove(at: path)
                        } else {
                            store.set(.string(value), at: path)
                        }
                        onChange?()
                    })
            ) {
                if optional {
                    Text(optionalLabel).tag("")
                } else if current.isEmpty {
                    Text("Choose…").tag("")
                }
                ForEach(options, id: \.0) { value, label in Text(label).tag(value) }
                if !includesCurrent { Text(current).tag(current) }
            }
            NativeSettingsFieldError(store: store, path: path)
        }
    }
}

private struct NativeSettingsNumber: View {
    @ObservedObject var store: SettingsFormStore
    let title: String
    let path: [String]
    var prompt = "Automatic"

    var body: some View {
        VStack(alignment: .leading, spacing: 5) {
            TextField(
                title,
                text: Binding(
                    get: {
                        if case .string(let text) = store.value(at: path) { return text }
                        guard let value = store.number(at: path) else { return "" }
                        return value.rounded() == value ? String(format: "%.0f", value) : String(value)
                    },
                    set: { text in
                        if text.isEmpty {
                            store.remove(at: path)
                        } else if let number = Double(text), number.isFinite {
                            store.set(.number(number), at: path)
                        } else {
                            store.set(.string(text), at: path)
                        }
                    }), prompt: Text(prompt)
            )
            .textFieldStyle(.roundedBorder)
            NativeSettingsFieldError(store: store, path: path)
        }
    }
}

private struct NativeSettingsOptionalBoolean: View {
    @ObservedObject var store: SettingsFormStore
    let title: String
    let path: [String]

    private var current: String {
        guard case .bool(let value) = store.value(at: path) else { return "" }
        return value ? "on" : "off"
    }

    var body: some View {
        Picker(
            title,
            selection: Binding(
                get: { current },
                set: { value in
                    if value.isEmpty { store.remove(at: path) } else { store.set(.bool(value == "on"), at: path) }
                })
        ) {
            Text("Automatic").tag("")
            Text("On").tag("on")
            Text("Off").tag("off")
        }
    }
}

private struct NativeSettingsSecret: View {
    @ObservedObject var store: SettingsFormStore
    let title: String
    let path: [String]
    var allowsRemoval = true

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            SecureField(
                title,
                text: Binding(
                    get: { store.secretText(at: path) }, set: { store.setSecretText($0, at: path) }),
                prompt: Text(
                    store.secretExists(at: path) ? "Saved value kept unless replaced" : "Enter value")
            )
            .textFieldStyle(.roundedBorder)
            HStack {
                if store.secretExists(at: path) {
                    Text("Saved value. Leave the field empty to keep it.").font(.caption).foregroundStyle(.secondary)
                    Spacer()
                    if allowsRemoval {
                        Button("Remove", role: .destructive) { store.remove(at: path) }.controlSize(.small)
                    }
                }
                if allowsRemoval, store.snapshot?.secretPaths.contains(path) == true, store.value(at: path) == nil {
                    Button("Restore Saved Value") { store.restoreSecret(at: path) }.controlSize(.small)
                }
            }
            NativeSettingsFieldError(store: store, path: path)
        }
        .disabled(store.isSaving || store.isRunningCommand)
        .help(
            store.isSaving
                ? "Wait for the credential update to finish" : "Leave the field empty to keep the saved value")
    }
}

private struct NativeProviderForm: View {
    @ObservedObject var store: SettingsFormStore
    let path: [String]
    let name: String
    @State private var confirmChatGPTConnection = false

    private var authSelection: String {
        let value = store.string(at: path + ["auth"])
        if value.lowercased() == "chatgpt" { return "chatgpt" }
        return value.isEmpty ? "api_key" : value
    }
    private var chatGPT: Bool { authSelection == "chatgpt" }
    private var apiType: String {
        let value = store.string(at: path + ["api_type"])
        if value.isEmpty { return chatGPT ? "open_ai_responses" : "open_ai_chat_completions" }
        switch value.trimmingCharacters(in: .whitespacesAndNewlines).lowercased() {
        case "open_ai_responses", "openai_responses", "responses": return "open_ai_responses"
        case "open_ai_chat_completions", "openai_chat_completions", "chat_completions":
            return "open_ai_chat_completions"
        case "anthropic_messages": return "anthropic_messages"
        case "gemini_generate_content": return "gemini_generate_content"
        case "gemini_interactions": return "gemini_interactions"
        default: return value
        }
    }
    private let apiTypes = [
        ("open_ai_responses", "OpenAI Responses"),
        ("open_ai_chat_completions", "OpenAI Chat Completions"),
        ("anthropic_messages", "Anthropic Messages"),
        ("gemini_generate_content", "Gemini Generate Content"),
        ("gemini_interactions", "Gemini Interactions"),
    ]

    var body: some View {
        Form {
            connectionSection
            if chatGPT { accountSection } else { credentialsSection }
            generationSection
            Section {
                Button("Sync Available Models") { Task { _ = await store.syncModels(provider: name) } }
                    .disabled(store.isBusy)
                    .help(
                        store.isBusy
                            ? "Wait for the settings operation to finish"
                            : "Refresh the models available from this provider")
            }
        }
        .formStyle(.grouped)
        .onChange(of: path) { _, _ in confirmChatGPTConnection = false }
        .confirmationDialog(
            "Switch to ChatGPT?", isPresented: $confirmChatGPTConnection, titleVisibility: .visible
        ) {
            Button("Change Connection and Remove API Key Settings", role: .destructive) {
                changeAuthentication("chatgpt")
            }
            Button("Cancel", role: .cancel) {}
        } message: {
            Text(
                "This replaces the provider connection with ChatGPT and removes its saved API key and API key environment variable setting. Cancel to keep the current connection."
            )
        }
    }

    private var connectionSection: some View {
        Section("Connection") {
            Picker("Authentication", selection: Binding(get: { authSelection }, set: requestAuthenticationChange)) {
                Text("API Key").tag("api_key")
                Text("ChatGPT").tag("chatgpt")
                if authSelection != "api_key" && authSelection != "chatgpt" {
                    Text("\(authSelection) (Current)").tag(authSelection)
                }
            }
            NativeSettingsFieldError(store: store, path: path + ["auth"])
            if chatGPT {
                LabeledContent("API Format", value: "OpenAI Responses")
                    .help("ChatGPT accounts use the OpenAI Responses API")
                LabeledContent("Base URL", value: "https://api.openai.com/v1")
                    .help("The ChatGPT account connection uses this endpoint")
            } else {
                NativeSettingsChoice(
                    store: store, title: "API Format", path: path + ["api_type"], options: apiTypes,
                    onChange: { cleanGenerationSettings(for: apiType) })
                NativeSettingsField(
                    store: store, title: "Base URL", path: path + ["base_url"], optional: true,
                    prompt: "Provider default")
            }
        }
    }

    private var credentialsSection: some View {
        Section("Credentials") {
            NativeSettingsField(
                store: store, title: "API Key Environment Variable", path: path + ["api_key_env"], optional: true)
            NativeSettingsSecret(store: store, title: "API Key", path: path + ["api_key"])
            Text(
                "Leave the key field empty to keep the saved key. You can also provide the key through an environment variable."
            )
            .font(.caption).foregroundStyle(.secondary)
        }
    }

    private var generationSection: some View {
        Section("Generation") {
            thinkingPicker
            NativeSettingsFieldError(store: store, path: path + ["thinking"])
            NativeSettingsFieldError(store: store, path: path + ["reasoning_effort"])
            NativeSettingsNumber(store: store, title: "Maximum Output Tokens", path: path + ["max_tokens"])
            NativeSettingsNumber(
                store: store, title: "Request Timeout (seconds)", path: path + ["request_timeout_secs"])
            if apiType == "anthropic_messages" {
                NativeSettingsOptionalBoolean(
                    store: store, title: "Cache Anthropic Static Prefix", path: path + ["anthropic_cache_static_prefix"]
                )
            }
        }
    }

    private var accountSection: some View {
        Section("ChatGPT Account") {
            Text(store.chatGPTConnected ? "Signed in" : "Sign in to use your ChatGPT account.").foregroundStyle(
                .secondary)
            HStack {
                Button("Sign In…") { Task { _ = await store.runChatGPTLogin() } }
                    .disabled(store.isBusy)
                    .help(
                        store.isBusy
                            ? "Wait for the settings operation to finish" : "Sign in to ChatGPT in your browser")
                Button("Sign Out") { Task { _ = await store.runChatGPTLogout() } }
                    .disabled(store.isBusy || !store.chatGPTConnected)
                    .help(
                        store.chatGPTConnected
                            ? "Sign out of the saved ChatGPT account" : "No ChatGPT account is signed in")
            }
            Text("Sign-in opens in your browser after valid pending changes are saved.").font(.caption).foregroundStyle(
                .secondary)
        }
    }

    private var thinkingPicker: some View {
        let current = store.string(at: path + ["thinking"])
        let legacy = store.string(at: path + ["reasoning_effort"])
        let value = current.isEmpty ? legacy : current
        let options = ConfigEditor.supportedThinkingValues(apiType: apiType)
        return Picker(
            "Thinking Level",
            selection: Binding(
                get: { value },
                set: { selected in
                    store.remove(at: path + ["reasoning_effort"])
                    if selected.isEmpty {
                        store.remove(at: path + ["thinking"])
                    } else {
                        store.set(.string(selected), at: path + ["thinking"])
                    }
                })
        ) {
            Text("Automatic").tag("")
            ForEach(options, id: \.self) { Text($0.capitalized).tag($0) }
            if !value.isEmpty, !options.contains(value) { Text("\(value) (Current)").tag(value) }
        }
    }

    private func requestAuthenticationChange(_ selection: String) {
        if selection == "chatgpt", !chatGPT,
            store.value(at: path + ["api_key"]) != nil || store.value(at: path + ["api_key_env"]) != nil
        {
            confirmChatGPTConnection = true
        } else {
            changeAuthentication(selection)
        }
    }

    private func changeAuthentication(_ selection: String) {
        if selection == "chatgpt" {
            store.remove(at: path + ["api_key"])
            store.remove(at: path + ["api_key_env"])
            store.set(.string("chatgpt"), at: path + ["auth"])
            store.set(.string("open_ai_responses"), at: path + ["api_type"])
            store.set(.string("https://api.openai.com/v1"), at: path + ["base_url"])
            cleanGenerationSettings(for: "open_ai_responses")
        } else if selection == "api_key" {
            store.remove(at: path + ["auth"])
        }
    }

    private func cleanGenerationSettings(for apiType: String) {
        let supported = ConfigEditor.supportedThinkingValues(apiType: apiType)
        let thinking = store.string(at: path + ["thinking"])
        let legacy = store.string(at: path + ["reasoning_effort"])
        if !thinking.isEmpty && !supported.contains(thinking) { store.remove(at: path + ["thinking"]) }
        if !legacy.isEmpty && (!supported.contains(legacy) || apiType != "open_ai_responses") {
            store.remove(at: path + ["reasoning_effort"])
        }
        if store.value(at: path + ["thinking"]) != nil { store.remove(at: path + ["reasoning_effort"]) }
        if apiType != "anthropic_messages", store.bool(at: path + ["anthropic_cache_static_prefix"]) {
            store.remove(at: path + ["anthropic_cache_static_prefix"])
        }
    }
}

private struct NativeModelForm: View {
    @ObservedObject var store: SettingsFormStore
    let path: [String]
    var body: some View {
        Form {
            Section("Model") {
                NativeSettingsChoice(
                    store: store, title: "Provider", path: path + ["provider"],
                    options: store.objectKeys(at: ["providers"]).map { ($0, $0) }, optional: false)
                NativeSettingsField(
                    store: store, title: "Model ID", path: path + ["model_id"],
                    prompt: "Model identifier from your provider")
                Text("Use this alias in SOE or assign it to a policy.").font(.caption).foregroundStyle(
                    .secondary)
            }
        }
        .formStyle(.grouped)
    }
}

private struct NativeDefaultPolicySection: View {
    @ObservedObject var store: SettingsFormStore
    private var options: [(String, String)] {
        store.objectKeys(at: ["blend", "policies"]).map { ($0, $0) }
    }
    var body: some View {
        Section("Default Selection") {
            if !options.isEmpty {
                NativeSettingsChoice(
                    store: store, title: "Default Policy", path: ["blend", "default_policy"], options: options)
            }
            Text("Used for new connections. Choose SOE in the composer to use one model for a conversation.")
                .font(.caption).foregroundStyle(.secondary)
        }
    }
}

private struct NativePolicyForm: View {
    @ObservedObject var store: SettingsFormStore
    let path: [String]
    private var models: [(String, String)] { store.objectKeys(at: ["models"]).map { ($0, $0) } }

    var body: some View {
        Form {
            NativeDefaultPolicySection(store: store)
            Section("Policy") {
                if path == ["blend"] {
                    NativeSettingsField(store: store, title: "Policy ID", path: path + ["policy_id"], optional: true)
                }
                NativeSettingsChoice(
                    store: store, title: "Default Model", path: path + ["default_model"], options: models,
                    optional: false)
                NativeSettingsNumber(store: store, title: "Version", path: path + ["version"], prompt: "1")
            }
            Section("Model Routing") {
                NativeSettingsChoice(
                    store: store, title: "After Tool Success", path: path + ["after_tool_success"],
                    options: models, optionalLabel: "Keep Current Model")
                NativeSettingsChoice(
                    store: store, title: "After Tool Error", path: path + ["after_tool_error"],
                    options: models, optionalLabel: "Keep Current Model")
                NativeSettingsChoice(
                    store: store, title: "Recovery Model", path: path + ["recovery_model"], options: models,
                    optionalLabel: "No Recovery Model")
                NativeSettingsChoice(
                    store: store, title: "Planning Model", path: path + ["planning_model"], options: models,
                    optionalLabel: "No Planning Model")
                NativeSettingsNumber(
                    store: store, title: "Recover After Steps Without Progress",
                    path: path + ["recovery_after_no_progress_steps"], prompt: "2")
                NativeSettingsNumber(
                    store: store, title: "Minimum Steps Before Switching Models",
                    path: path + ["minimum_model_dwell_steps"], prompt: "1")
            }
            Section("Model Capabilities") {
                NativeCapabilityEditor(
                    store: store, title: "Tool Calls", path: path + ["tool_call_capable_models"])
                NativeCapabilityEditor(
                    store: store, title: "Typed Completion", path: path + ["typed_completion_capable_models"])
            }
            Section("Tool Routes") { NativeToolRoutesEditor(store: store, path: path + ["tool_routes"]) }
        }
        .formStyle(.grouped)
    }
}

private struct NativeCapabilityEditor: View {
    @ObservedObject var store: SettingsFormStore
    let title: String
    let path: [String]
    private var selected: [SettingsValue] { store.arrayValues(at: path) }
    var body: some View {
        DisclosureGroup(title) {
            ForEach(store.objectKeys(at: ["models"]), id: \.self) { alias in
                Toggle(
                    alias,
                    isOn: Binding(
                        get: { selected.contains(.string(alias)) },
                        set: { enabled in
                            var values = selected.filter { $0 != .string(alias) }
                            if enabled { values.append(.string(alias)) }
                            store.set(.array(values), at: path)
                        })
                )
                .toggleStyle(.checkbox)
            }
            NativeSettingsFieldError(store: store, path: path)
        }
    }
}

private struct NativeToolRoutesEditor: View {
    @ObservedObject var store: SettingsFormStore
    let path: [String]
    @State private var selection: Int?
    @State private var confirmRemoval = false
    private var count: Int { store.arrayValues(at: path).count }
    private var selected: Int? {
        selection.flatMap { (0..<count).contains($0) ? $0 : nil } ?? (count > 0 ? 0 : nil)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            if count > 0 {
                List(selection: Binding(get: { selected }, set: { selection = $0 })) {
                    ForEach(0..<count, id: \.self) { index in
                        Text(store.string(at: path + [String(index), "id"])).tag(index)
                    }
                }
                .frame(minHeight: 90, maxHeight: 150)
                .accessibilityLabel("Tool routes")
            } else {
                Text("No tool-specific routes.").foregroundStyle(.secondary)
            }
            HStack {
                Button("Add Route", systemImage: "plus") {
                    let index = count
                    var suffix = index + 1
                    let existing = (0..<count).map { store.string(at: path + [String($0), "id"]) }
                    while existing.contains("route-\(suffix)") { suffix += 1 }
                    store.append(
                        .object([
                            "id": .string("route-\(suffix)"), "model": .string(""),
                            "matcher": .object(["type": .string("exact_name"), "value": .string("")]),
                        ]), at: path)
                    selection = index
                }
                Button("Remove Route", systemImage: "minus") { confirmRemoval = true }.disabled(
                    selected == nil)
            }
            .controlSize(.small)
            if let selected { routeForm(path + [String(selected)]) }
        }
        .confirmationDialog(
            "Remove Tool Route?", isPresented: $confirmRemoval, titleVisibility: .visible
        ) {
            Button("Remove", role: .destructive) {
                if let selected {
                    store.remove(at: path + [String(selected)])
                    selection = nil
                }
            }
            Button("Cancel", role: .cancel) {}
        }
    }

    @ViewBuilder private func routeForm(_ route: [String]) -> some View {
        NativeSettingsField(store: store, title: "Route ID", path: route + ["id"])
        NativeSettingsChoice(
            store: store, title: "Match", path: route + ["matcher", "type"],
            options: [
                ("exact_name", "Exact Tool Name"), ("regex_name", "Tool Name Pattern"),
                ("kind", "Interaction Kind"), ("event_trace_regex", "Event Trace Pattern"),
                ("event_trace_v1_regex", "Event Trace V1 Pattern"),
            ], optional: false)
        if store.string(at: route + ["matcher", "type"]) == "kind" {
            NativeSettingsChoice(
                store: store, title: "Interaction", path: route + ["matcher", "value"],
                options: [
                    "plan", "inspection", "mutation", "mutation_with_validation", "build", "dependency",
                    "validation", "generic",
                ].map { ($0, $0.replacingOccurrences(of: "_", with: " ").capitalized) }, optional: false)
        } else {
            NativeSettingsField(
                store: store, title: "Name or Regular Expression", path: route + ["matcher", "value"])
        }
        NativeSettingsChoice(
            store: store, title: "Model", path: route + ["model"],
            options: store.objectKeys(at: ["models"]).map { ($0, $0) }, optional: false)
        NativeSettingsNumber(store: store, title: "Priority", path: route + ["priority"], prompt: "0")
    }
}

private struct NativeMCPForm: View {
    @ObservedObject var store: SettingsFormStore
    let path: [String]
    @State private var pendingTransport: String?
    private var isHTTP: Bool { store.value(at: path + ["url"]) != nil }

    var body: some View {
        Form {
            Section("Server") {
                NativeSettingsField(store: store, title: "Name", path: path + ["name"])
                Picker(
                    "Transport", selection: Binding(get: { isHTTP ? "http" : "stdio" }, set: requestTransportChange)
                ) {
                    Text("Local Process (stdio)").tag("stdio")
                    Text("Streamable HTTP").tag("http")
                }
            }
            if isHTTP {
                Section("HTTP Connection") {
                    NativeSettingsField(
                        store: store, title: "Server URL", path: path + ["url"],
                        prompt: "https://example.com/mcp")
                    Text("Supports stateless MCP with compatibility for existing servers.").font(.caption)
                        .foregroundStyle(.secondary)
                }
                Section("HTTP Headers") {
                    NativeSecretMapEditor(store: store, path: path + ["headers"], kind: "Header")
                }
            } else {
                Section("Local Process") {
                    NativeSettingsField(
                        store: store, title: "Executable", path: path + ["command"],
                        prompt: "Absolute path to the executable")
                    Text("Choose an executable by its full path. Each argument below is passed separately.")
                        .font(.caption).foregroundStyle(.secondary)
                    Button("Choose Executable…", action: chooseExecutable)
                }
                Section("Arguments") { NativeArgumentsEditor(store: store, path: path + ["args"]) }
                Section("Environment Variables") {
                    NativeSecretMapEditor(store: store, path: path + ["env"], kind: "Variable")
                }
            }
            Section {
                Text("Saved changes take effect when you reconnect the workspace.").font(.caption)
                    .foregroundStyle(.secondary)
            }
        }
        .formStyle(.grouped)
        .onChange(of: path) { _, _ in pendingTransport = nil }
        .confirmationDialog(
            "Change Transport?",
            isPresented: Binding(
                get: { pendingTransport != nil }, set: { if !$0 { pendingTransport = nil } }
            ), titleVisibility: .visible
        ) {
            Button("Change and Remove Incompatible Settings", role: .destructive) {
                if let transport = pendingTransport { changeTransport(transport) }
                pendingTransport = nil
            }
            Button("Cancel", role: .cancel) { pendingTransport = nil }
        } message: {
            Text(
                pendingTransport == "http"
                    ? "The local executable, arguments, and environment variables will be removed. Streamable HTTP uses a URL and HTTP headers instead."
                    : "The server URL and HTTP header entries will be removed. A local process uses an executable and environment variables instead."
            )
        }
    }

    private func requestTransportChange(_ transport: String) {
        guard (transport == "http") != isHTTP else { return }
        let hasIncompatibleValues =
            transport == "http"
            ? !store.arrayValues(at: path + ["args"]).isEmpty || !store.objectKeys(at: path + ["env"]).isEmpty
            : !store.objectKeys(at: path + ["headers"]).isEmpty
        if hasIncompatibleValues { pendingTransport = transport } else { changeTransport(transport) }
    }

    private func changeTransport(_ transport: String) {
        if transport == "http" {
            store.remove(at: path + ["command"])
            store.remove(at: path + ["args"])
            store.remove(at: path + ["env"])
            store.set(.string(""), at: path + ["url"])
        } else {
            store.remove(at: path + ["url"])
            store.remove(at: path + ["headers"])
            store.set(.string(""), at: path + ["command"])
        }
    }

    private func chooseExecutable() {
        let panel = NSOpenPanel()
        panel.canChooseDirectories = false
        panel.canChooseFiles = true
        panel.prompt = "Choose Executable"
        panel.begin { response in
            if response == .OK, let url = panel.url {
                store.set(.string(url.path), at: path + ["command"])
            }
        }
    }
}

private struct NativeArgumentsEditor: View {
    @ObservedObject var store: SettingsFormStore
    let path: [String]
    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            ForEach(store.arrayValues(at: path).indices, id: \.self) { index in
                HStack(alignment: .top) {
                    NativeSettingsField(
                        store: store, title: "Argument \(index + 1)", path: path + [String(index)])
                    Button {
                        store.remove(at: path + [String(index)])
                    } label: {
                        Image(systemName: "minus.circle")
                    }
                    .buttonStyle(.borderless).accessibilityLabel("Remove argument \(index + 1)")
                }
            }
            Button("Add Argument", systemImage: "plus") { store.append(.string(""), at: path) }
                .controlSize(.small)
        }
    }
}

private struct NativeSecretMapEditor: View {
    @ObservedObject var store: SettingsFormStore
    let path: [String]
    let kind: String
    @State private var adding = false
    @State private var newKey = ""
    @State private var pendingRemoval: String?
    private var normalizedKey: String { newKey.trimmingCharacters(in: .whitespacesAndNewlines) }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            ForEach(store.objectKeys(at: path), id: \.self) { key in
                HStack(alignment: .top, spacing: 10) {
                    NativeSettingsSecret(store: store, title: key, path: path + [key], allowsRemoval: false)
                    Button {
                        pendingRemoval = key
                    } label: {
                        Image(systemName: "minus.circle")
                    }
                    .buttonStyle(.borderless).accessibilityLabel("Remove \(kind.lowercased()) \(key)")
                }
            }
            Button("Add \(kind)", systemImage: "plus") {
                newKey = ""
                adding = true
            }.controlSize(.small)
        }
        .sheet(isPresented: $adding) {
            NativeSettingsNameSheet(
                title: "Add \(kind)", label: "Name", name: $newKey,
                canAdd: !normalizedKey.isEmpty && !store.objectKeys(at: path).contains(normalizedKey),
                detail: "Values stay hidden. Existing values are kept unless you replace or remove them."
            ) {
                let key = newKey.trimmingCharacters(in: .whitespacesAndNewlines)
                store.set(.string(""), at: path + [key])
                adding = false
            } cancel: {
                adding = false
            }
        }
        .confirmationDialog(
            "Remove \(kind)?",
            isPresented: Binding(
                get: { pendingRemoval != nil }, set: { if !$0 { pendingRemoval = nil } }),
            titleVisibility: .visible
        ) {
            Button("Remove", role: .destructive) {
                if let key = pendingRemoval { store.remove(at: path + [key]) }
                pendingRemoval = nil
            }
            Button("Cancel", role: .cancel) { pendingRemoval = nil }
        }
    }
}

private struct NativeSkillsForm: View {
    @ObservedObject var store: SettingsFormStore
    let path: [String]

    var body: some View {
        Form {
            Section("Skill Folder") {
                NativeSettingsField(store: store, title: "Folder Path", path: path)
                Button("Reveal in Finder") { NSWorkspace.shared.activateFileViewerSelecting([resolvedURL]) }
                Text("Removing a folder from Settings does not delete any files.").font(.caption)
                    .foregroundStyle(.secondary)
            }
            Section("Discovered Skills") {
                if matchingSkills.isEmpty {
                    Text("No skills found in this folder. Reload after installing a skill.").foregroundStyle(
                        .secondary)
                }
                ForEach(matchingSkills) { skill in
                    VStack(alignment: .leading, spacing: 6) {
                        Text(skill.name).font(.headline)
                        if !skill.description.isEmpty {
                            Text(skill.description).font(.callout).foregroundStyle(.secondary)
                        }
                        Button("Reveal in Finder") {
                            NSWorkspace.shared.activateFileViewerSelecting([skill.directoryURL])
                        }
                    }
                }
            }
        }
        .formStyle(.grouped)
    }

    private var resolvedURL: URL {
        let value = store.string(at: path)
        if NSString(string: value).isAbsolutePath {
            return URL(fileURLWithPath: NSString(string: value).expandingTildeInPath)
        }
        return store.arabicaHomeURL.appendingPathComponent(value).standardizedFileURL
    }
    private var matchingSkills: [DiscoveredSkillItem] {
        store.discoveredSkills.filter { $0.rootPath == store.string(at: path) }
    }
}

private struct NativeUpdatesSettingsView: View {
    @EnvironmentObject private var updater: AppUpdater
    var body: some View {
        Form {
            Section("Arabica") {
                LabeledContent(
                    "Version",
                    value: Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String
                        ?? "Development")
            }
            Section("Software Updates") {
                Toggle(
                    "Automatically Check for Updates",
                    isOn: Binding(
                        get: { updater.automaticallyChecksForUpdates }, set: updater.setAutomaticChecks))
                Toggle(
                    "Download Updates and Install When Quitting",
                    isOn: Binding(
                        get: { updater.automaticallyDownloadsUpdates }, set: updater.setAutomaticDownloads)
                )
                .disabled(!updater.automaticallyChecksForUpdates)
                .help(
                    updater.automaticallyChecksForUpdates
                        ? "Install downloaded updates when Arabica quits"
                        : "Enable automatic update checks first")
                Button("Check for Updates…") { updater.checkForUpdates() }.disabled(
                    !updater.canCheckForUpdates)
                if updater.waitingForRuns {
                    Text("Arabica will restart after activity in all conversation windows finishes.")
                        .foregroundStyle(.secondary)
                }
                if !updater.isAvailable {
                    Text("Updates are available in packaged Arabica releases.").foregroundStyle(.secondary)
                }
            }
            .disabled(!updater.isAvailable)
        }
        .formStyle(.grouped)
    }
}
