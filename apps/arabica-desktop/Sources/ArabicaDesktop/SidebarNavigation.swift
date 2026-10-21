import Foundation

enum SidebarNavigation {
    enum Command: Equatable {
        case previous, next, first, last
    }

    static func orderedProjects(_ projects: [ProjectGroup], preferredOrder: [String]) -> [ProjectGroup] {
        let positions = Dictionary(uniqueKeysWithValues: preferredOrder.enumerated().map { ($1, $0) })
        return projects.enumerated().sorted { first, second in
            let firstPosition = positions[first.element.id] ?? (preferredOrder.count + first.offset)
            let secondPosition = positions[second.element.id] ?? (preferredOrder.count + second.offset)
            return firstPosition < secondPosition
        }.map(\.element)
    }

    static func filteredProjects(
        _ projects: [ProjectGroup], query: String,
        archivedSessionIDs: Set<String> = [], showingArchived: Bool = false,
        pinnedSessionIDs: Set<String> = []
    ) -> [ProjectGroup] {
        let query = query.trimmingCharacters(in: .whitespacesAndNewlines)
        return projects.compactMap { project in
            let scopedSessions = project.sessions.filter { archivedSessionIDs.contains($0.id) == showingArchived }
            let matchesProject =
                project.name.localizedStandardContains(query) || project.path.localizedStandardContains(query)
            let matches = scopedSessions.filter { session in
                query.isEmpty || matchesProject || session.title.localizedStandardContains(query)
                    || session.id.localizedStandardContains(query) || session.cwd.localizedStandardContains(query)
            }.enumerated().sorted { first, second in
                let firstPinned = pinnedSessionIDs.contains(first.element.id)
                let secondPinned = pinnedSessionIDs.contains(second.element.id)
                return firstPinned == secondPinned ? first.offset < second.offset : firstPinned
            }.map(\.element)
            if matches.isEmpty && (showingArchived || (!query.isEmpty && !matchesProject)) { return nil }
            return ProjectGroup(name: project.name, path: project.path, sessions: matches)
        }
    }

    static func visibleSessions(in project: ProjectGroup, isExpanded: Bool) -> [DesktopSession] {
        isExpanded ? project.sessions : Array(project.sessions.prefix(5))
    }

    static func sessionIDs(
        in projects: [ProjectGroup], expandedProjects: Set<String>, isSearching: Bool,
        collapsedProjects: Set<String> = []
    ) -> [String] {
        projects.flatMap { project in
            if !isSearching && collapsedProjects.contains(project.id) { return [String]() }
            return visibleSessions(in: project, isExpanded: isSearching || expandedProjects.contains(project.id)).map(
                \.id)
        }
    }

    static func destination(in sessionIDs: [String], currentID: String?, command: Command) -> String? {
        guard !sessionIDs.isEmpty else { return nil }
        switch command {
        case .first: return sessionIDs.first
        case .last: return sessionIDs.last
        case .previous, .next:
            guard let currentID, let position = sessionIDs.firstIndex(of: currentID) else {
                return command == .next ? sessionIDs.first : sessionIDs.last
            }
            let nextPosition = command == .next ? min(position + 1, sessionIDs.count - 1) : max(position - 1, 0)
            return sessionIDs[nextPosition]
        }
    }
}

enum SidebarLayout {
    static let defaultWidth: CGFloat = 280
    static let minimumWidth: CGFloat = 196
    static let maximumWidth: CGFloat = 380
    static let dividerWidth: CGFloat = 8
    static let minimumConversationWidth: CGFloat = 320

    static func canShowSidebar(availableWidth: CGFloat) -> Bool {
        availableWidth >= minimumWidth + minimumConversationWidth + dividerWidth
    }

    static func resolvedWidth(_ preferredWidth: CGFloat, availableWidth: CGFloat) -> CGFloat {
        let maximum = min(maximumWidth, availableWidth - minimumConversationWidth - dividerWidth)
        let minimum = min(minimumWidth, max(0, maximum))
        return min(max(preferredWidth, minimum), max(minimum, maximum))
    }
}
