import AppKit
import SwiftUI

struct SidebarOutlineConversation {
    let id: String
    var title: String
    let help: String
    var status: SidebarSessionStatus = .idle
    var isUnread = false
    var isPinned = false
    var menuItems: [SidebarMenuItem] = []
}

struct SidebarOutlineProject {
    let id: String
    var title: String
    let help: String
    var conversations: [SidebarOutlineConversation] = []
    var menuItems: [SidebarMenuItem] = []
}

/// A single native tree owns disclosure, selection, keyboard navigation, and scrolling.
struct SidebarOutlineView: NSViewRepresentable {
    let projects: [SidebarOutlineProject]
    let selectedProjectID: String?
    let selectedConversationID: String?
    let collapsedProjectIDs: Set<String>
    let showsAllConversationProjectIDs: Set<String>
    let isSearching: Bool
    let focusRequest: UUID?
    let onSelectProject: (String) -> Void
    let onSelectConversation: (String) -> Void
    let onSetProjectExpanded: (String, Bool) -> Void
    let onShowMore: (String) -> Void

    func makeCoordinator() -> Coordinator { Coordinator(self) }

    func makeNSView(context: Context) -> NSScrollView {
        let scrollView = NSScrollView()
        scrollView.drawsBackground = false
        scrollView.borderType = .noBorder
        scrollView.hasVerticalScroller = true
        scrollView.autohidesScrollers = true
        scrollView.automaticallyAdjustsContentInsets = false
        scrollView.contentInsets = NSEdgeInsets(top: 6, left: 4, bottom: 6, right: 4)

        let outline = SidebarTreeOutlineView()
        let column = NSTableColumn(identifier: NSUserInterfaceItemIdentifier("SidebarTree"))
        column.resizingMask = .autoresizingMask
        outline.addTableColumn(column)
        outline.outlineTableColumn = column
        outline.headerView = nil
        outline.style = .sourceList
        outline.selectionHighlightStyle = .regular
        outline.rowSizeStyle = .custom
        outline.rowHeight = 32
        outline.intercellSpacing = NSSize(width: 0, height: 2)
        outline.indentationPerLevel = 24
        outline.indentationMarkerFollowsCell = true
        outline.columnAutoresizingStyle = .uniformColumnAutoresizingStyle
        outline.backgroundColor = .clear
        outline.allowsMultipleSelection = false
        outline.allowsEmptySelection = true
        outline.usesAlternatingRowBackgroundColors = false
        outline.focusRingType = .none
        outline.setAccessibilityLabel("Projects and Conversations")
        outline.setAccessibilityHelp(
            "Use the arrow keys to navigate. Expand a folder to view its conversations. Press Shift-F10 for actions.")
        outline.dataSource = context.coordinator
        outline.delegate = context.coordinator
        outline.target = context.coordinator
        outline.action = #selector(Coordinator.clickedRow)
        outline.treeCoordinator = context.coordinator
        scrollView.documentView = outline
        context.coordinator.outline = outline
        context.coordinator.update(self)
        return scrollView
    }

    func updateNSView(_ scrollView: NSScrollView, context: Context) {
        context.coordinator.update(self)
    }

    final class Coordinator: NSObject, NSOutlineViewDataSource, NSOutlineViewDelegate {
        var parent: SidebarOutlineView
        weak var outline: SidebarTreeOutlineView?
        private(set) var roots: [SidebarTreeNode] = []
        private var nodes: [String: SidebarTreeNode] = [:]
        private var presentation: [SidebarTreePresentation] = []
        private var isUpdating = false
        private var isChangingDisclosure = false
        private var lastFocusRequest: UUID?
        private var lastFont: NSFont?
        private var menuTargets: [SidebarTreeMenuTarget] = []

        init(_ parent: SidebarOutlineView) {
            self.parent = parent
        }

