import AppKit
import Combine
import Darwin
import Foundation

@MainActor
final class ConfigEditor: ObservableObject {
    enum PendingAction {
        case provider(String)
        case model(String)
        case addProvider
        case addModel
        case reload
    }

    enum StatusKind {
        case information
        case success
        case error
    }

    struct ChatGPTAuthPreparation {
        fileprivate let launchDraft: [String]
    }

    @Published var providerOptions: [String] = []
    @Published var providerName = "primary"
    @Published var apiType = "open_ai_responses"
    @Published var authMode = "api_key"
    @Published var baseURL = "https://api.openai.com/v1"
    @Published var apiKeyEnv = "ARABICA_PROVIDER_PRIMARY_API_KEY"
    @Published var apiKey = ""
    @Published var hasSavedKey = false
    @Published var clearSavedKey = false
    @Published var modelOptions: [String] = []
    @Published var modelProviders: [String: String] = [:]
    @Published var modelAlias = "default"
    @Published var modelProvider = "primary"
    @Published var modelID = ""
    @Published var defaultPolicy = ""
    @Published var policyDefaultModel = "default"
    @Published var policyOptions: [String] = []
    @Published var configuredPolicies: [ConfiguredPolicyItem] = []
    @Published var thinking = ""
    @Published var status = ""
    @Published private(set) var statusKind: StatusKind = .information
    @Published var pendingAction: PendingAction?
    @Published var canAddEntries = false
    @Published var isSaving = false
    @Published var isChatGPTAuthRunning = false
    @Published var chatGPTConnected = false
    @Published var mcpServers: [MCPServerItem] = []
    @Published var skillRoots: [SkillRootItem] = []
    @Published var discoveredSkills: [DiscoveredSkillItem] = []
    @Published var isSyncingModels = false

    var modelsForSelectedProvider: [String] {
        let aliases = modelOptions.filter { modelProviders[$0] == providerName }
        return modelAlias.isEmpty || aliases.contains(modelAlias) ? aliases : aliases + [modelAlias]
    }

    var hasUnsavedChanges: Bool { draft != savedDraft }

    var canChangeChatGPTConnection: Bool {
        !isSaving && !isChatGPTAuthRunning && !isSyncingModels
            && (!chatGPTConnected || !hasUnsavedChanges)
    }

    var chatGPTAuthButtonTitle: String {
        if isChatGPTAuthRunning { return chatGPTConnected ? "Disconnecting…" : "Waiting for browser…" }
        if chatGPTConnected { return "Disconnect" }
        return hasUnsavedChanges && !isChatGPTBootstrapDraft ? "Save & Sign in" : "Sign in"
    }

    var chatGPTAuthHelp: String {
        if isChatGPTAuthRunning { return "Wait for the current account operation to finish." }
        if isSaving || isSyncingModels { return "Wait for the configuration update to finish." }
        if hasUnsavedChanges && !isChatGPTBootstrapDraft {
            return chatGPTConnected
                ? "Save Changes or discard your draft before disconnecting the account."
                : "Save the current configuration, then open ChatGPT sign-in in your browser."
        }
        return chatGPTConnected ? "Disconnect the locally saved ChatGPT account." : "Sign in to ChatGPT in your browser."
    }

    private var draft: [String] {
        [providerName, apiType, authMode, baseURL, apiKeyEnv, apiKey, String(clearSavedKey),
         modelAlias, modelProvider, modelID, defaultPolicy, policyDefaultModel, thinking]
    }

    private var original = ""
    private var savedDraft: [String] = []
    private var chatGPTBootstrapDraft: [String]?
    private var isChatGPTBootstrapDraft: Bool { chatGPTBootstrapDraft == draft }
    private let explicitConfigURL: URL?
    private var originalKey: String?
    private var providerSection = "providers.primary"
    private var modelSection: String?
    var configURL: URL {
        if let explicitConfigURL { return explicitConfigURL }
        let home = ProcessInfo.processInfo.environment["ARABICA_HOME"]
            .map { URL(fileURLWithPath: $0, isDirectory: true) }
            ?? FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent(".arabica", isDirectory: true)
        return home.appendingPathComponent("config.toml")
    }

    var arabicaHomeURL: URL {
        configURL.deletingLastPathComponent()
    }

    init(configURL: URL? = nil) {
        explicitConfigURL = configURL
        reload()
    }

