import AppKit
import XCTest

@testable import ArabicaDesktop

@MainActor
final class SidebarOutlineViewTests: XCTestCase {
    func testNativeTreeShowsFiveChildrenAndKeepsDisclosureSeparateFromShowMore() throws {
        let fixture = Fixture(conversationCount: 7)
        fixture.refresh()
        let project = try XCTUnwrap(fixture.coordinator.roots.first)
        XCTAssertEqual(fixture.outline.numberOfRows, 7)
        XCTAssertEqual(project.children.count, 6)
        XCTAssertEqual(project.children.last?.kind, .showMore)
        XCTAssertTrue(fixture.outline.isItemExpanded(project))

        fixture.outline.collapseItem(project)
        XCTAssertEqual(fixture.expansions.last?.0, "project")
        XCTAssertEqual(fixture.expansions.last?.1, false)
        XCTAssertTrue(fixture.showMore.isEmpty)

        fixture.view.collapsedProjectIDs = ["project"]
        fixture.refresh()
        XCTAssertEqual(fixture.outline.numberOfRows, 1)
        fixture.view.collapsedProjectIDs = []
        fixture.view.showsAllConversationProjectIDs = ["project"]
        fixture.refresh()
        XCTAssertEqual(fixture.outline.numberOfRows, 8)
        XCTAssertFalse(project.children.contains { $0.kind == .showMore })
    }

    func testSearchRevealsAllMatchesWithoutChangingSavedCollapseOrLimit() throws {
        let fixture = Fixture(conversationCount: 8)
        fixture.view.collapsedProjectIDs = ["project"]
        fixture.view.isSearching = true
        fixture.refresh()
        let project = try XCTUnwrap(fixture.coordinator.roots.first)
        XCTAssertEqual(project.children.count, 8)
        XCTAssertTrue(fixture.outline.isItemExpanded(project))
        fixture.outline.collapseItem(project)
        XCTAssertTrue(fixture.outline.isItemExpanded(project))
        XCTAssertTrue(fixture.expansions.isEmpty)

        fixture.view.isSearching = false
        fixture.refresh()
        XCTAssertFalse(fixture.outline.isItemExpanded(project))
        XCTAssertEqual(project.children.count, 6)
    }

    func testRefreshPreservesNodeIdentityAndDoesNotNavigate() throws {
        let fixture = Fixture(conversationCount: 2)
        fixture.view.selectedConversationID = "conversation-1"
        fixture.refresh()
        let project = try XCTUnwrap(fixture.coordinator.roots.first)
        let conversation = project.children[1]
        XCTAssertTrue(fixture.outline.item(atRow: fixture.outline.selectedRow) as? SidebarTreeNode === conversation)
        XCTAssertTrue(fixture.selectedConversations.isEmpty)
        XCTAssertTrue(fixture.selectedProjects.isEmpty)

        fixture.view.projects[0].conversations[1].title = "Updated conversation title"
        fixture.refresh()
        XCTAssertTrue(fixture.coordinator.roots.first === project)
        XCTAssertTrue(project.children[1] === conversation)
        XCTAssertEqual(conversation.title, "Updated conversation title")
        XCTAssertTrue(fixture.selectedConversations.isEmpty)
        XCTAssertTrue(fixture.selectedProjects.isEmpty)
    }

    func testCollapsingSelectedConversationsFolderDoesNotActivateWorkspace() throws {
        let fixture = Fixture(conversationCount: 2)
        fixture.view.selectedConversationID = "conversation-1"
        fixture.refresh()
        let project = try XCTUnwrap(fixture.coordinator.roots.first)

        fixture.outline.collapseItem(project)
        XCTAssertTrue(fixture.selectedConversations.isEmpty)
        XCTAssertTrue(fixture.selectedProjects.isEmpty)
        XCTAssertEqual(fixture.expansions.last?.0, "project")
        XCTAssertEqual(fixture.expansions.last?.1, false)

        fixture.view.collapsedProjectIDs = ["project"]
        fixture.refresh()
        XCTAssertTrue(fixture.selectedConversations.isEmpty)
        XCTAssertTrue(fixture.selectedProjects.isEmpty)
        XCTAssertEqual(fixture.view.selectedConversationID, "conversation-1")
    }

    func testContextMenuTargetsClickedConversationWithoutNavigatingAndUsesFreshActions() throws {
        let fixture = Fixture(conversationCount: 2)
        var opened: [String] = []
        fixture.view.selectedConversationID = "conversation-0"
        fixture.view.projects[0].conversations[1].menuItems = [
            SidebarMenuItem("rename", "Rename Conversation") { opened.append("original") },
            SidebarMenuItem("archive", "Archive Conversation", availability: .unavailable("Wait for the run.")) {},
        ]
        fixture.refresh()
        let secondConversationRow = 2
        let menu = try XCTUnwrap(fixture.coordinator.contextMenu(for: secondConversationRow))
        XCTAssertEqual(fixture.outline.selectedRow, 1)
        XCTAssertTrue(fixture.selectedConversations.isEmpty)
        XCTAssertEqual(menu.items[1].toolTip, "Wait for the run.")
        XCTAssertFalse(menu.items[1].isEnabled)
        menu.performActionForItem(at: 0)
        XCTAssertEqual(opened, ["original"])

        fixture.view.projects[0].conversations[1].menuItems = [
            SidebarMenuItem("rename", "Rename Conversation") { opened.append("updated") }
        ]
        fixture.refresh()
        let refreshed = try XCTUnwrap(fixture.coordinator.contextMenu(for: secondConversationRow))
        refreshed.performActionForItem(at: 0)
        XCTAssertEqual(opened, ["original", "updated"])
        XCTAssertEqual(fixture.outline.selectedRow, 1)
    }

