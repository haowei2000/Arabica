import AppKit
import SwiftUI

/// A deliberately bounded, offline preview. Unsupported syntax keeps its source visible
/// instead of dropping statements or loading scripts supplied by a message.
enum MermaidDiagram: Equatable {
    case flowchart(MermaidFlowchart)
    case sequence(MermaidSequence)

    static func parse(_ source: String) throws -> Self {
        guard source.count <= 30_000 else { throw MermaidPreviewError.tooLarge }
        let statements = try MermaidScanner.statements(source)
        guard let header = statements.first else { throw MermaidPreviewError.incomplete }
        if header == "sequenceDiagram" {
            return .sequence(try MermaidSequence.parse(Array(statements.dropFirst())))
        }
        let parts = header.split(whereSeparator: \.isWhitespace).map(String.init)
        if parts.count == 2, ["flowchart", "graph"].contains(parts[0]),
            let direction = MermaidFlowchart.Direction(rawValue: parts[1])
        {
            return .flowchart(try MermaidFlowchart.parse(Array(statements.dropFirst()), direction: direction))
        }
        throw MermaidPreviewError.unsupported
    }

    var title: String {
        switch self {
        case .flowchart: return "Flowchart"
        case .sequence: return "Sequence diagram"
        }
    }
}

enum MermaidPreviewError: LocalizedError {
    case unsupported, incomplete, tooLarge

    var errorDescription: String? {
        switch self {
        case .unsupported:
            return "Preview supports basic flowcharts and sequence diagrams. This syntax is available as source below."
        case .incomplete:
            return "The diagram is incomplete. Its source is available below."
        case .tooLarge:
            return "This diagram is too large to preview. Its source is available below."
        }
    }
}

struct MermaidFlowchart: Equatable {
    enum Direction: String {
        case topDown = "TD"
        case topToBottom = "TB"
        case bottomToTop = "BT"
        case leftToRight = "LR"
        case rightToLeft = "RL"
        var horizontal: Bool { self == .leftToRight || self == .rightToLeft }
        var reversed: Bool { self == .bottomToTop || self == .rightToLeft }
    }
    enum NodeShape: Equatable { case rectangle, rounded, circle, diamond }
    struct Node: Equatable, Identifiable {
        let id: String
        var label: String
        var shape: NodeShape
    }
    struct Edge: Equatable {
        let from: String
        let to: String
        let label: String
        let dashed: Bool
        let thick: Bool
        let arrow: Bool
    }

    let direction: Direction
    var nodes: [Node] = []
    var edges: [Edge] = []

    static func parse(_ statements: [String], direction: Direction) throws -> Self {
        var chart = Self(direction: direction)
        for statement in statements {
            var scanner = MermaidScanner(statement)
            var previous = try scanner.node()
            chart.upsert(previous.node, explicit: previous.explicit)
            while !scanner.isAtEnd {
                let edge = try scanner.edge()
                let next = try scanner.node()
                chart.upsert(next.node, explicit: next.explicit)
                chart.edges.append(
                    Edge(
                        from: previous.node.id, to: next.node.id, label: edge.label,
                        dashed: edge.token.contains("."), thick: edge.token.contains("="),
                        arrow: edge.token.hasSuffix(">")
                    ))
                previous = next
                guard chart.edges.count <= 160 else { throw MermaidPreviewError.tooLarge }
            }
            guard chart.nodes.count <= 80 else { throw MermaidPreviewError.tooLarge }
        }
        guard !chart.nodes.isEmpty else { throw MermaidPreviewError.incomplete }
        return chart
    }

    private mutating func upsert(_ node: Node, explicit: Bool) {
        if let index = nodes.firstIndex(where: { $0.id == node.id }) {
            if explicit { nodes[index] = node }
        } else {
            nodes.append(node)
        }
    }
}

struct MermaidSequence: Equatable {
    struct Participant: Equatable, Identifiable {
        let id: String
        var label: String
        var actor: Bool
    }
    struct Message: Equatable {
        let from: String
        let to: String
        let label: String
        let dashed: Bool
        let filledArrow: Bool
    }
    var participants: [Participant] = []
    var messages: [Message] = []
    var numbered = false

