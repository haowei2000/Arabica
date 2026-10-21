import Combine
import Foundation

indirect enum SettingsValue: Codable, Equatable, Sendable {
    case object([String: SettingsValue])
    case array([SettingsValue])
    case string(String)
    case bool(Bool)
    case number(Double)
    case null

    init(from decoder: Decoder) throws {
        let container = try decoder.singleValueContainer()
        if container.decodeNil() {
            self = .null
        } else if let value = try? container.decode(Bool.self) {
            self = .bool(value)
        } else if let value = try? container.decode(String.self) {
            self = .string(value)
        } else if let value = try? container.decode(Double.self) {
            self = .number(value)
        } else if let value = try? container.decode([SettingsValue].self) {
            self = .array(value)
        } else {
            self = .object(try container.decode([String: SettingsValue].self))
        }
    }

    func encode(to encoder: Encoder) throws {
        var container = encoder.singleValueContainer()
        switch self {
        case .object(let value): try container.encode(value)
        case .array(let value): try container.encode(value)
        case .string(let value): try container.encode(value)
        case .bool(let value): try container.encode(value)
        case .number(let value): try container.encode(value)
        case .null: try container.encodeNil()
        }
    }

    func value(at path: [String]) -> SettingsValue? {
        guard let head = path.first else { return self }
        let tail = Array(path.dropFirst())
        switch self {
        case .object(let object): return object[head]?.value(at: tail)
        case .array(let array):
            guard let index = Int(head), array.indices.contains(index) else { return nil }
            return array[index].value(at: tail)
        default: return nil
        }
    }

    fileprivate func applying(_ edit: SettingsEdit) throws -> SettingsValue {
        try updating(path: edit.path[...], operation: edit.operation, value: edit.value)
    }

    private func updating(
        path: ArraySlice<String>, operation: SettingsEdit.Operation, value: SettingsValue?
    ) throws -> SettingsValue {
        guard let head = path.first else {
            switch operation {
            case .set:
                guard let value else { throw SettingsFailure.invalidPath }
                return value
            case .append:
                guard case .array(var array) = self, let value else { throw SettingsFailure.invalidPath }
                array.append(value)
                return .array(array)
            case .remove: throw SettingsFailure.invalidPath
            }
        }
        switch self {
        case .object(var object):
            if path.count == 1 && operation == .remove {
                object.removeValue(forKey: head)
            } else {
                let child = object[head] ?? .object([:])
                object[head] = try child.updating(
                    path: path.dropFirst(), operation: operation, value: value)
            }
            return .object(object)
        case .array(var array):
            guard let index = Int(head), array.indices.contains(index) else {
                throw SettingsFailure.invalidPath
            }
            if path.count == 1 && operation == .remove {
                array.remove(at: index)
            } else {
                array[index] = try array[index].updating(
                    path: path.dropFirst(), operation: operation, value: value)
            }
            return .array(array)
        default: throw SettingsFailure.invalidPath
        }
    }
}

struct SettingsSnapshot: Codable, Equatable, Sendable {
    let revision: String
    let document: SettingsValue
    let secretPaths: [[String]]

    enum CodingKeys: String, CodingKey {
        case revision, document
        case secretPaths = "secret_paths"
    }
}

struct SettingsEdit: Codable, Equatable, Sendable {
    enum Operation: String, Codable, Sendable { case set, remove, append }
    let operation: Operation
    let path: [String]
    let value: SettingsValue?

    enum CodingKeys: String, CodingKey {
        case operation = "op"
        case path, value
    }
}

struct SettingsCommand: Sendable {
    let executable: URL?
    let arguments: [String]
    let home: URL
    let input: Data?
}

struct SettingsCommandResult: Sendable {
    let status: Int32
    let output: Data

    init(status: Int32, output: Data) {
        self.status = status
        self.output = output
    }
}

struct SettingsFailure: Error, Codable, Equatable, Sendable {
    let code: String
    let message: String
    let path: [String]

