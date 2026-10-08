import AppKit
import Combine
import Foundation

struct ChatItem: Identifiable {
    enum Kind { case user, assistant, thought, tool }
    let id = UUID()
    let kind: Kind
    var text: String
    var status: String?
    var toolID: String?
    var toolKind: String?
    var detail: String?
    var path: String?
}

struct DesktopSession: Identifiable {
    let id: String
    let cwd: String
    var title: String
}

struct PermissionPrompt: Identifiable {
    let id: Int
    let title: String
    let detail: String
    let options: [(id: String, name: String)]
    let method: String
    let params: [String: Any]
}

private struct SessionControlState {
    var configOptions: [[String: Any]] = []
    var modes: [[String: Any]] = []
    var currentModeID: String?
}

@MainActor
final class DesktopModel: ObservableObject {
    @Published var workspace: URL?
    @Published var sessions: [DesktopSession] = []
    @Published var selectedSessionID: String?
    @Published var items: [ChatItem] = []
    @Published var draft = ""
    @Published var attachedFiles: [URL] = []
    @Published var isRunning = false
    @Published var isConnecting = false
    @Published var isConnected = false
    @Published var errorText: String?
    @Published var permission: PermissionPrompt?
    @Published var configOptions: [[String: Any]] = []
    @Published var availableSessionModes: [[String: Any]] = []
    @Published var plan: [[String: Any]] = []
    @Published var usage: [String: Any] = [:]
    @Published var availableCommands: [[String: Any]] = []
    @Published var currentModeID: String?

    let evaluation = EvaluationModel()
    private let client = ACPClient()
    private let services = ACPClientServices()
    private var loadedSessionID: String?
    private var openSessionIDs = Set<String>()
    private var itemCache: [String: [ChatItem]] = [:]
    private var sessionControlCache: [String: SessionControlState] = [:]
    private var permissionQueue: [PermissionPrompt] = []

    init() {
        client.onNotification = { [weak self] method, params in
            self?.handleNotification(method, params: params)
        }
        client.onRequest = { [weak self] id, method, params in
            self?.handleRequest(id, method: method, params: params)
        }
        client.onExit = { [weak self] in
            self?.evaluation.reset()
            self?.services.reset()
            self?.isRunning = false
            if self?.isConnected == true, self?.isConnecting != true {
                self?.errorText = "Arabica stopped. Reopen the workspace to reconnect."
            }
            self?.isConnected = false
        }
        if let path = UserDefaults.standard.string(forKey: "lastWorkspace"),
           FileManager.default.fileExists(atPath: path) {
            Task { await openWorkspace(URL(fileURLWithPath: path)) }
        }
    }

    func chooseWorkspace() {
        let panel = NSOpenPanel()
        panel.canChooseDirectories = true
        panel.canChooseFiles = false
        panel.allowsMultipleSelection = false
        panel.prompt = "Open workspace"
        if panel.runModal() == .OK, let url = panel.url {
            Task { await openWorkspace(url) }
        }
    }

    func chooseFiles() {
        let panel = NSOpenPanel()
        panel.canChooseDirectories = false
        panel.canChooseFiles = true
        panel.allowsMultipleSelection = true
        panel.prompt = "Attach"
        if panel.runModal() == .OK { attachedFiles.append(contentsOf: panel.urls) }
    }

    func openWorkspace(_ url: URL) async {
        guard !isConnecting else { return }
        isConnecting = true
        isConnected = false
        errorText = nil
        evaluation.reset()
        client.stop()
        services.reset()
        workspace = url
        sessions = []
        selectedSessionID = nil
        loadedSessionID = nil
        openSessionIDs = []
        itemCache = [:]
        sessionControlCache = [:]
        permissionQueue = []
        permission = nil
        configOptions = []
        availableSessionModes = []
        plan = []
        usage = [:]
        availableCommands = []
        currentModeID = nil
        items = []
        do {
            try client.start(workspace: url)
            let initialized = try await client.initialize()
            evaluation.connect(supported: EvaluationCapability.supported(by: initialized)) { [weak self] method, params in
                guard let self else { throw ACPError.disconnected }
                return try await self.client.request(method, params: params, timeoutSeconds: 10)
            }
            isConnected = true
            UserDefaults.standard.set(url.path, forKey: "lastWorkspace")
            await refreshSessions()
        } catch {
            if case ACPError.disconnected = error {
                errorText = client.startupErrorMessage
                    ?? "Arabica's background process exited before ACP initialization. Check ~/.arabica/config.toml and reopen the workspace."
            } else {
                errorText = error.localizedDescription
            }
        }
        isConnecting = false
    }

