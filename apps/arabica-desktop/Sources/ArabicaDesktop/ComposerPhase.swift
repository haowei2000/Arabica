import Foundation

struct ComposerConfigurationOption: Identifiable, Equatable {
    struct Choice: Identifiable, Equatable {
        var id: String { value }
        let value: String
        let name: String
        let group: String?
    }

    let id: String
    let name: String
    let currentValue: String
    let choices: [Choice]

    init?(_ raw: [String: Any]) {
        guard let id = raw["id"] as? String,
            let currentValue = raw["currentValue"] as? String,
            let options = raw["options"] as? [[String: Any]],
            raw["type"] as? String == nil || raw["type"] as? String == "select"
        else { return nil }
        self.id = id
        name = raw["name"] as? String ?? id
        self.currentValue = currentValue
        choices = options.flatMap { option -> [Choice] in
            let group = option["name"] as? String
            let values = option["options"] as? [[String: Any]] ?? [option]
            return values.compactMap { choice in
                guard let value = choice["value"] as? String else { return nil }
                return Choice(
                    value: value, name: choice["name"] as? String ?? value,
                    group: option["options"] == nil ? nil : group)
            }
        }
    }

    var currentName: String { name(for: currentValue) }

    func name(for value: String) -> String {
        choices.first(where: { $0.value == value })?.name ?? value
    }
}

struct ComposerConfigurationFeedback: Equatable {
    enum Kind: Equatable { case applying, applied, failed }
    let kind: Kind
    let title: String
    let detail: String
}

/// Present the runtime's confirmed settings as one policy, with model settings scoped to SOE.
struct ComposerPolicySelection: Equatable {
    static let soeValue = "soe"

    let policy: ComposerConfigurationOption?
    let model: ComposerConfigurationOption?
    let thinking: ComposerConfigurationOption?

    init(options: [ComposerConfigurationOption]) {
        policy = options.first { $0.id == "blend.policy" }
        let isSOE = policy == nil || policy?.currentValue == Self.soeValue
        model = isSOE ? options.first { $0.id == "model" } : nil
        thinking = isSOE ? options.first { $0.id == "thinking" } : nil
    }

    var isSOE: Bool { policy == nil || policy?.currentValue == Self.soeValue }
    var hasOptions: Bool { policy != nil || model != nil }

    var currentName: String {
        guard isSOE else { return policy?.currentName ?? "Unknown" }
        return model.map { "SOE · \($0.currentName)" } ?? "SOE"
    }

    var help: String {
        guard hasOptions else { return "Policy settings become available after a conversation loads." }
        guard isSOE else {
            return "Policy: \(currentName). Model routing and thinking levels come from the configuration file."
        }
        let detail = thinking.map { " Thinking: \($0.currentName)." } ?? ""
        return "Policy: \(currentName).\(detail) Uses one model for every step."
    }
}

enum ComposerPrimaryAction: Equatable {
    case openWorkspace, reconnect, createConversation, reloadConversation, send

    var title: String {
        switch self {
        case .openWorkspace: return "Open Workspace…"
        case .reconnect: return "Reconnect"
        case .createConversation: return "New Conversation"
        case .reloadConversation: return "Reload"
        case .send: return "Send"
        }
    }

    var symbol: String {
        switch self {
        case .openWorkspace: return "folder"
        case .reconnect, .reloadConversation: return "arrow.clockwise"
        case .createConversation: return "square.and.pencil"
        case .send: return "arrow.up"
        }
    }
}

enum ComposerPhase: Equatable {
    case chooseWorkspace, connecting, disconnected, createConversation, creatingConversation
    case loadingConversation, conversationUnavailable, updatingOptions
    case waitingForPermission, working, stopping, empty, ready

    var primaryAction: ComposerPrimaryAction {
        switch self {
        case .chooseWorkspace: return .openWorkspace
        case .disconnected: return .reconnect
        case .createConversation: return .createConversation
        case .conversationUnavailable: return .reloadConversation
        default: return .send
        }
    }

    var title: String {
        switch self {
        case .chooseWorkspace: return "Choose a workspace"
        case .connecting: return "Connecting…"
        case .disconnected: return "Agent disconnected"
        case .createConversation: return "Start a conversation"
        case .creatingConversation: return "Creating conversation…"
        case .loadingConversation: return "Loading conversation…"
        case .conversationUnavailable: return "Conversation unavailable"
        case .updatingOptions: return "Applying session options…"
        case .waitingForPermission: return "Approval required"
        case .working: return "Agent is working"
        case .stopping: return "Stopping…"
        case .empty: return "Ready"
        case .ready: return "Ready to send"
        }
    }

    var detail: String {
        switch self {
        case .chooseWorkspace: return "Open a folder to connect the agent. Your draft stays here."
        case .connecting: return "Your draft is kept while the agent connects."
        case .disconnected: return "Reconnect before sending. Your draft is kept."
        case .createConversation: return "Create a conversation to send this draft."
        case .creatingConversation: return "Your current draft is kept while the new conversation is created."
        case .loadingConversation: return "You can keep writing while conversation history loads."
        case .conversationUnavailable: return "Reload this conversation before sending."
        case .updatingOptions: return "Wait for the agent to confirm the change before sending."
        case .waitingForPermission: return "Resolve the permission request to continue."
        case .working: return "You can draft the next message while the agent works."
        case .stopping: return "Waiting for the current run to finish. Your next draft is kept."
        case .empty: return "Write a message or attach a file."
        case .ready: return "Return to send. Shift-Return for a new line."
        }
    }

    var symbol: String {
        switch self {
        case .chooseWorkspace: return "folder"
        case .connecting, .creatingConversation, .loadingConversation, .updatingOptions:
            return "arrow.triangle.2.circlepath"
        case .disconnected: return "bolt.slash"
        case .createConversation: return "square.and.pencil"
        case .conversationUnavailable: return "exclamationmark.circle"
        case .waitingForPermission: return "hand.raised"
        case .working: return "sparkles"
        case .stopping: return "stop.circle"
        case .empty, .ready: return "checkmark.circle"
        }
    }

    var showsProgress: Bool {
        switch self {
        case .connecting, .creatingConversation, .loadingConversation, .updatingOptions, .working, .stopping:
            return true
        default: return false
        }
    }
}

@MainActor
extension DesktopModel {
    var composerConfigurationOptions: [ComposerConfigurationOption] {
        configOptions.compactMap(ComposerConfigurationOption.init).filter { !$0.choices.isEmpty }
    }

    var composerPhase: ComposerPhase {
        if isConnecting { return .connecting }
        if !isConnected { return workspace == nil ? .chooseWorkspace : .disconnected }
        if isCreatingSession { return .creatingConversation }
        if selectedSessionID == nil { return isLoadingSession ? .creatingConversation : .createConversation }
        if isLoadingSession { return .loadingConversation }
        if isStopping { return .stopping }
        if isWaitingForPermission { return .waitingForPermission }
        if isRunning { return .working }
        if !isSessionReady { return .conversationUnavailable }
        if isUpdatingSessionConfiguration { return .updatingOptions }
        return draft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty && attachedFiles.isEmpty ? .empty : .ready
    }
}