        func update(_ parent: SidebarOutlineView) {
            self.parent = parent
            guard let outline else { return }
            isUpdating = true
            defer { isUpdating = false }
            let font = sidebarTreeFont()
            let newPresentation = rebuildNodes()
            if newPresentation != presentation || font != lastFont {
                presentation = newPresentation
                lastFont = font
                let scrollOrigin = outline.enclosingScrollView?.contentView.bounds.origin
                outline.rowHeight = max(32, ceil(font.ascender - font.descender + 12))
                outline.reloadData()
                if let scrollOrigin, let scrollView = outline.enclosingScrollView {
                    scrollView.contentView.scroll(to: scrollOrigin)
                    scrollView.reflectScrolledClipView(scrollView.contentView)
                }
            }
            for project in roots {
                let shouldExpand = parent.isSearching || !parent.collapsedProjectIDs.contains(project.modelID)
                if shouldExpand != outline.isItemExpanded(project) {
                    if shouldExpand {
                        outline.expandItem(project)
                    } else {
                        outline.collapseItem(project)
                    }
                }
            }
            synchronizeSelection()
            if let request = parent.focusRequest, lastFocusRequest != request {
                lastFocusRequest = request
                focusResults()
            }
        }

        private func rebuildNodes() -> [SidebarTreePresentation] {
            var retained: [String: SidebarTreeNode] = [:]
            var result: [SidebarTreePresentation] = []
            roots = parent.projects.map { project in
                let projectNode = node(id: "project:\(project.id)", kind: .project, modelID: project.id)
                projectNode.title = project.title
                projectNode.help = project.help
                projectNode.menuItems = project.menuItems
                retained[projectNode.id] = projectNode
                result.append(projectNode.presentation)
                let showsAll = parent.isSearching || parent.showsAllConversationProjectIDs.contains(project.id)
                let conversations = showsAll ? project.conversations : Array(project.conversations.prefix(5))
                projectNode.children = conversations.map { conversation in
                    let child = node(
                        id: "conversation:\(project.id.utf8.count):\(project.id)\(conversation.id)",
                        kind: .conversation,
                        modelID: conversation.id)
                    child.title = conversation.title
                    child.help = conversation.help
                    child.status = conversation.status
                    child.isUnread = conversation.isUnread
                    child.isPinned = conversation.isPinned
                    child.menuItems = conversation.menuItems
                    retained[child.id] = child
                    result.append(child.presentation)
                    return child
                }
                if !showsAll && conversations.count < project.conversations.count {
                    let more = node(id: "more:\(project.id)", kind: .showMore, modelID: project.id)
                    more.title = "Show more"
                    more.help = "Show all \(project.conversations.count) conversations in \(project.title)."
                    retained[more.id] = more
                    result.append(more.presentation)
                    projectNode.children.append(more)
                }
                return projectNode
            }
            nodes = retained
            return result
        }

        private func node(id: String, kind: SidebarTreeNode.Kind, modelID: String) -> SidebarTreeNode {
            nodes[id] ?? SidebarTreeNode(id: id, kind: kind, modelID: modelID)
        }

        private func synchronizeSelection() {
            guard let outline else { return }
            let selectedNode: SidebarTreeNode?
            if let id = parent.selectedConversationID {
                selectedNode = roots.flatMap(\.children).first { $0.kind == .conversation && $0.modelID == id }
            } else if let id = parent.selectedProjectID {
                selectedNode = roots.first { $0.modelID == id }
            } else {
                selectedNode = nil
            }
            let row = selectedNode.map { outline.row(forItem: $0) } ?? -1
            if row != outline.selectedRow {
                outline.selectRowIndexes(row >= 0 ? IndexSet(integer: row) : [], byExtendingSelection: false)
            }
        }

        private func focusResults() {
            guard let outline else { return }
            // Defer until the representable has been attached to its window.
            DispatchQueue.main.async { [weak self, weak outline] in
                guard let self, let outline, !self.parent.projects.isEmpty else { return }
                outline.window?.makeFirstResponder(outline)
                if outline.selectedRow >= 0 { outline.scrollRowToVisible(outline.selectedRow) }
            }
        }

        func outlineView(_ outlineView: NSOutlineView, numberOfChildrenOfItem item: Any?) -> Int {
            (item as? SidebarTreeNode)?.children.count ?? roots.count
        }

        func outlineView(_ outlineView: NSOutlineView, child index: Int, ofItem item: Any?) -> Any {
            if let node = item as? SidebarTreeNode { return node.children[index] }
            return roots[index]
        }