    func refreshSessions() async {
        guard let workspace else { return }
        do {
            var entries: [[String: Any]] = []
            var cursor: String?
            var seen = Set<String>()
            repeat {
                var params: [String: Any] = ["cwd": workspace.path]
                if let cursor { params["cursor"] = cursor }
                let result = try await client.request("session/list", params: params)
                entries.append(contentsOf: result["sessions"] as? [[String: Any]] ?? [])
                cursor = result["nextCursor"] as? String
                if let cursor, !seen.insert(cursor).inserted { throw ACPError.invalidResponse }
            } while cursor != nil
            sessions = entries.compactMap { entry in
                guard let id = entry["sessionId"] as? String,
                      let cwd = entry["cwd"] as? String else { return nil }
                return DesktopSession(id: id, cwd: cwd, title: Self.sessionTitle(entry))
            }
        } catch {
            errorText = error.localizedDescription
        }
    }

    func newSession() async {
        guard let workspace else { return }
        errorText = nil
        do {
            let result = try await client.request("session/new", params: [
                "cwd": workspace.path, "mcpServers": [],
            ])
            guard let id = result["sessionId"] as? String else { throw ACPError.invalidResponse }
            applySessionControls(from: result, for: id)
            sessions.insert(DesktopSession(id: id, cwd: workspace.path, title: "New conversation"), at: 0)
            selectedSessionID = id
            loadedSessionID = id
            openSessionIDs.insert(id)
            items = []
            if selectedSessionID == id { evaluation.select(id) }
        } catch {
            errorText = error.localizedDescription
        }
    }

    func selectSession(_ id: String) async {
        guard let workspace else { return }
        if id == loadedSessionID { return }
        guard !isRunning else { return }
        if let previous = loadedSessionID { itemCache[previous] = items }
        evaluation.select(nil)
        selectedSessionID = id
        items = itemCache[id] ?? []
        errorText = nil
        if openSessionIDs.contains(id) {
            loadedSessionID = id
            restoreSessionControls(for: id)
            if selectedSessionID == id { evaluation.select(id) }
            return
        }
        configOptions = []
        availableSessionModes = []
        currentModeID = nil
        do {
            let method = itemCache[id] == nil ? "session/load" : "session/resume"
            let result = try await client.request(method, params: [
                "sessionId": id, "cwd": workspace.path, "mcpServers": [],
            ])
            applySessionControls(from: result, for: id)
            loadedSessionID = id
            openSessionIDs.insert(id)
            if selectedSessionID == id { evaluation.select(id) }
        } catch {
            errorText = error.localizedDescription
        }
    }

    func closeSession(_ id: String) async {
        guard !isRunning else { return }
        do {
            _ = try await client.request("session/close", params: ["sessionId": id])
            openSessionIDs.remove(id)
            sessionControlCache.removeValue(forKey: id)
            if selectedSessionID == id {
                itemCache[id] = items
                evaluation.select(nil)
                selectedSessionID = nil
                loadedSessionID = nil
                items = []
                configOptions = []
                availableSessionModes = []
                currentModeID = nil
            }
        } catch { errorText = error.localizedDescription }
    }

    func send() async {
        let content = draft.trimmingCharacters(in: .whitespacesAndNewlines)
        guard (!content.isEmpty || !attachedFiles.isEmpty), let id = selectedSessionID, !isRunning else { return }
        let files = attachedFiles
        draft = ""
        attachedFiles = []
        errorText = nil
        isRunning = true
        items.append(ChatItem(kind: .user, text: ([content] + files.map { "@\($0.lastPathComponent)" })
            .filter { !$0.isEmpty }.joined(separator: "\n")))
        if let index = sessions.firstIndex(where: { $0.id == id }), sessions[index].title == "New conversation" {
            sessions[index].title = String((content.isEmpty ? files.first?.lastPathComponent ?? "Conversation" : content).prefix(48))
        }
        do {
            var blocks: [[String: Any]] = []
            if !content.isEmpty { blocks.append(["type": "text", "text": content]) }
            blocks.append(contentsOf: files.map { ["type": "resource_link", "name": $0.lastPathComponent,
                                                   "uri": $0.absoluteString] })
            _ = try await client.request("session/prompt", params: [
                "sessionId": id,
                "prompt": blocks,
            ])
        } catch {
            errorText = error.localizedDescription
        }
        isRunning = false
        itemCache[id] = items
    }

    func cancel() {
        guard let id = selectedSessionID else { return }
        do { try client.notify("session/cancel", params: ["sessionId": id]) }
        catch { errorText = error.localizedDescription }
        if permission != nil { resolvePermission(nil) }
    }