    static func parse(_ statements: [String]) throws -> Self {
        var diagram = Self()
        for statement in statements {
            if statement == "autonumber" {
                // Starting numbering partway through a diagram has different semantics.
                guard diagram.messages.isEmpty else { throw MermaidPreviewError.unsupported }
                diagram.numbered = true
                continue
            }
            let declaration = #"^(participant|actor)\s+([\p{L}\p{N}_-]+)(?:\s+as\s+(.+))?$"#
            if let match = statement.matching(declaration) {
                let id = match[2]
                diagram.upsert(
                    id, label: match.count > 3 && !match[3].isEmpty ? match[3] : id, actor: match[1] == "actor")
                guard diagram.participants.count <= 20 else { throw MermaidPreviewError.tooLarge }
                continue
            }
            let message = #"^([\p{L}\p{N}_-]+?)\s*(-->>|->>|-->|->)\s*([\p{L}\p{N}_-]+)\s*:\s*(.*)$"#
            guard let match = statement.matching(message) else { throw MermaidPreviewError.unsupported }
            diagram.upsert(match[1])
            diagram.upsert(match[3])
            diagram.messages.append(
                Message(
                    from: match[1], to: match[3], label: MermaidScanner.label(match[4]),
                    dashed: match[2].hasPrefix("--"), filledArrow: match[2].hasSuffix(">>")
                ))
            guard diagram.participants.count <= 20, diagram.messages.count <= 100 else {
                throw MermaidPreviewError.tooLarge
            }
        }
        guard !diagram.participants.isEmpty else { throw MermaidPreviewError.incomplete }
        return diagram
    }

    private mutating func upsert(_ id: String, label: String? = nil, actor: Bool = false) {
        if let index = participants.firstIndex(where: { $0.id == id }) {
            if let label { participants[index] = Participant(id: id, label: MermaidScanner.label(label), actor: actor) }
        } else {
            participants.append(Participant(id: id, label: MermaidScanner.label(label ?? id), actor: actor))
        }
    }
}

private struct MermaidScanner {
    var remaining: Substring
    init(_ source: String) { remaining = source[...] }
    var isAtEnd: Bool { remaining.allSatisfy(\.isWhitespace) }

    static func statements(_ source: String) throws -> [String] {
        var result: [String] = []
        var statement = ""
        var nesting = 0
        var quoted = false
        let lines = source.replacingOccurrences(of: "\r\n", with: "\n")
            .replacingOccurrences(of: "\r", with: "\n").components(separatedBy: "\n")
        for line in lines {
            let trimmed = line.trimmingCharacters(in: .whitespaces)
            if nesting == 0, !quoted, trimmed.hasPrefix("%%") {
                guard !trimmed.hasPrefix("%%{") else { throw MermaidPreviewError.unsupported }
                continue
            }
            for character in line {
                if character == "\"" { quoted.toggle() }
                if !quoted {
                    if "[({".contains(character) { nesting += 1 }
                    if "])}".contains(character) { nesting -= 1 }
                }
                if character == ";", nesting == 0, !quoted {
                    let value = statement.trimmingCharacters(in: .whitespacesAndNewlines)
                    if !value.isEmpty { result.append(value) }
                    statement = ""
                } else {
                    statement.append(character)
                }
            }
            guard nesting >= 0 else { throw MermaidPreviewError.unsupported }
            if nesting == 0, !quoted {
                let value = statement.trimmingCharacters(in: .whitespacesAndNewlines)
                if !value.isEmpty { result.append(value) }
                statement = ""
            } else {
                statement.append("\n")
            }
        }
        guard nesting == 0, !quoted else { throw MermaidPreviewError.incomplete }
        return result
    }

