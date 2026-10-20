import Foundation

enum SidebarSessionStatus: Equatable {
    case idle, loading, loadFailed, running, stopping, needsApproval, failed, completed, cancelled, closed

    var title: String {
        switch self {
        case .idle: return "Ready"
        case .loading: return "Loading"
        case .loadFailed: return "Could not load"
        case .running: return "Running"
        case .stopping: return "Stopping"
        case .needsApproval: return "Approval required"
        case .failed: return "Failed"
        case .completed: return "Completed"
        case .cancelled: return "Cancelled"
        case .closed: return "Closed in This Window"
        }
    }

    var symbol: String {
        switch self {
        case .idle: return "bubble.left"
        case .loading: return "arrow.clockwise"
        case .loadFailed, .failed: return "exclamationmark.circle"
        case .running: return "sparkles"
        case .stopping: return "stop.circle"
        case .needsApproval: return "hand.raised"
        case .completed: return "checkmark.circle"
        case .cancelled: return "stop.circle"
        case .closed: return "xmark.circle"
        }
    }
}