    func reload() {
        chatGPTBootstrapDraft = nil
        status = ""
        statusKind = .information
        originalKey = nil
        hasSavedKey = false
        clearSavedKey = false
        apiKey = ""
        authMode = "api_key"
        chatGPTConnected = Self.hasChatGPTCredentials(at: arabicaHomeURL)
        do {
            original = try String(contentsOf: configURL, encoding: .utf8)
        } catch CocoaError.fileReadNoSuchFile {
            original = ""
        } catch {
            original = ""
            report("Could not read \(configURL.path): \(error.localizedDescription)", kind: .error)
            savedDraft = draft
            return
        }

        let document = ConfigDocument(original)
        let blend = document.values(in: "blend")
        let providerSections = document.childSectionNames(in: "providers").sorted()
        providerOptions = providerSections
        let modelSections = document.childSectionNames(in: "models").sorted()
        modelOptions = modelSections
        modelProviders = modelSections.reduce(into: [:]) { providers, alias in
            guard let provider = document.values(in: ConfigDocument.tablePath("models", alias))["provider"] else { return }
            providers[alias] = provider
        }
        policyOptions = document.childSectionNames(in: "blend.policies").sorted()
        let configuredPolicy = blend["default_policy"]
        defaultPolicy = configuredPolicy.flatMap { policyOptions.contains($0) ? $0 : nil } ?? policyOptions.first ?? ""
        if providerOptions.isEmpty, document.sectionNames.contains("provider") {
            providerOptions = ["primary"]
            providerSection = "provider"
            providerName = "primary"
        } else {
            if !providerOptions.contains(providerName) {
                providerName = providerOptions.first ?? "primary"
            }
            providerSection = ConfigDocument.tablePath("providers", providerName)
        }
        let initialPolicy = policyOptions.contains(defaultPolicy) ? defaultPolicy : policyOptions.first
        if let initialPolicy {
            policyDefaultModel = document.values(in: ConfigDocument.tablePath("blend", "policies", initialPolicy))["default_model"] ?? ""
        } else {
            policyDefaultModel = blend["default_model"] ?? ""
        }
        if modelOptions.isEmpty { modelOptions = ["default"] }
        if policyDefaultModel.isEmpty { policyDefaultModel = modelOptions.first ?? "default" }
        if !modelOptions.contains(modelAlias) {
            modelAlias = modelOptions.contains(policyDefaultModel) ? policyDefaultModel : modelOptions.first ?? "default"
        }
        modelSection = modelOptions.contains(modelAlias) ? ConfigDocument.tablePath("models", modelAlias) : nil
        let model = modelSection.map { document.values(in: $0) } ?? [:]
        modelProvider = model["provider"] ?? providerName
        if providerOptions.contains(modelProvider) {
            providerName = modelProvider
            providerSection = ConfigDocument.tablePath("providers", providerName)
        }
        loadProvider(from: document)
        modelID = model["model_id"] ?? (providerSection == "provider" ? document.values(in: providerSection)["model"] : nil) ?? ""
        canAddEntries = !providerSections.isEmpty && !modelSections.isEmpty

        // Policy items parsing
        configuredPolicies = document.configuredPolicies()

        // MCP and Skills discovery
        mcpServers = document.mcpServers()
        let roots = document.skillRoots(relativeTo: arabicaHomeURL)
        skillRoots = roots
        discoveredSkills = scanDiscoveredSkills(in: roots)
        savedDraft = draft
    }

    func selectProvider(_ name: String) {
        guard name != providerName else { return }
        request(.provider(name))
    }

    private func loadSelectedProvider(_ name: String) {
        providerName = name
        providerSection = ConfigDocument.tablePath("providers", name)
        loadProvider(from: ConfigDocument(original))
        if let alias = modelOptions.first(where: { modelProviders[$0] == name }) {
            modelAlias = alias
            modelSection = ConfigDocument.tablePath("models", alias)
            modelID = ConfigDocument(original).values(in: modelSection!)["model_id"] ?? ""
            modelProvider = name
        } else {
            modelProvider = name
            modelAlias = ""
            modelSection = nil
            modelID = ""
        }
        status = ""
        savedDraft = draft
    }

    func selectModel(_ alias: String) {
        guard alias != modelAlias else { return }
        request(.model(alias))
    }

    private func loadSelectedModel(_ alias: String) {
        modelAlias = alias
        modelSection = ConfigDocument.tablePath("models", alias)
        let model = ConfigDocument(original).values(in: modelSection!)
        modelProvider = model["provider"] ?? providerName
        modelID = model["model_id"] ?? ""
        if providerOptions.contains(modelProvider) {
            providerName = modelProvider
            providerSection = ConfigDocument.tablePath("providers", providerName)
            loadProvider(from: ConfigDocument(original))
        }
        status = ""
        savedDraft = draft
    }

    func selectPolicy(_ policy: String) {
        guard policyOptions.contains(policy), policy != defaultPolicy else { return }
        defaultPolicy = policy
        policyDefaultModel = ConfigDocument(original).values(in: ConfigDocument.tablePath("blend", "policies", policy))["default_model"] ?? modelOptions.first ?? ""
    }

    func selectAuthMode(_ mode: String) {
        let canBootstrap = mode == "chatgpt" && original.isEmpty && !hasUnsavedChanges
        authMode = mode
        if mode == "chatgpt" {
            setAPIType("open_ai_responses")
            baseURL = "https://api.openai.com/v1"
            apiKey = ""
            clearSavedKey = hasSavedKey
        } else {
            apiKeyEnv = Self.defaultAPIKeyEnvironmentVariable(providerName: providerName)
            clearSavedKey = false
        }
        chatGPTBootstrapDraft = canBootstrap ? draft : nil
    }

    func connectChatGPT() {
        runChatGPTAuth(
            arguments: ["chatgpt-login"],
            successMessage: "Connected to ChatGPT. Reopen the workspace to reconnect Arabica.",
            savingChanges: true)
    }

    func disconnectChatGPT() {
        runChatGPTAuth(arguments: ["chatgpt-logout"], successMessage: "ChatGPT disconnected.")
    }

    func syncModels(provider: String? = nil) {
        guard !isSyncingModels, !isChatGPTAuthRunning else { return }
        guard !hasUnsavedChanges else {
            report("Save or discard configuration changes before synchronizing models.")
            return
        }
        guard let executable = Self.arabicaExecutableURL() else {
            report("Arabica executable was not found. Set ARABICA_EXECUTABLE or rebuild the desktop app.", kind: .error)
            return
        }
        isSyncingModels = true
        var args = ["models", "sync"]
        let targetProvider = provider ?? (providerName.isEmpty ? nil : providerName)
        if let target = targetProvider {
            args.append(contentsOf: ["--provider", target])
        }
        let home = arabicaHomeURL
        Task { [weak self] in
            let result = await Task.detached(priority: .userInitiated) {
                Self.runCliCommand(executable: executable, arguments: args, home: home)
            }.value
            guard let self else { return }
            self.isSyncingModels = false
            if result.status == 0 {
                self.completeConfigurationUpdate(successMessage: "Models synchronized with provider successfully.")
            } else {
                let detail = result.output.trimmingCharacters(in: .whitespacesAndNewlines)
                self.report(detail.isEmpty ? "Model sync failed (exit \(result.status))." : detail, kind: .error)
            }
        }
    }