        func outlineView(_ outlineView: NSOutlineView, isItemExpandable item: Any) -> Bool {
            (item as? SidebarTreeNode)?.kind == .project
        }

        func outlineView(_ outlineView: NSOutlineView, shouldCollapseItem item: Any) -> Bool {
            !parent.isSearching
        }

        func outlineView(_ outlineView: NSOutlineView, viewFor tableColumn: NSTableColumn?, item: Any) -> NSView? {
            guard let node = item as? SidebarTreeNode else { return nil }
            let identifier = NSUserInterfaceItemIdentifier("SidebarTreeCell")
            let cell =
                outlineView.makeView(withIdentifier: identifier, owner: self) as? SidebarTreeCellView
                ?? SidebarTreeCellView()
            cell.identifier = identifier
            cell.configure(node, font: lastFont ?? sidebarTreeFont())
            cell.showMenu = { [weak self, weak cell, weak node] in
                guard let self, let cell, let node else { return false }
                return self.presentMenu(for: node, in: cell)
            }
            return cell
        }

        func outlineView(_ outlineView: NSOutlineView, rowViewForItem item: Any) -> NSTableRowView? {
            let row = SidebarTreeRowView()
            row.selectionHighlightStyle = .regular
            if let node = item as? SidebarTreeNode {
                row.setAccessibilityLabel(node.title)
                row.setAccessibilityHelp(node.help)
                row.hasMenu = !node.menuItems.isEmpty
                row.showMenu = { [weak self, weak row, weak node] in
                    guard let self, let row, let node else { return false }
                    return self.presentMenu(for: node, in: row)
                }
                row.setAccessibilityCustomActions(
                    row.hasMenu
                        ? [
                            NSAccessibilityCustomAction(name: "Show Actions") { [weak row] in
                                row?.accessibilityPerformShowMenu() ?? false
                            }
                        ] : [])
            }
            return row
        }

        func outlineViewSelectionDidChange(_ notification: Notification) {
            guard !isUpdating, !isChangingDisclosure, let outline,
                let node = outline.item(atRow: outline.selectedRow) as? SidebarTreeNode
            else {
                return
            }
            switch node.kind {
            case .project: parent.onSelectProject(node.modelID)
            case .conversation: parent.onSelectConversation(node.modelID)
            case .showMore: break
            }
        }

        func outlineViewItemWillCollapse(_ notification: Notification) { isChangingDisclosure = true }
        func outlineViewItemDidExpand(_ notification: Notification) { recordExpansion(notification, expanded: true) }
        func outlineViewItemDidCollapse(_ notification: Notification) {
            recordExpansion(notification, expanded: false)
            isChangingDisclosure = false
        }

        private func recordExpansion(_ notification: Notification, expanded: Bool) {
            guard !isUpdating, let node = notification.userInfo?["NSObject"] as? SidebarTreeNode else { return }
            guard !parent.isSearching else {
                if !expanded { outline?.expandItem(node) }
                return
            }
            parent.onSetProjectExpanded(node.modelID, expanded)
        }

        @objc func clickedRow() {
            guard let outline, let node = outline.item(atRow: outline.clickedRow) as? SidebarTreeNode,
                node.kind == .showMore
            else { return }
            parent.onShowMore(node.modelID)
        }

        func activateSelectedRow() -> Bool {
            guard let outline, let node = outline.item(atRow: outline.selectedRow) as? SidebarTreeNode,
                node.kind == .showMore
            else { return false }
            parent.onShowMore(node.modelID)
            return true
        }

        func contextMenu(for row: Int) -> NSMenu? {
            guard let outline, let node = outline.item(atRow: row) as? SidebarTreeNode,
                !node.menuItems.isEmpty
            else { return nil }
            return menu(for: node)
        }

        @discardableResult
        func showSelectedRowMenu() -> Bool {
            guard let outline, let node = outline.item(atRow: outline.selectedRow) as? SidebarTreeNode else {
                return false
            }
            let rowBounds = outline.rect(ofRow: outline.selectedRow)
            return presentMenu(for: node, in: outline, at: NSPoint(x: rowBounds.maxX - 24, y: rowBounds.maxY))
        }