    func testShowMoreNeedsActivationAndHomeEndMoveNativeSelection() throws {
        let fixture = Fixture(conversationCount: 7)
        fixture.refresh()
        fixture.outline.selectRowIndexes(IndexSet(integer: 6), byExtendingSelection: false)
        XCTAssertTrue(fixture.showMore.isEmpty)
        XCTAssertTrue(fixture.coordinator.activateSelectedRow())
        XCTAssertEqual(fixture.showMore, ["project"])

        fixture.outline.keyDown(with: try keyDown(code: 115))
        XCTAssertEqual(fixture.outline.selectedRow, 0)
        XCTAssertEqual(fixture.selectedProjects.last, "project")
        fixture.outline.keyDown(with: try keyDown(code: 119))
        XCTAssertEqual(fixture.outline.selectedRow, 6)
        XCTAssertEqual(fixture.showMore, ["project"])
    }

    func testNativeRowAndCellExposeContextMenuAccessibilityWithoutNavigating() throws {
        let fixture = Fixture(conversationCount: 1)
        fixture.view.projects[0].conversations[0].menuItems = [
            SidebarMenuItem("rename", "Rename Conversation") {}
        ]
        fixture.view.selectedConversationID = "conversation-0"
        fixture.refresh()
        let conversation = try XCTUnwrap(fixture.coordinator.roots.first?.children.first)
        let row = try XCTUnwrap(fixture.coordinator.outlineView(fixture.outline, rowViewForItem: conversation))
        let cell = try XCTUnwrap(
            fixture.coordinator.outlineView(
                fixture.outline, viewFor: fixture.outline.tableColumns[0], item: conversation))
        for view in [row, cell] {
            XCTAssertTrue(view.isAccessibilitySelectorAllowed(#selector(NSView.accessibilityPerformShowMenu)))
            XCTAssertEqual(view.accessibilityCustomActions()?.map(\.name), ["Show Actions"])
            // An unattached view cannot display a menu and must never navigate as a fallback.
            XCTAssertFalse(view.accessibilityPerformShowMenu())
        }
        fixture.outline.keyDown(with: try keyDown(code: 109, modifiers: .shift))
        XCTAssertEqual(fixture.outline.selectedRow, 1)
        XCTAssertTrue(fixture.selectedConversations.isEmpty)
        XCTAssertTrue(fixture.selectedProjects.isEmpty)

        let project = try XCTUnwrap(fixture.coordinator.roots.first)
        let folderRow = try XCTUnwrap(fixture.coordinator.outlineView(fixture.outline, rowViewForItem: project))
        XCTAssertFalse(folderRow.isAccessibilitySelectorAllowed(#selector(NSView.accessibilityPerformShowMenu)))
        XCTAssertEqual(folderRow.accessibilityCustomActions()?.count, 0)
    }

    private func keyDown(code: UInt16, modifiers: NSEvent.ModifierFlags = []) throws -> NSEvent {
        try XCTUnwrap(
            NSEvent.keyEvent(
                with: .keyDown, location: .zero, modifierFlags: modifiers, timestamp: 0,
                windowNumber: 0, context: nil, characters: "", charactersIgnoringModifiers: "",
                isARepeat: false, keyCode: code))
    }

    @MainActor
    private final class Fixture {
        var view: MutableView
        let outline = SidebarTreeOutlineView()
        var coordinator: SidebarOutlineView.Coordinator!
        var selectedProjects: [String] = []
        var selectedConversations: [String] = []
        var expansions: [(String, Bool)] = []
        var showMore: [String] = []

        init(conversationCount: Int) {
            view = MutableView(
                projects: [
                    SidebarOutlineProject(
                        id: "project", title: "Project", help: "/Project",
                        conversations: (0..<conversationCount).map {
                            SidebarOutlineConversation(
                                id: "conversation-\($0)", title: "Conversation \($0)", help: "Full title \($0)")
                        })
                ])
            coordinator = SidebarOutlineView.Coordinator(snapshot())
            outline.addTableColumn(NSTableColumn(identifier: NSUserInterfaceItemIdentifier("test")))
            outline.outlineTableColumn = outline.tableColumns[0]
            outline.dataSource = coordinator
            outline.delegate = coordinator
            outline.treeCoordinator = coordinator
            coordinator.outline = outline
        }

        func refresh() { coordinator.update(snapshot()) }

        private func snapshot() -> SidebarOutlineView {
            SidebarOutlineView(
                projects: view.projects, selectedProjectID: view.selectedProjectID,
                selectedConversationID: view.selectedConversationID,
                collapsedProjectIDs: view.collapsedProjectIDs,
                showsAllConversationProjectIDs: view.showsAllConversationProjectIDs,
                isSearching: view.isSearching, focusRequest: nil,
                onSelectProject: { [weak self] in self?.selectedProjects.append($0) },
                onSelectConversation: { [weak self] in self?.selectedConversations.append($0) },
                onSetProjectExpanded: { [weak self] in self?.expansions.append(($0, $1)) },
                onShowMore: { [weak self] in self?.showMore.append($0) })
        }
    }

    private struct MutableView {
        var projects: [SidebarOutlineProject]
        var selectedProjectID: String?
        var selectedConversationID: String?
        var collapsedProjectIDs: Set<String> = []
        var showsAllConversationProjectIDs: Set<String> = []
        var isSearching = false
    }
}
