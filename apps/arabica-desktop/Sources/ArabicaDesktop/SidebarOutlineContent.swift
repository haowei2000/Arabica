import AppKit

@MainActor
enum SidebarOutlineContent {
    static func projects(
        _ projects: [ProjectGroup], model: DesktopModel,
        onSelectConversation: @escaping (String) -> Void,
        onRenameConversation: @escaping (DesktopSession) -> Void,
        onSelectProject: ((String) -> Void)? = nil
    ) -> [SidebarOutlineProject] {
        projects.map { project in
            let isCurrentProject =
                model.workspace?.standardizedFileURL.path
                == URL(fileURLWithPath: project.path).standardizedFileURL.path
            return SidebarOutlineProject(
                id: project.id,
                title: project.name,
                help: [project.name, project.path, isCurrentProject ? "Current Workspace" : nil]
                    .compactMap { $0 }.joined(separator: "\n"),
                conversations: project.sessions.map { session in
                    conversation(
                        session, model: model, onSelect: onSelectConversation,
                        onRename: onRenameConversation)
                },
                menuItems: projectMenuItems(
                    project, model: model, isCurrentProject: isCurrentProject, onSelect: onSelectProject)
            )
        }
    }

    private static func conversation(
        _ session: DesktopSession, model: DesktopModel,
        onSelect: @escaping (String) -> Void,
        onRename: @escaping (DesktopSession) -> Void
    ) -> SidebarOutlineConversation {
        let status = model.sessionStatus(session.id)
        let isUnread = model.hasUnreadResult(session.id)
        let isPinned = model.sidebarLibrary.isPinned(session.id)
        let help = [
            session.title, session.cwd, isPinned ? "Pinned" : nil, status.title,
            isUnread ? "Unread" : nil, model.sessionError(session.id),
        ].compactMap { $0 }.joined(separator: "\n")
        return SidebarOutlineConversation(
            id: session.id, title: session.title, help: help,
            status: status, isUnread: isUnread, isPinned: isPinned,
            menuItems: conversationMenuItems(session, model: model, onSelect: onSelect, onRename: onRename)
        )
    }

    private static func projectMenuItems(
        _ project: ProjectGroup, model: DesktopModel, isCurrentProject: Bool, onSelect: ((String) -> Void)?
    ) -> [SidebarMenuItem] {
        [
            SidebarMenuItem(
                "select", "Use as Current Workspace", systemImage: "folder",
                availability: isCurrentProject
                    ? .unavailable("\(project.path) is the current workspace.")
                    : model.selectProjectAvailability(project.path),
                help: "Switch to \(project.path). Existing conversations keep running.", isOn: isCurrentProject
            ) {
                if let onSelect { onSelect(project.path) } else { model.activateProject(project.path) }
            },
            SidebarMenuItem(
                "new", "New Conversation", systemImage: "plus", availability: model.newConversationAvailability,
                help: "Create a new conversation in this project"
            ) {
                Task { await model.newSession(in: project.path) }
            },
            .separator("workspace"),
            SidebarMenuItem("reveal", "Reveal in Finder", systemImage: "folder") {
                NSWorkspace.shared.activateFileViewerSelecting([URL(fileURLWithPath: project.path)])
            },
            SidebarMenuItem("copy", "Copy Path", systemImage: "doc.on.doc") {
                copyToPasteboard(project.path)
            },
            .separator("organization"),
            SidebarMenuItem(
                "remove", "Remove from Sidebar", systemImage: "sidebar.left",
                availability: model.removeProjectAvailability(project.path),
                help: "Hide this project from the sidebar. Conversations and saved history are kept."
            ) {
                model.removeProject(project.path)
            },
        ]
    }

    private static func conversationMenuItems(
        _ session: DesktopSession, model: DesktopModel,
        onSelect: @escaping (String) -> Void,
        onRename: @escaping (DesktopSession) -> Void
    ) -> [SidebarMenuItem] {
        let isPinned = model.sidebarLibrary.isPinned(session.id)
        let isArchived = model.sidebarLibrary.isArchived(session.id)
        var result = [
            SidebarMenuItem("open", "Open Conversation", systemImage: "bubble.left") {
                onSelect(session.id)
            }
        ]
        if model.sessionStatus(session.id) == .loadFailed {
            result.append(
                SidebarMenuItem("retry", "Retry Loading", systemImage: "arrow.clockwise") {
                    onSelect(session.id)
                }
            )
        }
        if model.isSessionOpen(session.id) {
            result.append(
                SidebarMenuItem(
                    "close", "Close in This Window", systemImage: "xmark.circle",
                    availability: model.closeSessionAvailability(session.id),
                    help: "Close this conversation's connection in this window. Saved history and your draft are kept."
                ) {
                    Task { await model.closeSession(session.id) }
                }
            )
        }
        result.append(contentsOf: [
            .separator("organization"),
            SidebarMenuItem(
                "pin", isPinned ? "Unpin Conversation" : "Pin Conversation",
                systemImage: isPinned ? "pin.slash" : "pin"
            ) {
                model.sidebarLibrary.togglePinned(session.id)
            },
            SidebarMenuItem(
                "rename", "Rename…", systemImage: "pencil", availability: model.renameSessionAvailability(session.id),
                help: "Rename this conversation in saved history"
            ) {
                let availability = model.renameSessionAvailability(session.id)
                guard availability.isEnabled else {
                    model.errorText = availability.explanation
                    return
                }
                guard let currentSession = model.sessions.first(where: { $0.id == session.id }) else { return }
                onRename(currentSession)
            },
            SidebarMenuItem(
                "archive", isArchived ? "Restore Conversation" : "Archive Conversation",
                systemImage: isArchived ? "tray.and.arrow.up" : "archivebox",
                availability: model.archiveSessionAvailability(session.id, archived: !isArchived),
                help: isArchived
                    ? "Return this conversation to Active. Saved history and your draft are kept."
                    : "Move this conversation to Archived. You can restore it later; saved history and your draft are kept."
            ) {
                model.archiveSession(session.id, archived: !isArchived)
            },
            .separator("workspace"),
            SidebarMenuItem("reveal", "Reveal in Finder", systemImage: "folder") {
                NSWorkspace.shared.activateFileViewerSelecting([URL(fileURLWithPath: session.cwd)])
            },
            SidebarMenuItem("title", "Copy Title", systemImage: "doc.on.doc") {
                copyToPasteboard(session.title)
            },
            SidebarMenuItem("id", "Copy Conversation ID", systemImage: "number") {
                copyToPasteboard(session.id)
            },
        ])
        return result
    }

    private static func copyToPasteboard(_ value: String) {
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(value, forType: .string)
    }
}