    mutating func node() throws -> (node: MermaidFlowchart.Node, explicit: Bool) {
        skipSpace()
        var id = ""
        while let character = remaining.first {
            if character.isLetter || character.isNumber || character == "_"
                || (character == "-" && !remaining.hasPrefix("--") && !remaining.hasPrefix("-."))
            {
                id.append(character)
                remaining.removeFirst()
            } else {
                break
            }
        }
        guard !id.isEmpty, id.first != "-" else { throw MermaidPreviewError.unsupported }
        skipSpace()
        let shapes: [(String, String, MermaidFlowchart.NodeShape)] = [
            ("((", "))", .circle), ("([", "])", .rounded), ("[", "]", .rectangle),
            ("(", ")", .rounded), ("{", "}", .diamond),
        ]
        for (open, close, shape) in shapes where remaining.hasPrefix(open) {
            remaining = remaining.dropFirst(open.count)
            var text = ""
            var quoted = false
            while !remaining.isEmpty {
                if !quoted, remaining.hasPrefix(close) {
                    remaining = remaining.dropFirst(close.count)
                    return (.init(id: id, label: Self.label(text), shape: shape), true)
                }
                let character = remaining.removeFirst()
                if character == "\"" { quoted.toggle() }
                text.append(character)
            }
            throw MermaidPreviewError.incomplete
        }
        return (.init(id: id, label: id, shape: .rectangle), false)
    }

    mutating func edge() throws -> (token: String, label: String) {
        skipSpace()
        let tokens = ["-.->", "-->", "==>", "---", "-.-", "==="]
        guard let token = tokens.first(where: { remaining.hasPrefix($0) }) else {
            // Mermaid also allows labels between the two parts of a solid arrow.
            if remaining.hasPrefix("--"), let end = remaining.dropFirst(2).range(of: "-->") {
                let text = remaining[remaining.index(remaining.startIndex, offsetBy: 2)..<end.lowerBound]
                guard !text.isEmpty, !text.contains("|"), !text.contains("[") else {
                    throw MermaidPreviewError.unsupported
                }
                remaining = remaining[end.upperBound...]
                return ("-->", Self.label(String(text)))
            }
            throw MermaidPreviewError.unsupported
        }
        remaining = remaining.dropFirst(token.count)
        skipSpace()
        var text = ""
        if remaining.first == "|" {
            remaining.removeFirst()
            guard let end = remaining.firstIndex(of: "|") else { throw MermaidPreviewError.incomplete }
            text = String(remaining[..<end])
            remaining = remaining[remaining.index(after: end)...]
        }
        return (token, Self.label(text))
    }

    mutating func skipSpace() { remaining = remaining.drop(while: \.isWhitespace) }

    static func label(_ value: String) -> String {
        var text = value.trimmingCharacters(in: .whitespacesAndNewlines)
        if text.count >= 2, text.hasPrefix("\""), text.hasSuffix("\"") {
            text = String(text.dropFirst().dropLast())
        }
        return text.replacingOccurrences(
            of: #"<br\s*/?>"#, with: "\n", options: [.regularExpression, .caseInsensitive]
        )
        .replacingOccurrences(of: "&quot;", with: "\"")
        .replacingOccurrences(of: "&amp;", with: "&")
    }
}

extension String {
    fileprivate func matching(_ pattern: String) -> [String]? {
        guard let expression = try? NSRegularExpression(pattern: pattern),
            let match = expression.firstMatch(in: self, range: NSRange(startIndex..., in: self))
        else { return nil }
        return (0..<match.numberOfRanges).map { index in
            Range(match.range(at: index), in: self).map { String(self[$0]) } ?? ""
        }
    }
}

struct MermaidBlockView: View {
    let source: String
    @State private var showSource = false

    var body: some View {
        let result = Result { try MermaidDiagram.parse(source) }
        VStack(alignment: .leading, spacing: 10) {
            switch result {
            case .success(let diagram):
                HStack {
                    Label(diagram.title, systemImage: "point.3.connected.trianglepath.dotted")
                        .font(AppFont.sans(11, weight: .medium))
                        .foregroundStyle(Palette.muted)
                    Spacer()
                    Button(showSource ? "Preview" : "Source") { showSource.toggle() }
                        .font(AppFont.sans(11))
                        .buttonStyle(.plain)
                        .foregroundStyle(Palette.accent)
                        .help(showSource ? "Show diagram preview" : "Show and copy Mermaid source")
                }
                if showSource {
                    CodeBlockView(code: source, language: "mermaid")
                } else {
                    switch diagram {
                    case .flowchart(let chart): MermaidFlowchartView(chart: chart)
                    case .sequence(let sequence): MermaidSequenceView(sequence: sequence)
                    }
                }
            case .failure(let error):
                Label(error.localizedDescription, systemImage: "info.circle")
                    .font(AppFont.sans(11))
                    .foregroundStyle(Palette.muted)
                    .fixedSize(horizontal: false, vertical: true)
                CodeBlockView(code: source, language: "mermaid")
            }
        }
        .padding(12)
        .background(Palette.card, in: RoundedRectangle(cornerRadius: 8))
        .overlay(RoundedRectangle(cornerRadius: 8).stroke(Palette.rule, lineWidth: 1))
    }
}