    func setConfigOption(_ optionID: String, value: String) async {
        guard let id = selectedSessionID else { return }
        do {
            let result = try await client.request("session/set_config_option", params: [
                "sessionId": id, "configId": optionID, "value": value,
            ])
            configOptions = result["configOptions"] as? [[String: Any]] ?? configOptions
            cacheSessionControls(for: id)
        } catch { errorText = error.localizedDescription }
    }

    func setSessionMode(_ modeID: String) async {
        guard let id = selectedSessionID,
              availableSessionModes.contains(where: { $0["id"] as? String == modeID }) else { return }
        do {
            _ = try await client.request("session/set_mode", params: [
                "sessionId": id, "modeId": modeID,
            ])
            currentModeID = modeID
            cacheSessionControls(for: id)
        } catch { errorText = error.localizedDescription }
    }

    private func applySessionControls(from result: [String: Any], for sessionID: String) {
        configOptions = result["configOptions"] as? [[String: Any]] ?? []
        let modes = result["modes"] as? [String: Any] ?? [:]
        availableSessionModes = modes["availableModes"] as? [[String: Any]] ?? []
        currentModeID = modes["currentModeId"] as? String
        cacheSessionControls(for: sessionID)
    }

    private func restoreSessionControls(for sessionID: String) {
        let controls = sessionControlCache[sessionID] ?? SessionControlState()
        configOptions = controls.configOptions
        availableSessionModes = controls.modes
        currentModeID = controls.currentModeID
    }

    private func cacheSessionControls(for sessionID: String) {
        sessionControlCache[sessionID] = SessionControlState(
            configOptions: configOptions,
            modes: availableSessionModes,
            currentModeID: currentModeID
        )
    }

    func resolvePermission(_ optionID: String?) {
        guard let permission else { return }
        if permission.method != "session/request_permission" {
            self.permission = nil
            if !permissionQueue.isEmpty { self.permission = permissionQueue.removeFirst() }
            guard optionID != nil else {
                try? client.reject(id: permission.id, message: "The request was declined", code: -32000)
                return
            }
            Task {
                do {
                    let result = try await services.handle(permission.method, params: permission.params, workspace: workspace)
                    try client.respond(id: permission.id, result: result)
                } catch {
                    try? client.reject(id: permission.id, message: error.localizedDescription)
                }
            }
            return
        }
        let outcome: [String: Any] = optionID.map { ["outcome": "selected", "optionId": $0] }
            ?? ["outcome": "cancelled"]
        do { try client.respond(id: permission.id, result: ["outcome": outcome]) }
        catch { errorText = error.localizedDescription }
        self.permission = nil
        if !permissionQueue.isEmpty { self.permission = permissionQueue.removeFirst() }
    }

    private func handleRequest(_ id: Int, method: String, params: [String: Any]) {
        if method == "session/request_permission" {
            let tool = params["toolCall"] as? [String: Any] ?? [:]
            let title = tool["title"] as? String ?? "Run a tool"
            let detail = (tool["rawInput"] as? [String: Any]).flatMap { input in
                (try? JSONSerialization.data(withJSONObject: input, options: .prettyPrinted))
                    .flatMap { String(data: $0, encoding: .utf8) }
            } ?? ""
            let options = (params["options"] as? [[String: Any]] ?? []).compactMap { option -> (id: String, name: String)? in
                guard let optionID = option["optionId"] as? String,
                      let name = option["name"] as? String else { return nil }
                return (optionID, name)
            }
            let prompt = PermissionPrompt(id: id, title: title, detail: detail, options: options,
                                          method: method, params: params)
            if permission == nil { permission = prompt } else { permissionQueue.append(prompt) }
        } else if method == "fs/write_text_file" || method == "terminal/create" {
            let title = method == "fs/write_text_file" ? "Write a workspace file" : "Start a terminal command"
            let detail = Self.jsonDetail(params) ?? ""
            let prompt = PermissionPrompt(id: id, title: title, detail: detail,
                                          options: [("allow_once", "Allow once")],
                                          method: method, params: params)
            if permission == nil { permission = prompt } else { permissionQueue.append(prompt) }
        } else {
            Task {
                do {
                    let result = try await services.handle(method, params: params, workspace: workspace)
                    try client.respond(id: id, result: result)
                } catch ServiceError.unknown {
                    try? client.rejectUnknownRequest(id: id)
                } catch {
                    try? client.reject(id: id, message: error.localizedDescription)
                }
            }
        }
    }