        private func presentMenu(for node: SidebarTreeNode, in view: NSView, at point: NSPoint? = nil) -> Bool {
            guard !node.menuItems.isEmpty, view.window != nil else { return false }
            menu(for: node).popUp(
                positioning: nil, at: point ?? NSPoint(x: view.bounds.maxX - 24, y: view.bounds.maxY), in: view)
            return true
        }

        private func menu(for node: SidebarTreeNode) -> NSMenu {
            let menu = NSMenu()
            menu.autoenablesItems = false
            menuTargets.removeAll()
            for item in node.menuItems {
                guard !item.isSeparator else {
                    menu.addItem(.separator())
                    continue
                }
                let target = SidebarTreeMenuTarget(item)
                menuTargets.append(target)
                let command = NSMenuItem(
                    title: item.title, action: #selector(SidebarTreeMenuTarget.invokeMenuAction), keyEquivalent: "")
                command.target = target
                command.isEnabled = item.availability.isEnabled
                command.toolTip = item.effectiveHelp
                command.setAccessibilityHelp(item.effectiveHelp)
                command.state = item.isOn ? .on : .off
                if let image = item.systemImage {
                    command.image = NSImage(systemSymbolName: image, accessibilityDescription: nil)
                }
                menu.addItem(command)
            }
            return menu
        }
    }
}

final class SidebarTreeNode: NSObject {
    enum Kind: Equatable { case project, conversation, showMore }
    let id: String
    let kind: Kind
    let modelID: String
    var title = ""
    var help = ""
    var children: [SidebarTreeNode] = []
    var menuItems: [SidebarMenuItem] = []
    var status: SidebarSessionStatus = .idle
    var isUnread = false
    var isPinned = false

    init(id: String, kind: Kind, modelID: String) {
        self.id = id
        self.kind = kind
        self.modelID = modelID
    }

    var presentation: SidebarTreePresentation {
        SidebarTreePresentation(
            id: id, title: title, help: help, status: status, isUnread: isUnread,
            isPinned: isPinned, hasMenu: !menuItems.isEmpty)
    }
}

struct SidebarTreePresentation: Equatable {
    let id: String
    let title: String
    let help: String
    let status: SidebarSessionStatus
    let isUnread: Bool
    let isPinned: Bool
    let hasMenu: Bool
}

final class SidebarTreeOutlineView: NSOutlineView {
    weak var treeCoordinator: SidebarOutlineView.Coordinator?

    override func menu(for event: NSEvent) -> NSMenu? {
        treeCoordinator?.contextMenu(for: row(at: convert(event.locationInWindow, from: nil)))
    }

    override func keyDown(with event: NSEvent) {
        if event.keyCode == 109, event.modifierFlags.contains(.shift) {
            treeCoordinator?.showSelectedRowMenu()
            return
        }
        if event.keyCode == 36 || event.keyCode == 76, treeCoordinator?.activateSelectedRow() == true { return }
        if event.keyCode == 115 || event.keyCode == 119 {
            let destination = event.keyCode == 115 ? 0 : numberOfRows - 1
            guard destination >= 0, destination < numberOfRows else { return }
            selectRowIndexes(IndexSet(integer: destination), byExtendingSelection: false)
            scrollRowToVisible(destination)
            return
        }
        super.keyDown(with: event)
    }
}

private final class SidebarTreeRowView: NSTableRowView {
    var hasMenu = false
    var showMenu: (() -> Bool)?

    override func accessibilityPerformShowMenu() -> Bool { showMenu?() ?? false }

    override func isAccessibilitySelectorAllowed(_ selector: Selector) -> Bool {
        if selector == #selector(accessibilityPerformShowMenu) { return hasMenu }
        return super.isAccessibilitySelectorAllowed(selector)
    }

    override func drawSelection(in dirtyRect: NSRect) {
        guard selectionHighlightStyle != .none else { return }
        NSColor.labelColor.withAlphaComponent(isEmphasized ? 0.085 : 0.055).setFill()
        NSBezierPath(roundedRect: bounds.insetBy(dx: 2, dy: 1), xRadius: 6, yRadius: 6).fill()
    }
}

private final class SidebarTreeCellView: NSTableCellView {
    private let folder = NSImageView()
    private let title = NSTextField(labelWithString: "")
    private let indicators = NSStackView()
    private let actionButton = NSButton()
    private let actionContainer = NSView()
    private var hoverTracking: NSTrackingArea?
    private var hasMenu = false
    private var isHovered = false
    private var usesSecondaryTitle = false
    var showMenu: (() -> Bool)?