struct MermaidFlowchartLayout {
    let frames: [String: CGRect]
    let ranks: [String: Int]
    let size: CGSize

    init(chart: MermaidFlowchart, fontSize: CGFloat) {
        var ranks: [String: Int] = [:]
        var incoming = Dictionary(uniqueKeysWithValues: chart.nodes.map { ($0.id, 0) })
        for edge in chart.edges { incoming[edge.to, default: 0] += 1 }
        var queue = chart.nodes.filter { incoming[$0.id] == 0 }.map(\.id)
        for id in queue { ranks[id] = 0 }
        var cursor = 0
        while cursor < queue.count {
            let id = queue[cursor]
            cursor += 1
            for edge in chart.edges where edge.from == id {
                ranks[edge.to] = max(ranks[edge.to, default: 0], ranks[id, default: 0] + 1)
                incoming[edge.to, default: 0] -= 1
                if incoming[edge.to] == 0 { queue.append(edge.to) }
            }
        }
        // Cycles retain every edge and receive deterministic positions after the DAG.
        var cycleRank = (ranks.values.max() ?? -1) + 1
        for node in chart.nodes where !queue.contains(node.id) {
            ranks[node.id] = cycleRank
            cycleRank += 1
        }
        let font = NSFont.systemFont(ofSize: fontSize)
        let sizes = Dictionary(
            uniqueKeysWithValues: chart.nodes.map { node in
                let width: CGFloat = node.shape == .diamond ? 200 : 180
                let bounds = (node.label as NSString).boundingRect(
                    with: CGSize(width: width - 40, height: CGFloat.greatestFiniteMagnitude),
                    options: [.usesLineFragmentOrigin, .usesFontLeading], attributes: [.font: font]
                )
                let height = max(
                    node.shape == .diamond ? 84 : 52, ceil(bounds.height) + (node.shape == .diamond ? 48 : 24))
                let side = max(width, height)
                return (
                    node.id,
                    node.shape == .circle ? CGSize(width: side, height: side) : CGSize(width: width, height: height)
                )
            })
        var frames: [String: CGRect] = [:]
        let groups = Dictionary(grouping: chart.nodes, by: { ranks[$0.id, default: 0] })
        let groupKeys = groups.keys.sorted()
        let crossSpans = groupKeys.map { rank in
            groups[rank, default: []].reduce(CGFloat(0)) { sum, node in
                sum + (chart.direction.horizontal ? sizes[node.id]!.height : sizes[node.id]!.width) + 32
            } - 32
        }
        let crossSpan = max(220, crossSpans.max() ?? 220)
        var majorOffset: CGFloat = 24
        for (index, rank) in groupKeys.enumerated() {
            let group = groups[rank, default: []]
            let majorSize =
                group.map { chart.direction.horizontal ? sizes[$0.id]!.width : sizes[$0.id]!.height }.max() ?? 0
            var crossOffset = 24 + (crossSpan - crossSpans[index]) / 2
            for node in group {
                let size = sizes[node.id]!
                let origin =
                    chart.direction.horizontal
                    ? CGPoint(x: majorOffset + (majorSize - size.width) / 2, y: crossOffset)
                    : CGPoint(x: crossOffset, y: majorOffset + (majorSize - size.height) / 2)
                frames[node.id] = CGRect(origin: origin, size: size)
                crossOffset += (chart.direction.horizontal ? size.height : size.width) + 32
            }
            majorOffset += majorSize + 74
        }
        let size =
            chart.direction.horizontal
            ? CGSize(width: majorOffset - 50, height: crossSpan + 138)
            : CGSize(width: crossSpan + 138, height: majorOffset - 50)
        if chart.direction.reversed {
            for (id, frame) in frames {
                frames[id] =
                    chart.direction.horizontal
                    ? CGRect(x: size.width - frame.maxX, y: frame.minY, width: frame.width, height: frame.height)
                    : CGRect(x: frame.minX, y: size.height - frame.maxY, width: frame.width, height: frame.height)
            }
        }
        self.frames = frames
        self.ranks = ranks
        self.size = size
    }
}

