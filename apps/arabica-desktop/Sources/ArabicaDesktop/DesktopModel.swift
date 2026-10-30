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

struct DesktopSession: Identifiable, Hashable {
    let id: String
    let cwd: String
    var title: String
    var updatedAt: String?
}

struct ProjectGroup: Identifiable {
    var id: String { path }
    let name: String
    let path: String
    var sessions: [DesktopSession]
}

struct PermissionPrompt: Identifiable {
    let id: ACPRequestID
    let title: String
    let detail: String
    let options: [(id: String, name: String, kind: String?)]
    let method: String
    let params: [String: Any]
    var workspace: URL? = nil

    /// ACP permission option kinds, read from `kind` and falling back to the
    /// option ID for agents that omit it.
    static func optionKind(_ option: (id: String, name: String, kind: String?)) -> String {
        option.kind ?? option.id
    }

    /// Dialog order: rejections on the left, Allow once last so it sits in
    /// the macOS default-button position.
    var orderedOptions: [(id: String, name: String, kind: String?)] {
        let rank = ["reject_always": 0, "reject_once": 1, "allow_always": 2, "allow_once": 3]
        return options.enumerated()
            .sorted {
                let left = rank[Self.optionKind($0.element)] ?? 2
                let right = rank[Self.optionKind($1.element)] ?? 2
                return left == right ? $0.offset < $1.offset : left < right
            }
            .map(\.element)
    }

    var hasRejectOnceOption: Bool {
        options.contains { Self.optionKind($0) == "reject_once" }
    }
}

private struct SessionControlState {
    var configOptions: [[String: Any]] = []
    var modes: [[String: Any]] = []
    var currentModeID: String?
}

private struct ComposerDraft {
    var text = ""
    var files: [URL] = []
}

@MainActor
final class DesktopModel: ObservableObject {
    typealias SessionRequest = (String, [String: Any]) async throws -> [String: Any]
    typealias SessionNotification = (String, [String: Any]) throws -> Void

    @Published var workspace: URL? { didSet { syncSidebarActivity() } }
    @Published var sessions: [DesktopSession] = [] {
        didSet {
            sessionMutationGeneration = UUID()
            syncSidebarActivity()
        }
    }
    @Published var selectedSessionID: String? {
        didSet {
            guard selectedSessionID != oldValue else { return }
            queuedSendSessionID = nil
            let previous = ComposerDraft(text: draft, files: attachedFiles)
            if let oldValue { sessionDrafts[oldValue] = previous } else { unassignedDraft = previous }
            let next =
                selectedSessionID.flatMap { sessionDrafts[$0] }
                ?? (selectedSessionID == nil ? unassignedDraft : ComposerDraft())
            draft = next.text
            attachedFiles = next.files
        }
    }
    @Published var items: [ChatItem] = []
    @Published var draft = "" {
        didSet {
            if draft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty, attachedFiles.isEmpty {
                queuedSendSessionID = nil
            }
        }
    }
    /// The conversation whose draft sends as soon as its current run
    /// completes. Cleared by switching conversations, emptying the draft,
    /// or a run that fails or is stopped.
    @Published private(set) var queuedSendSessionID: String?
    var isSendQueued: Bool { queuedSendSessionID != nil && queuedSendSessionID == selectedSessionID }
    @Published private(set) var composerFocusRequest = UUID()
    @Published var attachedFiles: [URL] = []
    @Published private var activeRuns: [String: UUID] = [:] { didSet { syncSidebarActivity() } }
    @Published private var stoppingRuns: [String: UUID] = [:]
    @Published private var pendingSessionControls: [String: UUID] = [:] { didSet { syncSidebarActivity() } }
    @Published private var sessionConfigurationFeedback: [String: ComposerConfigurationFeedback] = [:]
    @Published private var sessionOutcomes: [String: SidebarSessionStatus] = [:]
    @Published private var sessionErrors: [String: String] = [:]
    @Published private var unreadSessionIDs = Set<String>()
    @Published private var closingSessionIDs = Set<String>() { didSet { syncSidebarActivity() } }
    @Published private var renamingSessionIDs = Set<String>() { didSet { syncSidebarActivity() } }
    @Published private var activeClientServices: [UUID: String] = [:] { didSet { syncSidebarActivity() } }
    @Published private(set) var canRenameSessions = false
    var composerConfigurationFeedback: ComposerConfigurationFeedback? {
        selectedSessionID.flatMap { sessionConfigurationFeedback[$0] }
    }
    var hasActiveRuns: Bool { !activeRuns.isEmpty }
    var hasActivityPreventingRelaunch: Bool { isConnecting || hasActiveSessionWork }

    var isRunning: Bool {
        selectedSessionID.map { activeRuns[$0] != nil } ?? false
    }
    func isRunning(sessionID: String) -> Bool { activeRuns[sessionID] != nil }
    var isStopping: Bool {
        guard let id = selectedSessionID, let run = activeRuns[id] else { return false }
        return stoppingRuns[id] == run
    }
    var isUpdatingSessionConfiguration: Bool {
        selectedSessionID.map { pendingSessionControls[$0] != nil } ?? false
    }
    var canChangeSessionConfiguration: Bool {
        isSessionReady && !isRunning && !isWaitingForPermission && !isUpdatingSessionConfiguration
    }
    @Published var isConnecting = false
    @Published var isConnected = false
    @Published private(set) var isLoadingSession = false
    @Published private(set) var isCreatingSession = false
    @Published private(set) var creatingProjectPath: String?
    @Published var errorText: String?
    @Published var permission: PermissionPrompt? { didSet { syncSidebarActivity() } }
    @Published var configOptions: [[String: Any]] = []
    @Published var availableSessionModes: [[String: Any]] = []
    @Published var plan: [[String: Any]] = []
    @Published var usage: [String: Any] = [:]
    @Published var availableCommands: [[String: Any]] = []
    @Published var currentModeID: String?

    var isSessionReady: Bool {
        selectedSessionID != nil && selectedSessionID == loadedSessionID && isConnected && !isConnecting
            && !isLoadingSession
    }

    var canSendMessage: Bool {
        isSessionReady && !isRunning && !isWaitingForPermission && !isUpdatingSessionConfiguration
            && (!draft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || !attachedFiles.isEmpty)
    }

