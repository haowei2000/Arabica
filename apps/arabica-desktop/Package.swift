// swift-tools-version: 5.10
import PackageDescription

let package = Package(
    name: "ArabicaDesktop",
    platforms: [.macOS(.v14)],
    products: [.executable(name: "ArabicaDesktop", targets: ["ArabicaDesktop"])],
    targets: [
        .executableTarget(
            name: "ArabicaDesktop",
            resources: [.copy("Resources/tool-icons")]
        ),
        .testTarget(name: "ArabicaDesktopTests", dependencies: ["ArabicaDesktop"]),
    ]
)
