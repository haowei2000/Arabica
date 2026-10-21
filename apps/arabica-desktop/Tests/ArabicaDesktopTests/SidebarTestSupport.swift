import Foundation

@testable import ArabicaDesktop

@MainActor
extension SidebarLibrary {
    static func testInstance() -> SidebarLibrary {
        SidebarLibrary(defaults: SidebarMemoryDefaults(suiteName: "ArabicaDesktopTests.Memory.\(UUID())")!)
    }
}

private final class SidebarMemoryDefaults: UserDefaults, @unchecked Sendable {
    private let lock = NSLock()
    private var values: [String: Any] = [:]

    override func data(forKey defaultName: String) -> Data? {
        lock.withLock { values[defaultName] as? Data }
    }

    override func set(_ value: Any?, forKey defaultName: String) {
        lock.withLock { values[defaultName] = value }
    }
}