private struct MermaidFlowchartView: View {
    let chart: MermaidFlowchart

    var body: some View {
        let layout = MermaidFlowchartLayout(chart: chart, fontSize: max(7, 12 + ThemeManager.shared.fontSizeDelta))
        ScrollView(layout.size.height > 520 ? [.horizontal, .vertical] : .horizontal) {
            ZStack(alignment: .topLeading) {
                Canvas { context, _ in
                    for (index, edge) in chart.edges.enumerated() {
                        guard let from = layout.frames[edge.from], let to = layout.frames[edge.to] else { continue }
                        let route = edgeRoute(from: from, to: to, edge: edge, index: index, layout: layout)
                        context.stroke(
                            route.path, with: .color(Palette.muted),
                            style: StrokeStyle(
                                lineWidth: edge.thick ? 2.5 : 1.25, dash: edge.dashed ? [5, 4] : []
                            ))
                        if edge.arrow {
                            drawArrow(context: &context, at: route.end, from: route.beforeEnd, filled: true)
                        }
                        if !edge.label.isEmpty {
                            let label = Text(edge.label).font(AppFont.sans(11)).foregroundStyle(Palette.ink)
                            let resolved = context.resolve(label)
                            let measured = resolved.measure(
                                in: CGSize(width: 220, height: CGFloat.greatestFiniteMagnitude))
                            let rect = CGRect(
                                x: route.label.x - measured.width / 2 - 4, y: route.label.y - measured.height / 2 - 3,
                                width: measured.width + 8, height: measured.height + 6)
                            context.fill(Path(roundedRect: rect, cornerRadius: 3), with: .color(Palette.card))
                            context.draw(resolved, in: rect.insetBy(dx: 4, dy: 3))
                        }
                    }
                }
                ForEach(chart.nodes) { node in
                    if let frame = layout.frames[node.id] {
                        Text(node.label)
                            .font(AppFont.sans(12))
                            .foregroundStyle(Palette.ink)
                            .multilineTextAlignment(.center)
                            .padding(.horizontal, node.shape == .diamond ? 32 : 16)
                            .frame(width: frame.width, height: frame.height)
                            .background { MermaidNodeShape(kind: node.shape).fill(Palette.codeBg) }
                            .overlay {
                                MermaidNodeShape(kind: node.shape).stroke(Palette.accent.opacity(0.65), lineWidth: 1)
                            }
                            .offset(x: frame.minX, y: frame.minY)
                    }
                }
            }
            .frame(width: layout.size.width, height: layout.size.height)
        }
        .frame(height: min(520, layout.size.height))
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(
            "Flowchart. " + chart.nodes.map(\.label).joined(separator: ". ") + ". "
                + chart.edges.map { edge in
                    let from = chart.nodes.first(where: { $0.id == edge.from })?.label ?? edge.from
                    let to = chart.nodes.first(where: { $0.id == edge.to })?.label ?? edge.to
                    return "\(from) to \(to)\(edge.label.isEmpty ? "" : ", " + edge.label)"
                }.joined(separator: ". "))
    }

