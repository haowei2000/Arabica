import Combine
import Foundation

@MainActor
final class SidebarState: ObservableObject {
    struct SelectionRequest: Equatable {
        let id = UUID()
        let sessionID: String
    }

    @Published var expandedProjects = Set<String>()
    @Published var collapsedProjects = Set<String>()
    @Published var showsArchived = false
    @Published var searchText = ""
    @Published var isSearchPresented = false
    @Published private(set) var conversationFocusRequest: UUID?
    @Published private(set) var projectOrder: [String] = []
    @Published private(set) var navigationSessionID: String?
    private var lastSelectedSessionID: String?
    private var pendingSelection: SelectionRequest?
    private var supersededSessionIDs = Set<String>()

    var hasSearchQuery: Bool {
        !searchText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    }

    func presentSearch() {
        isSearchPresented = true
    }

    func escapeSearch() {
        if searchText.isEmpty {
            isSearchPresented = false
        } else {
            searchText = ""
        }
    }

    func focusConversationResults() {
        isSearchPresented = false
        conversationFocusRequest = UUID()
    }

    func requestSelection(_ sessionID: String) -> SelectionRequest {
        if pendingSelection == nil { supersededSessionIDs.removeAll() }
        if let navigationSessionID { supersededSessionIDs.insert(navigationSessionID) }
        let request = SelectionRequest(sessionID: sessionID)
        pendingSelection = request
        navigationSessionID = sessionID
        return request
    }

    func isCurrent(_ request: SelectionRequest) -> Bool {
        pendingSelection == request
    }

    func reconcileSelection(_ selectedID: String?) {
        if let pendingSelection, selectedID != pendingSelection.sessionID {
            if let selectedID, supersededSessionIDs.contains(selectedID) { return }
        }
        pendingSelection = nil
        supersededSessionIDs.removeAll()
        navigationSessionID = selectedID
    }

    func completeSelection(_ request: SelectionRequest, selectedID: String?) {
        guard isCurrent(request) else { return }
        pendingSelection = nil
        supersededSessionIDs.removeAll()
        navigationSessionID = selectedID
    }

    func reconcileProjects(_ currentIDs: [String]) {
        let retained = projectOrder.filter { currentIDs.contains($0) }
        projectOrder = retained + currentIDs.filter { !retained.contains($0) }
    }

    func revealSelection(_ selectedID: String?, in projects: [ProjectGroup]) {
        guard selectedID != lastSelectedSessionID else { return }
        lastSelectedSessionID = selectedID
        guard let selectedID,
            let project = projects.first(where: { $0.sessions.contains(where: { $0.id == selectedID }) })
        else { return }
        collapsedProjects.remove(project.id)
        if let position = project.sessions.firstIndex(where: { $0.id == selectedID }), position >= 5 {
            expandedProjects.insert(project.id)
        }
    }
}