    func prepareChatGPTAuth(executable: URL?, savingChanges: Bool) -> ChatGPTAuthPreparation? {
        guard !isSaving, !isChatGPTAuthRunning, !isSyncingModels else { return nil }
        if hasUnsavedChanges && !savingChanges {
            report("Save or discard configuration changes before changing the ChatGPT connection.")
            return nil
        }
        guard executable != nil else {
            report("Arabica executable was not found. Set ARABICA_EXECUTABLE or rebuild the desktop app.", kind: .error)
            return nil
        }
        if savingChanges && isChatGPTBootstrapDraft {
            // A fresh login creates its own provider and model catalog. Only the
            // untouched initial ChatGPT draft can bypass model validation.
            do {
                guard try configurationOnDisk() == original else {
                    report("The configuration changed on disk. Reload it before signing in.", kind: .error)
                    return nil
                }
            } catch {
                report("Could not read configuration: \(error.localizedDescription)", kind: .error)
                return nil
            }
        } else {
            // Login also updates the on-disk model catalog, so commit edited drafts first.
            guard !hasUnsavedChanges || save() else { return nil }
        }
        return ChatGPTAuthPreparation(launchDraft: draft)
    }

    private func runChatGPTAuth(arguments: [String], successMessage: String, savingChanges: Bool = false) {
        let executable = Self.arabicaExecutableURL()
        guard let preparation = prepareChatGPTAuth(executable: executable, savingChanges: savingChanges),
              let executable else { return }
        isChatGPTAuthRunning = true
        let home = arabicaHomeURL
        Task { [weak self] in
            let result = await Task.detached(priority: .userInitiated) {
                Self.runCliCommand(executable: executable, arguments: ["auth"] + arguments, home: home)
            }.value
            guard let self else { return }
            self.isChatGPTAuthRunning = false
            if result.status == 0 {
                self.completeConfigurationUpdate(successMessage: successMessage, authPreparation: preparation)
            } else {
                let detail = result.output.trimmingCharacters(in: .whitespacesAndNewlines)
                self.report(detail.isEmpty ? "ChatGPT connection change failed (exit \(result.status))." : detail, kind: .error)
            }
        }
    }

    func completeConfigurationUpdate(successMessage: String, authPreparation: ChatGPTAuthPreparation? = nil) {
        chatGPTConnected = Self.hasChatGPTCredentials(at: arabicaHomeURL)
        let canReload = authPreparation.map { draft == $0.launchDraft } ?? !hasUnsavedChanges
        guard canReload else {
            report("\(successMessage) Your unsaved edits were kept. Reload the configuration to review the changes on disk.", kind: .success)
            return
        }
        reload()
        if modelOptions.contains(policyDefaultModel) { selectModel(policyDefaultModel) }
        report(successMessage, kind: .success)
    }

    private static func arabicaExecutableURL() -> URL? {
        if let override = ProcessInfo.processInfo.environment["ARABICA_EXECUTABLE"],
           FileManager.default.isExecutableFile(atPath: override) {
            return URL(fileURLWithPath: override)
        }
        if let bundled = Bundle.main.resourceURL?.appendingPathComponent("arabica"),
           FileManager.default.isExecutableFile(atPath: bundled.path) {
            return bundled
        }
        return nil
    }

    nonisolated private static func runCliCommand(executable: URL, arguments: [String], home: URL) -> (status: Int32, output: String) {
        let process = Process()
        process.executableURL = executable
        process.arguments = arguments
        var environment = ProcessInfo.processInfo.environment
        environment["ARABICA_HOME"] = home.path
        process.environment = environment
        let output = Pipe()
        process.standardOutput = output
        process.standardError = output
        do {
            try process.run()
            let data = output.fileHandleForReading.readDataToEndOfFile()
            process.waitUntilExit()
            return (process.terminationStatus, String(data: data, encoding: .utf8) ?? "")
        } catch {
            return (1, error.localizedDescription)
        }
    }

    private static func hasChatGPTCredentials(at home: URL) -> Bool {
        let path = home.appendingPathComponent("chatgpt-auth.json")
        guard let data = try? Data(contentsOf: path),
              let credentials = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let accessToken = credentials["access_token"] as? String else { return false }
        return !accessToken.isEmpty
    }

    func addProvider() {
        guard canAddEntries else { return }
        request(.addProvider)
    }

    private func createProvider() {
        let name = nextName(prefix: "provider", options: providerOptions)
        providerOptions.append(name)
        providerName = name
        providerSection = ConfigDocument.tablePath("providers", name)
        modelProvider = name
        let alias = nextName(prefix: "model", options: modelOptions)
        modelOptions.append(alias)
        modelProviders[alias] = name
        modelAlias = alias
        modelSection = nil
        modelID = ""
        authMode = "api_key"
        apiType = "open_ai_responses"
        baseURL = "https://api.openai.com/v1"
        apiKeyEnv = Self.defaultAPIKeyEnvironmentVariable(providerName: name)
        apiKey = ""
        originalKey = nil
        hasSavedKey = false
        clearSavedKey = false
        thinking = ""
        report("Enter a model ID, then save to add provider \(name) and model \(alias).")
    }

    func addModel() {
        guard canAddEntries else { return }
        request(.addModel)
    }

    private func createModel() {
        let alias = nextName(prefix: "model", options: modelOptions)
        modelOptions.append(alias)
        modelProviders[alias] = providerName
        modelAlias = alias
        modelSection = nil
        modelProvider = providerName
        modelID = ""
        report("Enter a model ID, then save to add model \(alias).")
    }

    func requestReload() { request(.reload) }

    private func request(_ action: PendingAction) {
        if hasUnsavedChanges {
            pendingAction = action
        } else {
            perform(action)
        }
    }