    let evaluation = EvaluationModel()
    let sidebarLibrary: SidebarLibrary
    private var libraryObservation: AnyCancellable?
    private let client: ACPClient
    private let sessionRequest: SessionRequest
    private let sessionNotification: SessionNotification
    private let services = ACPClientServices()
    private var loadedSessionID: String?
    @Published private var openSessionIDs = Set<String>()
    private var itemCache: [String: [ChatItem]] = [:]
    private var sessionControlCache: [String: SessionControlState] = [:]
    @Published private var permissionQueue: [PermissionPrompt] = [] { didSet { syncSidebarActivity() } }
    private var sessionDrafts: [String: ComposerDraft] = [:]
    private var unassignedDraft = ComposerDraft()
    private var connectionGeneration = UUID()
    private var navigationGeneration = UUID()
    @Published private var sessionLoadGenerations: [String: UUID] = [:] { didSet { syncSidebarActivity() } }
    private var sessionWorkspaces: [String: URL] = [:]
    private let activityOwner = UUID()
    private var isClosed = false
    private var sessionMutationGeneration = UUID()
    private var cachedProjectGroups: ProjectGroupsCache?

    private struct ProjectGroupsCache {
        let sessionGeneration: UUID
        let projectPaths: [String]
        let hiddenProjectPaths: Set<String>
        let workspacePath: String?
        let groups: [ProjectGroup]
    }

    private static let activityFormatter = ISO8601DateFormatter()
    private static let fractionalActivityFormatter: ISO8601DateFormatter = {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return formatter
    }()

