import Foundation

private final class ACPStartupDiagnostic: @unchecked Sendable {
    private let lock = NSLock()
    private var message: String?

    func consume(_ data: Data) {
        guard let text = String(data: data, encoding: .utf8) else { return }
        let safeMessage: String?
        if text.contains("invalid TOML in") {
            if let range = text.range(of: #"at line ([0-9]+), column ([0-9]+)"#, options: .regularExpression) {
                safeMessage = "Configuration file was rejected at \(text[range])."
            } else {
                safeMessage = "The Arabica configuration file was rejected."
            }
        } else if text.contains("configure [providers.<name>]") {
            safeMessage = "Arabica needs a provider, model alias, and blend configuration in ~/.arabica/config.toml."
        } else {
            safeMessage = nil
        }
        guard let safeMessage else { return }
        lock.lock()
        message = safeMessage
        lock.unlock()
    }

    var value: String? {
        lock.lock()
        defer { lock.unlock() }
        return message
    }

    func reset() {
        lock.lock()
        message = nil
        lock.unlock()
    }
}

enum ACPError: LocalizedError {
    case executableMissing
    case disconnected
    case invalidResponse
    case remote(String)

    var errorDescription: String? {
        switch self {
        case .executableMissing: "Arabica executable was not found. Build the bundled app or set ARABICA_EXECUTABLE."
        case .disconnected: "The Arabica agent disconnected."
        case .invalidResponse: "The agent returned an invalid ACP response."
        case .remote(let message): message
        }
    }
}

enum ACPRemoteErrorMessage {
    static func resolve(_ error: [String: Any]) -> String {
        let message = error["message"] as? String ?? "ACP request failed"
        guard message == "Internal error" else { return message }

        if let detail = error["data"] as? String {
            let trimmed = detail.trimmingCharacters(in: .whitespacesAndNewlines)
            if !trimmed.isEmpty { return trimmed }
        }
        if let detail = error["data"] as? [String: Any] {
            for key in ["message", "detail", "reason"] {
                if let value = detail[key] as? String,
                   !value.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                    return value
                }
            }
        }
        return "The agent could not complete this run. Check the provider configuration and connection."
    }
}

struct ACPLineDecoder {
    private var buffer = Data()

    mutating func append(_ data: Data) -> [[String: Any]] {
        buffer.append(data)
        var messages: [[String: Any]] = []
        while let newline = buffer.firstIndex(of: 0x0A) {
            let line = buffer.prefix(upTo: newline)
            buffer.removeSubrange(...newline)
            if let object = try? JSONSerialization.jsonObject(with: line),
               let message = object as? [String: Any] {
                messages.append(message)
            }
        }
        return messages
    }
}

@MainActor
final class ACPClient {
    var onNotification: ((String, [String: Any]) -> Void)?
    var onRequest: ((Int, String, [String: Any]) -> Void)?
    var onExit: (() -> Void)?

    private var process: Process?
    private var input: FileHandle?
    private var output: FileHandle?
    private var errorOutput: FileHandle?
    private var decoder = ACPLineDecoder()
    private let startupDiagnostic = ACPStartupDiagnostic()
    private var nextID = 1
    private var pending: [Int: CheckedContinuation<[String: Any], Error>] = [:]

    var startupErrorMessage: String? { startupDiagnostic.value }

    func start(workspace: URL) throws {
        guard process == nil else { return }
        startupDiagnostic.reset()
        let executable = try Self.executableURL()
        let child = Process()
        child.executableURL = executable
        child.arguments = ["acp"]
        child.currentDirectoryURL = workspace
        let stdin = Pipe()
        let stdout = Pipe()
        let stderr = Pipe()
        child.standardInput = stdin
        child.standardOutput = stdout
        child.standardError = stderr
        child.terminationHandler = { [weak self, weak child] _ in
            Task { @MainActor in
                guard let self, let child, self.process === child else { return }
                self.disconnect()
            }
        }
        stdout.fileHandleForReading.readabilityHandler = { [weak self] handle in
            let data = handle.availableData
            Task { @MainActor in
                guard self?.output === handle else { return }
                self?.receive(data)
            }
        }
        // Drain stderr so a busy agent cannot block on a full pipe. It may contain
        // provider diagnostics, so never forward it into the UI or a log file.
        stderr.fileHandleForReading.readabilityHandler = { [startupDiagnostic] handle in
            let data = handle.availableData
            startupDiagnostic.consume(data)
        }
        try child.run()
        process = child
        input = stdin.fileHandleForWriting
        output = stdout.fileHandleForReading
        errorOutput = stderr.fileHandleForReading
    }

