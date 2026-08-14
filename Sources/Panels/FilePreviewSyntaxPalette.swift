import Foundation

/// The semantic syntax colors shared by the native file editor and diff viewer.
struct FilePreviewSyntaxPalette: Equatable, Sendable {
    let keyword: SIMD3<UInt8>
    let type: SIMD3<UInt8>
    let string: SIMD3<UInt8>
    let number: SIMD3<UInt8>
    let comment: SIMD3<UInt8>
    let function: SIMD3<UInt8>
    let attribute: SIMD3<UInt8>

    var jsonObject: [String: String] {
        [
            "keyword": Self.cssHex(keyword),
            "type": Self.cssHex(type),
            "string": Self.cssHex(string),
            "number": Self.cssHex(number),
            "comment": Self.cssHex(comment),
            "function": Self.cssHex(function),
            "attribute": Self.cssHex(attribute),
        ]
    }

    static let dark = FilePreviewSyntaxPalette(
        keyword: SIMD3(250, 120, 168),
        type: SIMD3(102, 217, 240),
        string: SIMD3(153, 214, 140),
        number: SIMD3(242, 181, 125),
        comment: SIMD3(128, 143, 158),
        function: SIMD3(140, 194, 252),
        attribute: SIMD3(217, 176, 252)
    )

    static let light = FilePreviewSyntaxPalette(
        keyword: SIMD3(168, 33, 112),
        type: SIMD3(33, 107, 140),
        string: SIMD3(33, 128, 51),
        number: SIMD3(158, 92, 13),
        comment: SIMD3(102, 117, 133),
        function: SIMD3(38, 92, 199),
        attribute: SIMD3(115, 69, 168)
    )

    private static func cssHex(_ color: SIMD3<UInt8>) -> String {
        String(format: "#%02x%02x%02x", Int(color.x), Int(color.y), Int(color.z))
    }
}
