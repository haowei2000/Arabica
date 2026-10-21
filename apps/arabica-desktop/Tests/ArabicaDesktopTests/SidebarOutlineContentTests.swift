import XCTest

@testable import ArabicaDesktop

@MainActor
final class SidebarOutlineContentTests: XCTestCase {
    private func model(request: DesktopModel.SessionRequest? = nil) -> DesktopModel {
        let model = DesktopModel(
            restoreWorkspace: false, sessionRequest: request ?? { _, _ in [:] },
            sidebarLibrary: .testInstance())
        model.isConnected = true
        model.workspace = URL(fileURLWithPath: "/tmp/project-a")
        model.sessions = [DesktopSession(id: "a", cwd: "/tmp/project-a", title: "Conversation A")]
        model.configureSessionCapabilities([
            "_meta": ["arabica.session": ["schemaVersion": 1, "rename": "_arabica/session/rename"]]
        ])
        return model
    }

    func testSingleLineRowsKeepFullContextAndStatusInHelp() throws {
        let model = model()
        let run = model.beginRun(for: "a")
        model.finishRun(for: "a", runID: run, outcome: .failed, error: "The command failed.")
        model.sidebarLibrary.togglePinned("a")

        let content = SidebarOutlineContent.projects(
            model.projectGroups, model: model, onSelectConversation: { _ in }, onRenameConversation: { _ in })
        let project = try XCTUnwrap(content.first)
        XCTAssertTrue(project.help.contains("/tmp/project-a"))
        XCTAssertTrue(project.help.contains("Current Workspace"))
        let select = try XCTUnwrap(project.menuItems.first { $0.id == "select" })
        XCTAssertTrue(select.isOn)
        XCTAssertFalse(select.availability.isEnabled)

        let conversation = try XCTUnwrap(project.conversations.first)
        XCTAssertEqual(conversation.status, .failed)
        XCTAssertTrue(conversation.isUnread)
        XCTAssertTrue(conversation.isPinned)
        for detail in ["Conversation A", "/tmp/project-a", "Pinned", "Failed", "Unread", "The command failed."] {
            XCTAssertTrue(conversation.help.contains(detail), "Missing detail: \(detail)")
        }
    }

    func testMenuActionsRecheckBusyStateAndRenameUsesCurrentTitle() throws {
        let model = model()
        var renamedSession: DesktopSession?
        let content = SidebarOutlineContent.projects(
            model.projectGroups, model: model, onSelectConversation: { _ in },
            onRenameConversation: { renamedSession = $0 })
        let conversation = try XCTUnwrap(content.first?.conversations.first)
        let archive = try XCTUnwrap(conversation.menuItems.first { $0.id == "archive" })
        let rename = try XCTUnwrap(conversation.menuItems.first { $0.id == "rename" })
        XCTAssertTrue(archive.availability.isEnabled)
        XCTAssertTrue(rename.availability.isEnabled)

        let run = model.beginRun(for: "a")
        archive.action?()
        rename.action?()
        XCTAssertFalse(model.sidebarLibrary.isArchived("a"))
        XCTAssertNil(renamedSession)
        XCTAssertEqual(model.errorText, "Stop this conversation’s task or wait for it to finish.")

        model.finishRun(for: "a", runID: run)
        model.sessions[0].title = "Updated title"
        rename.action?()
        XCTAssertEqual(renamedSession?.title, "Updated title")
    }

    func testNewConversationTargetsTheFolderWhoseMenuWasOpened() async throws {
        let created = expectation(description: "Conversation created in target folder")
        var creationPath: String?
        let model = model { method, params in
            if method == "session/new" {
                creationPath = params["cwd"] as? String
                created.fulfill()
                return ["sessionId": "new-b"]
            }
            return [:]
        }
        let target = ProjectGroup(name: "Project B", path: "/tmp/project-b", sessions: [])
        let content = SidebarOutlineContent.projects(
            [target], model: model, onSelectConversation: { _ in }, onRenameConversation: { _ in })
        let newConversation = try XCTUnwrap(content.first?.menuItems.first { $0.id == "new" })
        newConversation.action?()
        await fulfillment(of: [created], timeout: 1)
        XCTAssertEqual(creationPath, "/tmp/project-b")
    }
}
