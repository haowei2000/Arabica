import Foundation

final class ACPStartupDiagnostic: @unchecked Sendable {
    private let lock = NSLock()
    private var rejectedMarker = Marker("invalid TOML in")
    private var setupMarker = Marker("configure [providers.<name>]")
    private var location = Location()
    private var configurationRejected = false
    private var needsSetup = false

    func consume(_ data: Data) {
        lock.lock()
        defer { lock.unlock() }
        // Stderr can split anywhere, including inside UTF-8 paths or diagnostic
        // markers. Recognize ASCII bytes without retaining raw stderr, paths,
        // source excerpts, or credentials. Only match counters and bounded line
        // and column digits survive a read.
        for byte in data {
            if rejectedMarker.consume(byte) {
                configurationRejected = true
                location = Location()
            }
            if setupMarker.consume(byte) { needsSetup = true }
            if configurationRejected { location.consume(byte) }
        }
    }

    var value: String? {
        lock.lock()
        defer { lock.unlock() }
        if configurationRejected {
            let position = location.value.map { " at line \($0.line), column \($0.column)" } ?? ""
            return "The Arabica configuration file was rejected\(position). "
                + "It contains invalid or unsupported TOML. Open Settings to review and repair the configuration."
        }
        if needsSetup {
            return "Arabica needs a provider, model alias, and blend configuration. Open Settings to configure them."
        }
        return nil
    }

    func reset() {
        lock.lock()
        defer { lock.unlock() }
        rejectedMarker.reset()
        setupMarker.reset()
        location = Location()
        configurationRejected = false
        needsSetup = false
    }

    private struct Marker {
        private let bytes: [UInt8]
        private let fallback: [Int]
        private var matched = 0

        init(_ literal: String) {
            bytes = Array(literal.utf8)
            var prefixes = Array(repeating: 0, count: bytes.count)
            var count = 0
            for index in 1..<bytes.count {
                while count > 0, bytes[index] != bytes[count] { count = prefixes[count - 1] }
                if bytes[index] == bytes[count] { count += 1 }
                prefixes[index] = count
            }
            fallback = prefixes
        }

        mutating func consume(_ byte: UInt8) -> Bool {
            while matched > 0, byte != bytes[matched] { matched = fallback[matched - 1] }
            if byte == bytes[matched] { matched += 1 }
            guard matched == bytes.count else { return false }
            matched = fallback[matched - 1]
            return true
        }

        mutating func reset() { matched = 0 }
    }

    private struct Location {
        private enum Phase { case searching, line, separator, column, finished }
        private static let separator = Array(", column ".utf8)
        private var prefix = Marker("at line ")
        private var phase = Phase.searching
        private var separatorIndex = 0
        private var line = ""
        private var column = ""

        var value: (line: Int, column: Int)? {
            guard let line = Int(line), line > 0, let column = Int(column), column > 0 else { return nil }
            return (line, column)
        }

        mutating func consume(_ byte: UInt8) {
            switch phase {
            case .searching:
                if prefix.consume(byte) { phase = .line }
            case .line:
                if (0x30...0x39).contains(byte) {
                    guard line.count < 9 else {
                        restart(with: byte)
                        return
                    }
                    line.append(Character(UnicodeScalar(byte)))
                } else if !line.isEmpty, byte == Self.separator[0] {
                    separatorIndex = 1
                    phase = .separator
                } else {
                    restart(with: byte)
                }
            case .separator:
                guard byte == Self.separator[separatorIndex] else {
                    restart(with: byte)
                    return
                }
                separatorIndex += 1
                if separatorIndex == Self.separator.count { phase = .column }
            case .column:
                if (0x30...0x39).contains(byte) {
                    guard column.count < 9 else {
                        restart(with: byte)
                        return
                    }
                    column.append(Character(UnicodeScalar(byte)))
                } else if value != nil {
                    phase = .finished
                } else {
                    restart(with: byte)
                }
            case .finished:
                break
            }
        }

        private mutating func restart(with byte: UInt8) {
            self = Location()
            if prefix.consume(byte) { phase = .line }
        }
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
        guard ["Internal error", "Invalid params", "Invalid request"].contains(message) else { return message }

