import Foundation

struct SidebarActionAvailability: Equatable {
    let isEnabled: Bool
    let explanation: String?

    static let enabled = Self(isEnabled: true, explanation: nil)

    static func unavailable(_ explanation: String) -> Self {
        Self(isEnabled: false, explanation: explanation)
    }
}

extension DesktopModel {
    var newConversationAvailability: SidebarActionAvailability {
        if isConnecting { return .unavailable("Wait for the agent to connect.") }
        if !isConnected { return .unavailable("Open a project to reconnect to the agent.") }
        if isCreatingSession { return .unavailable("Wait for the new conversation to open.") }
        return .enabled
    }

    func selectProjectAvailability(_ path: String) -> SidebarActionAvailability {
        if isConnecting { return .unavailable("Wait for the agent to connect before selecting another project.") }
        return .enabled
    }

    func removeProjectAvailability(_ path: String) -> SidebarActionAvailability {
        let path = URL(fileURLWithPath: path).standardizedFileURL.path
        if workspace?.standardizedFileURL.path == path {
            return .unavailable("Select another project before removing this project from the sidebar.")
        }
        if sessions.contains(where: {
            URL(fileURLWithPath: $0.cwd).standardizedFileURL.path == path
                && sidebarSessionBusyExplanation($0.id) != nil
        }) {
            return .unavailable(
                "Wait for this project’s current operations to finish before removing it from the sidebar.")
        }
        if sidebarLibrary.protectedProjectPaths.contains(path) {
            return .unavailable(
                "This project is open in another window. Select another project there before removing it from the sidebar."
            )
        }
        return .enabled
    }

    func renameSessionAvailability(_ id: String) -> SidebarActionAvailability {
        if isConnecting { return .unavailable("Wait for the agent to connect before renaming this conversation.") }
        if !isConnected { return .unavailable("Reconnect to the agent to rename this conversation.") }
        if !canRenameSessions { return .unavailable("The connected agent does not support renaming conversations.") }
        if !sessions.contains(where: { $0.id == id }) {
            return .unavailable("This conversation is no longer in the sidebar. Refresh the conversation list.")
        }
        if let explanation = sidebarSessionBusyExplanation(id) { return .unavailable(explanation) }
        return .enabled
    }

    func archiveSessionAvailability(_ id: String, archived: Bool) -> SidebarActionAvailability {
        guard archived else { return .enabled }
        if let explanation = sidebarSessionBusyExplanation(id) { return .unavailable(explanation) }
        return .enabled
    }

    func closeSessionAvailability(_ id: String) -> SidebarActionAvailability {
        if isConnecting { return .unavailable("Wait for the agent to connect before closing this conversation.") }
        if !isConnected { return .unavailable("Reconnect to the agent to close this conversation in this window.") }
        if isCreatingSession {
            return .unavailable("Wait for the new conversation to open before closing this conversation.")
        }
        if !isSessionOpen(id) { return .unavailable("This conversation is already closed in this window.") }
        if let explanation = sidebarSessionBusyExplanation(id) { return .unavailable(explanation) }
        return .enabled
    }
}