    func initialize() async throws {
        let result = try await request("initialize", params: [
            "protocolVersion": 1,
            "clientCapabilities": ["fs": ["readTextFile": true, "writeTextFile": true], "terminal": true],
            "clientInfo": ["name": "arabica-desktop", "version": "0.1.0"],
        ])
        guard result["protocolVersion"] as? Int == 1 else { throw ACPError.invalidResponse }
    }

    func request(_ method: String, params: [String: Any]) async throws -> [String: Any] {
        guard process?.isRunning == true else { throw ACPError.disconnected }
        let id = nextID
        nextID += 1
        return try await withCheckedThrowingContinuation { continuation in
            pending[id] = continuation
            do {
                try write(["jsonrpc": "2.0", "id": id, "method": method, "params": params])
            } catch {
                pending.removeValue(forKey: id)?.resume(throwing: error)
            }
        }
    }

    func notify(_ method: String, params: [String: Any]) throws {
        try write(["jsonrpc": "2.0", "method": method, "params": params])
    }

    func respond(id: Int, result: [String: Any]) throws {
        try write(["jsonrpc": "2.0", "id": id, "result": result])
    }

    func rejectUnknownRequest(id: Int) throws {
        try write([
            "jsonrpc": "2.0", "id": id,
            "error": ["code": -32601, "message": "Method not found"],
        ])
    }

    func reject(id: Int, message: String, code: Int = -32602) throws {
        try write(["jsonrpc": "2.0", "id": id,
                   "error": ["code": code, "message": message]])
    }

    func stop() {
        output?.readabilityHandler = nil
        errorOutput?.readabilityHandler = nil
        process?.terminationHandler = nil
        process?.terminate()
        disconnect()
    }

    private func write(_ value: [String: Any]) throws {
        guard let input else { throw ACPError.disconnected }
        var data = try JSONSerialization.data(withJSONObject: value)
        data.append(0x0A)
        try input.write(contentsOf: data)
    }

    private func receive(_ data: Data) {
        guard !data.isEmpty else {
            disconnect()
            return
        }
        for message in decoder.append(data) {
            route(message)
        }
    }

    private func route(_ message: [String: Any]) {
        if let id = message["id"] as? Int, let method = message["method"] as? String {
            onRequest?(id, method, message["params"] as? [String: Any] ?? [:])
        } else if let id = message["id"] as? Int, let continuation = pending.removeValue(forKey: id) {
            if let error = message["error"] as? [String: Any] {
                continuation.resume(throwing: ACPError.remote(ACPRemoteErrorMessage.resolve(error)))
            } else if let result = message["result"] as? [String: Any] {
                continuation.resume(returning: result)
            } else {
                continuation.resume(throwing: ACPError.invalidResponse)
            }
        } else if let method = message["method"] as? String {
            onNotification?(method, message["params"] as? [String: Any] ?? [:])
        }
    }

    private func disconnect() {
        guard process != nil else { return }
        output?.readabilityHandler = nil
        errorOutput?.readabilityHandler = nil
        input = nil
        output = nil
        errorOutput = nil
        process = nil
        let waiting = pending
        pending.removeAll()
        for continuation in waiting.values { continuation.resume(throwing: ACPError.disconnected) }
        onExit?()
    }

    private static func executableURL() throws -> URL {
        if let override = ProcessInfo.processInfo.environment["ARABICA_EXECUTABLE"],
           FileManager.default.isExecutableFile(atPath: override) {
            return URL(fileURLWithPath: override)
        }
        if let bundled = Bundle.main.resourceURL?.appendingPathComponent("arabica"),
           FileManager.default.isExecutableFile(atPath: bundled.path) {
            return bundled
        }
        throw ACPError.executableMissing
    }
}