        if let detail = error["data"] as? String {
            let trimmed = detail.trimmingCharacters(in: .whitespacesAndNewlines)
            if !trimmed.isEmpty { return trimmed }
        }
        if let detail = error["data"] as? [String: Any] {
            for key in ["message", "detail", "reason"] {
                if let value = detail[key] as? String,
                    !value.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
                {
                    return value
                }
            }
        }
        return message == "Internal error"
            ? "The agent could not complete this run. Check the provider configuration and connection."
            : message
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
                let message = object as? [String: Any]
            {
                messages.append(message)
            }
        }
        return messages
    }
}

enum ACPRequestID: Hashable, CustomStringConvertible, Sendable {
    case int(Int)
    case string(String)

    var description: String {
        switch self {
        case .int(let i): return String(i)
        case .string(let s): return s
        }
    }

    var jsonValue: Any {
        switch self {
        case .int(let i): return i
        case .string(let s): return s
        }
    }

    init?(from raw: Any?) {
        if let i = raw as? Int {
            self = .int(i)
        } else if let s = raw as? String {
            self = .string(s)
        } else if let num = raw as? NSNumber {
            self = .int(num.intValue)
        } else {
            return nil
        }
    }
}

@MainActor
final class ACPClient {
    var onNotification: ((String, [String: Any]) -> Void)?
    var onRequest: ((ACPRequestID, String, [String: Any]) -> Void)?
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

    func initialize() async throws -> [String: Any] {
        let result = try await request(
            "initialize",
            params: [
                "protocolVersion": 1,
                "clientCapabilities": ["fs": ["readTextFile": true, "writeTextFile": true], "terminal": true],
                "clientInfo": ["name": "arabica-desktop", "version": "0.1.0"],
            ])
        guard result["protocolVersion"] as? Int == 1 else { throw ACPError.invalidResponse }
        return result
    }

    func request(_ method: String, params: [String: Any], timeoutSeconds: UInt64? = nil) async throws -> [String: Any] {
        guard process?.isRunning == true else { throw ACPError.disconnected }
        let id = nextID
        nextID += 1
        let timeout = timeoutSeconds.map { seconds in
            Task { [weak self] in
                do { try await Task.sleep(for: .seconds(seconds)) } catch { return }
                self?.pending.removeValue(forKey: id)?.resume(throwing: ACPError.remote("ACP request timed out."))
            }
        }
        defer { timeout?.cancel() }
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

    func respond(id: ACPRequestID, result: [String: Any]) throws {
        try write(["jsonrpc": "2.0", "id": id.jsonValue, "result": result])
    }

    func rejectUnknownRequest(id: ACPRequestID) throws {
        try write([
            "jsonrpc": "2.0", "id": id.jsonValue,
            "error": ["code": -32601, "message": "Method not found"],
        ])
    }

    func reject(id: ACPRequestID, message: String, code: Int = -32602) throws {
        try write([
            "jsonrpc": "2.0", "id": id.jsonValue,
            "error": ["code": code, "message": message],
        ])
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

    func route(_ message: [String: Any]) {
        if let method = message["method"] as? String {
            let params = message["params"] as? [String: Any] ?? [:]
            if let rawID = message["id"], let id = ACPRequestID(from: rawID) {
                onRequest?(id, method, params)
            } else {
                onNotification?(method, params)
            }
        } else if let rawID = message["id"],
            let reqID = ACPRequestID(from: rawID),
            case .int(let id) = reqID,
            let continuation = pending.removeValue(forKey: id)
        {
            if let error = message["error"] as? [String: Any] {
                continuation.resume(throwing: ACPError.remote(ACPRemoteErrorMessage.resolve(error)))
            } else if let result = message["result"] as? [String: Any] {
                continuation.resume(returning: result)
            } else {
                continuation.resume(throwing: ACPError.invalidResponse)
            }
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
            FileManager.default.isExecutableFile(atPath: override)
        {
            return URL(fileURLWithPath: override)
        }
        if let bundled = Bundle.main.resourceURL?.appendingPathComponent("arabica"),
            FileManager.default.isExecutableFile(atPath: bundled.path)
        {
            return bundled
        }
        throw ACPError.executableMissing
    }
}