    static let invalidPath = SettingsFailure(
        code: "invalid_path", message: "This configuration field is not available.", path: [])
}

@MainActor
final class SettingsFormStore: ObservableObject {
    enum Phase { case idle, loading, ready, saving, failed }
    enum StatusKind { case information, success, error }
    typealias Executor = @Sendable (SettingsCommand) async throws -> SettingsCommandResult

    @Published private(set) var draft: SettingsValue = .object([:])
    @Published private(set) var snapshot: SettingsSnapshot?
    @Published private(set) var phase: Phase = .idle
    @Published private(set) var status = ""
    @Published private(set) var statusKind: StatusKind = .information
    @Published private(set) var fieldErrors: [[String]: String] = [:]
    @Published private(set) var lastErrorCode: String?
    @Published private(set) var needsReconnect = false
    @Published private(set) var chatGPTConnected = false
    @Published private(set) var isRunningCommand = false
    @Published private(set) var skillRoots: [SkillRootItem] = []
    @Published private(set) var discoveredSkills: [DiscoveredSkillItem] = []

    let arabicaHomeURL: URL
    var configURL: URL { arabicaHomeURL.appendingPathComponent("config.toml") }
    var isSaving: Bool { phase == .saving }
    var isBusy: Bool { phase == .loading || isSaving || isRunningCommand }
    var hasUnsavedChanges: Bool { !pending.isEmpty || !inFlight.isEmpty }

    private let executableURL: URL?
    private let executor: Executor
    private let debounceNanoseconds: UInt64
    private var started = false
    private var pending: [SettingsEdit] = []
    private var inFlight: [SettingsEdit] = []
    private var debounceTask: Task<Void, Never>?
    private var flushTask: Task<Bool, Never>?
    private var flushID: UUID?
    private var loading = false
    private var contextGeneration = UUID()

    init(
        homeURL: URL? = nil, executableURL: URL? = nil, debounceNanoseconds: UInt64 = 600_000_000,
        executor: Executor? = nil
    ) {
        arabicaHomeURL =
            homeURL
            ?? ProcessInfo.processInfo.environment["ARABICA_HOME"].map {
                URL(fileURLWithPath: $0, isDirectory: true)
            }
            ?? FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent(
                ".arabica", isDirectory: true)
        self.executableURL = executableURL ?? Self.findExecutable()
        self.debounceNanoseconds = debounceNanoseconds
        self.executor = executor ?? { @Sendable command in try await Self.executeCommand(command) }
    }

    func start() async {
        started = true
        if !hasUnsavedChanges && !isBusy && !loading {
            _ = await reload()
        } else if hasUnsavedChanges && lastErrorCode == nil {
            scheduleSave()
        }
    }

    @discardableResult
    func reload() async -> Bool {
        guard !isBusy && !loading else { return false }
        guard !hasUnsavedChanges else {
            report(
                SettingsFailure(
                    code: "unsaved_changes",
                    message: "Your edits were kept. Save them or explicitly discard them before reloading.",
                    path: []))
            return false
        }
        return await loadSnapshot(discardingChanges: false)
    }

    @discardableResult
    func discardAndReload() async -> Bool {
        guard !isBusy && !loading else { return false }
        debounceTask?.cancel()
        return await loadSnapshot(discardingChanges: true)
    }

    @discardableResult
    func discardChanges() async -> Bool {
        guard !isBusy && !loading, let snapshot else { return false }
        debounceTask?.cancel()
        pending = []
        draft = snapshot.document
        clearError()
        phase = .ready
        status = "Unsaved changes discarded."
        return true
    }

