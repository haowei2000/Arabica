import Foundation
import UniformTypeIdentifiers

enum PromptAttachments {
    static let maxImageBytes = 5 * 1024 * 1024
    static let maxImages = 4

    static func blocks(for files: [URL], supportsImages: Bool) throws -> [[String: Any]] {
        var imageCount = 0
        return try files.map { url in
            let type = UTType(filenameExtension: url.pathExtension)
            guard type?.conforms(to: .image) == true else {
                return ["type": "resource_link", "name": url.lastPathComponent, "uri": url.absoluteString]
            }
            guard supportsImages else {
                throw ACPError.remote("This agent does not support image input. Rebuild or update the Arabica agent.")
            }
            guard let mimeType = type?.preferredMIMEType,
                  ["image/png", "image/jpeg", "image/webp", "image/gif"].contains(mimeType) else {
                throw ACPError.remote("Images must be PNG, JPEG, WebP, or GIF. Convert \(url.lastPathComponent) before attaching it.")
            }
            imageCount += 1
            guard imageCount <= maxImages else {
                throw ACPError.remote("Attach at most 4 images per message.")
            }
            let handle = try FileHandle(forReadingFrom: url)
            defer { try? handle.close() }
            let data = try handle.read(upToCount: maxImageBytes + 1) ?? Data()
            guard !data.isEmpty, data.count <= maxImageBytes else {
                throw ACPError.remote("Each image must contain between 1 byte and 5 MiB: \(url.lastPathComponent)")
            }
            return ["type": "image", "mimeType": mimeType, "data": data.base64EncodedString()]
        }
    }
}