    private func handleNotification(_ method: String, params: [String: Any]) {
        guard method == "session/update",
              let sessionID = params["sessionId"] as? String,
              sessionID == selectedSessionID,
              let update = params["update"] as? [String: Any],
              let kind = update["sessionUpdate"] as? String else { return }
        switch kind {
        case "user_message_chunk":
            // A live prompt is already shown optimistically; replay needs this.
            if !isRunning { appendChunk(Self.contentText(update), kind: .user) }
        case "agent_message_chunk": appendChunk(Self.contentText(update), kind: .assistant)
        case "agent_thought_chunk": appendChunk(Self.contentText(update), kind: .thought)
        case "tool_call", "tool_call_update":
            let toolID = update["toolCallId"] as? String
            let title = update["title"] as? String
            let status = update["status"] as? String
            if let toolID, let index = items.firstIndex(where: { $0.toolID == toolID }) {
                if let title { items[index].text = title }
                if let status { items[index].status = status }
                if let toolKind = update["kind"] as? String { items[index].toolKind = toolKind }
                if let content = update["content"] as? [[String: Any]] {
                    items[index].detail = Self.toolDetail(content)
                }
                if items[index].detail == nil {
                    items[index].detail = Self.jsonDetail(update["rawOutput"] ?? update["rawInput"])
                }
                if let path = (update["locations"] as? [[String: Any]])?.first?["path"] as? String {
                    items[index].path = path
                }
            } else {
                items.append(ChatItem(kind: .tool, text: title ?? "Tool call", status: status, toolID: toolID,
                                      toolKind: update["kind"] as? String,
                                      detail: Self.toolDetail(update["content"] as? [[String: Any]] ?? [])
                                        ?? Self.jsonDetail(update["rawOutput"] ?? update["rawInput"]),
                                      path: (update["locations"] as? [[String: Any]])?.first?["path"] as? String))
            }
        case "plan":
            plan = update["entries"] as? [[String: Any]] ?? []
        case "config_option_update":
            configOptions = update["configOptions"] as? [[String: Any]] ?? []
            cacheSessionControls(for: sessionID)
        case "available_commands_update":
            availableCommands = update["availableCommands"] as? [[String: Any]] ?? []
        case "current_mode_update":
            currentModeID = update["currentModeId"] as? String
            cacheSessionControls(for: sessionID)
        case "session_info_update":
            if let title = update["title"] as? String,
               let index = sessions.firstIndex(where: { $0.id == sessionID }) { sessions[index].title = title }
        case "usage_update": usage = update
        default: break
        }
        itemCache[sessionID] = items
    }

    private func appendChunk(_ text: String, kind: ChatItem.Kind) {
        guard !text.isEmpty else { return }
        if let last = items.indices.last, items[last].kind == kind {
            items[last].text += text
        } else {
            items.append(ChatItem(kind: kind, text: text))
        }
    }

    private static func contentText(_ update: [String: Any]) -> String {
        guard let content = update["content"] as? [String: Any] else { return "" }
        switch content["type"] as? String {
        case "text": return content["text"] as? String ?? ""
        case "resource_link": return (content["uri"] as? String).map { "@\($0)" } ?? ""
        case "image": return "[Image]"
        case "audio": return "[Audio]"
        case "resource": return "[Resource]"
        default: return ""
        }
    }

    static func toolDetail(_ blocks: [[String: Any]]) -> String? {
        let parts = blocks.compactMap { block -> String? in
            switch block["type"] as? String {
            case "content":
                let content = block["content"] as? [String: Any] ?? [:]
                return content["text"] as? String ?? (content["type"] as? String).map { "[\($0) content]" }
            case "diff":
                let path = block["path"] as? String ?? "File"
                let old = block["oldText"] as? String
                let new = block["newText"] as? String ?? ""
                return "\(path)\n\(old.map { "− \($0)\n" } ?? "")+ \(new)"
            case "terminal":
                return (block["terminalId"] as? String).map { "Terminal \($0)" }
            default: return nil
            }
        }
        return parts.isEmpty ? nil : parts.joined(separator: "\n\n")
    }

    private static func jsonDetail(_ value: Any?) -> String? {
        guard let value, JSONSerialization.isValidJSONObject(value),
              let data = try? JSONSerialization.data(withJSONObject: value, options: [.prettyPrinted, .sortedKeys]) else {
            return value as? String
        }
        return String(data: data, encoding: .utf8)
    }

    private static func sessionTitle(_ entry: [String: Any]) -> String {
        if let title = entry["title"] as? String, !title.isEmpty { return title }
        let id = entry["sessionId"] as? String ?? ""
        return "Conversation \(id.prefix(8))"
    }
}