    func value(at path: [String]) -> SettingsValue? { draft.value(at: path) }
    func string(at path: [String]) -> String {
        guard case .string(let value) = value(at: path) else { return "" }
        return value
    }
    func bool(at path: [String]) -> Bool {
        guard case .bool(let value) = value(at: path) else { return false }
        return value
    }
    func number(at path: [String]) -> Double? {
        guard case .number(let value) = value(at: path) else { return nil }
        return value
    }
    func objectKeys(at path: [String]) -> [String] {
        guard case .object(let value) = value(at: path) else { return [] }
        return value.keys.sorted()
    }
    func arrayValues(at path: [String]) -> [SettingsValue] {
        guard case .array(let value) = value(at: path) else { return [] }
        return value
    }
    func fieldError(at path: [String]) -> String? { fieldErrors[path] }
    func uniqueName(prefix: String, at path: [String]) -> String {
        let existing = Set(objectKeys(at: path))
        if !existing.contains(prefix) { return prefix }
        var suffix = 2
        while existing.contains("\(prefix)\(suffix)") { suffix += 1 }
        return "\(prefix)\(suffix)"
    }

    func set(_ value: SettingsValue, at path: [String]) {
        guard self.value(at: path) != value else { return }
        enqueue(SettingsEdit(operation: .set, path: path, value: value))
    }
    func remove(at path: [String]) {
        guard value(at: path) != nil else { return }
        enqueue(SettingsEdit(operation: .remove, path: path, value: nil))
    }
    func append(_ value: SettingsValue, at path: [String]) {
        if self.value(at: path) == nil {
            set(.array([value]), at: path)
        } else {
            enqueue(SettingsEdit(operation: .append, path: path, value: value))
        }
    }

    func secretText(at path: [String]) -> String { string(at: path) }
    func secretExists(at path: [String]) -> Bool {
        guard let snapshot else { return false }
        var paths = Set(snapshot.secretPaths)
        var document = snapshot.document
        for edit in inFlight + pending {
            if edit.operation == .remove {
                paths = Set(paths.filter { !$0.starts(with: edit.path) })
                if let indexString = edit.path.last, let index = Int(indexString),
                    case .array = document.value(at: Array(edit.path.dropLast()))
                {
                    let parent = Array(edit.path.dropLast())
                    paths = Set(
                        paths.map { oldPath in
                            guard oldPath.starts(with: parent), oldPath.count > parent.count,
                                let oldIndex = Int(oldPath[parent.count]), oldIndex > index
                            else { return oldPath }
                            var shifted = oldPath
                            shifted[parent.count] = String(oldIndex - 1)
                            return shifted
                        })
                }
            } else if edit.operation == .set {
                paths = Set(paths.filter { !$0.starts(with: edit.path) || $0 == edit.path })
            }
            document = (try? document.applying(edit)) ?? document
        }
        return paths.contains(path)
    }
    func setSecretText(_ text: String, at path: [String]) {
        if text.isEmpty { restoreSecret(at: path) } else { set(.string(text), at: path) }
    }
    func restoreSecret(at path: [String]) {
        guard !loading && !isSaving && !isRunningCommand, let snapshot else { return }
        var states: [SettingsValue] = []
        var document = inFlight.reduce(snapshot.document) { (try? $0.applying($1)) ?? $0 }
        for edit in pending {
            states.append(document)
            document = (try? document.applying(edit)) ?? document
        }
        var target = path
        var retained: [SettingsEdit] = []
        for index in pending.indices.reversed() {
            let edit = pending[index]
            if edit.path == target && edit.operation != .append { continue }
            retained.append(edit)
            if edit.operation == .remove, let removedIndex = edit.path.last.flatMap(Int.init) {
                let parent = Array(edit.path.dropLast())
                if case .array = states[index].value(at: parent), target.starts(with: parent),
                    target.count > parent.count, let targetIndex = Int(target[parent.count]),
                    targetIndex >= removedIndex
                {
                    target[parent.count] = String(targetIndex + 1)
                }
            }
        }
        pending = Array(retained.reversed())
        rebuildDraft()
        clearError()
        if !isSaving { phase = .ready }
        status = hasUnsavedChanges ? "Changes pending." : "Existing secret kept."
        scheduleSave()
    }

