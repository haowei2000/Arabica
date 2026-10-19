// swift-tools-version: 5.10
import PackageDescription

let package = Package(
    name: "ArabicaDesktop",
    platforms: [.macOS(.v14)],
    products: [.executable(name: "ArabicaDesktop", targets: ["ArabicaDesktop"])],
    dependencies: [
        .package(url: "https://github.com/sparkle-project/Sparkle", exact: "2.10.0"),
    ],
    targets: [
        .executableTarget(
            name: "ArabicaDesktop",
            dependencies: [.product(name: "Sparkle", package: "Sparkle")],
            resources: [.copy("Resources/tool-icons")]
        ),
        .testTarget(name: "ArabicaDesktopTests", dependencies: ["ArabicaDesktop"]),
    ]
)
