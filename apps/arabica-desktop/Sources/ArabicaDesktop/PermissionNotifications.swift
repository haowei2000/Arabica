import AppKit
import Foundation
import UserNotifications

extension Notification.Name {
    static let arabicaPermissionNotificationOpened = Notification.Name("arabica.permissionNotificationOpened")
}

@MainActor
final class PermissionNotifications: NSObject, UNUserNotificationCenterDelegate {
    static let shared = PermissionNotifications()
    private var isApplicationActive = true

    private override init() {
        super.init()
        UNUserNotificationCenter.current().delegate = self
        UNUserNotificationCenter.current().requestAuthorization(options: [.alert, .sound]) { _, _ in }
        NotificationCenter.default.addObserver(
            self,
            selector: #selector(applicationDidBecomeActive),
            name: NSApplication.didBecomeActiveNotification,
            object: nil
        )
        NotificationCenter.default.addObserver(
            self,
            selector: #selector(applicationDidResignActive),
            name: NSApplication.didResignActiveNotification,
            object: nil
        )
    }

    func post(sessionID: String, title: String, detail: String) {
        guard !isApplicationActive else { return }
        let content = UNMutableNotificationContent()
        content.title = "Permission required"
        content.subtitle = title
        content.body = detail.isEmpty ? "Arabica is waiting for your approval." : String(detail.prefix(240))
        content.sound = .default
        content.userInfo = ["sessionId": sessionID]
        let request = UNNotificationRequest(
            identifier: "arabica-permission-\(sessionID)",
            content: content,
            trigger: nil
        )
        UNUserNotificationCenter.current().add(request)
    }

    @objc private func applicationDidBecomeActive() { isApplicationActive = true }
    @objc private func applicationDidResignActive() { isApplicationActive = false }

    nonisolated func userNotificationCenter(
        _ center: UNUserNotificationCenter,
        didReceive response: UNNotificationResponse,
        withCompletionHandler completionHandler: @escaping () -> Void
    ) {
        let sessionID = response.notification.request.content.userInfo["sessionId"] as? String
        Task { @MainActor in
            NSApp.activate(ignoringOtherApps: true)
            if let sessionID {
                NotificationCenter.default.post(
                    name: .arabicaPermissionNotificationOpened,
                    object: nil,
                    userInfo: ["sessionId": sessionID]
                )
            }
            completionHandler()
        }
    }
}
