import AppKit
import Combine
import Darwin
import Foundation

@MainActor
final class ConfigEditor: ObservableObject {
    @Published var providerName = "primary"
    @Published var apiType = "open_ai_responses"
    @Published var baseURL = "https://api.openai.com/v1"
    @Published var apiKeyEnv = "ARABICA_PROVIDER_PRIMARY_API_KEY"
    @Published var apiKey = ""
    @Published var hasSavedKey = false
    @Published var clearSavedKey = false
    @Published var modelAlias = "default"
    @Published var modelID = ""
    @Published var defaultPolicy = ""
    @Published var policyOptions: [String] = []
    @Published var policyStatus = ""
    @Published var thinking = ""
    @Published var status = ""
    @Published var isUnsupported = false
    @Published var isSaving = false

    private var original = ""
    private var originalKey: String?
    private var providerSection = "provider"
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
        isUnsupported = false
        policyStatus = ""
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
        policyOptions = document.sectionNames
            .filter { $0.hasPrefix("blend.policies.") }
            .map { String($0.dropFirst("blend.policies.".count)) }
        defaultPolicy = blend["default_policy"] ?? policyOptions.first ?? ""
        let providers = document.sectionNames.filter { $0.hasPrefix("providers.") }
        let models = document.sectionNames.filter { $0.hasPrefix("models.") }
        if providers.count > 1 || models.count > 1 || document.sectionNames.contains(where: { $0.hasPrefix("provider.") }) {
            isUnsupported = true
            status = "This form edits one default provider and model. Your file has multiple providers or model aliases; use the advanced config file instead."
            return
        }

        if let current = providers.first {
            providerSection = current
            providerName = String(current.dropFirst("providers.".count))
        } else if document.sectionNames.contains("provider") {
            providerSection = "provider"
            providerName = "primary"
        } else {
            providerSection = "providers.primary"
            providerName = "primary"
        }
        modelSection = models.first
        if !policyOptions.isEmpty, !defaultPolicy.isEmpty {
            let policy = document.values(in: "blend.policies.\(defaultPolicy)")
            let alias = policy["default_model"]
            if let alias, document.sectionNames.contains("models.\(alias)") {
                modelSection = "models.\(alias)"
            }
        } else if let alias = blend["default_model"], document.sectionNames.contains("models.\(alias)") {
            modelSection = "models.\(alias)"
        }
        modelAlias = modelSection.map { String($0.dropFirst("models.".count)) } ?? "default"

        let provider = document.values(in: providerSection)
        apiType = provider["api_type"] ?? apiType
        baseURL = provider["base_url"] ?? baseURL
        apiKeyEnv = provider["api_key_env"] ?? "ARABICA_PROVIDER_\(providerName.uppercased().replacingOccurrences(of: "[^A-Z0-9]", with: "_", options: .regularExpression))_API_KEY"
        originalKey = provider["api_key"]
        hasSavedKey = originalKey != nil
        thinking = provider["thinking"] ?? provider["reasoning_effort"] ?? ""

        if let modelSection {
            let model = document.values(in: modelSection)
            modelID = model["model_id"] ?? ""
            if providerSection.hasPrefix("providers."), let configuredProvider = model["provider"],
               configuredProvider != providerName {
                isUnsupported = true
                status = "The default model points to a different provider. Edit the advanced config file to avoid changing its routing."
            }
        } else {
            modelID = provider["model"] ?? ""
        }
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
        guard !isSaving, !isUnsupported else { return }
        let cleanProvider = providerName.trimmingCharacters(in: .whitespacesAndNewlines)
        let cleanAlias = modelAlias.trimmingCharacters(in: .whitespacesAndNewlines)
        let cleanModel = modelID.trimmingCharacters(in: .whitespacesAndNewlines)
        if let thinkingError = Self.thinkingValidationError(apiType: apiType, thinking: thinking) {
            status = thinkingError
            return
        }
        guard !cleanProvider.isEmpty, !cleanAlias.isEmpty, !cleanModel.isEmpty,
              !apiType.isEmpty, let url = URL(string: baseURL), ["http", "https"].contains(url.scheme?.lowercased() ?? "") else {
            status = "Enter a provider name, model alias, model ID, API type, and a valid HTTP(S) base URL."
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
                newAPIKey: apiKey.isEmpty ? nil : apiKey,
                removeAPIKey: clearSavedKey
            )
            try writeSecurely(result, to: configURL)
            original = result
            providerSection = "providers.\(cleanProvider)"
            modelSection = "models.\(cleanAlias)"
            providerName = cleanProvider
            modelAlias = cleanAlias
            if let key = apiKey.isEmpty ? (clearSavedKey ? nil : originalKey) : apiKey {
                originalKey = key
            } else {
                originalKey = nil
            }
            apiKey = ""
            hasSavedKey = originalKey != nil
            clearSavedKey = false
            isUnsupported = false
            status = "Saved to \(configURL.path). Reopen the workspace to reconnect Arabica."
        } catch {
            status = "Could not save config: \(error.localizedDescription)"
        }
    }

    func saveDefaultPolicy() {
        guard policyOptions.contains(defaultPolicy) else {
            policyStatus = "Choose a policy reported in this configuration."
            return
        }
        do {
            let current = try String(contentsOf: configURL, encoding: .utf8)
            let result = try ConfigDocument(current).settingDefaultPolicy(defaultPolicy)
            try writeSecurely(result, to: configURL)
            original = result
            policyStatus = "Default policy saved. New conversations will start with \(defaultPolicy)."
        } catch {
            policyStatus = "Could not save default policy: \(error.localizedDescription)"
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
        let alias = String(newModelSection.dropFirst("models.".count))
        let namedPolicies = result.sectionNames.filter { $0.hasPrefix("blend.policies.") }
        if !namedPolicies.isEmpty {
            let policySection = "blend.policies.\(defaultPolicy)"
            guard !defaultPolicy.isEmpty, result.range(of: policySection) != nil else {
                throw EditorError.multipleManagedSections
            }
            result.set("default_policy", value: Self.quote(defaultPolicy), in: "blend")
            result.set("default_model", value: Self.quote(alias), in: policySection)
        } else {
            result.set("default_model", value: Self.quote(alias), in: "blend")
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
