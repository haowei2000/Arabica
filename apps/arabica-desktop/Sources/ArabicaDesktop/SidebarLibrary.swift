import Combine
import Foundation

@MainActor
final class SidebarLibrary: ObservableObject {
    static let shared = SidebarLibrary()

    @Published private(set) var projectPaths: [String]
    @Published private(set) var hiddenProjectPaths: Set<String>
    @Published private(set) var pinnedSessionIDs: Set<String>
    @Published private(set) var archivedSessionIDs: Set<String>
    @Published private(set) var busySessionIDs = Set<String>()
    @Published private(set) var protectedProjectPaths = Set<String>()

    private let defaults: UserDefaults
    private let storageKey: String
    private var busySessionsByOwner: [UUID: Set<String>] = [:]
    private var protectedProjectsByOwner: [UUID: Set<String>] = [:]

    init(defaults: UserDefaults = .standard, storageKey: String = "sidebarLibrary.v1") {
        self.defaults = defaults
        self.storageKey = storageKey
        let stored = defaults.data(forKey: storageKey).flatMap { try? JSONDecoder().decode(StoredState.self, from: $0) }
        let hiddenPaths = Set((stored?.hiddenProjectPaths ?? []).compactMap(Self.normalizedPath))
        var seenPaths = Set<String>()
        projectPaths = (stored?.projectPaths ?? []).compactMap(Self.normalizedPath).filter {
            !hiddenPaths.contains($0) && seenPaths.insert($0).inserted
        }
        hiddenProjectPaths = hiddenPaths
        pinnedSessionIDs = Set((stored?.pinnedSessionIDs ?? []).filter(Self.isValidSessionID))
        archivedSessionIDs = Set((stored?.archivedSessionIDs ?? []).filter(Self.isValidSessionID))
    }

    func addProject(_ path: String) {
        guard let path = Self.normalizedPath(path) else { return }
        let wasHidden = hiddenProjectPaths.remove(path) != nil
        if !projectPaths.contains(path) {
            projectPaths.append(path)
        } else if !wasHidden {
            return
        }
        persist()
    }

    func removeProject(_ path: String) {
        guard let path = Self.normalizedPath(path) else { return }
        let wasVisible = projectPaths.contains(path)
        let wasAdded = hiddenProjectPaths.insert(path).inserted
        guard wasVisible || wasAdded else { return }
        projectPaths.removeAll { $0 == path }
        persist()
    }

    func togglePinned(_ id: String) {
        guard Self.isValidSessionID(id) else { return }
        if pinnedSessionIDs.contains(id) {
            pinnedSessionIDs.remove(id)
        } else {
            pinnedSessionIDs.insert(id)
        }
        persist()
    }

    func setArchived(_ archived: Bool, for id: String) {
        guard Self.isValidSessionID(id), archivedSessionIDs.contains(id) != archived else { return }
        if archived {
            archivedSessionIDs.insert(id)
        } else {
            archivedSessionIDs.remove(id)
        }
        persist()
    }

    func isPinned(_ id: String) -> Bool {
        pinnedSessionIDs.contains(id)
    }

    func isArchived(_ id: String) -> Bool {
        archivedSessionIDs.contains(id)
    }

    func updateActivity(owner: UUID, sessionIDs: Set<String>, projectPaths: Set<String>) {
        let sessions = Set(sessionIDs.filter(Self.isValidSessionID))
        let projects = Set(projectPaths.compactMap(Self.normalizedPath))
        guard (busySessionsByOwner[owner] ?? []) != sessions || (protectedProjectsByOwner[owner] ?? []) != projects
        else {
            return
        }
        if sessions.isEmpty {
            busySessionsByOwner.removeValue(forKey: owner)
        } else {
            busySessionsByOwner[owner] = sessions
        }
        if projects.isEmpty {
            protectedProjectsByOwner.removeValue(forKey: owner)
        } else {
            protectedProjectsByOwner[owner] = projects
        }
        publishActivity()
    }

    func clearActivity(owner: UUID) {
        let hadSessions = busySessionsByOwner.removeValue(forKey: owner) != nil
        let hadProjects = protectedProjectsByOwner.removeValue(forKey: owner) != nil
        if hadSessions || hadProjects { publishActivity() }
    }

    private func publishActivity() {
        let sessions = busySessionsByOwner.values.reduce(into: Set<String>()) { $0.formUnion($1) }
        let projects = protectedProjectsByOwner.values.reduce(into: Set<String>()) { $0.formUnion($1) }
        if busySessionIDs != sessions { busySessionIDs = sessions }
        if protectedProjectPaths != projects { protectedProjectPaths = projects }
    }

    private func persist() {
        let state = StoredState(
            projectPaths: projectPaths,
            hiddenProjectPaths: hiddenProjectPaths.sorted(),
            pinnedSessionIDs: pinnedSessionIDs.sorted(),
            archivedSessionIDs: archivedSessionIDs.sorted())
        guard let data = try? JSONEncoder().encode(state) else { return }
        defaults.set(data, forKey: storageKey)
    }

    private nonisolated static func normalizedPath(_ path: String) -> String? {
        guard !path.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { return nil }
        return URL(fileURLWithPath: path).standardizedFileURL.path
    }

    private nonisolated static func isValidSessionID(_ id: String) -> Bool {
        !id.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    }

    private struct StoredState: Codable {
        let version: Int
        let projectPaths: [String]
        let hiddenProjectPaths: [String]
        let pinnedSessionIDs: [String]
        let archivedSessionIDs: [String]

        init(
            projectPaths: [String], hiddenProjectPaths: [String], pinnedSessionIDs: [String],
            archivedSessionIDs: [String]
        ) {
            version = 1
            self.projectPaths = projectPaths
            self.hiddenProjectPaths = hiddenProjectPaths
            self.pinnedSessionIDs = pinnedSessionIDs
            self.archivedSessionIDs = archivedSessionIDs
        }

        init(from decoder: Decoder) throws {
            let values = try decoder.container(keyedBy: CodingKeys.self)
            version = (try? values.decode(Int.self, forKey: .version)) ?? 1
            projectPaths = (try? values.decode([String].self, forKey: .projectPaths)) ?? []
            hiddenProjectPaths = (try? values.decode([String].self, forKey: .hiddenProjectPaths)) ?? []
            pinnedSessionIDs = (try? values.decode([String].self, forKey: .pinnedSessionIDs)) ?? []
            archivedSessionIDs = (try? values.decode([String].self, forKey: .archivedSessionIDs)) ?? []
        }
    }
}
