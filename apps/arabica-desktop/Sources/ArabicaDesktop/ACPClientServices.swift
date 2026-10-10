import Foundation

@MainActor
final class ACPClientServices {
    private final class Terminal {
        let process: Process
        let pipe: Pipe
        let sessionID: String
        let outputLimit: Int
        var output = Data()
        var truncated = false

        init(process: Process, pipe: Pipe, sessionID: String, outputLimit: Int) {
            self.process = process
            self.pipe = pipe
            self.sessionID = sessionID
            self.outputLimit = outputLimit
        }

        func append(_ data: Data) {
            output.append(data)
            if output.count > outputLimit {
                output.removeFirst(output.count - outputLimit)
                truncated = true
            }
        }

        func snapshot() -> [String: Any] {
            var bytes = output
            while !bytes.isEmpty && String(data: bytes, encoding: .utf8) == nil {
                bytes.removeFirst()
            }
            var result: [String: Any] = ["output": String(data: bytes, encoding: .utf8) ?? "",
                                         "truncated": truncated]
            if !process.isRunning { result["exitStatus"] = exitStatus() }
            return result
        }

        func exitStatus() -> [String: Any] {
            if process.terminationReason == .uncaughtSignal {
                return ["signal": String(process.terminationStatus)]
            }
            return ["exitCode": max(0, process.terminationStatus)]
        }
    }

    private var terminals: [String: Terminal] = [:]

    func reset() {
        for terminal in terminals.values {
            terminal.pipe.fileHandleForReading.readabilityHandler = nil
            if terminal.process.isRunning { terminal.process.terminate() }
        }
        terminals.removeAll()
    }

    func handle(_ method: String, params: [String: Any], workspace: URL?) async throws -> [String: Any] {
        guard let workspace,
              let sessionID = params["sessionId"] as? String, !sessionID.isEmpty else {
            throw ServiceError.invalid("Missing workspace or session ID")
        }
        switch method {
        case "fs/read_text_file":
            let url = try checkedPath(params["path"], workspace: workspace, forWrite: false)
            let content = try String(contentsOf: url, encoding: .utf8)
            let lines = content.components(separatedBy: "\n")
            let line = params["line"] as? Int ?? 1
            guard line > 0 else { throw ServiceError.invalid("Line must be positive") }
            let limit = params["limit"] as? Int ?? lines.count
            guard limit >= 0 else { throw ServiceError.invalid("Limit must not be negative") }
            let start = min(line - 1, lines.count)
            return ["content": lines[start..<min(lines.count, start + limit)].joined(separator: "\n")]
        case "fs/write_text_file":
            let url = try checkedPath(params["path"], workspace: workspace, forWrite: true)
            guard let content = params["content"] as? String else { throw ServiceError.invalid("Missing content") }
            try content.write(to: url, atomically: true, encoding: .utf8)
            return [:]
        case "terminal/create":
            guard let command = params["command"] as? String, !command.isEmpty else {
                throw ServiceError.invalid("Missing command")
            }
            let cwd = try checkedPath(params["cwd"] ?? workspace.path, workspace: workspace, forWrite: false)
            var isDirectory: ObjCBool = false
            guard FileManager.default.fileExists(atPath: cwd.path, isDirectory: &isDirectory), isDirectory.boolValue else {
                throw ServiceError.invalid("Terminal cwd must be a directory")
            }
            let process = Process()
            process.executableURL = URL(fileURLWithPath: "/usr/bin/env")
            process.arguments = [command] + (params["args"] as? [String] ?? [])
            process.currentDirectoryURL = cwd
            var environment = ProcessInfo.processInfo.environment
            for item in params["env"] as? [[String: String]] ?? [] {
                if let name = item["name"], let value = item["value"] { environment[name] = value }
            }
            process.environment = environment
            let pipe = Pipe()
            process.standardOutput = pipe
            process.standardError = pipe
            process.standardInput = Pipe()
            let id = UUID().uuidString
            let limit = max(1024, min(params["outputByteLimit"] as? Int ?? 1_000_000, 10_000_000))
            let terminal = Terminal(process: process, pipe: pipe, sessionID: sessionID, outputLimit: limit)
            pipe.fileHandleForReading.readabilityHandler = { [weak terminal] handle in
                let bytes = handle.availableData
                Task { @MainActor in terminal?.append(bytes) }
            }
            try process.run()
            terminals[id] = terminal
            return ["terminalId": id]
        case "terminal/output", "terminal/release", "terminal/kill", "terminal/wait_for_exit":
            guard let id = params["terminalId"] as? String,
                  let terminal = terminals[id], terminal.sessionID == sessionID else {
                throw ServiceError.invalid("Unknown terminal")
            }
            switch method {
            case "terminal/output": return terminal.snapshot()
            case "terminal/kill":
                if terminal.process.isRunning { terminal.process.terminate() }
                return [:]
            case "terminal/release":
                terminal.pipe.fileHandleForReading.readabilityHandler = nil
                if terminal.process.isRunning { terminal.process.terminate() }
                terminals.removeValue(forKey: id)
                return [:]
            default:
                let process = terminal.process
                if process.isRunning {
                    await withCheckedContinuation { continuation in
                        DispatchQueue.global(qos: .utility).async {
                            process.waitUntilExit()
                            continuation.resume()
                        }
                    }
                }
                return ["exitStatus": terminal.exitStatus()]
            }
        default: throw ServiceError.unknown
        }
    }

    private func checkedPath(_ value: Any?, workspace: URL, forWrite: Bool) throws -> URL {
        guard let path = value as? String, path.hasPrefix("/") else {
            throw ServiceError.invalid("Path must be absolute")
        }
        let root = workspace.resolvingSymlinksInPath().standardizedFileURL
        let candidate = URL(fileURLWithPath: path).standardizedFileURL
        let checked = forWrite ? candidate.deletingLastPathComponent().resolvingSymlinksInPath() : candidate.resolvingSymlinksInPath()
        guard checked.path == root.path || checked.path.hasPrefix(root.path + "/") else {
            throw ServiceError.invalid("Path is outside the workspace")
        }
        if forWrite && (try? FileManager.default.destinationOfSymbolicLink(atPath: candidate.path)) != nil {
            throw ServiceError.invalid("Writing through a symlink is not allowed")
        }
        if forWrite && FileManager.default.fileExists(atPath: candidate.path) {
            let target = candidate.resolvingSymlinksInPath()
            guard target.path == root.path || target.path.hasPrefix(root.path + "/") else {
                throw ServiceError.invalid("Path is outside the workspace")
            }
        }
        return candidate
    }
}

enum ServiceError: LocalizedError {
    case invalid(String)
    case unknown

    var errorDescription: String? {
        switch self {
        case .invalid(let message): message
        case .unknown: "Method not found"
        }
    }
}