    func saveAndContinue(_ action: PendingAction? = nil) {
        guard let action = action ?? pendingAction else { return }
        pendingAction = nil
        guard save() else { return }
        perform(action)
    }

    func discardAndContinue(_ action: PendingAction? = nil) {
        guard let action = action ?? pendingAction else { return }
        pendingAction = nil
        reload()
        if case .reload = action { return }
        perform(action)
    }

    private func perform(_ action: PendingAction) {
        switch action {
        case .provider(let name): loadSelectedProvider(name)
        case .model(let alias): loadSelectedModel(alias)
        case .addProvider: createProvider()
        case .addModel: createModel()
        case .reload: reload()
        }
    }

    func setAPIType(_ value: String) {
        apiType = value
        if !thinking.isEmpty, !Self.supportedThinkingValues(apiType: value).contains(thinking) {
            thinking = ""
        }
    }

    private func report(_ message: String, kind: StatusKind = .information) {
        status = message
        statusKind = kind
    }

    private func loadProvider(from document: ConfigDocument) {
        let values = document.values(in: providerSection)
        apiType = values["api_type"] ?? "open_ai_responses"
        authMode = values["auth"]?.lowercased() == "chatgpt" ? "chatgpt" : "api_key"
        baseURL = values["base_url"] ?? "https://api.openai.com/v1"
        apiKeyEnv = values["api_key_env"] ?? Self.defaultAPIKeyEnvironmentVariable(providerName: providerName)
        originalKey = values["api_key"]
        hasSavedKey = originalKey != nil
        apiKey = ""
        clearSavedKey = false
        thinking = values["thinking"] ?? values["reasoning_effort"] ?? ""
        if apiType == "open_ai_responses", ["off", "default"].contains(thinking) {
            thinking = ""
        }
    }

    private static func defaultAPIKeyEnvironmentVariable(providerName: String) -> String {
        "ARABICA_PROVIDER_\(providerName.uppercased().replacingOccurrences(of: "[^A-Z0-9]", with: "_", options: .regularExpression))_API_KEY"
    }

    private func nextName(prefix: String, options: [String]) -> String {
        var suffix = 2
        while options.contains("\(prefix)\(suffix)") { suffix += 1 }
        return "\(prefix)\(suffix)"
    }

    func openConfigFile() {
        do {
            try FileManager.default.createDirectory(at: configURL.deletingLastPathComponent(), withIntermediateDirectories: true)
            if !FileManager.default.fileExists(atPath: configURL.path) {
                try Data().write(to: configURL)
            }
            NSWorkspace.shared.open(configURL)
        } catch {
            report("Could not open config file: \(error.localizedDescription)", kind: .error)
        }
    }

    @discardableResult
    func save() -> Bool {
        guard !isSaving else { return false }
        guard !isChatGPTAuthRunning, !isSyncingModels else {
            report("Wait for the current connection or model update to finish before saving.")
            return false
        }
        let cleanProvider = providerName.trimmingCharacters(in: .whitespacesAndNewlines)
        let cleanAlias = modelAlias.trimmingCharacters(in: .whitespacesAndNewlines)
        let cleanModelProvider = modelProvider.trimmingCharacters(in: .whitespacesAndNewlines)
        let cleanModel = modelID.trimmingCharacters(in: .whitespacesAndNewlines)
        let cleanAPIKeyEnv = apiKeyEnv.trimmingCharacters(in: .whitespacesAndNewlines)
        let cleanPolicyDefaultModel = policyDefaultModel.trimmingCharacters(in: .whitespacesAndNewlines)
        if let thinkingError = Self.thinkingValidationError(apiType: apiType, thinking: thinking) {
            report(thinkingError, kind: .error)
            return false
        }
        guard !cleanProvider.isEmpty, !cleanAlias.isEmpty, !cleanModelProvider.isEmpty, !cleanModel.isEmpty,
              !apiType.isEmpty, let url = URL(string: baseURL.trimmingCharacters(in: .whitespacesAndNewlines)),
              ["http", "https"].contains(url.scheme?.lowercased() ?? ""), !(url.host?.isEmpty ?? true) else {
            report("Enter a model ID and a valid HTTP(S) base URL, including its hostname.", kind: .error)
            return false
        }
        if authMode == "chatgpt", (apiType != "open_ai_responses" || baseURL.trimmingCharacters(in: CharacterSet(charactersIn: "/")) != "https://api.openai.com/v1") {
            report("ChatGPT login requires the OpenAI Responses API at https://api.openai.com/v1.", kind: .error)
            return false
        }
        let defaultModel = cleanPolicyDefaultModel.isEmpty ? cleanAlias : cleanPolicyDefaultModel
        guard modelOptions.contains(defaultModel) || defaultModel == cleanAlias else {
            report("Choose a default model that exists in the model catalog.", kind: .error)
            return false
        }
        guard authMode == "chatgpt" || !cleanAPIKeyEnv.isEmpty || !apiKey.isEmpty || (originalKey != nil && !clearSavedKey) else {
            report("Enter an API key or the name of an environment variable that contains it.", kind: .error)
            return false
        }

        isSaving = true
        defer { isSaving = false }
        do {
            let diskContents = try configurationOnDisk()
            guard diskContents == original else {
                report("The configuration changed on disk. Reload it before saving to avoid overwriting those changes.", kind: .error)
                return false
            }
            var providerValues = [
                "api_type": ConfigDocument.quote(apiType),
                "base_url": ConfigDocument.quote(baseURL.trimmingCharacters(in: .whitespacesAndNewlines)),
            ]
            if authMode == "chatgpt" {
                providerValues["auth"] = ConfigDocument.quote("chatgpt")
            } else if !cleanAPIKeyEnv.isEmpty {
                providerValues["api_key_env"] = ConfigDocument.quote(cleanAPIKeyEnv)
            }
            let result = try ConfigDocument(original).updating(
                oldProviderSection: providerSection,
                newProviderSection: ConfigDocument.tablePath("providers", cleanProvider),
                oldModelSection: modelSection,
                newModelSection: ConfigDocument.tablePath("models", cleanAlias),
                providerValues: providerValues,
                modelValues: ["provider": ConfigDocument.quote(cleanProvider), "model_id": ConfigDocument.quote(cleanModel)],
                thinking: thinking.trimmingCharacters(in: .whitespacesAndNewlines),
                defaultPolicy: defaultPolicy,
                defaultModelAlias: defaultModel,
                newAPIKey: authMode == "chatgpt" || apiKey.isEmpty ? nil : apiKey,
                removeAPIKey: clearSavedKey || authMode == "chatgpt",
                removeAuth: authMode != "chatgpt",
                removeAPIKeyEnv: authMode == "chatgpt" || cleanAPIKeyEnv.isEmpty
            )
            try writeSecurely(result, to: configURL)
            original = result
            reload()
            report("Configuration saved. Reopen the workspace to apply connection and routing changes.", kind: .success)
            return true
        } catch {
            report("Could not save configuration: \(error.localizedDescription)", kind: .error)
            return false
        }
    }