    private func edgeRoute(
        from: CGRect, to: CGRect, edge: MermaidFlowchart.Edge, index: Int,
        layout: MermaidFlowchartLayout
    ) -> (path: Path, end: CGPoint, beforeEnd: CGPoint, label: CGPoint) {
        let horizontal = chart.direction.horizontal
        let reverse: CGFloat = chart.direction.reversed ? -1 : 1
        let fromRank = layout.ranks[edge.from, default: 0]
        let toRank = layout.ranks[edge.to, default: 0]
        var points: [CGPoint]
        if toRank - fromRank != 1 {
            // Route self loops, back edges, and edges skipping layers outside the nodes.
            let lane = (horizontal ? layout.size.height : layout.size.width) - 20 - CGFloat(index % 4) * 14
            points =
                horizontal
                ? [
                    CGPoint(x: from.midX, y: from.maxY), CGPoint(x: from.midX, y: lane),
                    CGPoint(x: to.midX + (edge.from == edge.to ? 20 : 0), y: lane),
                    CGPoint(x: to.midX + (edge.from == edge.to ? 20 : 0), y: to.maxY),
                ]
                : [
                    CGPoint(x: from.maxX, y: from.midY), CGPoint(x: lane, y: from.midY),
                    CGPoint(x: lane, y: to.midY + (edge.from == edge.to ? 16 : 0)),
                    CGPoint(x: to.maxX, y: to.midY + (edge.from == edge.to ? 16 : 0)),
                ]
        } else if horizontal {
            let start = CGPoint(x: reverse > 0 ? from.maxX : from.minX, y: from.midY)
            let end = CGPoint(x: reverse > 0 ? to.minX : to.maxX, y: to.midY)
            let middle = (start.x + end.x) / 2
            points = [start, CGPoint(x: middle, y: start.y), CGPoint(x: middle, y: end.y), end]
        } else {
            let start = CGPoint(x: from.midX, y: reverse > 0 ? from.maxY : from.minY)
            let end = CGPoint(x: to.midX, y: reverse > 0 ? to.minY : to.maxY)
            let middle = (start.y + end.y) / 2
            points = [start, CGPoint(x: start.x, y: middle), CGPoint(x: end.x, y: middle), end]
        }
        var path = Path()
        path.addLines(points)
        return (
            path, points[3], points[2], CGPoint(x: (points[1].x + points[2].x) / 2, y: (points[1].y + points[2].y) / 2)
        )
    }
}

private struct MermaidNodeShape: Shape {
    let kind: MermaidFlowchart.NodeShape
    func path(in rect: CGRect) -> Path {
        switch kind {
        case .rectangle: return Path(roundedRect: rect, cornerRadius: 3)
        case .rounded: return Path(roundedRect: rect, cornerRadius: min(22, rect.height / 2))
        case .circle: return Path(ellipseIn: rect)
        case .diamond:
            var path = Path()
            path.addLines([
                CGPoint(x: rect.midX, y: rect.minY), CGPoint(x: rect.maxX, y: rect.midY),
                CGPoint(x: rect.midX, y: rect.maxY), CGPoint(x: rect.minX, y: rect.midY),
            ])
            path.closeSubpath()
            return path
        }
    }
}

private struct MermaidSequenceView: View {
    let sequence: MermaidSequence