    @discardableResult
    func flush() async -> Bool {
        debounceTask?.cancel()
        debounceTask = nil
        if let flushTask {
            let id = flushID
            let result = await flushTask.value
            if flushID == id {
                self.flushTask = nil
                flushID = nil
            }
            return result && hasUnsavedChanges ? await flush() : result
        }
        guard !loading && !isRunningCommand else { return false }
        guard hasUnsavedChanges else { return snapshot != nil }
        let task = Task { @MainActor [weak self] in await self?.savePending() ?? false }
        let id = UUID()
        flushID = id
        flushTask = task
        let result = await task.value
        if flushID == id {
            flushTask = nil
            flushID = nil
        }
        return result && hasUnsavedChanges ? await flush() : result
    }

    func markReconnected() { needsReconnect = false }
    @discardableResult
    func runChatGPTLogin() async -> Bool {
        await runExternal(
            arguments: ["auth", "chatgpt-login"],
            success: "Connected to ChatGPT. Apply settings to reconnect the workspace.")
    }
    @discardableResult
    func runChatGPTLogout() async -> Bool {
        await runExternal(
            arguments: ["auth", "chatgpt-logout"],
            success: "ChatGPT disconnected. Apply settings to reconnect the workspace.")
    }
    @discardableResult
    func syncModels(provider: String? = nil) async -> Bool {
        var arguments = ["models", "sync"]
        if let provider, !provider.isEmpty { arguments += ["--provider", provider] }
        return await runExternal(
            arguments: arguments,
            success: "Models synchronized. Apply settings to reconnect the workspace.")
    }

    private func enqueue(_ edit: SettingsEdit) {
        guard snapshot != nil && !loading && !isRunningCommand else { return }
        do {
            draft = try draft.applying(edit)
            pending.append(edit)
            clearError()
            if !isSaving { phase = .ready }
            status = "Changes pending."
            scheduleSave()
        } catch {
            report(
                SettingsFailure(
                    code: "invalid_path", message: "This configuration field is not available.",
                    path: edit.path))
        }
    }

    private func scheduleSave() {
        debounceTask?.cancel()
        guard started && !pending.isEmpty && lastErrorCode == nil else { return }
        let delay = debounceNanoseconds
        debounceTask = Task { @MainActor [weak self] in
            do { try await Task.sleep(nanoseconds: delay) } catch { return }
            guard !Task.isCancelled, let self else { return }
            // flush owns the running save; do not cancel this task from flush.
            self.debounceTask = nil
            _ = await self.flush()
        }
    }

    private struct ApplyPayload: Encodable {
        let expected_revision: String
        let edits: [SettingsEdit]
    }
    private struct ErrorEnvelope: Decodable { let error: SettingsFailure }

    private func savePending() async -> Bool {
        while !pending.isEmpty {
            guard let snapshot else { return false }
            let batch = pending
            pending = []
            inFlight = batch
            phase = .saving
            status = "Saving…"
            do {
                let input = try JSONEncoder().encode(
                    ApplyPayload(expected_revision: snapshot.revision, edits: batch))
                let response = try await command(["config", "apply"], input: input)
                let updated = try decodeSnapshot(response)
                self.snapshot = updated
                contextGeneration = UUID()
                inFlight = []
                rebuildDraft()
                clearError()
                needsReconnect = true
            } catch {
                let receivedNewEdits = !pending.isEmpty
                pending = batch + pending
                inFlight = []
                rebuildDraft()
                report(error)
                if receivedNewEdits {
                    // The rejection describes the older batch. Recheck the
                    // newer draft once, including edits made while awaiting it.
                    clearError()
                    phase = .ready
                    status = "Changes pending."
                    scheduleSave()
                } else {
                    // An unchanged invalid draft never retries by itself.
                    debounceTask?.cancel()
                    debounceTask = nil
                }
                return false
            }
        }
        phase = .ready
        statusKind = .success
        status = "Saved."
        Task { @MainActor [weak self] in await self?.refreshLocalContext() }
        return true
    }