    init(
        restoreWorkspace: Bool = true, sessionRequest: SessionRequest? = nil,
        sessionNotification: SessionNotification? = nil, sidebarLibrary: SidebarLibrary? = nil
    ) {
        let client = ACPClient()
        self.client = client
        let library = sidebarLibrary ?? .shared
        self.sidebarLibrary = library
        self.sessionRequest =
            sessionRequest ?? { method, params in
                try await client.request(method, params: params)
            }
        self.sessionNotification =
            sessionNotification ?? { method, params in
                try client.notify(method, params: params)
            }
        libraryObservation = library.objectWillChange.sink { [weak self] in self?.objectWillChange.send() }
        client.onNotification = { [weak self] method, params in
            self?.handleNotification(method, params: params)
        }
        client.onRequest = { [weak self] id, method, params in
            self?.handleRequest(id, method: method, params: params)
        }
        client.onExit = { [weak self] in
            self?.recordInterruptedRuns()
            self?.evaluation.reset()
            self?.services.reset()
            self?.activeRuns = [:]
            self?.stoppingRuns = [:]
            self?.pendingSessionControls = [:]
            self?.sessionConfigurationFeedback = [:]
            self?.navigationGeneration = UUID()
            self?.isLoadingSession = false
            self?.isCreatingSession = false
            self?.creatingProjectPath = nil
            self?.loadedSessionID = nil
            self?.openSessionIDs = []
            self?.canRenameSessions = false
            self?.sessionLoadGenerations = [:]
            self?.activeClientServices = [:]
            if self?.isConnected == true, self?.isConnecting != true {
                self?.errorText = "Arabica stopped. Reopen the workspace to reconnect."
            }
            self?.isConnected = false
        }
        if restoreWorkspace, let path = UserDefaults.standard.string(forKey: "lastWorkspace"),
            FileManager.default.fileExists(atPath: path)
        {
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

    func closeWindow() {
        isClosed = true
        connectionGeneration = UUID()
        navigationGeneration = UUID()
        isConnected = false
        isConnecting = false
        isLoadingSession = false
        isCreatingSession = false
        creatingProjectPath = nil
        loadedSessionID = nil
        sessionLoadGenerations = [:]
        activeRuns = [:]
        stoppingRuns = [:]
        pendingSessionControls = [:]
        sessionConfigurationFeedback = [:]
        activeClientServices = [:]
        permission = nil
        permissionQueue = []
        evaluation.reset()
        services.reset()
        client.stop()
        canRenameSessions = false
        sidebarLibrary.clearActivity(owner: activityOwner)
    }

    func chooseFiles() {
        let panel = NSOpenPanel()
        panel.canChooseDirectories = false
        panel.canChooseFiles = true
        panel.allowsMultipleSelection = true
        panel.prompt = "Attach"
        if panel.runModal() == .OK { attachFiles(panel.urls) }
    }

    func attachFiles(_ urls: [URL]) {
        var seen = Set(attachedFiles.map(\.standardizedFileURL))
        attachedFiles.append(contentsOf: urls.filter { seen.insert($0.standardizedFileURL).inserted })
    }

    func openWorkspace(_ url: URL) async {
        guard !isConnecting else { return }
        if isConnected {
            activateProject(url.path)
            return
        }
        await connectWorkspace(url)
    }

    func reconnectWorkspace(_ url: URL) async {
        guard !isConnecting else { return }
        guard !hasActiveSessionWork else {
            errorText =
                "Finish or stop active tasks and resolve approvals before reconnecting. Your saved settings will apply after reconnecting."
            return
        }
        await connectWorkspace(url)
    }

    private var hasActiveSessionWork: Bool {
        !activeRuns.isEmpty || !pendingSessionControls.isEmpty || !sessionLoadGenerations.isEmpty
            || isCreatingSession || !closingSessionIDs.isEmpty || !renamingSessionIDs.isEmpty || permission != nil
            || !permissionQueue.isEmpty || !activeClientServices.isEmpty
    }

    var canCreateSession: Bool { newConversationAvailability.isEnabled }

    func activateProject(_ path: String) {
        guard selectProjectAvailability(path).isEnabled else { return }
        let url = URL(fileURLWithPath: path).standardizedFileURL
        sidebarLibrary.addProject(url.path)
        if !isConnected {
            Task { await openWorkspace(url) }
            return
        }
        if workspace?.standardizedFileURL == url, selectedSessionID == nil { return }
        if let previous = selectedSessionID { itemCache[previous] = items }
        navigationGeneration = UUID()
        isLoadingSession = false
        workspace = url
        UserDefaults.standard.set(url.path, forKey: "lastWorkspace")
        selectedSessionID = nil
        loadedSessionID = nil
        items = []
        configOptions = []
        availableSessionModes = []
        currentModeID = nil
        plan = []
        usage = [:]
        availableCommands = []
        errorText = nil
        evaluation.select(nil)
    }

    func canRemoveProject(_ path: String) -> Bool {
        removeProjectAvailability(path).isEnabled
    }

    func removeProject(_ path: String) {
        guard canRemoveProject(path) else { return }
        sidebarLibrary.removeProject(path)
    }

    func isSessionOpen(_ id: String) -> Bool { openSessionIDs.contains(id) }

    func sidebarSessionBusyExplanation(_ id: String) -> String? {
        if hasPermission(for: id) { return "Resolve this conversation’s approval request first." }
        if sessionLoadGenerations[id] != nil { return "Wait for this conversation to finish loading." }
        if closingSessionIDs.contains(id) { return "This conversation is closing in this window." }
        if renamingSessionIDs.contains(id) { return "Wait for this conversation to finish renaming." }
        if activeRuns[id] != nil { return "Stop this conversation’s task or wait for it to finish." }
        if pendingSessionControls[id] != nil { return "Wait for this conversation’s settings change to finish." }
        if activeClientServices.values.contains(id) {
            return "Wait for this conversation’s current operation to finish."
        }
        if sidebarLibrary.busySessionIDs.contains(id) {
            return "This conversation is busy in another window. Wait for its current operation to finish."
        }
        return nil
    }

    func canCloseSession(_ id: String) -> Bool { closeSessionAvailability(id).isEnabled }

    func canArchiveSession(_ id: String) -> Bool { archiveSessionAvailability(id, archived: true).isEnabled }

    func archiveSession(_ id: String, archived: Bool) {
        let availability = archiveSessionAvailability(id, archived: archived)
        guard availability.isEnabled else {
            errorText = availability.explanation
            return
        }
        sidebarLibrary.setArchived(archived, for: id)
        if archived, selectedSessionID == id, let session = sessions.first(where: { $0.id == id }) {
            activateProject(session.cwd)
        }
    }

    func sessionStatus(_ id: String) -> SidebarSessionStatus {
        if hasPermission(for: id) { return .needsApproval }
        if sessionLoadGenerations[id] != nil { return .loading }
        if let run = activeRuns[id] { return stoppingRuns[id] == run ? .stopping : .running }
        return sessionOutcomes[id] ?? .idle
    }

    func sessionError(_ id: String) -> String? { sessionErrors[id] }

    func hasUnreadResult(_ id: String) -> Bool { unreadSessionIDs.contains(id) }

    private func hasPermission(for id: String) -> Bool {
        ([permission].compactMap { $0 } + permissionQueue).contains { $0.params["sessionId"] as? String == id }
    }

    func serviceWorkspace(for params: [String: Any]) throws -> URL {
        guard let id = params["sessionId"] as? String else { throw ServiceError.invalid("Missing session ID") }
        if let url = sessionWorkspaces[id] { return url }
        guard let session = sessions.first(where: { $0.id == id }), session.cwd.hasPrefix("/") else {
            throw ServiceError.invalid("Unknown session workspace")
        }
        let url = URL(fileURLWithPath: session.cwd)
        return url.standardizedFileURL
    }

    func performClientService(_ method: String, params: [String: Any]) async throws -> [String: Any] {
        try await services.handle(method, params: params, workspace: serviceWorkspace(for: params))
    }

    func configureSessionCapabilities(_ initialized: [String: Any]) {
        let capability = (initialized["_meta"] as? [String: Any])?["arabica.session"] as? [String: Any]
        canRenameSessions =
            capability?["schemaVersion"] as? Int == 1
            && capability?["rename"] as? String == "_arabica/session/rename"
    }

    func renameSession(_ id: String, title: String) async -> Bool {
        let title = title.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !title.isEmpty, title.unicodeScalars.count <= 200,
            !title.unicodeScalars.contains(where: CharacterSet.controlCharacters.contains)
        else {
            errorText = "Use a title of 1–200 characters without line breaks or control characters."
            return false
        }
        let availability = renameSessionAvailability(id)
        guard availability.isEnabled else {
            errorText = availability.explanation
            return false
        }
        let connection = connectionGeneration
        renamingSessionIDs.insert(id)
        defer { renamingSessionIDs.remove(id) }
        do {
            let result = try await sessionRequest("_arabica/session/rename", ["sessionId": id, "title": title])
            guard connectionGeneration == connection else { return false }
            guard result["sessionId"] as? String == id, let committed = result["title"] as? String, committed == title,
                let index = sessions.firstIndex(where: { $0.id == id })
            else { throw ACPError.invalidResponse }
            sessions[index].title = committed
            errorText = nil
            return true
        } catch {
            if connectionGeneration == connection { errorText = error.localizedDescription }
            return false
        }
    }

    private static func activityTimestamp() -> String { activityFormatter.string(from: Date()) }

    private static func activityDate(_ timestamp: String?) -> Date? {
        guard let timestamp else { return nil }
        return fractionalActivityFormatter.date(from: timestamp) ?? activityFormatter.date(from: timestamp)
    }

    private func touchSession(_ id: String) {
        guard let index = sessions.firstIndex(where: { $0.id == id }) else { return }
        var session = sessions.remove(at: index)
        session.updatedAt = Self.activityTimestamp()
        sessions.insert(session, at: 0)
    }

    private func recordInterruptedRuns() {
        for id in activeRuns.keys {
            sessionOutcomes[id] = .failed
            sessionErrors[id] = "The agent disconnected before this task finished. Reconnect to continue."
            if selectedSessionID != id { unreadSessionIDs.insert(id) }
        }
        for id in sessionLoadGenerations.keys {
            sessionOutcomes[id] = .loadFailed
            sessionErrors[id] = "The agent disconnected while loading this conversation. Reconnect and retry."
        }
        permission = nil
        permissionQueue = []
    }

    private func syncSidebarActivity() {
        guard !isClosed else {
            sidebarLibrary.clearActivity(owner: activityOwner)
            return
        }
        let requests = ([permission].compactMap { $0 } + permissionQueue).compactMap {
            $0.params["sessionId"] as? String
        }
        let busy = Set(activeRuns.keys).union(pendingSessionControls.keys).union(sessionLoadGenerations.keys)
            .union(closingSessionIDs).union(renamingSessionIDs).union(activeClientServices.values).union(requests)
        var paths = Set(
            busy.compactMap { id in sessionWorkspaces[id]?.path ?? sessions.first(where: { $0.id == id })?.cwd })
        if let workspace { paths.insert(workspace.standardizedFileURL.path) }
        sidebarLibrary.updateActivity(owner: activityOwner, sessionIDs: busy, projectPaths: paths)
    }

    private func connectWorkspace(_ url: URL) async {
        client.stop()
        let connection = UUID()
        connectionGeneration = connection
        navigationGeneration = UUID()
        isLoadingSession = false
        isCreatingSession = false
        creatingProjectPath = nil
        isConnecting = true
        isConnected = false
        errorText = nil
        evaluation.reset()
        services.reset()
        activeRuns = [:]
        stoppingRuns = [:]
        pendingSessionControls = [:]
        sessionConfigurationFeedback = [:]
        workspace = url
        sessions = []
        selectedSessionID = nil
        loadedSessionID = nil
        openSessionIDs = []
        sessionLoadGenerations = [:]
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
        sessionWorkspaces = [:]
        closingSessionIDs = []
        renamingSessionIDs = []
        canRenameSessions = false
        sidebarLibrary.addProject(url.path)
        do {
            try client.start(workspace: url)
            let initialized = try await client.initialize()
            guard connectionGeneration == connection else { return }
            configureSessionCapabilities(initialized)
            evaluation.connect(supported: EvaluationCapability.supported(by: initialized)) {
                [weak self] method, params in
                guard let self else { throw ACPError.disconnected }
                return try await self.client.request(method, params: params, timeoutSeconds: 10)
            }
            isConnected = true
            UserDefaults.standard.set(url.path, forKey: "lastWorkspace")
            await refreshSessions()
        } catch {
            guard connectionGeneration == connection else { return }
            if case ACPError.disconnected = error {
                errorText =
                    client.startupErrorMessage
                    ?? "Arabica's background process exited before ACP initialization. Check ~/.arabica/config.toml and reopen the workspace."
            } else {
                errorText = error.localizedDescription
            }
        }
        if connectionGeneration == connection { isConnecting = false }
    }

    static func projectName(for cwd: String) -> String {
        let parts = cwd.split(separator: "/")
        if let wtIndex = parts.firstIndex(of: "worktrees"), wtIndex + 1 < parts.count {
            return String(parts[wtIndex + 1])
        }
        if let pIndex = parts.firstIndex(of: "Projects"), pIndex + 1 < parts.count {
            return String(parts[pIndex + 1])
        }
        let last = URL(fileURLWithPath: cwd).lastPathComponent
        return last.isEmpty ? "Project" : last
    }

    var projectGroups: [ProjectGroup] {
        let projectPaths = sidebarLibrary.projectPaths
        let hiddenProjectPaths = sidebarLibrary.hiddenProjectPaths
        let activePath = workspace?.standardizedFileURL.path
        if let cachedProjectGroups,
            cachedProjectGroups.sessionGeneration == sessionMutationGeneration,
            cachedProjectGroups.projectPaths == projectPaths,
            cachedProjectGroups.hiddenProjectPaths == hiddenProjectPaths,
            cachedProjectGroups.workspacePath == activePath
        {
            return cachedProjectGroups.groups
        }

        // Model updates include every draft edit and streamed chunk. Reuse the sidebar
        // index until its own inputs change, and parse dates outside the sort comparator.
        var map: [String: [(session: DesktopSession, activity: Date)]] = [:]
        for session in sessions {
            let path = URL(fileURLWithPath: session.cwd).standardizedFileURL.path
            guard !hiddenProjectPaths.contains(path) else { continue }
            map[path, default: []].append((session, Self.activityDate(session.updatedAt) ?? .distantPast))
        }
        for path in projectPaths where !hiddenProjectPaths.contains(path) {
            if map[path] == nil { map[path] = [] }
        }
        if let activePath, map[activePath] == nil { map[activePath] = [] }
        var groups = map.map { path, entries in
            ProjectGroup(
                name: Self.projectName(for: path), path: path,
                sessions: entries.sorted { $0.activity > $1.activity }.map(\.session))
        }
        groups.sort { a, b in
            if (a.path == activePath) != (b.path == activePath) { return a.path == activePath }
            let order = a.name.localizedStandardCompare(b.name)
            return order == .orderedSame ? a.path < b.path : order == .orderedAscending
        }
        cachedProjectGroups = ProjectGroupsCache(
            sessionGeneration: sessionMutationGeneration,
            projectPaths: projectPaths,
            hiddenProjectPaths: hiddenProjectPaths,
            workspacePath: activePath,
            groups: groups)
        return groups
    }

    func refreshSessions() async {
        guard isConnected else { return }
        let connection = connectionGeneration
        let snapshot = sessionMutationGeneration
        do {
            var entries: [[String: Any]] = []
            var cursor: String?
            var seen = Set<String>()
            repeat {
                var params: [String: Any] = [:]
                if let cursor { params["cursor"] = cursor }
                let result = try await sessionRequest("session/list", params)
                guard connectionGeneration == connection, sessionMutationGeneration == snapshot else { return }
                entries.append(contentsOf: result["sessions"] as? [[String: Any]] ?? [])
                cursor = result["nextCursor"] as? String
                if let cursor, !seen.insert(cursor).inserted { throw ACPError.invalidResponse }
            } while cursor != nil
            sessions = entries.compactMap { entry in
                guard let id = entry["sessionId"] as? String,
                    let cwd = entry["cwd"] as? String
                else { return nil }
                return DesktopSession(
                    id: id,
                    cwd: cwd,
                    title: Self.sessionTitle(entry),
                    updatedAt: entry["updatedAt"] as? String
                )
            }
        } catch {
            if connectionGeneration == connection { errorText = error.localizedDescription }
        }
    }

    func newSession(in cwd: String? = nil) async {
        let targetCwd = cwd ?? workspace?.path
        guard let targetCwd else {
            chooseWorkspace()
            return
        }
        guard canCreateSession else { return }
        let connection = connectionGeneration
        let navigation = UUID()
        navigationGeneration = navigation
        isLoadingSession = true
        isCreatingSession = true
        creatingProjectPath = URL(fileURLWithPath: targetCwd).standardizedFileURL.path
        defer {
            if connectionGeneration == connection {
                isCreatingSession = false
                creatingProjectPath = nil
                if navigationGeneration == navigation { isLoadingSession = false }
            }
        }
        errorText = nil
        let beganWithoutSession = selectedSessionID == nil
        do {
            let result = try await sessionRequest(
                "session/new",
                [
                    "cwd": targetCwd, "mcpServers": [],
                ])
            guard connectionGeneration == connection else { return }
            guard let id = result["sessionId"] as? String else { throw ACPError.invalidResponse }
            cacheSessionControls(from: result, for: id)
            openSessionIDs.insert(id)
            sessionWorkspaces[id] = URL(fileURLWithPath: targetCwd).standardizedFileURL
            sidebarLibrary.addProject(targetCwd)
            let newSession = DesktopSession(
                id: id, cwd: targetCwd, title: "New conversation", updatedAt: Self.activityTimestamp())
            if !sessions.contains(where: { $0.id == id }) { sessions.insert(newSession, at: 0) }
            guard navigationGeneration == navigation else { return }
            restoreSessionControls(for: id)
            if workspace?.path != targetCwd {
                workspace = URL(fileURLWithPath: targetCwd)
                UserDefaults.standard.set(targetCwd, forKey: "lastWorkspace")
            }
            if let previous = selectedSessionID { itemCache[previous] = items }
            let pendingDraft =
                beganWithoutSession && selectedSessionID == nil
                ? ComposerDraft(text: draft, files: attachedFiles) : nil
            selectedSessionID = id
            if let pendingDraft {
                draft = pendingDraft.text
                attachedFiles = pendingDraft.files
                unassignedDraft = ComposerDraft()
            }
            plan = []
            usage = [:]
            availableCommands = []
            loadedSessionID = id
            items = []
            composerFocusRequest = UUID()
            if selectedSessionID == id { evaluation.select(id) }
        } catch {
            if connectionGeneration == connection, navigationGeneration == navigation {
                errorText = error.localizedDescription
            }
        }
    }

    func selectSession(_ id: String) async {
        guard let session = sessions.first(where: { $0.id == id }) else { return }
        if id == selectedSessionID, id == loadedSessionID, !isLoadingSession { return }
        if id == selectedSessionID, isLoadingSession, sessionLoadGenerations[id] != nil { return }
        let connection = connectionGeneration
        let navigation = UUID()
        navigationGeneration = navigation
        isLoadingSession = true
        defer {
            if connectionGeneration == connection, selectedSessionID == id, sessionLoadGenerations[id] == nil {
                isLoadingSession = false
            }
        }
        if let previous = selectedSessionID { itemCache[previous] = items }
        evaluation.select(nil)
        selectedSessionID = id
        unreadSessionIDs.remove(id)
        loadedSessionID = nil
        plan = []
        usage = [:]
        availableCommands = []
        items = itemCache[id] ?? []
        errorText = sessionErrors[id]
        sidebarLibrary.addProject(session.cwd)
        sessionWorkspaces[id] = sessionWorkspaces[id] ?? URL(fileURLWithPath: session.cwd).standardizedFileURL
        if workspace?.path != session.cwd {
            workspace = URL(fileURLWithPath: session.cwd)
            UserDefaults.standard.set(session.cwd, forKey: "lastWorkspace")
        }
        if openSessionIDs.contains(id) {
            loadedSessionID = id
            restoreSessionControls(for: id)
            if selectedSessionID == id { evaluation.select(id) }
            return
        }
        configOptions = []
        availableSessionModes = []
        currentModeID = nil
        // Reuse the original restore when navigation returns before it finishes.
        // Its result belongs to the conversation, rather than the navigation that started it.
        guard sessionLoadGenerations[id] == nil else { return }
        sessionLoadGenerations[id] = navigation
        if sessionOutcomes[id] == .loadFailed {
            sessionOutcomes.removeValue(forKey: id)
            sessionErrors.removeValue(forKey: id)
            errorText = nil
        }
        defer {
            if sessionLoadGenerations[id] == navigation { sessionLoadGenerations.removeValue(forKey: id) }
        }
        do {
            let method = itemCache[id] == nil ? "session/load" : "session/resume"
            let result = try await sessionRequest(
                method,
                [
                    "sessionId": id, "cwd": session.cwd, "mcpServers": [],
                ])
            guard connectionGeneration == connection, sessionLoadGenerations[id] == navigation else { return }
            cacheSessionControls(from: result, for: id)
            openSessionIDs.insert(id)
            if sessionOutcomes[id] == .closed { sessionOutcomes.removeValue(forKey: id) }
            guard selectedSessionID == id else { return }
            loadedSessionID = id
            restoreSessionControls(for: id)
            evaluation.select(id)
        } catch {
            if connectionGeneration == connection, sessionLoadGenerations[id] == navigation {
                sessionOutcomes[id] = .loadFailed
                sessionErrors[id] = error.localizedDescription
                if selectedSessionID == id {
                    errorText = error.localizedDescription
                } else {
                    unreadSessionIDs.insert(id)
                }
            }
        }
    }

    func closeSession(_ id: String) async {
        guard canCloseSession(id) else { return }
        closingSessionIDs.insert(id)
        defer { closingSessionIDs.remove(id) }
        let connection = connectionGeneration
        do {
            _ = try await sessionRequest("session/close", ["sessionId": id])
            guard connectionGeneration == connection else { return }
            openSessionIDs.remove(id)
            sessionLoadGenerations.removeValue(forKey: id)
            sessionControlCache.removeValue(forKey: id)
            pendingSessionControls.removeValue(forKey: id)
            sessionConfigurationFeedback.removeValue(forKey: id)
            services.release(sessionID: id)
            sessionOutcomes[id] = .closed
            sessionErrors.removeValue(forKey: id)
            unreadSessionIDs.remove(id)
            if selectedSessionID == id {
                navigationGeneration = UUID()
                isLoadingSession = false
                isCreatingSession = false
                creatingProjectPath = nil
                itemCache[id] = items
                evaluation.select(nil)
                selectedSessionID = nil
                loadedSessionID = nil
                items = []
                configOptions = []
                availableSessionModes = []
                currentModeID = nil
                errorText = nil
            }
        } catch {
            if connectionGeneration == connection {
                errorText = error.localizedDescription
            }
        }
    }

    /// Return in the composer: sends when possible, creates the first
    /// conversation for a draft written before one existed, and queues the
    /// draft while the agent is still working.
    func submitDraft() async {
        let hasDraft = !draft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || !attachedFiles.isEmpty
        guard hasDraft else { return }
        if selectedSessionID == nil {
            guard isConnected, !isConnecting, !isCreatingSession, workspace != nil else { return }
            await newSession()
            await send()
            return
        }
        if isRunning, !isStopping, let id = selectedSessionID {
            queuedSendSessionID = id
            return
        }
        await send()
    }

    func cancelQueuedSend() { queuedSendSessionID = nil }

    func send() async {
        let content = draft.trimmingCharacters(in: .whitespacesAndNewlines)
        guard canSendMessage, let id = selectedSessionID else { return }
        let files = attachedFiles
        draft = ""
        attachedFiles = []
        errorText = nil
        sessionConfigurationFeedback.removeValue(forKey: id)
        let runID = beginRun(for: id)
        items.append(
            ChatItem(
                kind: .user,
                text: ([content] + files.map { "@\($0.lastPathComponent)" })
                    .filter { !$0.isEmpty }.joined(separator: "\n")))
        itemCache[id] = items
        if let index = sessions.firstIndex(where: { $0.id == id }), sessions[index].title == "New conversation" {
            sessions[index].title = String(
                (content.isEmpty ? files.first?.lastPathComponent ?? "Conversation" : content).prefix(48))
        }
        do {
            var blocks: [[String: Any]] = []
            if !content.isEmpty { blocks.append(["type": "text", "text": content]) }
            blocks.append(
                contentsOf: files.map {
                    [
                        "type": "resource_link", "name": $0.lastPathComponent,
                        "uri": $0.absoluteString,
                    ]
                })
            let result = try await sessionRequest(
                "session/prompt",
                [
                    "sessionId": id,
                    "prompt": blocks,
                ])
            let stopReason = result["stopReason"] as? String
            finishRun(
                for: id, runID: runID,
                outcome: ["cancelled", "canceled"].contains(stopReason ?? "") ? .cancelled : .completed)
        } catch {
            finishRun(for: id, runID: runID, outcome: .failed, error: error.localizedDescription)
        }
    }

    @discardableResult
    func beginRun(for sessionID: String) -> UUID {
        let runID = UUID()
        activeRuns[sessionID] = runID
        sidebarLibrary.setArchived(false, for: sessionID)
        sessionOutcomes.removeValue(forKey: sessionID)
        sessionErrors.removeValue(forKey: sessionID)
        unreadSessionIDs.remove(sessionID)
        touchSession(sessionID)
        return runID
    }

    func finishRun(for sessionID: String, runID: UUID, outcome: SidebarSessionStatus = .completed, error: String? = nil)
    {
        guard activeRuns[sessionID] == runID else { return }
        activeRuns.removeValue(forKey: sessionID)
        stoppingRuns.removeValue(forKey: sessionID)
        sessionOutcomes[sessionID] = outcome
        if let error { sessionErrors[sessionID] = error }
        if selectedSessionID != sessionID { unreadSessionIDs.insert(sessionID) } else if let error { errorText = error }
        touchSession(sessionID)
        if selectedSessionID == sessionID { itemCache[sessionID] = items }
        if queuedSendSessionID == sessionID {
            queuedSendSessionID = nil
            if outcome == .completed, selectedSessionID == sessionID, error == nil {
                Task { await send() }
            }
        }
    }

    func cancel() {
        guard let id = selectedSessionID, let run = activeRuns[id], !isStopping else { return }
        do {
            try sessionNotification("session/cancel", ["sessionId": id])
            stoppingRuns[id] = run
            if permission?.params["sessionId"] as? String == id { resolvePermission(nil) }
        } catch { errorText = error.localizedDescription }
    }

    func setConfigOption(_ optionID: String, value: String) async {
        guard let id = selectedSessionID, canChangeSessionConfiguration else { return }
        let option = composerConfigurationOptions.first { $0.id == optionID }
        guard option?.currentValue != value else { return }
        let name = option?.name ?? optionID
        let valueName = option?.name(for: value) ?? value
        let connection = connectionGeneration
        let update = UUID()
        pendingSessionControls[id] = update
        errorText = nil
        sessionConfigurationFeedback[id] = ComposerConfigurationFeedback(
            kind: .applying, title: "Applying \(name): \(valueName)…",
            detail: "Waiting for the agent to confirm this conversation's setting.")
        defer { if pendingSessionControls[id] == update { pendingSessionControls.removeValue(forKey: id) } }
        do {
            let result = try await sessionRequest(
                "session/set_config_option",
                [
                    "sessionId": id, "configId": optionID, "value": value,
                ])
            guard connectionGeneration == connection, pendingSessionControls[id] == update else { return }
            guard let options = result["configOptions"] as? [[String: Any]] else {
                throw ACPError.invalidResponse
            }
            var controls = sessionControlCache[id] ?? SessionControlState()
            controls.configOptions = options
            sessionControlCache[id] = controls
            if selectedSessionID == id, loadedSessionID == id {
                configOptions = controls.configOptions
            }
            guard let confirmed = options.first(where: { $0["id"] as? String == optionID }),
                confirmed["currentValue"] as? String == value
            else {
                throw ACPError.remote(
                    "The agent did not confirm \(name): \(valueName). Check the current setting and try again.")
            }
            sessionConfigurationFeedback[id] = ComposerConfigurationFeedback(
                kind: .applied, title: "\(name): \(valueName) applied",
                detail: "The current settings below apply to the next message in this conversation.")
        } catch {
            if connectionGeneration == connection, pendingSessionControls[id] == update {
                sessionConfigurationFeedback[id] = ComposerConfigurationFeedback(
                    kind: .failed, title: "\(name) change failed", detail: error.localizedDescription)
                if selectedSessionID == id { errorText = error.localizedDescription }
            }
        }
    }

    func applyConfigOptions(_ options: [[String: Any]], for sessionID: String) {
        guard selectedSessionID == sessionID || sessionControlCache[sessionID] != nil else { return }
        var controls = sessionControlCache[sessionID] ?? SessionControlState()
        controls.configOptions = options
        sessionControlCache[sessionID] = controls
        if selectedSessionID == sessionID { configOptions = options }
    }

    func setSessionMode(_ modeID: String) async {
        guard let id = selectedSessionID,
            canChangeSessionConfiguration,
            currentModeID != modeID,
            availableSessionModes.contains(where: { $0["id"] as? String == modeID })
        else { return }
        let connection = connectionGeneration
        let update = UUID()
        let modeName = availableSessionModes.first { $0["id"] as? String == modeID }?["name"] as? String ?? modeID
        pendingSessionControls[id] = update
        errorText = nil
        sessionConfigurationFeedback[id] = ComposerConfigurationFeedback(
            kind: .applying, title: "Applying Mode: \(modeName)…",
            detail: "Waiting for the agent to confirm this conversation's setting.")
        defer { if pendingSessionControls[id] == update { pendingSessionControls.removeValue(forKey: id) } }
        do {
            _ = try await sessionRequest(
                "session/set_mode",
                [
                    "sessionId": id, "modeId": modeID,
                ])
            guard connectionGeneration == connection, pendingSessionControls[id] == update else { return }
            var controls = sessionControlCache[id] ?? SessionControlState()
            controls.currentModeID = modeID
            sessionControlCache[id] = controls
            if selectedSessionID == id, loadedSessionID == id { currentModeID = modeID }
            sessionConfigurationFeedback[id] = ComposerConfigurationFeedback(
                kind: .applied, title: "Mode: \(modeName) applied",
                detail: "The current settings below apply to the next message in this conversation.")
        } catch {
            if connectionGeneration == connection, pendingSessionControls[id] == update {
                sessionConfigurationFeedback[id] = ComposerConfigurationFeedback(
                    kind: .failed, title: "Mode change failed", detail: error.localizedDescription)
                if selectedSessionID == id { errorText = error.localizedDescription }
            }
        }
    }

    private func cacheSessionControls(from result: [String: Any], for sessionID: String) {
        let modes = result["modes"] as? [String: Any] ?? [:]
        sessionControlCache[sessionID] = SessionControlState(
            configOptions: result["configOptions"] as? [[String: Any]] ?? [],
            modes: modes["availableModes"] as? [[String: Any]] ?? [],
            currentModeID: modes["currentModeId"] as? String
        )
    }

    private func restoreSessionControls(for sessionID: String) {
        let controls = sessionControlCache[sessionID] ?? SessionControlState()
        configOptions = controls.configOptions
        availableSessionModes = controls.modes
        currentModeID = controls.currentModeID
    }

    var isWaitingForPermission: Bool {
        guard let sessionID = selectedSessionID else { return false }
        return ([permission].compactMap { $0 } + permissionQueue).contains {
            $0.params["sessionId"] as? String == sessionID
        }
    }

    func dismissPermission(_ prompt: PermissionPrompt?) {
        if let prompt {
            permission = prompt
        } else {
            resolvePermission(nil)
        }
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
            let connection = connectionGeneration
            let service = UUID()
            activeClientServices[service] = permission.params["sessionId"] as? String
            Task {
                defer { activeClientServices.removeValue(forKey: service) }
                guard connectionGeneration == connection else { return }
                do {
                    guard let root = permission.workspace else {
                        throw ServiceError.invalid("Unknown session workspace")
                    }
                    let result = try await services.handle(
                        permission.method, params: permission.params, workspace: root)
                    guard connectionGeneration == connection else { return }
                    try client.respond(id: permission.id, result: result)
                } catch {
                    guard connectionGeneration == connection else { return }
                    try? client.reject(id: permission.id, message: error.localizedDescription)
                }
            }
            return
        }
        let outcome: [String: Any] =
            optionID.map { ["outcome": "selected", "optionId": $0] }
            ?? ["outcome": "cancelled"]
        do { try client.respond(id: permission.id, result: ["outcome": outcome]) } catch {
            errorText = error.localizedDescription
        }
        self.permission = nil
        if !permissionQueue.isEmpty { self.permission = permissionQueue.removeFirst() }
    }

    func handleRequest(_ id: ACPRequestID, method: String, params: [String: Any]) {
        let requestWorkspace = try? serviceWorkspace(for: params)
        if method == "session/request_permission" {
            let tool = params["toolCall"] as? [String: Any] ?? [:]
            let title =
                (tool["title"] as? String)
                ?? (params["title"] as? String)
                ?? "Run a tool"
            let rawInput = tool["rawInput"] ?? params["rawInput"]
            let detail = Self.jsonDetail(rawInput) ?? ""
            let options = (params["options"] as? [[String: Any]] ?? []).compactMap {
                option -> (id: String, name: String, kind: String?)? in
                guard let optionID = option["optionId"] as? String,
                    let name = option["name"] as? String
                else { return nil }
                return (optionID, name, option["kind"] as? String)
            }
            let prompt = PermissionPrompt(
                id: id, title: title, detail: detail, options: options,
                method: method, params: params, workspace: requestWorkspace)
            if permission == nil { permission = prompt } else { permissionQueue.append(prompt) }
        } else if method == "fs/write_text_file" || method == "terminal/create" {
            guard let requestWorkspace else {
                try? client.reject(id: id, message: "Unknown session workspace")
                return
            }
            let title = method == "fs/write_text_file" ? "Write a workspace file" : "Start a terminal command"
            let detail = requestWorkspace.path + "\n\n" + (Self.jsonDetail(params) ?? "")
            let prompt = PermissionPrompt(
                id: id, title: title, detail: detail,
                options: [("allow_once", "Allow once", "allow_once")],
                method: method, params: params, workspace: requestWorkspace)
            if permission == nil { permission = prompt } else { permissionQueue.append(prompt) }
        } else {
            guard
                ["fs/read_text_file", "terminal/output", "terminal/release", "terminal/kill", "terminal/wait_for_exit"]
                    .contains(method)
            else {
                try? client.rejectUnknownRequest(id: id)
                return
            }
            let connection = connectionGeneration
            let service = UUID()
            activeClientServices[service] = params["sessionId"] as? String
            Task {
                defer { activeClientServices.removeValue(forKey: service) }
                guard connectionGeneration == connection else { return }
                do {
                    guard let requestWorkspace else { throw ServiceError.invalid("Unknown session workspace") }
                    let result = try await services.handle(method, params: params, workspace: requestWorkspace)
                    guard connectionGeneration == connection else { return }
                    try client.respond(id: id, result: result)
                } catch ServiceError.unknown {
                    try? client.rejectUnknownRequest(id: id)
                } catch {
                    guard connectionGeneration == connection else { return }
                    try? client.reject(id: id, message: error.localizedDescription)
                }
            }
        }
    }

    func handleNotification(_ method: String, params: [String: Any]) {
        guard method == "session/update",
            let sessionID = params["sessionId"] as? String,
            let update = params["update"] as? [String: Any],
            let kind = update["sessionUpdate"] as? String
        else { return }
        let isSelected = sessionID == selectedSessionID
        if kind == "session_info_update" {
            if let index = sessions.firstIndex(where: { $0.id == sessionID }) {
                if let title = update["title"] as? String { sessions[index].title = title }
                if let updatedAt = update["updatedAt"] as? String { sessions[index].updatedAt = updatedAt }
            }
            return
        }
        if kind == "config_option_update" {
            guard let options = update["configOptions"] as? [[String: Any]] else { return }
            applyConfigOptions(options, for: sessionID)
            if pendingSessionControls[sessionID] == nil { sessionConfigurationFeedback.removeValue(forKey: sessionID) }
            return
        }
        if kind == "current_mode_update" {
            guard let modeID = update["currentModeId"] as? String else { return }
            var controls = sessionControlCache[sessionID] ?? SessionControlState()
            controls.currentModeID = modeID
            sessionControlCache[sessionID] = controls
            if pendingSessionControls[sessionID] == nil { sessionConfigurationFeedback.removeValue(forKey: sessionID) }
            if isSelected { currentModeID = modeID }
            return
        }
        if !isSelected,
            ![
                "user_message_chunk", "agent_message_chunk", "agent_thought_chunk",
                "tool_call", "tool_call_update",
            ].contains(kind)
        {
            return
        }
        var items = isSelected ? self.items : itemCache[sessionID] ?? []
        switch kind {
        case "user_message_chunk":
            // A live prompt is already shown optimistically; replay needs this.
            if activeRuns[sessionID] == nil { Self.appendChunk(Self.contentText(update), kind: .user, to: &items) }
        case "agent_message_chunk": Self.appendChunk(Self.contentText(update), kind: .assistant, to: &items)
        case "agent_thought_chunk": Self.appendChunk(Self.contentText(update), kind: .thought, to: &items)
        case "tool_call", "tool_call_update":
            let toolID = update["toolCallId"] as? String
            let fields = (update["fields"] as? [String: Any]) ?? [:]
            let title = (fields["title"] as? String) ?? (update["title"] as? String)
            let status = (fields["status"] as? String) ?? (update["status"] as? String)
            let toolKind = (fields["kind"] as? String) ?? (update["kind"] as? String)
            let content = (fields["content"] as? [[String: Any]]) ?? (update["content"] as? [[String: Any]])
            let rawOutput = fields["rawOutput"] ?? update["rawOutput"]
            let rawInput = fields["rawInput"] ?? update["rawInput"]
            let locations = (fields["locations"] as? [[String: Any]]) ?? (update["locations"] as? [[String: Any]])
            let path = locations?.first?["path"] as? String

            let newDetail =
                (content != nil ? Self.toolDetail(content!) : nil)
                ?? Self.jsonDetail(rawOutput)
                ?? Self.jsonDetail(rawInput)

            if let toolID, let index = items.firstIndex(where: { $0.toolID == toolID }) {
                if let title { items[index].text = title }
                if let status { items[index].status = status }
                if let toolKind { items[index].toolKind = toolKind }
                if let newDetail, !newDetail.isEmpty { items[index].detail = newDetail }
                if let path { items[index].path = path }
            } else {
                items.append(
                    ChatItem(
                        kind: .tool,
                        text: title ?? "Tool call",
                        status: status,
                        toolID: toolID,
                        toolKind: toolKind,
                        detail: newDetail,
                        path: path
                    ))
            }
        case "plan":
            plan = update["entries"] as? [[String: Any]] ?? []
        case "available_commands_update":
            availableCommands = update["availableCommands"] as? [[String: Any]] ?? []
        case "session_info_update":
            if let title = update["title"] as? String,
                let index = sessions.firstIndex(where: { $0.id == sessionID })
            {
                sessions[index].title = title
            }
        case "usage_update": usage = update
        default: break
        }
        itemCache[sessionID] = items
        if isSelected { self.items = items }
    }

    private static func appendChunk(_ text: String, kind: ChatItem.Kind, to items: inout [ChatItem]) {
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
                if let content = block["content"] as? [String: Any] {
                    if let text = content["text"] as? String { return text }
                    if let type = content["type"] as? String { return "[\(type) content]" }
                } else if let text = block["content"] as? String {
                    return text
                } else if let text = block["text"] as? String {
                    return text
                }
                return nil
            case "diff":
                let path = block["path"] as? String ?? "File"
                let old = block["oldText"] as? String
                let new = block["newText"] as? String ?? ""
                return "\(path)\n\(old.map { "− \($0)\n" } ?? "")+ \(new)"
            case "terminal":
                return (block["terminalId"] as? String).map { "Terminal \($0)" }
            default:
                if let text = block["text"] as? String { return text }
                if let content = block["content"] as? String { return content }
                return Self.jsonDetail(block)
            }
        }
        let joined = parts.joined(separator: "\n\n").trimmingCharacters(in: .whitespacesAndNewlines)
        return joined.isEmpty ? nil : joined
    }

    private static func jsonDetail(_ value: Any?) -> String? {
        guard let value, JSONSerialization.isValidJSONObject(value),
            let data = try? JSONSerialization.data(withJSONObject: value, options: [.prettyPrinted, .sortedKeys])
        else {
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