    var body: some View {
        let fontSize = max(7, 11 + ThemeManager.shared.fontSizeDelta)
        let width = CGFloat(sequence.participants.count) * 200 + 80
        let headerHeight =
            sequence.participants.map { participant in
                let labelHeight = (participant.label as NSString).boundingRect(
                    with: CGSize(width: 136, height: CGFloat.greatestFiniteMagnitude),
                    options: [.usesLineFragmentOrigin, .usesFontLeading],
                    attributes: [.font: NSFont.systemFont(ofSize: fontSize + 1)]
                ).height
                return max(54, ceil(labelHeight) + (participant.actor ? 42 : 24))
            }.max() ?? 54
        let rowHeights = sequence.messages.enumerated().map { index, message in
            let from = sequence.participants.firstIndex(where: { $0.id == message.from }) ?? 0
            let to = sequence.participants.firstIndex(where: { $0.id == message.to }) ?? 0
            let labelWidth = from == to ? CGFloat(180) : CGFloat(abs(from - to)) * 200 - 16
            let label = (sequence.numbered ? "\(index + 1). " : "") + message.label
            let labelHeight = (label as NSString).boundingRect(
                with: CGSize(width: labelWidth, height: CGFloat.greatestFiniteMagnitude),
                options: [.usesLineFragmentOrigin, .usesFontLeading],
                attributes: [.font: NSFont.systemFont(ofSize: fontSize)]
            ).height
            return max(CGFloat(66), ceil(labelHeight) + (from == to ? 54 : 34))
        }
        let height = rowHeights.reduce(headerHeight + 66, +)
        ScrollView(height > 520 ? [.horizontal, .vertical] : .horizontal) {
            ZStack(alignment: .topLeading) {
                Canvas { context, _ in
                    for index in sequence.participants.indices {
                        let x = CGFloat(index) * 200 + 120
                        var line = Path()
                        line.move(to: CGPoint(x: x, y: headerHeight + 10))
                        line.addLine(to: CGPoint(x: x, y: height - 16))
                        context.stroke(
                            line, with: .color(Palette.rule), style: StrokeStyle(lineWidth: 1, dash: [5, 4]))
                    }
                    var y: CGFloat = headerHeight + 28
                    for (index, message) in sequence.messages.enumerated() {
                        let from = sequence.participants.firstIndex(where: { $0.id == message.from }) ?? 0
                        let to = sequence.participants.firstIndex(where: { $0.id == message.to }) ?? 0
                        let start = CGPoint(x: CGFloat(from) * 200 + 120, y: y + rowHeights[index] - 24)
                        let end = CGPoint(x: CGFloat(to) * 200 + 120, y: start.y)
                        var points = [start, end]
                        if from == to {
                            points = [
                                CGPoint(x: start.x, y: start.y - 20), CGPoint(x: start.x + 54, y: start.y - 20),
                                CGPoint(x: start.x + 54, y: start.y), end,
                            ]
                        }
                        var line = Path()
                        line.addLines(points)
                        context.stroke(
                            line, with: .color(Palette.muted),
                            style: StrokeStyle(lineWidth: 1.25, dash: message.dashed ? [5, 4] : []))
                        drawArrow(
                            context: &context, at: end, from: points[points.count - 2], filled: message.filledArrow)
                        let label = Text((sequence.numbered ? "\(index + 1). " : "") + message.label)
                            .font(AppFont.sans(11)).foregroundStyle(Palette.ink)
                        let left = from == to ? start.x + 8 : min(start.x, end.x) + 8
                        let labelWidth = from == to ? CGFloat(180) : abs(start.x - end.x) - 16
                        context.draw(
                            label,
                            in: CGRect(
                                x: left, y: y, width: labelWidth, height: rowHeights[index] - (from == to ? 48 : 28)))
                        y += rowHeights[index]
                    }
                }
                ForEach(Array(sequence.participants.enumerated()), id: \.element.id) { index, participant in
                    VStack(spacing: 3) {
                        if participant.actor { Image(systemName: "person").font(AppFont.sans(14)) }
                        Text(participant.label).font(AppFont.sans(12, weight: .medium))
                    }
                    .foregroundStyle(Palette.ink)
                    .multilineTextAlignment(.center)
                    .padding(.horizontal, 12)
                    .frame(width: 160, height: headerHeight)
                    .background(Palette.codeBg, in: RoundedRectangle(cornerRadius: 6))
                    .overlay(RoundedRectangle(cornerRadius: 6).stroke(Palette.accent.opacity(0.65), lineWidth: 1))
                    .offset(x: CGFloat(index) * 200 + 40, y: 10)
                }
            }
            .frame(width: width, height: height)
        }
        .frame(height: min(520, height))
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(
            "Sequence diagram. "
                + sequence.messages.map { message in
                    let from = sequence.participants.first(where: { $0.id == message.from })?.label ?? message.from
                    let to = sequence.participants.first(where: { $0.id == message.to })?.label ?? message.to
                    return "\(from) to \(to): \(message.label)"
                }.joined(separator: ". "))
    }
}

private func drawArrow(context: inout GraphicsContext, at end: CGPoint, from start: CGPoint, filled: Bool) {
    let angle = atan2(end.y - start.y, end.x - start.x)
    let length: CGFloat = 7
    let left = CGPoint(x: end.x - length * cos(angle - .pi / 6), y: end.y - length * sin(angle - .pi / 6))
    let right = CGPoint(x: end.x - length * cos(angle + .pi / 6), y: end.y - length * sin(angle + .pi / 6))
    var arrow = Path()
    arrow.addLines([left, end, right])
    if filled {
        arrow.closeSubpath()
        context.fill(arrow, with: .color(Palette.muted))
    } else {
        context.stroke(arrow, with: .color(Palette.muted), lineWidth: 1.25)
    }
}