    override init(frame frameRect: NSRect) {
        super.init(frame: frameRect)
        title.lineBreakMode = .byTruncatingTail
        title.maximumNumberOfLines = 1
        title.setContentCompressionResistancePriority(.defaultLow, for: .horizontal)
        title.setContentHuggingPriority(.defaultLow, for: .horizontal)
        title.isSelectable = false
        title.drawsBackground = false
        textField = title
        folder.contentTintColor = .secondaryLabelColor
        folder.imageScaling = .scaleProportionallyDown
        folder.setContentHuggingPriority(.required, for: .horizontal)
        folder.setAccessibilityElement(false)
        imageView = folder
        indicators.orientation = .horizontal
        indicators.spacing = 5
        indicators.setContentCompressionResistancePriority(.required, for: .horizontal)
        actionButton.bezelStyle = .inline
        actionButton.isBordered = false
        actionButton.image = NSImage(systemSymbolName: "ellipsis", accessibilityDescription: nil)
        actionButton.contentTintColor = .secondaryLabelColor
        actionButton.target = self
        actionButton.action = #selector(openMenu)
        actionButton.translatesAutoresizingMaskIntoConstraints = false
        actionContainer.addSubview(actionButton)
        let content = NSStackView(views: [folder, title, indicators, actionContainer])
        content.orientation = .horizontal
        content.alignment = .centerY
        content.distribution = .fill
        content.spacing = 7
        content.translatesAutoresizingMaskIntoConstraints = false
        addSubview(content)
        NSLayoutConstraint.activate([
            content.leadingAnchor.constraint(equalTo: leadingAnchor, constant: 2),
            content.trailingAnchor.constraint(equalTo: trailingAnchor, constant: -4),
            content.centerYAnchor.constraint(equalTo: centerYAnchor),
            folder.widthAnchor.constraint(equalToConstant: 16),
            folder.heightAnchor.constraint(equalToConstant: 16),
            actionContainer.widthAnchor.constraint(equalToConstant: 24),
            actionContainer.heightAnchor.constraint(equalToConstant: 24),
            actionButton.centerXAnchor.constraint(equalTo: actionContainer.centerXAnchor),
            actionButton.centerYAnchor.constraint(equalTo: actionContainer.centerYAnchor),
            actionButton.widthAnchor.constraint(equalToConstant: 24),
            actionButton.heightAnchor.constraint(equalToConstant: 24),
        ])
    }

    required init?(coder: NSCoder) { fatalError("init(coder:) has not been implemented") }

    override var backgroundStyle: NSView.BackgroundStyle {
        didSet {
            title.textColor = usesSecondaryTitle ? .secondaryLabelColor : .labelColor
            updateMenuVisibility()
        }
    }

    override func updateTrackingAreas() {
        super.updateTrackingAreas()
        if let hoverTracking { removeTrackingArea(hoverTracking) }
        let tracking = NSTrackingArea(
            rect: bounds, options: [.mouseEnteredAndExited, .activeInActiveApp, .inVisibleRect], owner: self,
            userInfo: nil)
        addTrackingArea(tracking)
        hoverTracking = tracking
    }

    override func mouseEntered(with event: NSEvent) {
        isHovered = true
        updateMenuVisibility()
    }
    override func mouseExited(with event: NSEvent) {
        isHovered = false
        updateMenuVisibility()
    }