    static func thinkingValidationError(apiType: String, thinking: String) -> String? {
        let value = thinking.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !value.isEmpty else { return nil }

        let supported = supportedThinkingValues(apiType: apiType)
        guard supported.contains(value) else {
            return "Thinking value \(value.debugDescription) is not supported for \(apiType). Choose \(supported.joined(separator: " or "))."
        }
        return nil
    }

    static func supportedThinkingValues(apiType: String) -> [String] {
        switch apiType {
        case "open_ai_chat_completions":
            return ["off", "on"]
        case "open_ai_responses":
            return ["low", "medium", "high", "xhigh", "max"]
        default:
            return ["off"]
        }
    }

    private func writeSecurely(_ text: String, to url: URL) throws {
        try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
        if FileManager.default.fileExists(atPath: url.path) {
            let values = try url.resourceValues(forKeys: [.isSymbolicLinkKey, .isRegularFileKey])
            guard values.isSymbolicLink != true, values.isRegularFile == true else {
                throw CocoaError(.fileWriteInvalidFileName)
            }
        }
        let temporary = url.deletingLastPathComponent().appendingPathComponent(".config-\(UUID().uuidString).tmp")
        let descriptor = Darwin.open(temporary.path, O_WRONLY | O_CREAT | O_EXCL, S_IRUSR | S_IWUSR)
        guard descriptor >= 0 else { throw POSIXError(POSIXErrorCode(rawValue: errno) ?? .EIO) }
        do {
            let handle = FileHandle(fileDescriptor: descriptor, closeOnDealloc: true)
            try handle.write(contentsOf: Data(text.utf8))
            try handle.synchronize()
            try handle.close()
            let result = temporary.path.withCString { source in
                url.path.withCString { destination in Darwin.rename(source, destination) }
            }
            guard result == 0 else { throw POSIXError(POSIXErrorCode(rawValue: errno) ?? .EIO) }
        } catch {
            try? FileManager.default.removeItem(at: temporary)
            throw error
        }
    }

    private func configurationOnDisk() throws -> String {
        do {
            return try String(contentsOf: configURL, encoding: .utf8)
        } catch CocoaError.fileReadNoSuchFile {
            return ""
        }
    }
}

struct ConfigDocument {
    private var lines: [String]
    var sectionNames: [String] { lines.compactMap(Self.sectionName) }

    init(_ text: String) { lines = text.components(separatedBy: .newlines) }

    static func tablePath(_ keys: String...) -> String {
        keys.map { isBareKey($0) ? $0 : quote($0) }.joined(separator: ".")
    }

    func childSectionNames(in section: String) -> [String] {
        guard let parent = Self.keyPath(section) else { return [] }
        return sectionNames.compactMap { name in
            guard let path = Self.keyPath(name), path.count == parent.count + 1,
                  Array(path.dropLast()) == parent else { return nil }
            return path.last
        }
    }

    func values(in section: String) -> [String: String] {
        guard let range = range(of: section) else { return [:] }
        var result: [String: String] = [:]
        for line in lines[range] {
            guard let (key, value) = Self.keyValue(line), let decoded = Self.decode(value) else { continue }
            result[key] = decoded
        }
        return result
    }

    func updating(
        oldProviderSection: String,
        newProviderSection: String,
        oldModelSection: String?,
        newModelSection: String,
        providerValues: [String: String],
        modelValues: [String: String],
        thinking: String,
        defaultPolicy: String,
        defaultModelAlias: String,
        newAPIKey: String?,
        removeAPIKey: Bool,
        removeAuth: Bool = false,
        removeAPIKeyEnv: Bool = false
    ) throws -> String {
        var result = self
        if Self.keyPath(oldProviderSection) != Self.keyPath(newProviderSection), result.range(of: newProviderSection) != nil {
            throw EditorError.multipleManagedSections
        }
        if let oldModelSection, Self.keyPath(oldModelSection) != Self.keyPath(newModelSection),
           result.range(of: newModelSection) != nil { throw EditorError.multipleManagedSections }

        if Self.keyPath(oldProviderSection) == ["provider"] {
            result.renameSection("provider", to: newProviderSection)
            result.removeKey("model", in: newProviderSection)
        } else if result.range(of: newProviderSection) == nil {
            result.appendSection(newProviderSection)
        }
        for (key, value) in providerValues { result.set(key, value: value, in: newProviderSection) }
        if removeAuth { result.removeKey("auth", in: newProviderSection) }
        if removeAPIKeyEnv { result.removeKey("api_key_env", in: newProviderSection) }
        if !thinking.isEmpty {
            result.set("thinking", value: Self.quote(thinking), in: newProviderSection)
        }
        result.removeKey("reasoning_effort", in: newProviderSection)
        if thinking.isEmpty { result.removeKey("thinking", in: newProviderSection) }
        if let newAPIKey {
            result.set("api_key", value: Self.quote(newAPIKey), in: newProviderSection)
        } else if removeAPIKey {
            result.removeKey("api_key", in: newProviderSection)
        }
        if let oldModelSection {
            if Self.keyPath(oldModelSection) != Self.keyPath(newModelSection) { result.renameSection(oldModelSection, to: newModelSection) }
        } else if result.range(of: newModelSection) == nil {
            result.appendSection(newModelSection)
        }
        for (key, value) in modelValues { result.set(key, value: value, in: newModelSection) }
        if result.range(of: "blend") == nil { result.appendSection("blend") }
        let namedPolicies = result.childSectionNames(in: "blend.policies")
        if !namedPolicies.isEmpty {
            let policySection = Self.tablePath("blend", "policies", defaultPolicy)
            guard !defaultPolicy.isEmpty, result.range(of: policySection) != nil else {
                throw EditorError.multipleManagedSections
            }
            result.set("default_policy", value: Self.quote(defaultPolicy), in: "blend")
            result.set("default_model", value: Self.quote(defaultModelAlias), in: policySection)
        } else {
            result.set("default_model", value: Self.quote(defaultModelAlias), in: "blend")
        }
        return result.lines.joined(separator: "\n").trimmingCharacters(in: .newlines) + "\n"
    }

