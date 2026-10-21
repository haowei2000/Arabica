import XCTest

@testable import ArabicaDesktop

final class SidebarNavigationTests: XCTestCase {
    private func project(_ id: String, name: String = "Project", sessionCount: Int) -> ProjectGroup {
        ProjectGroup(
            name: name, path: "/tmp/\(id)",
            sessions: (0..<sessionCount).map {
                DesktopSession(id: "\(id)-\($0)", cwd: "/tmp/\(id)", title: "Conversation \($0)")
            })
    }

    func testNavigationSkipsCollapsedSessionsAndKeepsProjectOrder() {
        let projects = [project("first", sessionCount: 7), project("second", sessionCount: 2)]
        let visible = SidebarNavigation.sessionIDs(in: projects, expandedProjects: [], isSearching: false)
        XCTAssertEqual(visible, ["first-0", "first-1", "first-2", "first-3", "first-4", "second-0", "second-1"])
        XCTAssertEqual(SidebarNavigation.destination(in: visible, currentID: "first-4", command: .next), "second-0")
        XCTAssertEqual(SidebarNavigation.destination(in: visible, currentID: "second-0", command: .previous), "first-4")
        let expanded = SidebarNavigation.sessionIDs(in: projects, expandedProjects: ["/tmp/first"], isSearching: false)
        XCTAssertEqual(expanded.count, 9)
    }

    func testNavigationHasStableBoundsAndHandlesMissingSelection() {
        let ids = ["first", "second", "third"]
        XCTAssertEqual(SidebarNavigation.destination(in: ids, currentID: "first", command: .previous), "first")
        XCTAssertEqual(SidebarNavigation.destination(in: ids, currentID: "third", command: .next), "third")
        XCTAssertEqual(SidebarNavigation.destination(in: ids, currentID: "missing", command: .next), "first")
        XCTAssertEqual(SidebarNavigation.destination(in: ids, currentID: nil, command: .previous), "third")
        XCTAssertEqual(SidebarNavigation.destination(in: ids, currentID: "second", command: .first), "first")
        XCTAssertEqual(SidebarNavigation.destination(in: ids, currentID: "second", command: .last), "third")
        XCTAssertNil(SidebarNavigation.destination(in: [], currentID: nil, command: .next))
    }

    func testSearchShowsEveryMatchAndPreservesIdenticalProjectNames() {
        let projects = [
            project("first", name: "Demo", sessionCount: 8), project("second", name: "Demo", sessionCount: 2),
        ]
        let titleMatches = SidebarNavigation.filteredProjects(projects, query: "Conversation 6")
        XCTAssertEqual(titleMatches.map(\.id), ["/tmp/first"])
        XCTAssertEqual(titleMatches.first?.sessions.map(\.id), ["first-6"])
        let projectMatches = SidebarNavigation.filteredProjects(projects, query: " demo ")
        XCTAssertEqual(projectMatches.map(\.id), ["/tmp/first", "/tmp/second"])
        XCTAssertEqual(
            SidebarNavigation.sessionIDs(in: projectMatches, expandedProjects: [], isSearching: true).count, 10)
        XCTAssertTrue(SidebarNavigation.filteredProjects(projects, query: "missing").isEmpty)
    }

    func testProjectSelectionDoesNotReorderExistingGroups() {
        let first = project("first", sessionCount: 2)
        let second = project("second", sessionCount: 2)
        let added = project("new", sessionCount: 1)
        let ordered = SidebarNavigation.orderedProjects([second, added, first], preferredOrder: [first.id, second.id])
        XCTAssertEqual(ordered.map(\.id), [first.id, second.id, added.id])
    }

    func testWholeProjectCollapseIsIndependentFromHistoryExpansion() {
        let projects = [project("first", sessionCount: 7), project("second", sessionCount: 2)]
        let visible = SidebarNavigation.sessionIDs(
            in: projects, expandedProjects: ["/tmp/first"], isSearching: false,
            collapsedProjects: ["/tmp/first"])
        XCTAssertEqual(visible, ["second-0", "second-1"])
        let searching = SidebarNavigation.sessionIDs(
            in: projects, expandedProjects: [], isSearching: true, collapsedProjects: ["/tmp/first"])
        XCTAssertEqual(searching.count, 9)
    }

    func testArchiveScopeHasNoDuplicatesAndKeepsEmptyActiveProjects() {
        let projects = [project("first", sessionCount: 3), project("empty", sessionCount: 0)]
        let archived: Set<String> = ["first-1"]
        let active = SidebarNavigation.filteredProjects(projects, query: "", archivedSessionIDs: archived)
        XCTAssertEqual(active.map(\.id), ["/tmp/first", "/tmp/empty"])
        XCTAssertEqual(active.first?.sessions.map(\.id), ["first-0", "first-2"])
        let archive = SidebarNavigation.filteredProjects(
            projects, query: "", archivedSessionIDs: archived, showingArchived: true)
        XCTAssertEqual(archive.map(\.id), ["/tmp/first"])
        XCTAssertEqual(archive.first?.sessions.map(\.id), ["first-1"])
        let scopedSearch = SidebarNavigation.filteredProjects(
            projects, query: "Conversation 1", archivedSessionIDs: archived)
        XCTAssertTrue(scopedSearch.isEmpty)
    }

    func testPinnedSessionsSortFirstWithoutChangingRelativeHistoryOrder() {
        let projects = [project("first", sessionCount: 7)]
        let pinned = SidebarNavigation.filteredProjects(
            projects, query: "", pinnedSessionIDs: ["first-2", "first-6"])
        XCTAssertEqual(
            pinned.first?.sessions.map(\.id),
            ["first-2", "first-6", "first-0", "first-1", "first-3", "first-4", "first-5"])
        XCTAssertEqual(SidebarNavigation.sessionIDs(in: pinned, expandedProjects: [], isSearching: false).count, 5)
    }

    func testFilterMatchesFullPathAndConversationID() {
        let projects = [
            project("first", name: "Demo", sessionCount: 3), project("second", name: "Demo", sessionCount: 2),
        ]
        XCTAssertEqual(
            SidebarNavigation.filteredProjects(projects, query: "/tmp/second").map(\.id), ["/tmp/second"])
        XCTAssertEqual(
            SidebarNavigation.filteredProjects(projects, query: "first-2").first?.sessions.map(\.id), ["first-2"])
    }

    func testSidebarWidthRespectsBoundsAndConversationSpace() {
        XCTAssertTrue(SidebarLayout.canShowSidebar(availableWidth: 540))
        XCTAssertFalse(SidebarLayout.canShowSidebar(availableWidth: 480))
        XCTAssertEqual(SidebarLayout.resolvedWidth(240, availableWidth: 1000), 240)
        XCTAssertEqual(SidebarLayout.resolvedWidth(100, availableWidth: 1000), 196)
        XCTAssertEqual(SidebarLayout.resolvedWidth(600, availableWidth: 1000), 380)
        XCTAssertEqual(SidebarLayout.resolvedWidth(380, availableWidth: 540), 212)
        XCTAssertEqual(SidebarLayout.resolvedWidth(240, availableWidth: 480), 152)
        XCTAssertEqual(SidebarLayout.resolvedWidth(240, availableWidth: 300), 0)
    }
}
