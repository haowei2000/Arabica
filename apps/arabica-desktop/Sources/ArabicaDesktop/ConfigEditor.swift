import AppKit
import Combine
import Darwin
import Foundation

@MainActor
final class ConfigEditor: ObservableObject {
    @Published var providerOptions: [String] = []
    @Published var providerName = "primary"
    @Published var apiType = "open_ai_responses"
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
    @Published var thinking = ""
    @Published var status = ""
    @Published var canAddEntries = false
    @Published var isSaving = false

    var modelsForSelectedProvider: [String] {
        modelOptions.filter { modelProviders[$0] == providerName }
    }

    private var original = ""
    private var originalKey: String?
    private var providerSection = "providers.primary"
    private var modelSection: String?
    private var configURL: URL {
        let home = ProcessInfo.processInfo.environment["ARABICA_HOME"]
            .map { URL(fileURLWithPath: $0, isDirectory: true) }
            ?? FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent(".arabica", isDirectory: true)
        return home.appendingPathComponent("config.toml")
    }

    init() { reload() }

    func reload() {
        status = ""
        originalKey = nil
        hasSavedKey = false
        clearSavedKey = false
        apiKey = ""
        do {
            original = try String(contentsOf: configURL, encoding: .utf8)
        } catch CocoaError.fileReadNoSuchFile {
            original = ""
        } catch {
            original = ""
            status = "Could not read \(configURL.path): \(error.localizedDescription)"
            return
        }

        let document = ConfigDocument(original)
        let blend = document.values(in: "blend")
        let providerSections = document.sectionNames.filter { $0.hasPrefix("providers.") }.sorted()
        providerOptions = providerSections.map { String($0.dropFirst("providers.".count)) }
        let modelSections = document.sectionNames.filter { $0.hasPrefix("models.") }.sorted()
        modelOptions = modelSections.map { String($0.dropFirst("models.".count)) }
        modelProviders = modelSections.reduce(into: [:]) { providers, section in
            guard let provider = document.values(in: section)["provider"] else { return }
            providers[String(section.dropFirst("models.".count))] = provider
        }
        policyOptions = document.sectionNames
            .filter { $0.hasPrefix("blend.policies.") }
            .map { String($0.dropFirst("blend.policies.".count)) }
            .sorted()
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
            providerSection = "providers.\(providerName)"
        }
        let initialPolicy = policyOptions.contains(defaultPolicy) ? defaultPolicy : policyOptions.first
        if let initialPolicy {
            policyDefaultModel = document.values(in: "blend.policies.\(initialPolicy)")["default_model"] ?? ""
        } else {
            policyDefaultModel = blend["default_model"] ?? ""
        }
        if modelOptions.isEmpty { modelOptions = ["default"] }
        if policyDefaultModel.isEmpty { policyDefaultModel = modelOptions.first ?? "default" }
        if !modelOptions.contains(modelAlias) {
            modelAlias = modelOptions.contains(policyDefaultModel) ? policyDefaultModel : modelOptions.first ?? "default"
        }
        modelSection = modelOptions.contains(modelAlias) ? "models.\(modelAlias)" : nil
        let model = modelSection.map { document.values(in: $0) } ?? [:]
        modelProvider = model["provider"] ?? providerName
        if providerOptions.contains(modelProvider) {
            providerName = modelProvider
            providerSection = "providers.\(providerName)"
        }
        loadProvider(from: document)
        modelID = model["model_id"] ?? (providerSection == "provider" ? document.values(in: providerSection)["model"] : nil) ?? ""
        canAddEntries = !providerSections.isEmpty && !modelSections.isEmpty
    }

    func selectProvider(_ name: String) {
        guard name != providerName else { return }
        guard saveBeforeSwitch() else { return }
        providerName = name
        providerSection = "providers.\(name)"
        loadProvider(from: ConfigDocument(original))
        if let alias = modelOptions.first(where: { modelProviders[$0] == name }) {
            modelAlias = alias
            modelSection = "models.\(alias)"
            modelID = ConfigDocument(original).values(in: modelSection!)["model_id"] ?? ""
        } else {
            modelProvider = name
            modelAlias = ""
            modelSection = nil
            modelID = ""
        }
        status = ""
    }

    func selectModel(_ alias: String) {
        guard alias != modelAlias else { return }
        guard saveBeforeSwitch() else { return }
        modelAlias = alias
        modelSection = "models.\(alias)"
        let model = ConfigDocument(original).values(in: modelSection!)
        modelProvider = model["provider"] ?? providerName
        modelID = model["model_id"] ?? ""
        if providerOptions.contains(modelProvider) {
            providerName = modelProvider
            providerSection = "providers.\(providerName)"
            loadProvider(from: ConfigDocument(original))
        }
        status = ""
    }

    func selectPolicy(_ policy: String) {
        guard policy != defaultPolicy else { return }
        defaultPolicy = policy
        policyDefaultModel = ConfigDocument(original).values(in: "blend.policies.\(policy)")["default_model"] ?? modelOptions.first ?? ""
    }

    func addProvider() {
        guard canAddEntries, saveBeforeSwitch() else {
            if !canAddEntries { status = "Save one provider and model before adding another entry." }
            return
        }
        let name = nextName(prefix: "provider", options: providerOptions)
        providerOptions.append(name)
        providerName = name
        providerSection = "providers.\(name)"
        modelProvider = name
        let alias = nextName(prefix: "model", options: modelOptions)
        modelOptions.append(alias)
        modelProviders[alias] = name
        modelAlias = alias
        modelSection = nil
        modelID = ""
        apiType = "open_ai_responses"
        baseURL = "https://api.openai.com/v1"
        apiKeyEnv = Self.defaultAPIKeyEnvironmentVariable(providerName: name)
        apiKey = ""
        originalKey = nil
        hasSavedKey = false
        clearSavedKey = false
        thinking = ""
        status = "New [providers.\(name)] and [models.\(alias)] will be written when you save."
    }

    func addModel() {
        guard canAddEntries, (modelAlias.isEmpty || saveBeforeSwitch()) else {
            if !canAddEntries { status = "Save one provider and model before adding another entry." }
            return
        }
        let alias = nextName(prefix: "model", options: modelOptions)
        modelOptions.append(alias)
        modelProviders[alias] = providerName
        modelAlias = alias
        modelSection = nil
        modelProvider = providerName
        modelID = ""
        status = "New [models.\(alias)] will be written when you save."
    }

    private func saveBeforeSwitch() -> Bool {
        save()
        return status.hasPrefix("Saved to ")
    }

    private func loadProvider(from document: ConfigDocument) {
        let values = document.values(in: providerSection)
        apiType = values["api_type"] ?? "open_ai_responses"
        baseURL = values["base_url"] ?? "https://api.openai.com/v1"
        apiKeyEnv = values["api_key_env"] ?? Self.defaultAPIKeyEnvironmentVariable(providerName: providerName)
        originalKey = values["api_key"]
        hasSavedKey = originalKey != nil
        apiKey = ""
        clearSavedKey = false
        thinking = values["thinking"] ?? values["reasoning_effort"] ?? ""
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
            status = "Could not open config file: \(error.localizedDescription)"
        }
    }

    func save() {
        guard !isSaving else { return }
        let cleanProvider = providerName.trimmingCharacters(in: .whitespacesAndNewlines)
        let cleanAlias = modelAlias.trimmingCharacters(in: .whitespacesAndNewlines)
        let cleanModelProvider = modelProvider.trimmingCharacters(in: .whitespacesAndNewlines)
        let cleanModel = modelID.trimmingCharacters(in: .whitespacesAndNewlines)
        let cleanPolicyDefaultModel = policyDefaultModel.trimmingCharacters(in: .whitespacesAndNewlines)
        if let thinkingError = Self.thinkingValidationError(apiType: apiType, thinking: thinking) {
            status = thinkingError
            return
        }
        guard !cleanProvider.isEmpty, !cleanAlias.isEmpty, !cleanModelProvider.isEmpty, !cleanModel.isEmpty,
              !apiType.isEmpty, let url = URL(string: baseURL), ["http", "https"].contains(url.scheme?.lowercased() ?? "") else {
            status = "Enter a provider name, model alias, model ID, API type, and a valid HTTP(S) base URL."
            return
        }
        let defaultModel = cleanPolicyDefaultModel.isEmpty ? cleanAlias : cleanPolicyDefaultModel
        guard modelOptions.contains(defaultModel) || defaultModel == cleanAlias else {
            status = "The blend default_model must reference a model alias in [models.<alias>]."
            return
        }
        guard !apiKeyEnv.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty ||
                !(apiKey.isEmpty && originalKey == nil) ||
                !(ProcessInfo.processInfo.environment[apiKeyEnv]?.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty ?? true) else {
            status = "Enter an API key or the name of an environment variable that contains it."
            return
        }

        isSaving = true
        defer { isSaving = false }
        do {
            let result = try ConfigDocument(original).updating(
                oldProviderSection: providerSection,
                newProviderSection: "providers.\(cleanProvider)",
                oldModelSection: modelSection,
                newModelSection: "models.\(cleanAlias)",
                providerValues: [
                    "api_type": ConfigDocument.quote(apiType),
                    "base_url": ConfigDocument.quote(baseURL.trimmingCharacters(in: .whitespacesAndNewlines)),
                    "api_key_env": ConfigDocument.quote(apiKeyEnv.trimmingCharacters(in: .whitespacesAndNewlines)),
                ],
                modelValues: ["provider": ConfigDocument.quote(cleanProvider), "model_id": ConfigDocument.quote(cleanModel)],
                thinking: thinking.trimmingCharacters(in: .whitespacesAndNewlines),
                defaultPolicy: defaultPolicy,
                defaultModelAlias: defaultModel,
                newAPIKey: apiKey.isEmpty ? nil : apiKey,
                removeAPIKey: clearSavedKey
            )
            try writeSecurely(result, to: configURL)
            original = result
            reload()
            status = "Saved to \(configURL.path). Reopen the workspace to reconnect Arabica."
        } catch {
            status = "Could not save config: \(error.localizedDescription)"
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
            return ["off", "low", "medium", "high"]
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
}

struct ConfigDocument {
    private var lines: [String]
    var sectionNames: [String] { lines.compactMap(Self.sectionName) }

    init(_ text: String) { lines = text.components(separatedBy: .newlines) }

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
        removeAPIKey: Bool
    ) throws -> String {
        var result = self
        if oldProviderSection != newProviderSection, result.range(of: newProviderSection) != nil {
            throw EditorError.multipleManagedSections
        }
        if let oldModelSection, oldModelSection != newModelSection,
           result.range(of: newModelSection) != nil { throw EditorError.multipleManagedSections }

        if oldProviderSection == "provider" {
            result.renameSection("provider", to: newProviderSection)
            result.removeKey("model", in: newProviderSection)
        } else if result.range(of: newProviderSection) == nil {
            result.appendSection(newProviderSection)
        }
        for (key, value) in providerValues { result.set(key, value: value, in: newProviderSection) }
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
            if oldModelSection != newModelSection { result.renameSection(oldModelSection, to: newModelSection) }
        } else if result.range(of: newModelSection) == nil {
            result.appendSection(newModelSection)
        }
        for (key, value) in modelValues { result.set(key, value: value, in: newModelSection) }
        if result.range(of: "blend") == nil { result.appendSection("blend") }
        let namedPolicies = result.sectionNames.filter { $0.hasPrefix("blend.policies.") }
        if !namedPolicies.isEmpty {
            let policySection = "blend.policies.\(defaultPolicy)"
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
        guard range(of: "blend.policies.\(policyID)") != nil else {
            throw EditorError.multipleManagedSections
        }
        var result = self
        if result.range(of: "blend") == nil { result.appendSection("blend") }
        result.set("default_policy", value: Self.quote(policyID), in: "blend")
        return result.lines.joined(separator: "\n").trimmingCharacters(in: .newlines) + "\n"
    }

    private mutating func renameSection(_ name: String, to replacement: String) {
        guard let index = lines.firstIndex(where: { Self.sectionName($0) == name }) else { return }
        lines[index] = "[\(replacement)]"
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
        guard let start = lines.firstIndex(where: { Self.sectionName($0) == section }) else { return nil }
        let end = lines[(start + 1)...].firstIndex(where: { Self.isTableHeader($0) }) ?? lines.endIndex
        return (start + 1)..<end
    }

    private static func isTableHeader(_ line: String) -> Bool {
        let value = line.trimmingCharacters(in: .whitespaces)
        return value.hasPrefix("[") && value.hasSuffix("]")
    }

    private static func sectionName(_ line: String) -> String? {
        let value = line.trimmingCharacters(in: .whitespaces)
        guard value.hasPrefix("["), value.hasSuffix("]"), !value.hasPrefix("[[") else { return nil }
        return String(value.dropFirst().dropLast()).trimmingCharacters(in: .whitespaces)
    }

    private static func keyValue(_ line: String) -> (key: String, value: String)? {
        let content = withoutComment(line)
        guard let equals = content.firstIndex(of: "=") else { return nil }
        let key = content[..<equals].trimmingCharacters(in: .whitespaces)
        guard !key.isEmpty else { return nil }
        return (key, content[content.index(after: equals)...].trimmingCharacters(in: .whitespaces))
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
        if value.first == "'", value.last == "'" { return String(value.dropFirst().dropLast()) }
        guard value.first == "\"", value.last == "\"" else { return nil }
        guard let data = "[\(value)]".data(using: .utf8),
              let decoded = try? JSONSerialization.jsonObject(with: data) as? [String], decoded.count == 1 else { return nil }
        return decoded[0]
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
}

private enum EditorError: LocalizedError {
    case multipleManagedSections
    var errorDescription: String? { "The config contains multiple provider/model sections. Open it in a text editor to avoid losing custom routing." }
}