    func settingDefaultPolicy(_ policyID: String) throws -> String {
        guard range(of: Self.tablePath("blend", "policies", policyID)) != nil else {
            throw EditorError.multipleManagedSections
        }
        var result = self
        if result.range(of: "blend") == nil { result.appendSection("blend") }
        result.set("default_policy", value: Self.quote(policyID), in: "blend")
        return result.lines.joined(separator: "\n").trimmingCharacters(in: .newlines) + "\n"
    }

    private mutating func renameSection(_ name: String, to replacement: String) {
        guard let path = Self.keyPath(name),
              let index = lines.firstIndex(where: { Self.tableHeader($0)?.keys == path }) else { return }
        let content = Self.withoutComment(lines[index])
        let comment = String(lines[index].dropFirst(content.count))
        lines[index] = "[\(replacement)]" + (comment.isEmpty ? "" : " \(comment)")
    }

    private mutating func appendSection(_ name: String) {
        if !lines.isEmpty && !lines.last!.isEmpty { lines.append("") }
        lines.append("[\(name)]")
    }

    private mutating func set(_ key: String, value: String, in section: String) {
        if let range = range(of: section) {
            for index in range where Self.keyValue(lines[index])?.key == key {
                lines[index] = "\(key) = \(value)"
                return
            }
            lines.insert("\(key) = \(value)", at: range.upperBound)
        } else {
            appendSection(section)
            lines.append("\(key) = \(value)")
        }
    }

    private mutating func removeKey(_ key: String, in section: String) {
        guard let range = range(of: section) else { return }
        for index in range.reversed() where Self.keyValue(lines[index])?.key == key {
            lines.remove(at: index)
        }
    }

    private func range(of section: String) -> Range<Int>? {
        guard let path = Self.keyPath(section),
              let start = lines.firstIndex(where: {
                  guard let header = Self.tableHeader($0) else { return false }
                  return !header.isArray && header.keys == path
              }) else { return nil }
        let end = lines[(start + 1)...].firstIndex(where: { Self.isTableHeader($0) }) ?? lines.endIndex
        return (start + 1)..<end
    }

    private static func isTableHeader(_ line: String) -> Bool {
        tableHeader(line) != nil
    }

    private static func sectionName(_ line: String) -> String? {
        guard let header = tableHeader(line), !header.isArray else { return nil }
        return header.keys.map { isBareKey($0) ? $0 : quote($0) }.joined(separator: ".")
    }

    private static func tableHeader(_ line: String) -> (keys: [String], isArray: Bool)? {
        let content = withoutComment(line).trimmingCharacters(in: .whitespaces)
        let isArray = content.hasPrefix("[[")
        let bracketCount = isArray ? 2 : 1
        guard content.hasPrefix("["), content.hasSuffix(isArray ? "]]" : "]"),
              content.count > bracketCount * 2,
              let keys = keyPath(String(content.dropFirst(bracketCount).dropLast(bracketCount))) else { return nil }
        return (keys, isArray)
    }

    private static func isBareKey(_ value: String) -> Bool {
        !value.isEmpty && value.unicodeScalars.allSatisfy {
            (0x30...0x39).contains($0.value) || (0x41...0x5A).contains($0.value) ||
                (0x61...0x7A).contains($0.value) || $0 == "_" || $0 == "-"
        }
    }

    private static func keyPath(_ source: String) -> [String]? {
        let characters = Array(source)
        var index = 0
        var keys: [String] = []
        while index < characters.count {
            while index < characters.count, characters[index].isWhitespace { index += 1 }
            guard index < characters.count else { return nil }
            let start = index
            let first = characters[index]
            if first == "\"" || first == "'" {
                index += 1
                var escaped = false
                while index < characters.count {
                    let character = characters[index]
                    index += 1
                    if escaped { escaped = false; continue }
                    if first == "\"", character == "\\" { escaped = true; continue }
                    if character == first { break }
                }
                guard let decoded = decode(String(characters[start..<index])) else { return nil }
                keys.append(decoded)
            } else {
                while index < characters.count, characters[index] != ".", !characters[index].isWhitespace { index += 1 }
                let key = String(characters[start..<index])
                guard isBareKey(key) else { return nil }
                keys.append(key)
            }
            while index < characters.count, characters[index].isWhitespace { index += 1 }
            if index == characters.count { return keys }
            guard characters[index] == "." else { return nil }
            index += 1
        }
        return nil
    }