    private func rebuildDraft() {
        guard let snapshot else { return }
        draft = (inFlight + pending).reduce(snapshot.document) { (try? $0.applying($1)) ?? $0 }
    }

    private func loadSnapshot(discardingChanges: Bool) async -> Bool {
        loading = true
        phase = .loading
        status = "Loading configuration…"
        defer { loading = false }
        do {
            let updated = try decodeSnapshot(try await command(["config", "inspect"]))
            if discardingChanges { pending = [] }
            snapshot = updated
            contextGeneration = UUID()
            rebuildDraft()
            clearError()
            phase = .ready
            status = "Configuration loaded."
            Task { @MainActor [weak self] in await self?.refreshLocalContext() }
            return true
        } catch {
            report(error)
            return false
        }
    }

    private func runExternal(arguments: [String], success: String) async -> Bool {
        guard !isRunningCommand && !loading else { return false }
        guard await flush() else { return false }
        guard !isRunningCommand && !loading else { return false }
        isRunningCommand = true
        defer { isRunningCommand = false }
        statusKind = .information
        status = arguments.first == "auth" ? "Waiting for account operation…" : "Synchronizing models…"
        do {
            let response = try await command(arguments)
            try checkResponse(response)
            let updated = try decodeSnapshot(try await command(["config", "inspect"]))
            snapshot = updated
            contextGeneration = UUID()
            rebuildDraft()
            clearError()
            phase = .ready
            statusKind = .success
            status = success
            needsReconnect = true
            Task { @MainActor [weak self] in await self?.refreshLocalContext() }
            return true
        } catch {
            report(error)
            return false
        }
    }

    private func command(_ arguments: [String], input: Data? = nil) async throws
        -> SettingsCommandResult
    {
        try await executor(
            SettingsCommand(
                executable: executableURL, arguments: arguments, home: arabicaHomeURL, input: input))
    }

    private func checkResponse(_ response: SettingsCommandResult) throws {
        guard response.status == 0 else {
            if let error = try? JSONDecoder().decode(ErrorEnvelope.self, from: response.output).error {
                throw error
            }
            throw SettingsFailure(
                code: "command_failed",
                message: "Arabica could not complete the command (exit \(response.status)).", path: [])
        }
    }

    private func decodeSnapshot(_ response: SettingsCommandResult) throws -> SettingsSnapshot {
        try checkResponse(response)
        guard let snapshot = try? JSONDecoder().decode(SettingsSnapshot.self, from: response.output),
            case .object = snapshot.document
        else {
            throw SettingsFailure(
                code: "invalid_response", message: "Arabica returned an unreadable configuration response.",
                path: [])
        }
        return snapshot
    }

    private func clearError() {
        fieldErrors = [:]
        lastErrorCode = nil
        statusKind = .information
    }

    private func report(_ error: Error) {
        let failure =
            error as? SettingsFailure
            ?? SettingsFailure(
                code: "command_failed",
                message:
                    "Could not run Arabica. Check that the bundled command-line executable is available.",
                path: [])
        phase = .failed
        statusKind = .error
        lastErrorCode = failure.code
        status =
            failure.code == "conflict"
            ? "The configuration changed on disk. Your edits were kept. Reload and discard them to review the external changes."
            : failure.message
        fieldErrors = failure.path.isEmpty ? [:] : [failure.path: failure.message]
    }

