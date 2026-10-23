import XCTest

@testable import ArabicaDesktop

@MainActor
final class SidebarStateTests: XCTestCase {
    func testProjectRefreshRetainsWindowSearchExpansionAndOrdering() {
        let state = SidebarState()
        state.reconcileProjects(["first", "second"])
        state.searchText = "Draft"
        state.expandedProjects.insert("first")
        state.collapsedProjects.insert("second")
        state.showsArchived = true

        state.reconcileProjects(["second", "new", "first"])

        XCTAssertEqual(state.projectOrder, ["first", "second", "new"])
        XCTAssertEqual(state.searchText, "Draft")
        XCTAssertEqual(state.expandedProjects, ["first"])
        XCTAssertEqual(state.collapsedProjects, ["second"])
        XCTAssertTrue(state.showsArchived)

        state.reconcileProjects(["second", "new"])
        XCTAssertEqual(state.projectOrder, ["second", "new"])
        XCTAssertEqual(state.searchText, "Draft")
    }

    func testWindowsHaveIndependentSidebarState() {
        let first = SidebarState()
        let second = SidebarState()
        first.searchText = "First window"
        first.expandedProjects.insert("project")
        first.reconcileProjects(["project"])
        first.presentSearch()

        XCTAssertTrue(second.searchText.isEmpty)
        XCTAssertTrue(second.expandedProjects.isEmpty)
        XCTAssertTrue(second.projectOrder.isEmpty)
        XCTAssertTrue(second.collapsedProjects.isEmpty)
        XCTAssertFalse(second.showsArchived)
        XCTAssertFalse(second.isSearchPresented)
    }

    func testSearchEscapeClearsTheFilterBeforeDismissingThePopover() {
        let state = SidebarState()
        state.searchText = "A conversation"
        state.presentSearch()
        XCTAssertTrue(state.hasSearchQuery)

        state.escapeSearch()
        XCTAssertEqual(state.searchText, "")
        XCTAssertFalse(state.hasSearchQuery)
        XCTAssertTrue(state.isSearchPresented)

        state.escapeSearch()
        XCTAssertFalse(state.isSearchPresented)
    }

    func testMovingFromSearchToResultsPreservesTheFilterAndRequestsFocus() {
        let state = SidebarState()
        let originalRequest = state.conversationFocusRequest
        state.searchText = "session"
        state.presentSearch()
        state.focusConversationResults()

        XCTAssertEqual(state.searchText, "session")
        XCTAssertFalse(state.isSearchPresented)
        XCTAssertNotEqual(state.conversationFocusRequest, originalRequest)
        let firstRequest = state.conversationFocusRequest
        state.focusConversationResults()
        XCTAssertNotEqual(state.conversationFocusRequest, firstRequest)
    }

    func testWhitespaceQueryDoesNotMarkTheToolbarFilterActive() {
        let state = SidebarState()
        state.searchText = " \n  "
        XCTAssertFalse(state.hasSearchQuery)
    }

    func testSelectingAConversationRevealsItsCollapsedProject() {
        let state = SidebarState()
        let project = ProjectGroup(
            name: "Project", path: "/tmp/project",
            sessions: [DesktopSession(id: "first", cwd: "/tmp/project", title: "First")])
        state.collapsedProjects.insert(project.id)
        state.revealSelection("first", in: [project])
        XCTAssertFalse(state.collapsedProjects.contains(project.id))
        state.collapsedProjects.insert(project.id)
        state.revealSelection("first", in: [project])
        XCTAssertTrue(state.collapsedProjects.contains(project.id))
    }

    func testReappearingDoesNotOverrideManualCollapseUntilSelectionChanges() {
        let state = SidebarState()
        let project = ProjectGroup(
            name: "Project", path: "/tmp/project",
            sessions: (0..<7).map {
                DesktopSession(id: "session-\($0)", cwd: "/tmp/project", title: "Conversation \($0)")
            })
        state.revealSelection("session-6", in: [project])
        XCTAssertTrue(state.expandedProjects.contains(project.id))

        state.expandedProjects.remove(project.id)
        state.revealSelection("session-6", in: [project])
        XCTAssertFalse(state.expandedProjects.contains(project.id))

        state.revealSelection("session-0", in: [project])
        state.revealSelection("session-6", in: [project])
        XCTAssertTrue(state.expandedProjects.contains(project.id))
    }

    func testRapidNavigationAdvancesBeforeSessionRequestsStart() throws {
        let state = SidebarState()
        let ids = ["first", "second", "third", "fourth"]
        state.reconcileSelection("first")
        var requests: [SidebarState.SelectionRequest] = []

        for _ in 0..<3 {
            let destination = try XCTUnwrap(
                SidebarNavigation.destination(
                    in: ids, currentID: state.navigationSessionID, command: .next))
            requests.append(state.requestSelection(destination))
        }

        XCTAssertEqual(state.navigationSessionID, "fourth")
        XCTAssertFalse(state.isCurrent(requests[0]))
        XCTAssertFalse(state.isCurrent(requests[1]))
        XCTAssertTrue(state.isCurrent(requests[2]))

        state.reconcileSelection("second")
        state.completeSelection(requests[0], selectedID: "second")
        state.reconcileSelection("third")
        XCTAssertEqual(state.navigationSessionID, "fourth")

        state.reconcileSelection("fourth")
        XCTAssertFalse(state.isCurrent(requests[2]))
        state.completeSelection(requests[2], selectedID: "fourth")
        XCTAssertEqual(state.navigationSessionID, "fourth")
        XCTAssertFalse(state.isCurrent(requests[2]))
    }

    func testExternalSelectionSupersedesQueuedSidebarNavigation() {
        let state = SidebarState()
        state.reconcileSelection("first")
        let request = state.requestSelection("second")

        state.reconcileSelection("new-conversation")
        state.completeSelection(request, selectedID: "second")

        XCTAssertFalse(state.isCurrent(request))
        XCTAssertEqual(state.navigationSessionID, "new-conversation")
    }

    func testMatchingModelSelectionReleasesIntentBeforeLoadingFinishes() {
        let state = SidebarState()
        state.reconcileSelection("first")
        _ = state.requestSelection("second")
        let latest = state.requestSelection("third")

        state.reconcileSelection("third")
        XCTAssertEqual(state.navigationSessionID, "third")
        XCTAssertFalse(state.isCurrent(latest))

        state.reconcileSelection("second")
        state.completeSelection(latest, selectedID: "third")
        XCTAssertEqual(state.navigationSessionID, "second")
    }

    func testUnavailableSelectionAndDisconnectRestoreActualSelection() {
        let state = SidebarState()
        state.reconcileSelection("first")
        let unavailable = state.requestSelection("missing")
        state.completeSelection(unavailable, selectedID: "first")
        XCTAssertEqual(state.navigationSessionID, "first")

        let pending = state.requestSelection("second")
        state.reconcileSelection(nil)
        XCTAssertNil(state.navigationSessionID)
        XCTAssertFalse(state.isCurrent(pending))
    }
}
