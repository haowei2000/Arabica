import SwiftUI

struct SidebarResizeHandle: View {
    @Binding var preferredWidth: CGFloat
    let availableWidth: CGFloat
    @State private var dragStartWidth: CGFloat?
    @State private var isHovered = false
    @FocusState private var hasFocus: Bool

    private var width: CGFloat {
        SidebarLayout.resolvedWidth(preferredWidth, availableWidth: availableWidth)
    }

    var body: some View {
        Rectangle()
            .fill(Color.clear)
            .overlay {
                Rectangle()
                    .fill(isHovered || hasFocus || dragStartWidth != nil ? Palette.accent.opacity(0.6) : Palette.rule)
                    .frame(width: 1)
            }
            .frame(width: SidebarLayout.dividerWidth)
            .frame(maxHeight: .infinity)
            .contentShape(Rectangle())
            .focusable()
            .focused($hasFocus)
            .focusEffectDisabled()
            .gesture(
                DragGesture(minimumDistance: 0, coordinateSpace: .global)
                    .onChanged { gesture in
                        if dragStartWidth == nil { dragStartWidth = width }
                        resize(to: (dragStartWidth ?? width) + gesture.translation.width)
                    }
                    .onEnded { _ in dragStartWidth = nil }
            )
            .simultaneousGesture(
                TapGesture(count: 2).onEnded {
                    resize(to: SidebarLayout.defaultWidth)
                }
            )
            .onHover { hovering in
                guard hovering != isHovered else { return }
                isHovered = hovering
                if hovering { NSCursor.resizeLeftRight.push() } else { NSCursor.pop() }
            }
            .onDisappear {
                if isHovered {
                    NSCursor.pop()
                    isHovered = false
                }
            }
            .onKeyPress(.leftArrow) {
                resize(to: width - 16)
                return .handled
            }
            .onKeyPress(.rightArrow) {
                resize(to: width + 16)
                return .handled
            }
            .accessibilityElement(children: .ignore)
            .accessibilityLabel("Sidebar width")
            .accessibilityValue("\(Int(width)) points")
            .accessibilityHint("Drag to resize, or use Left and Right Arrow. Double-click to reset.")
            .accessibilityAdjustableAction { direction in
                switch direction {
                case .increment: resize(to: width + 16)
                case .decrement: resize(to: width - 16)
                @unknown default: break
                }
            }
            .help("Drag to resize sidebar. Double-click to reset.")
    }

    private func resize(to proposedWidth: CGFloat) {
        preferredWidth = SidebarLayout.resolvedWidth(proposedWidth, availableWidth: availableWidth)
    }
}
