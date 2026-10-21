import AppKit

enum ArabicaBrandAsset {
    @MainActor static let image: NSImage? = {
        // Packaged apps already contain the icon rendered from static/arabica-icon.svg.
        if let url = Bundle.main.url(forResource: "arabica-icon", withExtension: "png"),
            let image = NSImage(contentsOf: url)
        {
            return image
        }
        // Resolve the SwiftPM bundle only when running outside the packaged app.
        guard let url = Bundle.module.url(forResource: "arabica-icon", withExtension: "png") else { return nil }
        return NSImage(contentsOf: url)
    }()
}
