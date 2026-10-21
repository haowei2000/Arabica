import Foundation

/// Shared action descriptors for native outline row and context menus.
struct SidebarMenuItem: Identifiable {
    let id: String
    let title: String
    let systemImage: String?
    let availability: SidebarActionAvailability
    let help: String?
    let isOn: Bool
    let action: (() -> Void)?

    var isSeparator: Bool { action == nil }
    var effectiveHelp: String? { availability.explanation ?? help }

    init(
        _ id: String, _ title: String, systemImage: String? = nil,
        availability: SidebarActionAvailability = .enabled, help: String? = nil,
        isOn: Bool = false, action: @escaping () -> Void
    ) {
        self.id = id
        self.title = title
        self.systemImage = systemImage
        self.availability = availability
        self.help = help
        self.isOn = isOn
        self.action = action
    }

    private init(separator id: String) {
        self.id = id
        title = ""
        systemImage = nil
        availability = .enabled
        help = nil
        isOn = false
        action = nil
    }

    static func separator(_ id: String) -> Self { Self(separator: id) }
}
