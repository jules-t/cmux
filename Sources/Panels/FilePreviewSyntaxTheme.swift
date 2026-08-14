import AppKit
import Foundation

/// Foreground colors for each token kind, tuned for dark and light editor
/// backgrounds. The base (uncolored) text keeps the editor's own foreground.
/// Built and consumed entirely on the main thread, so it does not need to be
/// `Sendable` (which `NSColor` is not).
struct FilePreviewSyntaxTheme {
    let keyword: NSColor
    let type: NSColor
    let string: NSColor
    let number: NSColor
    let comment: NSColor
    let function: NSColor
    let attribute: NSColor

    func color(for kind: FilePreviewSyntaxTokenKind) -> NSColor {
        switch kind {
        case .keyword: return keyword
        case .type: return type
        case .string: return string
        case .number: return number
        case .comment: return comment
        case .function: return function
        case .attribute: return attribute
        }
    }

    /// Decides whether to use the dark palette by inspecting the editor's
    /// foreground color: light text means a dark background, and vice versa.
    /// Sidesteps the `usesClearContentBackground` case where the content
    /// background color is `.clear` and carries no usable luminance.
    static func prefersDarkPalette(foreground: NSColor) -> Bool {
        guard let rgb = foreground.usingColorSpace(.sRGB) else { return true }
        let luminance = 0.2126 * rgb.redComponent
            + 0.7152 * rgb.greenComponent
            + 0.0722 * rgb.blueComponent
        return luminance >= 0.5
    }

    static func theme(prefersDark: Bool) -> FilePreviewSyntaxTheme {
        prefersDark ? .dark : .light
    }

    static let dark = FilePreviewSyntaxTheme(palette: .dark)

    static let light = FilePreviewSyntaxTheme(palette: .light)

    private init(palette: FilePreviewSyntaxPalette) {
        keyword = Self.color(palette.keyword)
        type = Self.color(palette.type)
        string = Self.color(palette.string)
        number = Self.color(palette.number)
        comment = Self.color(palette.comment)
        function = Self.color(palette.function)
        attribute = Self.color(palette.attribute)
    }

    private static func color(_ components: SIMD3<UInt8>) -> NSColor {
        NSColor(
            srgbRed: CGFloat(components.x) / 255,
            green: CGFloat(components.y) / 255,
            blue: CGFloat(components.z) / 255,
            alpha: 1
        )
    }
}