    private static func keyValue(_ line: String) -> (key: String, value: String)? {
        let content = withoutComment(line)
        var quote: Character?
        var escaped = false
        for index in content.indices {
            let character = content[index]
            if escaped { escaped = false; continue }
            if character == "\\", quote == "\"" { escaped = true; continue }
            if character == "\"" || character == "'" {
                if quote == character { quote = nil } else if quote == nil { quote = character }
            }
            if character == "=", quote == nil {
                guard let keys = keyPath(String(content[..<index])), keys.count == 1 else { return nil }
                return (keys[0], content[content.index(after: index)...].trimmingCharacters(in: .whitespaces))
            }
        }
        return nil
    }

    private static func withoutComment(_ line: String) -> String {
        var quote: Character?
        var escaped = false
        for (index, character) in line.enumerated() {
            if escaped { escaped = false; continue }
            if character == "\\", quote == "\"" { escaped = true; continue }
            if character == "\"" || character == "'" {
                if quote == character { quote = nil } else if quote == nil { quote = character }
            }
            if character == "#", quote == nil { return String(line.prefix(index)) }
        }
        return line
    }

    private static func decode(_ value: String) -> String? {
        guard value.count >= 2 else { return nil }
        let body = String(value.dropFirst().dropLast())
        guard !body.contains("\n"), !body.contains("\r") else { return nil }
        if value.first == "'", value.last == "'", !body.contains("'") { return body }
        guard value.first == "\"", value.last == "\"" else { return nil }
        var decoded = ""
        var iterator = body.unicodeScalars.makeIterator()
        while let scalar = iterator.next() {
            guard scalar.value >= 0x20 || scalar.value == 0x09 else { return nil }
            guard scalar.value != 0x7F, scalar != "\"" else { return nil }
            if scalar != "\\" {
                decoded.unicodeScalars.append(scalar)
                continue
            }
            guard let escaped = iterator.next() else { return nil }
            switch escaped {
            case "b": decoded += "\u{08}"
            case "t": decoded += "\t"
            case "n": decoded += "\n"
            case "f": decoded += "\u{0C}"
            case "r": decoded += "\r"
            case "\"": decoded += "\""
            case "\\": decoded += "\\"
            case "u", "U":
                var digits = ""
                for _ in 0..<(escaped == "u" ? 4 : 8) {
                    guard let digit = iterator.next(), digit.isASCII,
                          Character(String(digit)).isHexDigit else { return nil }
                    digits.unicodeScalars.append(digit)
                }
                guard let number = UInt32(digits, radix: 16), let unicode = UnicodeScalar(number) else { return nil }
                decoded.unicodeScalars.append(unicode)
            default: return nil
            }
        }
        return decoded
    }

    static func quote(_ value: String) -> String {
        var encoded = "\""
        for scalar in value.unicodeScalars {
            switch scalar.value {
            case 0x22: encoded += "\\\""
            case 0x5C: encoded += "\\\\"
            case 0x08: encoded += "\\b"
            case 0x09: encoded += "\\t"
            case 0x0A: encoded += "\\n"
            case 0x0C: encoded += "\\f"
            case 0x0D: encoded += "\\r"
            case 0x00...0x1F, 0x7F:
                encoded += String(format: "\\u%04X", scalar.value)
            default: encoded.unicodeScalars.append(scalar)
            }
        }
        encoded += "\""
        return encoded
    }

    func mcpServers() -> [MCPServerItem] {
        var servers: [MCPServerItem] = []
        var currentLines: [String] = []
        var inMcp = false

        for line in lines {
            let header = Self.tableHeader(line)
            if let header, header.isArray, header.keys == ["mcp"] {
                if inMcp {
                    if let item = Self.parseMcpItem(from: currentLines) {
                        servers.append(item)
                    }
                    currentLines.removeAll()
                }
                inMcp = true
                continue
            } else if let header, inMcp {
                if header.isArray || header.keys.first != "mcp" || header.keys.count == 1 {
                    if let item = Self.parseMcpItem(from: currentLines) {
                        servers.append(item)
                    }
                    currentLines.removeAll()
                    inMcp = false
                }
            }
            if inMcp {
                currentLines.append(line)
            }
        }
        if inMcp, let item = Self.parseMcpItem(from: currentLines) {
            servers.append(item)
        }
        return servers
    }

    private static func parseMcpItem(from lines: [String]) -> MCPServerItem? {
        var name = ""
        var command: String?
        var args: [String] = []
        var url: String?
        var envKeys: [String] = []
        var inEnv = false

        for line in lines {
            if let header = tableHeader(line) {
                inEnv = !header.isArray && header.keys == ["mcp", "env"]
                continue
            }
            if inEnv {
                if let (key, _) = keyValue(line) {
                    envKeys.append(key)
                }
            } else {
                guard let (key, rawValue) = keyValue(line) else { continue }
                if key == "name" {
                    name = decode(rawValue) ?? rawValue
                } else if key == "command" {
                    command = decode(rawValue) ?? rawValue
                } else if key == "url" {
                    url = decode(rawValue) ?? rawValue
                } else if key == "args" {
                    if let decoded = decodeStringArray(rawValue) {
                        args = decoded
                    }
                }
            }
        }
        guard !name.isEmpty else { return nil }
        return MCPServerItem(
            name: name,
            command: command,
            args: args,
            url: url,
            envKeys: envKeys
        )
    }

    func skillRoots(relativeTo arabicaHome: URL) -> [SkillRootItem] {
        guard let range = range(of: "skills") else { return [] }
        var roots: [SkillRootItem] = []
        for line in lines[range] {
            guard let (key, value) = Self.keyValue(line), key == "roots" else { continue }
            guard let paths = Self.decodeStringArray(value) else { continue }
            for pathStr in paths {
                let resolvedURL: URL
                if pathStr.hasPrefix("/") {
                    resolvedURL = URL(fileURLWithPath: pathStr, isDirectory: true)
                } else {
                    resolvedURL = arabicaHome.appendingPathComponent(pathStr, isDirectory: true)
                }
                var isDir: ObjCBool = false
                let exists = FileManager.default.fileExists(atPath: resolvedURL.path, isDirectory: &isDir) && isDir.boolValue
                roots.append(SkillRootItem(rawPath: pathStr, resolvedURL: resolvedURL, exists: exists))
            }
        }
        return roots
    }

