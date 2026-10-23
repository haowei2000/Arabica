import Foundation

/// Reuses semantic parsing without retaining fonts, colors, or any view state.
@MainActor
final class MarkdownRenderCache {
    static let shared = MarkdownRenderCache()

    private var blockCache = MarkdownSourceCache<[MarkdownBlock]>(
        entryLimit: 128, sourceByteLimit: 4 * 1024 * 1024, entrySourceByteLimit: 256 * 1024
    )
    private var inlineCache = MarkdownSourceCache<AttributedString>(
        entryLimit: 2048, sourceByteLimit: 2 * 1024 * 1024, entrySourceByteLimit: 64 * 1024
    )

    func blocks(for source: String) -> [MarkdownBlock] {
        blockCache.value(for: source) { MarkdownBlock.parse(source) }
    }

    func inline(for source: String) -> AttributedString {
        inlineCache.value(for: source) {
            let options = AttributedString.MarkdownParsingOptions(
                interpretedSyntax: .inlineOnlyPreservingWhitespace
            )
            return (try? AttributedString(markdown: source, options: options)) ?? AttributedString(source)
        }
    }
}

/// A deterministic LRU with hard entry and source-size limits, including for streaming drafts.
struct MarkdownSourceCache<Value> {
    private struct Entry {
        let value: Value
        let sourceBytes: Int
        var access: UInt64
    }

    private let entryLimit: Int
    private let sourceByteLimit: Int
    private let entrySourceByteLimit: Int
    private var entries: [String: Entry] = [:]
    private var access: UInt64 = 0
    private(set) var sourceBytes: Int = 0

    var count: Int { entries.count }

    init(entryLimit: Int, sourceByteLimit: Int, entrySourceByteLimit: Int) {
        self.entryLimit = max(0, entryLimit)
        self.sourceByteLimit = max(0, sourceByteLimit)
        self.entrySourceByteLimit = max(0, entrySourceByteLimit)
    }

    mutating func value(for source: String, makeValue: () -> Value) -> Value {
        let stamp = nextAccess()
        if var entry = entries[source] {
            entry.access = stamp
            entries[source] = entry
            return entry.value
        }

        let value = makeValue()
        let bytes = source.utf8.count
        guard entryLimit > 0, sourceByteLimit > 0,
            bytes <= sourceByteLimit, bytes <= entrySourceByteLimit
        else { return value }

        while entries.count >= entryLimit || sourceBytes > sourceByteLimit - bytes {
            guard let oldest = entries.min(by: { $0.value.access < $1.value.access }) else { break }
            sourceBytes -= oldest.value.sourceBytes
            entries.removeValue(forKey: oldest.key)
        }
        entries[source] = Entry(value: value, sourceBytes: bytes, access: stamp)
        sourceBytes += bytes
        return value
    }

    private mutating func nextAccess() -> UInt64 {
        if access == .max {
            // Preserve LRU ordering even if a process outlives the counter.
            let keys: [String] = entries.sorted(by: { $0.value.access < $1.value.access }).map(\.key)
            for (index, key) in keys.enumerated() {
                entries[key]?.access = UInt64(index)
            }
            access = UInt64(entries.count)
        }
        access += 1
        return access
    }
}