    private func refreshLocalContext() async {
        let generation = UUID()
        contextGeneration = generation
        let home = arabicaHomeURL
        let rootValues = arrayValues(at: ["skills", "roots"])
        let context = await Task.detached(priority: .utility) {
            let authURL = home.appendingPathComponent("chatgpt-auth.json")
            let authData = try? Data(contentsOf: authURL)
            let auth = authData.flatMap { try? JSONSerialization.jsonObject(with: $0) as? [String: Any] }
            let connected = !(auth?["access_token"] as? String ?? "").isEmpty
            let roots = rootValues.compactMap { value -> SkillRootItem? in
                guard case .string(let path) = value else { return nil }
                let url =
                    path.hasPrefix("/") ? URL(fileURLWithPath: path) : home.appendingPathComponent(path)
                var directory: ObjCBool = false
                let exists =
                    FileManager.default.fileExists(atPath: url.path, isDirectory: &directory)
                    && directory.boolValue
                return SkillRootItem(rawPath: path, resolvedURL: url, exists: exists)
            }
            var skills: [DiscoveredSkillItem] = []
            for root in roots where root.exists {
                let direct = root.resolvedURL.appendingPathComponent("SKILL.md")
                let directories: [URL]
                if FileManager.default.fileExists(atPath: direct.path) {
                    directories = [root.resolvedURL]
                } else {
                    directories =
                        (try? FileManager.default.contentsOfDirectory(
                            at: root.resolvedURL, includingPropertiesForKeys: nil, options: .skipsHiddenFiles))
                        ?? []
                }
                for directory in directories {
                    let manifest = directory.appendingPathComponent("SKILL.md")
                    guard let text = try? String(contentsOf: manifest, encoding: .utf8) else { continue }
                    let lines = text.components(separatedBy: .newlines)
                    var name = directory.lastPathComponent
                    var description = ""
                    if lines.first?.trimmingCharacters(in: .whitespaces) == "---" {
                        for line in lines.dropFirst().prefix(while: {
                            $0.trimmingCharacters(in: .whitespaces) != "---"
                        }) {
                            if line.hasPrefix("name:") {
                                name = String(line.dropFirst(5)).trimmingCharacters(in: .whitespaces)
                            }
                            if line.hasPrefix("description:") {
                                description = String(line.dropFirst(12)).trimmingCharacters(in: .whitespaces)
                            }
                        }
                    }
                    skills.append(
                        DiscoveredSkillItem(
                            name: name, description: description, directoryURL: directory, rootPath: root.rawPath)
                    )
                }
            }
            return (connected, roots, skills.sorted { $0.name < $1.name })
        }.value
        guard contextGeneration == generation, rootValues == arrayValues(at: ["skills", "roots"]) else {
            return
        }
        chatGPTConnected = context.0
        skillRoots = context.1
        discoveredSkills = context.2
    }

    private static func findExecutable() -> URL? {
        if let override = ProcessInfo.processInfo.environment["ARABICA_EXECUTABLE"],
            FileManager.default.isExecutableFile(atPath: override)
        {
            return URL(fileURLWithPath: override)
        }
        if let bundled = Bundle.main.resourceURL?.appendingPathComponent("arabica"),
            FileManager.default.isExecutableFile(atPath: bundled.path)
        {
            return bundled
        }
        return nil
    }

    nonisolated private static func executeCommand(_ command: SettingsCommand) async throws
        -> SettingsCommandResult
    {
        try await Task.detached(priority: .userInitiated) {
            guard let executable = command.executable else {
                throw SettingsFailure(
                    code: "executable_missing",
                    message:
                        "Arabica executable was not found. Set ARABICA_EXECUTABLE or rebuild the desktop app.",
                    path: [])
            }
            let process = Process()
            process.executableURL = executable
            process.arguments = command.arguments
            var environment = ProcessInfo.processInfo.environment
            environment["ARABICA_HOME"] = command.home.path
            process.environment = environment
            let output = Pipe()
            let input = Pipe()
            process.standardOutput = output
            // Protocol errors are returned as JSON on stdout. Discard stderr
            // rather than retain or surface possible credential diagnostics.
            process.standardError = FileHandle.nullDevice
            process.standardInput = input
            try process.run()
            let writer = Task.detached {
                if let payload = command.input {
                    try? input.fileHandleForWriting.write(contentsOf: payload)
                }
                try? input.fileHandleForWriting.close()
            }
            let data = output.fileHandleForReading.readDataToEndOfFile()
            process.waitUntilExit()
            await writer.value
            return SettingsCommandResult(status: process.terminationStatus, output: data)
        }.value
    }
}