    func configuredPolicies() -> [ConfiguredPolicyItem] {
        childSectionNames(in: "blend.policies").map { id in
            let vals = values(in: Self.tablePath("blend", "policies", id))
            let version = vals["version"].flatMap { UInt64($0) }
            let defaultModel = vals["default_model"] ?? ""
            let afterSuccess = vals["after_tool_success"]
            let afterError = vals["after_tool_error"]
            let recoveryModel = vals["recovery_model"]
            let planningModel = vals["planning_model"]
            let stallThreshold = vals["recovery_after_no_progress_steps"].flatMap { Int($0) }
            let dwellSteps = vals["minimum_model_dwell_steps"].flatMap { Int($0) }
            return ConfiguredPolicyItem(
                id: id,
                version: version,
                defaultModel: defaultModel,
                afterToolSuccess: afterSuccess,
                afterToolError: afterError,
                recoveryModel: recoveryModel,
                planningModel: planningModel,
                stallThreshold: stallThreshold,
                dwellSteps: dwellSteps
            )
        }.sorted(by: { $0.id < $1.id })
    }

    private static func decodeStringArray(_ raw: String) -> [String]? {
        let trimmed = raw.trimmingCharacters(in: .whitespaces)
        guard trimmed.hasPrefix("["), trimmed.hasSuffix("]") else { return nil }
        guard let data = trimmed.data(using: .utf8),
              let json = try? JSONSerialization.jsonObject(with: data) as? [String] else {
            return nil
        }
        return json
    }
}

struct ConfiguredPolicyItem: Identifiable, Equatable {
    let id: String
    let version: UInt64?
    let defaultModel: String
    let afterToolSuccess: String?
    let afterToolError: String?
    let recoveryModel: String?
    let planningModel: String?
    let stallThreshold: Int?
    let dwellSteps: Int?
}

private enum EditorError: LocalizedError {
    case multipleManagedSections
    var errorDescription: String? { "The config contains multiple provider/model sections. Open it in a text editor to avoid losing custom routing." }
}

struct MCPServerItem: Identifiable, Equatable {
    var id: String { name }
    let name: String
    let command: String?
    let args: [String]
    let url: String?
    let envKeys: [String]

    var transportType: String {
        if command != nil { return "stdio" }
        if url != nil { return "http" }
        return "unknown"
    }

    var summary: String {
        if let command {
            return ([command] + args).joined(separator: " ")
        }
        if let url {
            return url
        }
        return "unconfigured"
    }
}

struct SkillRootItem: Identifiable, Equatable {
    var id: String { resolvedURL.path }
    let rawPath: String
    let resolvedURL: URL
    let exists: Bool
}

struct DiscoveredSkillItem: Identifiable, Equatable {
    var id: String { directoryURL.path }
    let name: String
    let description: String
    let directoryURL: URL
    let rootPath: String
}

private func scanDiscoveredSkills(in roots: [SkillRootItem]) -> [DiscoveredSkillItem] {
    var skills: [DiscoveredSkillItem] = []
    let fm = FileManager.default

    for root in roots where root.exists {
        let rootURL = root.resolvedURL
        let directSkillMD = rootURL.appendingPathComponent("SKILL.md")
        if fm.fileExists(atPath: directSkillMD.path) {
            let (name, desc) = parseSkillMetadata(from: directSkillMD, fallbackName: rootURL.lastPathComponent)
            skills.append(DiscoveredSkillItem(name: name, description: desc, directoryURL: rootURL, rootPath: root.rawPath))
            continue
        }

        guard let contents = try? fm.contentsOfDirectory(at: rootURL, includingPropertiesForKeys: [.isDirectoryKey], options: [.skipsHiddenFiles]) else {
            continue
        }
        for item in contents {
            var isDir: ObjCBool = false
            if fm.fileExists(atPath: item.path, isDirectory: &isDir), isDir.boolValue {
                let skillMD = item.appendingPathComponent("SKILL.md")
                if fm.fileExists(atPath: skillMD.path) {
                    let (name, desc) = parseSkillMetadata(from: skillMD, fallbackName: item.lastPathComponent)
                    skills.append(DiscoveredSkillItem(name: name, description: desc, directoryURL: item, rootPath: root.rawPath))
                }
            }
        }
    }
    return skills.sorted(by: { $0.name < $1.name })
}

private func parseSkillMetadata(from url: URL, fallbackName: String) -> (name: String, description: String) {
    guard let content = try? String(contentsOf: url, encoding: .utf8) else {
        return (fallbackName, "")
    }
    var name = fallbackName
    var description = ""
    let lines = content.components(separatedBy: .newlines)
    var inFrontmatter = false
    var frontmatterPassed = false

    for line in lines {
        let trimmed = line.trimmingCharacters(in: .whitespaces)
        if trimmed == "---" {
            if inFrontmatter {
                frontmatterPassed = true
                break
            } else if !frontmatterPassed {
                inFrontmatter = true
                continue
            }
        }
        if inFrontmatter {
            if trimmed.hasPrefix("name:") {
                let val = trimmed.dropFirst("name:".count).trimmingCharacters(in: .whitespaces)
                if !val.isEmpty { name = val }
            } else if trimmed.hasPrefix("description:") {
                let val = trimmed.dropFirst("description:".count).trimmingCharacters(in: .whitespaces)
                if !val.isEmpty { description = val }
            }
        }
    }
    return (name, description)
}