    func configure(_ node: SidebarTreeNode, font: NSFont) {
        title.font = font
        title.stringValue = node.title
        usesSecondaryTitle = node.kind == .showMore
        title.textColor = usesSecondaryTitle ? .secondaryLabelColor : .labelColor
        folder.isHidden = node.kind != .project
        folder.image = NSImage(systemSymbolName: "folder", accessibilityDescription: "Folder")
        hasMenu = !node.menuItems.isEmpty
        actionContainer.isHidden = !hasMenu
        setAccessibilityCustomActions(
            hasMenu
                ? [
                    NSAccessibilityCustomAction(name: "Show Actions") { [weak self] in
                        self?.accessibilityPerformShowMenu() ?? false
                    }
                ] : [])
        actionButton.toolTip = "Actions for \(node.title)"
        actionButton.setAccessibilityLabel("Actions for \(node.title)")
        for view in indicators.arrangedSubviews {
            indicators.removeArrangedSubview(view)
            view.removeFromSuperview()
        }
        if node.kind == .conversation {
            switch node.status {
            case .loading, .running, .stopping:
                let progress = NSProgressIndicator()
                progress.style = .spinning
                progress.controlSize = .small
                progress.isIndeterminate = true
                progress.startAnimation(nil)
                progress.setAccessibilityLabel(node.status.title)
                indicators.addArrangedSubview(progress)
                progress.widthAnchor.constraint(equalToConstant: 12).isActive = true
                progress.heightAnchor.constraint(equalToConstant: 12).isActive = true
            case .needsApproval:
                addIndicator("hand.raised.fill", color: .systemOrange, help: node.status.title)
            case .loadFailed, .failed:
                addIndicator("exclamationmark.circle.fill", color: .systemOrange, help: node.status.title)
            case .idle, .completed, .cancelled, .closed: break
            }
            if node.isPinned { addIndicator("pin.fill", color: .tertiaryLabelColor, help: "Pinned") }
            if node.isUnread { addIndicator("circle.fill", color: .controlAccentColor, help: "Unread", size: 6) }
        }
        indicators.isHidden = indicators.arrangedSubviews.isEmpty
        let state = node.kind == .conversation ? ". \(node.status.title)\(node.isUnread ? ", unread" : "")" : ""
        toolTip = node.help.isEmpty ? node.title + state : node.help + state
        setAccessibilityLabel(node.title)
        setAccessibilityHelp(toolTip)
        updateMenuVisibility()
    }

    private func addIndicator(_ symbol: String, color: NSColor, help: String, size: CGFloat = 12) {
        let image = NSImageView()
        image.image = NSImage(systemSymbolName: symbol, accessibilityDescription: help)
        image.contentTintColor = color
        image.toolTip = help
        image.setAccessibilityLabel(help)
        indicators.addArrangedSubview(image)
        image.widthAnchor.constraint(equalToConstant: size).isActive = true
        image.heightAnchor.constraint(equalToConstant: size).isActive = true
    }

    private func updateMenuVisibility() {
        let row = superview as? NSTableRowView
        actionButton.isHidden = !hasMenu || !(isHovered || row?.isSelected == true)
    }

    override func accessibilityPerformShowMenu() -> Bool { showMenu?() ?? false }

    override func isAccessibilitySelectorAllowed(_ selector: Selector) -> Bool {
        if selector == #selector(accessibilityPerformShowMenu) { return hasMenu }
        return super.isAccessibilitySelectorAllowed(selector)
    }

    @objc private func openMenu() { _ = showMenu?() }
}

private final class SidebarTreeMenuTarget: NSObject {
    private let item: SidebarMenuItem
    init(_ item: SidebarMenuItem) { self.item = item }
    @objc func invokeMenuAction() {
        guard item.availability.isEnabled else { return }
        item.action?()
    }
}

private func sidebarTreeFont() -> NSFont {
    let size = max(7, 13.5 + ThemeManager.shared.fontSizeDelta)
    let family = ThemeManager.shared.fontNormal.trimmingCharacters(in: .whitespacesAndNewlines)
    switch family.lowercased() {
    case "", "system", "default": return .systemFont(ofSize: size)
    case "system monospaced", "monospaced": return .monospacedSystemFont(ofSize: size, weight: .regular)
    case "system serif", "serif":
        return NSFont.systemFont(ofSize: size).fontDescriptor.withDesign(.serif).flatMap {
            NSFont(descriptor: $0, size: size)
        } ?? .systemFont(ofSize: size)
    case "system rounded", "rounded":
        return NSFont.systemFont(ofSize: size).fontDescriptor.withDesign(.rounded).flatMap {
            NSFont(descriptor: $0, size: size)
        } ?? .systemFont(ofSize: size)
    default: return NSFont(name: family, size: size) ?? .systemFont(ofSize: size)
    }
}
